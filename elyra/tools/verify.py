"""Verify draft tool packages (sandbox-staged pytest, hash-bound record).

Scope: stage drafts under ``sandboxes/sandbox0/tools/.verify/<name>/``, run
allowlisted pytest (guest when isolation on + pyenv_ready; host when off),
write ``.verify.json`` only on pass with content_hash of draft tree.

Trust boundary
--------------
- Isolation **on**: for ``sandbox_python``, guest smoke-loads the declared
  module under the **verify** stage tree (import + function callable) before
  pytest (KD-G6). Then guest ``python3 -m pytest`` via warm lifecycle;
  requires ``pyenv_ready`` (curated env includes pytest). Fail closed
  ``guest_pytest_unavailable`` when pyenv missing (KD22). Fail
  ``sandbox_unavailable`` when lifecycle/mount unusable. No host pytest
  fallback when isolation is on. Smoke fail reasons:
  ``verify_guest_module_missing``, ``verify_guest_module_import_failed``,
  ``verify_guest_function_not_found``.
- Isolation **off** (``ELYRA_SANDBOX=0``): host ``sys.executable -m pytest``
  with process-level isolation only (scrubbed env, shell=False) for CI.
  No guest visibility claim; host smoke-import is optional/not required.

Fail-closed mitigations:
  - Host PATH is never merged into the host-stub child env.
  - After pytest, any **new** packages under ``tools/local/`` planted during
    the run are removed and the verify fails (blocks the known
    “pass tests by writing tools/local” promote-bypass).
"""

from __future__ import annotations

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
from elyra.sandbox.paths import (
    GUEST_WORKSPACE_ROOT,
    PRIMARY_NAME,
    ensure_host_tree,
    guest_env,
    isolation_enabled,
)
from elyra.tools.guest_exec import (
    EXECUTOR_BACKEND_HOST_STUB,
    EXECUTOR_BACKEND_MICROSANDBOX,
)
from elyra.tools.package_hash import VERIFY_RECORD_NAME, content_hash
from elyra.tools.policy import DRAFT_ALLOWED_RUNNER_KINDS, is_valid_tool_name
from elyra.tools.registry import drafts_dir
from elyra.tools.schema import load_schema_json

_LOG = logging.getLogger(__name__)

REQUIRED_PACKAGE_FILES = ("TOOL.md", "schema.json", "runner.json")
DEFAULT_VERIFY_TIMEOUT_SECONDS = 120
# Retained log tail written into .verify.json / returned to the model.
_LOG_TAIL_CHARS = 8000

# Match elyra.sandbox.sandbox._MINIMAL_PATH — never merge host PATH.
_MINIMAL_PATH = "/usr/bin:/bin:/usr/local/bin"

# Guest pytest argv after python3.
_GUEST_PYTEST_ARGV = ["-m", "pytest", "tests/", "-q", "--tb=short", "-p", "no:cacheprovider"]

# Guest smoke-load of sandbox_python module (before pytest; isolation on).
_GUEST_SMOKE_TIMEOUT_SECONDS = 30.0


def draft_package_dir(paths: ElyraPaths, name: str) -> Path:
    """Resolved path to ``tools/drafts/<name>/`` (does not require existence)."""
    return (drafts_dir(paths) / name).resolve()


def verify_stage_dir(paths: ElyraPaths, name: str) -> Path:
    """Staging root: ``sandboxes/sandbox0/tools/.verify/<name>/`` (guest-visible RW)."""
    host_root = ensure_host_tree(PRIMARY_NAME, paths)
    return (host_root / "tools" / ".verify" / name).resolve()


