"""Verify draft tool packages (sandbox-staged pytest, hash-bound record).

Scope: stage drafts under data/sandbox/.verify/, run allowlisted pytest,
write .verify.json only on pass with content_hash of draft tree.
Out of scope: promote, install_tool_draft writes, registry scan,
container/namespace isolation for the verify child (S1 process-level only).

Trust boundary (S1)
-------------------
Verify runs package tests as a host subprocess with process-level isolation
only: ``shell=False``, scrubbed env matching ``Sandbox.run`` (no host PATH
merge, no secret inherit), ``cwd`` = staged package under
``data/sandbox/.verify/<name>/``. The child is **not** a chroot/container;
it can open absolute host paths and use the network (same residual as
sandbox ``run``).

Fail-closed mitigations in S1:
  - Host PATH is never merged into the child env.
  - After pytest, any **new** packages under ``tools/local/`` planted during
    the run are removed and the verify fails (blocks the known
    “pass tests by writing tools/local” promote-bypass).
  - Full FS/network isolation is out of scope until stronger sandbox hardening.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths
from elyra.tools.policy import DRAFT_ALLOWED_RUNNER_KINDS, is_valid_tool_name
from elyra.tools.registry import drafts_dir
from elyra.tools.schema import load_schema_json

_LOG = logging.getLogger(__name__)

VERIFY_RECORD_NAME = ".verify.json"
REQUIRED_PACKAGE_FILES = ("TOOL.md", "schema.json", "runner.json")
DEFAULT_VERIFY_TIMEOUT_SECONDS = 120
# Retained log tail written into .verify.json / returned to the model.
_LOG_TAIL_CHARS = 8000

# Match elyra.sandbox.sandbox._MINIMAL_PATH — never merge host PATH.
_MINIMAL_PATH = "/usr/bin:/bin:/usr/local/bin"


def draft_package_dir(paths: ElyraPaths, name: str) -> Path:
    """Resolved path to ``tools/drafts/<name>/`` (does not require existence)."""
    return (drafts_dir(paths) / name).resolve()


def verify_stage_dir(paths: ElyraPaths, name: str) -> Path:
    """Staging root: ``data/sandbox/.verify/<name>/``."""
    return (paths.data_dir / "sandbox" / ".verify" / name).resolve()


def content_hash(package_dir: Path) -> str:
    """SHA-256 over sorted ``(relpath, bytes)`` excluding ``.verify.json``.

    Paths use POSIX separators relative to ``package_dir``. Directories are
    not hashed; only regular files participate.
    """
    package_dir = Path(package_dir)
    entries: list[tuple[str, Path]] = []
    if not package_dir.is_dir():
        return hashlib.sha256().hexdigest()
    for path in package_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(package_dir).as_posix()
        # Exclude verify sidecar anywhere named .verify.json
        if path.name == VERIFY_RECORD_NAME or rel == VERIFY_RECORD_NAME:
            continue
        entries.append((rel, path))
    digest = hashlib.sha256()
    for rel, path in sorted(entries, key=lambda item: item[0]):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_verify_record(package_dir: Path) -> dict[str, Any] | None:
    """Load ``.verify.json`` if present and a JSON object; else None."""
    path = Path(package_dir) / VERIFY_RECORD_NAME
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("unreadable verify record %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def delete_verify_record(package_dir: Path) -> bool:
    """Remove ``.verify.json`` if present. Returns True if deleted."""
    path = Path(package_dir) / VERIFY_RECORD_NAME
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError as exc:
        _LOG.warning("failed to delete verify record %s: %s", path, exc)
        return False


def validate_draft_package(package_dir: Path) -> str | None:
    """Return error_reason if draft package is incomplete or illegal for promote.

    Checks required files, tests/, schema parse, and runner.kind allowlist
    (sandbox_shell | sandbox_python only — builtin forbidden for drafts).
    """
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        return "draft_missing"
    for filename in REQUIRED_PACKAGE_FILES:
        if not (package_dir / filename).is_file():
            return f"incomplete_package:missing_{filename}"
    tests_dir = package_dir / "tests"
    if not tests_dir.is_dir():
        return "incomplete_package:missing_tests"
    # At least one test file under tests/ (optional strictness: dir exists is enough
    # for package shape; pytest may collect zero tests and still exit 0 — keep shape).
    try:
        load_schema_json(package_dir)
    except FileNotFoundError:
        return "incomplete_package:missing_schema.json"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return f"invalid_schema:{type(exc).__name__}"

    runner_path = package_dir / "runner.json"
    try:
        with runner_path.open(encoding="utf-8") as handle:
            runner = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return f"invalid_runner:{type(exc).__name__}"
    if not isinstance(runner, dict):
        return "invalid_runner:not_object"
    kind = str(runner.get("kind") or "").strip().lower()
    if kind == "builtin":
        return "builtin_kind_forbidden"
    if kind not in DRAFT_ALLOWED_RUNNER_KINDS:
        return f"invalid_runner_kind:{kind or 'missing'}"
    return None


def scrubbed_verify_env(*, home: Path | str) -> dict[str, str]:
    """Minimal child env matching ``Sandbox.run`` scrubbing.

    Host env is **never** merged (no host PATH append). ``sys.executable`` is
    absolute in argv, so the interpreter parent need not be on PATH.
    PYTHONPATH and other loader keys are left unset (not inherited).
    """
    return {
        "PATH": _MINIMAL_PATH,
        "HOME": str(home),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "TERM": "dumb",
    }


# Backward-compatible private alias
_scrubbed_verify_env = scrubbed_verify_env


def local_tool_package_names(paths: ElyraPaths) -> frozenset[str]:
    """Directory basenames under ``tools/local/`` (non-dot dirs only)."""
    local_root = (paths.tools_dir / "local").resolve()
    if not local_root.is_dir():
        return frozenset()
    names: set[str] = set()
    try:
        for child in local_root.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                names.add(child.name)
    except OSError:
        return frozenset()
    return frozenset(names)


def remove_planted_local_packages(
    paths: ElyraPaths, planted: frozenset[str]
) -> list[str]:
    """Best-effort remove newly planted ``tools/local/<name>/`` dirs. Returns removed."""
    removed: list[str] = []
    local_root = (paths.tools_dir / "local").resolve()
    for name in sorted(planted):
        if not is_valid_tool_name(name):
            continue
        target = (local_root / name).resolve()
        try:
            if not target.is_relative_to(local_root):
                continue
            if target.is_dir():
                shutil.rmtree(target)
                removed.append(name)
        except OSError as exc:
            _LOG.warning("failed to remove planted local package %s: %s", name, exc)
    return removed


def stage_draft_for_verify(paths: ElyraPaths, name: str, draft_dir: Path) -> Path:
    """Wipe and recreate ``data/sandbox/.verify/<name>/`` from draft (no .verify.json)."""
    stage = verify_stage_dir(paths, name)
    if stage.exists():
        shutil.rmtree(stage)
    stage.parent.mkdir(parents=True, exist_ok=True)
    # copytree then strip verify sidecar if present
    shutil.copytree(draft_dir, stage, ignore=shutil.ignore_patterns(VERIFY_RECORD_NAME))
    # Defense: remove any nested .verify.json that ignore_patterns missed
    for leftover in stage.rglob(VERIFY_RECORD_NAME):
        try:
            leftover.unlink()
        except OSError:
            pass
    return stage


def run_staged_pytest(
    stage_dir: Path,
    *,
    timeout_seconds: float,
) -> tuple[int, str, bool]:
    """Run allowlisted pytest on staged package. Returns (rc, combined_log, timed_out).

    argv is fixed: ``[sys.executable, -m, pytest, tests/, -q, --tb=short]``.
    ``shell=False``; cwd = staged root; env scrubbed like sandbox (no host PATH).
    Never runs against repo tests/. Process-level isolation only (see module
    trust boundary).
    """
    argv = [
        sys.executable,
        "-m",
        "pytest",
        "tests/",
        "-q",
        "--tb=short",
        "-p",
        "no:cacheprovider",
    ]
    env = scrubbed_verify_env(home=stage_dir)
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            cwd=str(stage_dir),
            env=env,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", errors="replace")
        err = (exc.stderr or b"").decode("utf-8", errors="replace")
        log = _combine_log(out, err, timed_out=True)
        return (-1, log, True)
    out = completed.stdout.decode("utf-8", errors="replace")
    err = completed.stderr.decode("utf-8", errors="replace")
    log = _combine_log(out, err, timed_out=False)
    return (int(completed.returncode), log, False)


def _combine_log(stdout: str, stderr: str, *, timed_out: bool) -> str:
    parts: list[str] = []
    if timed_out:
        parts.append("[verify timed out]")
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(stderr)
    text = "\n".join(parts).strip()
    if len(text) > _LOG_TAIL_CHARS:
        text = text[-_LOG_TAIL_CHARS:]
    return text


def write_verify_record(
    package_dir: Path,
    *,
    tool_name: str,
    content_hash_value: str,
    passed: bool,
    log: str,
) -> Path:
    """Write ``.verify.json`` under the draft package."""
    record = {
        "tool_name": tool_name,
        "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "content_hash": content_hash_value,
        "passed": passed,
        "log": log,
    }
    path = Path(package_dir) / VERIFY_RECORD_NAME
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def verify_draft_tool(
    paths: ElyraPaths,
    name: str,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Full verify algorithm. Returns a result dict for the tool handler.

    Keys: ok, error_reason (optional), content_hash, passed, log, stage_dir.
    On pass, writes ``.verify.json`` with passed=true. On fail, does not write
    a passed record (no passed:true file left behind).
    """
    if not isinstance(name, str) or not is_valid_tool_name(name):
        return {"ok": False, "error_reason": "invalid_name", "passed": False}
    name = name.strip()
    draft_dir = draft_package_dir(paths, name)
    if not draft_dir.is_dir():
        return {"ok": False, "error_reason": "draft_missing", "passed": False}

    shape_err = validate_draft_package(draft_dir)
    if shape_err is not None:
        return {"ok": False, "error_reason": shape_err, "passed": False}

    timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(DEFAULT_VERIFY_TIMEOUT_SECONDS)
    )
    if timeout <= 0:
        timeout = float(DEFAULT_VERIFY_TIMEOUT_SECONDS)

    try:
        stage = stage_draft_for_verify(paths, name, draft_dir)
    except OSError as exc:
        _LOG.warning("stage failed for %s: %s", name, exc)
        return {
            "ok": False,
            "error_reason": f"stage_failed:{type(exc).__name__}",
            "passed": False,
        }

    # Snapshot tools/local before pytest so planted packages fail closed.
    local_before = local_tool_package_names(paths)

    rc, log, timed_out = run_staged_pytest(stage, timeout_seconds=timeout)
    tree_hash = content_hash(draft_dir)

    local_after = local_tool_package_names(paths)
    planted = frozenset(local_after - local_before)
    if planted:
        removed = remove_planted_local_packages(paths, planted)
        _LOG.warning(
            "verify %s: tests planted tools/local packages %s (removed=%s)",
            name,
            sorted(planted),
            removed,
        )
        return {
            "ok": False,
            "error_reason": "verify_local_planted",
            "passed": False,
            "content_hash": tree_hash,
            "log": log,
            "returncode": rc,
            "stage_dir": str(stage),
            "planted": sorted(planted),
            "planted_removed": removed,
        }

    passed = rc == 0 and not timed_out
    if not passed:
        reason = "verify_timeout" if timed_out else "verify_failed"
        return {
            "ok": False,
            "error_reason": reason,
            "passed": False,
            "content_hash": tree_hash,
            "log": log,
            "returncode": rc,
            "stage_dir": str(stage),
        }

    write_verify_record(
        draft_dir,
        tool_name=name,
        content_hash_value=tree_hash,
        passed=True,
        log=log,
    )
    return {
        "ok": True,
        "passed": True,
        "content_hash": tree_hash,
        "log": log,
        "returncode": rc,
        "stage_dir": str(stage),
        "tool_name": name,
    }


__all__ = [
    "DEFAULT_VERIFY_TIMEOUT_SECONDS",
    "VERIFY_RECORD_NAME",
    "content_hash",
    "delete_verify_record",
    "draft_package_dir",
    "load_verify_record",
    "local_tool_package_names",
    "remove_planted_local_packages",
    "scrubbed_verify_env",
    "stage_draft_for_verify",
    "validate_draft_package",
    "verify_draft_tool",
    "verify_stage_dir",
    "write_verify_record",
]