def guest_verify_stage_path(name: str) -> str:
    """Guest absolute path for the staged verify package."""
    return f"{GUEST_WORKSPACE_ROOT}/tools/.verify/{name}"


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
    # Shape hygiene for sandbox_python / sandbox_shell (PR4 / KD19–21).
    from elyra.tools.runner import validate_runner_fields

    shape_err = validate_runner_fields(kind, runner)
    if shape_err:
        return shape_err

    # Fail closed when sandbox_python module cannot resolve to a real file.
    # Prevents hollow promote (callable:true) then module_not_found at call time
    # — live dogfood failure mode with nested packages like impl.web_search.
    if kind == "sandbox_python":
        from elyra.tools.guest_exec import resolve_module_file

        module = runner.get("module")
        if isinstance(module, str) and module.strip():
            if resolve_module_file(package_dir, module.strip()) is None:
                return "invalid_runner:module_not_found"
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
    """Wipe and recreate ``tools/.verify/<name>/`` from draft (no .verify.json).

    Stages under the primary host tree so the package is guest-visible when
    isolation is on. Atomic-ish: write under ``.verify/.stage.<name>.*`` then
    replace into place.
    """
    stage = verify_stage_dir(paths, name)
    parent = stage.parent
    parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}.{os.urandom(4).hex()}"
    work = parent / f".stage.{name}.{token}"
    if work.exists() or work.is_symlink():
        if work.is_dir() and not work.is_symlink():
            shutil.rmtree(work)
        else:
            work.unlink(missing_ok=True)
    try:
        shutil.copytree(
            draft_dir,
            work,
            ignore=shutil.ignore_patterns(VERIFY_RECORD_NAME, "__pycache__"),
        )
        for leftover in work.rglob(VERIFY_RECORD_NAME):
            try:
                leftover.unlink()
            except OSError:
                pass
        # Swap into place
        backup: Path | None = None
        if stage.exists() or stage.is_symlink():
            backup = parent / f".old.{name}.{token}"
            if backup.exists() or backup.is_symlink():
                if backup.is_dir() and not backup.is_symlink():
                    shutil.rmtree(backup)
                else:
                    backup.unlink(missing_ok=True)
            os.rename(stage, backup)
        try:
            os.rename(work, stage)
        except OSError:
            if backup is not None and backup.exists():
                try:
                    os.rename(backup, stage)
                except OSError:
                    pass
            raise
        if backup is not None and backup.exists():
            try:
                if backup.is_dir() and not backup.is_symlink():
                    shutil.rmtree(backup)
                else:
                    backup.unlink(missing_ok=True)
            except OSError:
                pass
    finally:
        if work.exists() or work.is_symlink():
            try:
                if work.is_dir() and not work.is_symlink():
                    shutil.rmtree(work)
                else:
                    work.unlink(missing_ok=True)
            except OSError:
                pass
    return stage


def run_staged_pytest(
    stage_dir: Path,
    *,
    timeout_seconds: float,
) -> tuple[int, str, bool]:
    """Host pytest on staged package (isolation off only).

    Returns (rc, combined_log, timed_out).
    argv is fixed: ``[sys.executable, -m, pytest, tests/, -q, --tb=short]``.
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


def guest_verify_module_path(name: str, module_rel: str) -> str:
    """Guest absolute path to a module file under the verify stage tree."""
    rel = Path(module_rel).as_posix().lstrip("/")
    return f"{guest_verify_stage_path(name)}/{rel}"


def _guest_smoke_source(*, guest_script: str, func_name: str) -> str:
    """Build ``python3 -c`` body: import staged module + require callable function.

    Exit codes (KD-G6 / design §4):
      0 — ok
      2 — guest path missing
      3 — function missing / not callable
      other non-zero — import/exec failure
    """
    # Emit stderr on fail exits so guest_exec empty-stream crash heuristic
    # does not rewrite intentional SystemExit(2/3) as sandbox_unavailable.
    return f"""
import importlib.util
import sys
from pathlib import Path

script = Path({guest_script!r})
if not script.is_file():
    print("verify_guest_module_missing", file=sys.stderr)
    raise SystemExit(2)
spec = importlib.util.spec_from_file_location("_elyra_verify_smoke", script)
if spec is None or spec.loader is None:
    print("verify_guest_module_import_failed:no_spec", file=sys.stderr)
    raise SystemExit(1)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fn = getattr(module, {func_name!r}, None)
if fn is None or not callable(fn):
    print("verify_guest_function_not_found", file=sys.stderr)
    raise SystemExit(3)
""".strip()


def _load_runner_json(package_dir: Path) -> dict[str, Any] | None:
    """Load runner.json as a dict, or None on failure."""
    runner_path = Path(package_dir) / "runner.json"
    try:
        with runner_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def run_guest_module_smoke(
    paths: ElyraPaths,
    name: str,
    stage_dir: Path,
    *,
    module: str,
    function: str,
    timeout_seconds: float | None = None,
) -> dict[str, Any] | None:
    """Guest smoke-load of sandbox_python module under verify stage (KD-G6).

    Proves the verify-staged draft is importable on the guest before pytest
    and before writing a passed ``.verify.json``. Not a production stage-once
    gate (that lives on ``stage_package_for_guest``).

    Returns None on success; error dict (``ok=False``, ``error_reason``) on
    failure. Distinct reasons:
      - ``verify_guest_module_missing``
      - ``verify_guest_module_import_failed``
      - ``verify_guest_function_not_found``
    """
    from elyra.sandbox.registry import get_sandbox_lifecycle
    from elyra.tools.guest_exec import (
        GuestIsolationError,
        GuestTimeoutError,
        guest_exec_raw,
        resolve_module_file,
    )

    del paths  # lifecycle bridge; stage is already guest-visible under host tree
    life = get_sandbox_lifecycle()
    if life is None:
        return {
            "ok": False,
            "error_reason": "sandbox_unavailable:lifecycle_unregistered",
            "passed": False,
            "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
        }
    if getattr(life, "client_unusable", False):
        return {
            "ok": False,
            "error_reason": "sandbox_unavailable:client_unusable",
            "passed": False,
            "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
        }

    mod_file = resolve_module_file(stage_dir, module)
    if mod_file is None:
        return {
            "ok": False,
            "error_reason": "verify_guest_module_missing",
            "passed": False,
            "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
            "module": module,
        }
    rel = mod_file.relative_to(Path(stage_dir).resolve()).as_posix()
    guest_script = guest_verify_module_path(name, rel)
    func_name = (function or "run").strip() or "run"
    smoke_src = _guest_smoke_source(guest_script=guest_script, func_name=func_name)
    smoke_timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None and timeout_seconds > 0
        else float(_GUEST_SMOKE_TIMEOUT_SECONDS)
    )
    # Cap smoke at short budget even when verify timeout is large.
    smoke_timeout = min(smoke_timeout, float(_GUEST_SMOKE_TIMEOUT_SECONDS))

    try:
        result = guest_exec_raw(
            "python3",
            ["-B", "-c", smoke_src],
            cwd=guest_verify_stage_path(name),
            timeout=smoke_timeout,
            env=guest_env(),
        )
    except GuestTimeoutError:
        return {
            "ok": False,
            "error_reason": "verify_guest_module_import_failed",
            "passed": False,
            "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
            "log": _combine_log("", "[verify guest smoke timed out]", timed_out=True),
            "timed_out": True,
            "module": module,
            "function": func_name,
            "guest_script": guest_script,
        }
    except GuestIsolationError as exc:
        return {
            "ok": False,
            "error_reason": f"sandbox_unavailable:{exc.message}",
            "passed": False,
            "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
            "anomaly": exc.anomaly,
        }

    exit_code = int(result.exit_code)
    out = str(result.stdout_text or "")
    err = str(result.stderr_text or "")
    log = _combine_log(out, err, timed_out=False)
    if exit_code == 0:
        return None
    if exit_code == 2:
        reason = "verify_guest_module_missing"
    elif exit_code == 3:
        reason = "verify_guest_function_not_found"
    else:
        reason = "verify_guest_module_import_failed"
    return {
        "ok": False,
        "error_reason": reason,
        "passed": False,
        "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
        "log": log,
        "returncode": exit_code,
        "module": module,
        "function": func_name,
        "guest_script": guest_script,
    }


def run_guest_pytest(
    paths: ElyraPaths,
    name: str,
    *,
    timeout_seconds: float,
) -> tuple[int, str, bool] | dict[str, Any]:
    """Guest pytest on staged package. Returns (rc, log, timed_out) or error dict.

    Error dict keys: ``ok=False``, ``error_reason``.
    """
    from elyra.sandbox.pyenv import pyenv_ready
    from elyra.sandbox.registry import get_sandbox_lifecycle
    from elyra.tools.guest_exec import (
        GuestIsolationError,
        GuestTimeoutError,
        guest_exec_raw,
    )

    life = get_sandbox_lifecycle()
    if life is None:
        return {
            "ok": False,
            "error_reason": "sandbox_unavailable:lifecycle_unregistered",
            "passed": False,
            "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
        }
    if getattr(life, "client_unusable", False):
        return {
            "ok": False,
            "error_reason": "sandbox_unavailable:client_unusable",
            "passed": False,
            "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
        }

    host_root = ensure_host_tree(PRIMARY_NAME, paths)
    if not pyenv_ready(host_root):
        return {
            "ok": False,
            "error_reason": "guest_pytest_unavailable",
            "passed": False,
            "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
            "pyenv_ready": False,
        }

    guest_cwd = guest_verify_stage_path(name)
    try:
        result = guest_exec_raw(
            "python3",
            list(_GUEST_PYTEST_ARGV),
            cwd=guest_cwd,
            timeout=float(timeout_seconds),
            env=guest_env(),
        )
    except GuestTimeoutError:
        return (
            -1,
            _combine_log("", "[verify timed out in guest]", timed_out=True),
            True,
        )
    except GuestIsolationError as exc:
        return {
            "ok": False,
            "error_reason": f"sandbox_unavailable:{exc.message}",
            "passed": False,
            "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
            "anomaly": exc.anomaly,
        }

    out = str(result.stdout_text or "")
    err = str(result.stderr_text or "")
    log = _combine_log(out, err, timed_out=False)
    return (int(result.exit_code), log, False)


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
    executor_backend: str | None = None,
) -> Path:
    """Write ``.verify.json`` under the draft package."""
    record: dict[str, Any] = {
        "tool_name": tool_name,
        "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "content_hash": content_hash_value,
        "passed": passed,
        "log": log,
    }
    if executor_backend is not None:
        record["executor_backend"] = executor_backend
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

    Keys: ok, error_reason (optional), content_hash, passed, log, stage_dir,
    executor_backend.
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

    # Snapshot tools/local before smoke/pytest so planted packages fail closed.
    local_before = local_tool_package_names(paths)

    iso = isolation_enabled()
    if iso:
        # KD-G6: guest smoke-load sandbox_python under .verify/ before pytest
        # and before writing a passed .verify.json (verify-stage importability).
        runner = _load_runner_json(stage)
        kind = (
            str((runner or {}).get("kind") or "").strip().lower()
            if runner is not None
            else ""
        )
        if kind == "sandbox_python" and runner is not None:
            module = str(runner.get("module") or "").strip()
            function = str(runner.get("function") or "run").strip() or "run"
            if module:
                smoke_err = run_guest_module_smoke(
                    paths,
                    name,
                    stage,
                    module=module,
                    function=function,
                    timeout_seconds=timeout,
                )
                if smoke_err is not None:
                    smoke_err.setdefault("stage_dir", str(stage))
                    smoke_err.setdefault("content_hash", content_hash(draft_dir))
                    return smoke_err

        guest_result = run_guest_pytest(paths, name, timeout_seconds=timeout)
        if isinstance(guest_result, dict):
            guest_result.setdefault("stage_dir", str(stage))
            guest_result.setdefault("content_hash", content_hash(draft_dir))
            return guest_result
        rc, log, timed_out = guest_result
        backend = EXECUTOR_BACKEND_MICROSANDBOX
    else:
        rc, log, timed_out = run_staged_pytest(stage, timeout_seconds=timeout)
        backend = EXECUTOR_BACKEND_HOST_STUB

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
            "executor_backend": backend,
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
            "executor_backend": backend,
        }

    write_verify_record(
        draft_dir,
        tool_name=name,
        content_hash_value=tree_hash,
        passed=True,
        log=log,
        executor_backend=backend,
    )
    return {
        "ok": True,
        "passed": True,
        "content_hash": tree_hash,
        "log": log,
        "returncode": rc,
        "stage_dir": str(stage),
        "tool_name": name,
        "executor_backend": backend,
    }


__all__ = [
    "DEFAULT_VERIFY_TIMEOUT_SECONDS",
    "VERIFY_RECORD_NAME",
    "content_hash",
    "delete_verify_record",
    "draft_package_dir",
    "guest_verify_module_path",
    "guest_verify_stage_path",
    "load_verify_record",
    "local_tool_package_names",
    "remove_planted_local_packages",
    "run_guest_module_smoke",
    "run_guest_pytest",
    "run_staged_pytest",
    "scrubbed_verify_env",
    "stage_draft_for_verify",
    "validate_draft_package",
    "verify_draft_tool",
    "verify_stage_dir",
    "write_verify_record",
]
