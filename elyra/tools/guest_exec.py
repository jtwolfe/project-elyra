"""Guest exec helpers for sandbox_python / sandbox_shell runners.

Scope: atomic package stage into host ``sandboxes/sandbox0/tools/``, guest
exec via lifecycle bridge, host-stub path when ``ELYRA_SANDBOX=0`` (KD19),
args bridge ``ELYRA_TOOL_ARGS`` (KD20), return map (KD21).

Trust boundary
--------------
- Isolation **on**: guest-only; fail closed when lifecycle missing/unusable.
  No silent host fallback (KD6).
- Isolation **off** (``ELYRA_SANDBOX=0``): host stub for tests/CI only.
- Staging never follows symlinks out of the host tree (symlink-hardened copy).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from elyra.config import ElyraPaths, resolve_paths
from elyra.sandbox.errors import (
    BridgeReentrancyError,
    BridgeShutdownError,
    BridgeTimeoutError,
    EnsureLockTimeoutError,
    SandboxClientUnusableError,
    SandboxError,
)
from elyra.sandbox.paths import (
    GUEST_ENV_SANDBOX_ROOT,
    GUEST_WORKSPACE_ROOT,
    PRIMARY_NAME,
    ensure_host_tree,
    guest_env,
)
from elyra.sandbox.protocol import ExecResult
from elyra.sandbox.registry import get_sandbox_lifecycle
from elyra.tools.package_hash import VERIFY_RECORD_NAME, content_hash
from elyra.tools.types import ToolContext, ToolResult


class _RunnerLike(Protocol):
    """Minimal runner surface used by guest/host dispatch (avoids cycle)."""

    kind: str
    argv: list[str] | None
    module: str | None
    function: str | None


_LOG = logging.getLogger(__name__)

# Package-runner timeouts (DESIGN: default 30s, cap 60s).
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
MAX_TOOL_TIMEOUT_SECONDS = 60.0
_BRIDGE_SLACK_SECONDS = 5.0
_RECONNECT_BACKOFF_SECONDS = 0.05

# Stream tails retained in ToolResult payloads.
_STREAM_TAIL_CHARS = 8_000
_MINIMAL_PATH = "/usr/bin:/bin:/usr/local/bin"

# Names/suffixes excluded from staged package trees.
# Runtime stage marker is never copied from source; written after successful stage.
STAGE_MARKER_NAME = ".elyra_stage.json"
_STAGE_IGNORE_NAMES = frozenset({"__pycache__", ".stage", ".verify", STAGE_MARKER_NAME})
_STAGE_IGNORE_SUFFIXES = frozenset({".pyc", ".pyo"})
_STAGE_MARKER_SCHEMA_VERSION = 1

EXECUTOR_BACKEND_MICROSANDBOX = "microsandbox"
EXECUTOR_BACKEND_HOST_STUB = "host_stub"

ENV_TOOL_ARGS = "ELYRA_TOOL_ARGS"


# ---------------------------------------------------------------------------
# Timeouts / validation helpers (shared with runner shapes)
# ---------------------------------------------------------------------------


def clamp_tool_timeout(timeout: object | None) -> float:
    """Return a finite tool timeout in (0, MAX], default 30s."""
    if timeout is None:
        return DEFAULT_TOOL_TIMEOUT_SECONDS
    if isinstance(timeout, bool):
        return DEFAULT_TOOL_TIMEOUT_SECONDS
    try:
        value = float(timeout)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_TOOL_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_TOOL_TIMEOUT_SECONDS
    return min(value, MAX_TOOL_TIMEOUT_SECONDS)


def is_safe_module_rel(module: str) -> bool:
    """True when ``module`` is a package-relative path or dotted import (no abs / ``..``).

    Accepts both path form (``impl/main.py``, ``impl/main``) and Python dotted
    import form (``impl.main``). Rejects absolute paths and parent traversal.
    """
    raw = (module or "").strip().replace("\\", "/")
    if not raw:
        return False
    # Absolute POSIX or Windows drive — Path catches most; also reject leading /
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
        return False
    path = Path(raw)
    if path.is_absolute():
        return False
    if any(part == ".." for part in path.parts):
        return False
    # Empty segments (e.g. "a//b" or leading/trailing slash leftovers)
    if any(part == "" for part in path.parts):
        return False
    return True


def is_public_function_name(name: str) -> bool:
    """True for a public identifier suitable as RunnerSpec.function."""
    raw = (name or "").strip()
    if not raw or not raw.isidentifier():
        return False
    if raw.startswith("_"):
        return False
    if raw.startswith("__") and raw.endswith("__"):
        return False
    return True


def _module_rel_candidates(module: str) -> list[str]:
    """Candidate relative paths for a runner ``module`` field.

    Order prefers the literal path (and ``.py`` suffix), then dotted-import
    conversion (``impl.web_search`` → ``impl/web_search.py``). That matches
    create-tool / fixture language and live dogfood packages.
    """
    raw = (module or "").strip().replace("\\", "/")
    if not raw:
        return []
    out: list[str] = []

    def add(rel: str) -> None:
        rel = rel.strip().replace("\\", "/")
        if rel and rel not in out:
            out.append(rel)

    add(raw)
    if not raw.endswith(".py"):
        add(f"{raw}.py")

    # Dotted Python module → posix path under package (only when not already a path).
    if "/" not in raw and not raw.endswith(".py"):
        parts = raw.split(".")
        if len(parts) >= 1 and all(p.isidentifier() for p in parts):
            as_path = "/".join(parts)
            add(as_path)
            add(f"{as_path}.py")
    return out


def resolve_module_file(package_dir: Path, module: str) -> Path | None:
    """Resolve ``module`` under ``package_dir``; None if missing or escapes.

    ``module`` may be a relative path (``impl/main.py``) or a dotted import
    path (``impl.main`` → ``impl/main.py``). First existing regular file wins.
    """
    if not is_safe_module_rel(module):
        return None
    root = package_dir.resolve()
    for rel in _module_rel_candidates(module):
        if not is_safe_module_rel(rel):
            continue
        candidate = (package_dir / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Atomic stage into guest-visible tools/ (content-hash skip gate)
# ---------------------------------------------------------------------------


def load_stage_marker(dest: Path) -> dict[str, Any] | None:
    """Load ``.elyra_stage.json`` if present and a valid JSON object; else None.

    Corrupt / unreadable markers return None (never skip — fail closed to restage).
    """
    path = Path(dest) / STAGE_MARKER_NAME
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("unreadable stage marker %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_complete_stage_marker(
    dest: Path,
    *,
    content_hash_value: str,
    package_name: str,
) -> None:
    """Write complete stage marker only after a successful stage/refresh."""
    payload = {
        "schema_version": _STAGE_MARKER_SCHEMA_VERSION,
        "incomplete": False,
        "content_hash": content_hash_value,
        "staged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "package_name": package_name,
    }
    marker_path = Path(dest) / STAGE_MARKER_NAME
    marker_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _unlink_stage_marker(dest: Path) -> None:
    """Invalidate any complete claim before mutate (missing marker ⇒ never skip)."""
    marker = Path(dest) / STAGE_MARKER_NAME
    try:
        marker.unlink(missing_ok=True)
    except OSError as exc:
        _LOG.debug("stage marker unlink failed %s: %s", marker, exc)


def _has_payload_files(dest: Path) -> bool:
    """True if dest has at least one regular file that is not the stage marker."""
    dest = Path(dest)
    if not dest.is_dir():
        return False
    for path in dest.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if path.name == STAGE_MARKER_NAME:
            continue
        return True
    return False


def host_stage_looks_complete(dest: Path, package_dir: Path) -> bool:
    """True when staged dest satisfies source runner expectations (KD-G1).

    Reads **source** ``runner.json`` (not dest's possibly stale copy). Incomplete
    dests must never be skippable.
    """
    dest = Path(dest)
    package_dir = Path(package_dir)
    if not dest.is_dir() or dest.is_symlink():
        return False

    runner_path = package_dir / "runner.json"
    if not runner_path.is_file():
        return False
    try:
        with runner_path.open(encoding="utf-8") as handle:
            runner = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(runner, dict):
        return False

    kind = str(runner.get("kind") or "").strip()
    if kind == "sandbox_python":
        module = runner.get("module")
        if not isinstance(module, str) or not module.strip():
            return False
        if not is_safe_module_rel(module.strip()):
            return False
        return resolve_module_file(dest, module.strip()) is not None

    if kind == "sandbox_shell":
        argv = runner.get("argv")
        if not isinstance(argv, list) or not argv:
            # Absolute-cmd style still needs a non-empty payload tree.
            return _has_payload_files(dest)
        argv0 = str(argv[0]).strip()
        if not argv0:
            return False
        is_absolute = argv0.startswith("/") or (len(argv0) >= 2 and argv0[1] == ":")
        has_dotdot = ".." in Path(argv0).parts
        rel = argv0.replace("\\", "/")
        # Package-relative when no abs / .. and looks like a path (sep or
        # extension) rather than a bare command name like ``python3``.
        looks_package_relative = (
            not is_absolute
            and not has_dotdot
            and ("/" in rel or Path(rel).suffix != "")
        )
        if looks_package_relative:
            candidate = (dest / rel).resolve()
            try:
                candidate.relative_to(dest.resolve())
            except ValueError:
                return False
            return candidate.is_file()
        return _has_payload_files(dest)

    # Unknown / missing kind: dest dir with ≥1 regular payload file.
    if not kind:
        return False
    return _has_payload_files(dest)


def _stage_marker_allows_skip(
    marker: dict[str, Any] | None,
    *,
    src_hash: str,
) -> bool:
    """True when marker claims complete for the given source content hash."""
    if marker is None:
        return False
    if marker.get("schema_version") != _STAGE_MARKER_SCHEMA_VERSION:
        return False
    if marker.get("incomplete") is True:
        return False
    if marker.get("content_hash") != src_hash:
        return False
    return True


def stage_package_for_guest(
    paths: ElyraPaths,
    package_dir: Path,
    *,
    strip_verify_record: bool = False,
    force: bool = False,
) -> Path:
    """Copy package into ``sandboxes/sandbox0/tools/<name>/`` (atomic-ish).

    Content-hash stage gate (KD-G1): when dest already has a complete marker
    whose hash matches the **source** package and the dest looks complete,
    skip re-stage (unless ``force=True``).

    Skip assumes identical stage options: when ``strip_verify_record=True`` and
    dest still has ``.verify.json``, skip is refused so the strip contract is
    honored even if the content hash is unchanged.

    When dest already exists as a real directory, refresh **in place** (no
    top-level ``os.rename(dest, backup)``) so the package dentry is preserved.
    First stage (no dest) still uses ``tools/.stage/<name>.<pid>.<token>/`` then
    renames into place. Marker is unlinked before mutate and written complete
    only after full success (KD-G2).
    Excludes ``__pycache__`` / ``.pyc`` / stage marker. Optionally strips
    ``.verify.json``. Returns the host destination directory.
    """
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        raise OSError(f"package_dir is not a directory: {package_dir}")
    name = package_dir.name
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise OSError(f"invalid package name for stage: {name!r}")

    host_root = ensure_host_tree(PRIMARY_NAME, paths)
    tools_dir = _ensure_real_subdir(host_root, "tools")
    dest = tools_dir / name

    # Always hash SOURCE (never dest) for the gate and marker.
    src_hash = content_hash(package_dir)

    # Refuse skip when strip is requested but dest still carries a verify record.
    strip_needs_restage = strip_verify_record and (
        dest.is_dir() and (dest / VERIFY_RECORD_NAME).is_file()
    )

    if (
        not force
        and not strip_needs_restage
        and dest.is_dir()
        and not dest.is_symlink()
        and _stage_marker_allows_skip(load_stage_marker(dest), src_hash=src_hash)
        and host_stage_looks_complete(dest, package_dir)
    ):
        return dest.resolve()

    # Mutate path: invalidate complete claim BEFORE any restage when dest exists.
    dest_is_real_dir = dest.is_dir() and not dest.is_symlink()
    if dest_is_real_dir:
        _unlink_stage_marker(dest)

    if dest_is_real_dir:
        # Update: NEVER os.rename(dest, backup) — preserve top-level dentry.
        try:
            _in_place_refresh(
                package_dir,
                dest,
                sandbox_root=host_root,
                strip_verify_record=strip_verify_record,
            )
        except OSError:
            # Marker already unlinked; leave absent so next call cannot skip.
            raise
        write_complete_stage_marker(
            dest,
            content_hash_value=src_hash,
            package_name=name,
        )
        return dest.resolve()

    # First stage (no dest), or replace non-dir dest (symlink/file).
    if dest.exists() or dest.is_symlink():
        _safe_rmtree(dest)

    stage_root = _ensure_real_subdir(host_root, "tools", ".stage")
    token = f"{os.getpid()}.{uuid.uuid4().hex[:8]}"
    work = stage_root / f"{name}.{token}"

    if work.exists() or work.is_symlink():
        _safe_rmtree(work)
    work.mkdir(mode=0o755)

    try:
        _safe_copytree_into(
            package_dir,
            work,
            sandbox_root=host_root,
            strip_verify_record=strip_verify_record,
        )
        # dest did not exist (or was cleared); rename work into place.
        os.rename(work, dest)
        write_complete_stage_marker(
            dest,
            content_hash_value=src_hash,
            package_name=name,
        )
    finally:
        if work.exists() or work.is_symlink():
            _safe_rmtree(work)

    return dest.resolve()


def guest_tools_package_path(name: str) -> str:
    """Guest absolute path for a staged package directory."""
    return f"{GUEST_WORKSPACE_ROOT}/tools/{name}"


def guest_module_path(name: str, module: str) -> str:
    """Guest absolute path to staged module file (posix)."""
    rel = Path(module).as_posix().lstrip("/")
    if not rel.endswith(".py"):
        # Guest loader uses the path as given; host resolve may have added .py
        pass
    return f"{GUEST_WORKSPACE_ROOT}/tools/{name}/{rel}"


# ---------------------------------------------------------------------------
# Return map (KD21)
# ---------------------------------------------------------------------------


def map_python_exec_result(
    *,
    exit_code: int,
    stdout: str,
    stderr: str,
    executor_backend: str,
    timed_out: bool = False,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
    isolation: bool = False,
) -> ToolResult:
    """Map guest/host python runner streams → ToolResult (KD21 closed rules)."""
    out_tail = _tail(stdout)
    err_tail = _tail(stderr)
    base_streams: dict[str, Any] = {
        "exit_code": exit_code,
        "returncode": exit_code,
        "stdout": out_tail,
        "stderr": err_tail,
        "executor_backend": executor_backend,
    }
    if timed_out:
        base_streams["timed_out"] = True
    if stdout_truncated:
        base_streams["stdout_truncated"] = True
    if stderr_truncated:
        base_streams["stderr_truncated"] = True

    if timed_out:
        return ToolResult(
            ok=False,
            payload=base_streams,
            error_reason="guest_timeout" if isolation else "host_timeout",
        )

    # Rule 2: non-zero exit
    if exit_code != 0:
        return ToolResult(
            ok=False,
            payload=base_streams,
            error_reason=("guest_nonzero_exit" if isolation else "host_nonzero_exit"),
        )

    stripped = (stdout or "").strip()
    # Rule 6: empty stdout + exit 0
    if not stripped:
        return ToolResult(
            ok=True,
            payload={
                "executor_backend": executor_backend,
                "exit_code": exit_code,
                "returncode": exit_code,
            },
        )

    try:
        parsed: Any = json.loads(stripped)
    except json.JSONDecodeError:
        # Rule 7
        return ToolResult(
            ok=False,
            payload=base_streams,
            error_reason="invalid_guest_json",
        )

    # Rule 5: non-object JSON
    if not isinstance(parsed, dict):
        return ToolResult(
            ok=True,
            payload={
                **base_streams,
                "result": parsed,
            },
        )

    # Rule 3 / 4: object with or without ok
    payload = {**parsed, "executor_backend": executor_backend}
    # Prefer stream tails when payload lacks them
    payload.setdefault("exit_code", exit_code)
    payload.setdefault("returncode", exit_code)
    if "stdout" not in payload:
        payload["stdout"] = out_tail
    if "stderr" not in payload:
        payload["stderr"] = err_tail
    if "ok" in parsed:
        return ToolResult(
            ok=bool(parsed["ok"]),
            payload=payload,
            error_reason=None if parsed["ok"] else "tool_returned_not_ok",
        )
    return ToolResult(ok=True, payload=payload)


def map_shell_exec_result(
    *,
    exit_code: int,
    stdout: str,
    stderr: str,
    executor_backend: str,
    timed_out: bool = False,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
    isolation: bool = False,
) -> ToolResult:
    """Map shell runner streams → ToolResult (exit-based ok)."""
    payload: dict[str, Any] = {
        "exit_code": exit_code,
        "returncode": exit_code,
        "stdout": _tail(stdout),
        "stderr": _tail(stderr),
        "executor_backend": executor_backend,
    }
    if timed_out:
        payload["timed_out"] = True
        return ToolResult(
            ok=False,
            payload=payload,
            error_reason="guest_timeout" if isolation else "host_timeout",
        )
    if stdout_truncated:
        payload["stdout_truncated"] = True
    if stderr_truncated:
        payload["stderr_truncated"] = True
    if exit_code != 0:
        return ToolResult(
            ok=False,
            payload=payload,
            error_reason=("guest_nonzero_exit" if isolation else "host_nonzero_exit"),
        )
    return ToolResult(ok=True, payload=payload)


def isolation_unavailable_result(
    reason: str,
    *,
    anomaly: str | None = None,
) -> ToolResult:
    """Fail-closed isolation ToolResult (no silent host fallback)."""
    detail = reason.strip() or "unavailable"
    anomaly_id = anomaly or detail
    return ToolResult(
        ok=False,
        payload={
            "isolation": True,
            "anomaly": anomaly_id,
            "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
        },
        error_reason=f"sandbox_unavailable:{detail}",
    )


# ---------------------------------------------------------------------------
# Host stub (ELYRA_SANDBOX=0 only — KD19)
# ---------------------------------------------------------------------------


def host_stub_dispatch(
    runner: _RunnerLike,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    package_dir: Path,
) -> ToolResult:
    """Execute sandbox_* runners on the host (tests/CI only)."""
    package_dir = Path(package_dir)
    timeout = clamp_tool_timeout(
        args.get("timeout") if isinstance(args, dict) else None
    )
    if runner.kind == "sandbox_python":
        return _host_stub_python(
            runner, args, ctx, package_dir=package_dir, timeout=timeout
        )
    if runner.kind == "sandbox_shell":
        return _host_stub_shell(
            runner, args, ctx, package_dir=package_dir, timeout=timeout
        )
    return ToolResult(
        ok=False,
        payload={},
        error_reason=f"unknown_runner_kind:{runner.kind}",
    )


def _host_stub_python(
    runner: _RunnerLike,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    package_dir: Path,
    timeout: float,
) -> ToolResult:
    del timeout  # in-process call; timeout reserved for subprocess path
    module = (runner.module or "").strip()
    func_name = (runner.function or "run").strip() or "run"
    if not is_safe_module_rel(module):
        return ToolResult(ok=False, payload={}, error_reason="invalid_runner:module")
    if not is_public_function_name(func_name):
        return ToolResult(ok=False, payload={}, error_reason="invalid_runner:function")
    mod_path = resolve_module_file(package_dir, module)
    if mod_path is None:
        return ToolResult(
            ok=False,
            payload={
                "executor_backend": EXECUTOR_BACKEND_HOST_STUB,
                "module": module,
                "tried": _module_rel_candidates(module),
            },
            error_reason="module_not_found",
        )
    try:
        spec = importlib.util.spec_from_file_location(
            f"_elyra_host_tool_{package_dir.name}", mod_path
        )
        if spec is None or spec.loader is None:
            return ToolResult(
                ok=False,
                payload={"executor_backend": EXECUTOR_BACKEND_HOST_STUB},
                error_reason="module_load_failed",
            )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, func_name, None)
        if fn is None or not callable(fn):
            return ToolResult(
                ok=False,
                payload={"executor_backend": EXECUTOR_BACKEND_HOST_STUB},
                error_reason="function_not_found",
            )
        # KD21: single dict call — not **args
        result = fn(args if isinstance(args, dict) else {})
    except Exception as exc:  # noqa: BLE001 — surface as tool error
        _LOG.exception("host_stub sandbox_python error: %s", package_dir)
        return ToolResult(
            ok=False,
            payload={"executor_backend": EXECUTOR_BACKEND_HOST_STUB},
            error_reason=f"handler_error:{type(exc).__name__}",
        )

    # Serialize like the guest runner so return map is shared.
    if isinstance(result, dict):
        stdout = json.dumps(result)
    else:
        stdout = json.dumps({"result": result})
    return map_python_exec_result(
        exit_code=0,
        stdout=stdout,
        stderr="",
        executor_backend=EXECUTOR_BACKEND_HOST_STUB,
        isolation=False,
    )


def _host_stub_shell(
    runner: _RunnerLike,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    package_dir: Path,
    timeout: float,
) -> ToolResult:
    argv = list(runner.argv or [])
    if not argv or not str(argv[0]).strip():
        return ToolResult(ok=False, payload={}, error_reason="invalid_runner:argv")
    argv = [str(a) for a in argv]

    paths = ctx.paths if ctx.paths is not None else resolve_paths()
    host_root = ensure_host_tree(PRIMARY_NAME, paths)
    tmp_dir = host_root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    args_path: Path | None = None
    try:
        args_path = tmp_dir / f"elyra_tool_args_{uuid.uuid4().hex}.json"
        args_path.write_text(
            json.dumps(args if isinstance(args, dict) else {}),
            encoding="utf-8",
        )
        env = _scrubbed_host_env(
            home=host_root,
            extra={ENV_TOOL_ARGS: str(args_path)},
        )
        # Prefer sandbox.run when available and cwd is root; shell packages need
        # cwd=package_dir so use subprocess with the same scrub policy.
        try:
            completed = subprocess.run(
                argv,
                shell=False,
                cwd=str(package_dir.resolve()),
                env=env,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            return map_shell_exec_result(
                exit_code=int(completed.returncode),
                stdout=completed.stdout.decode("utf-8", errors="replace"),
                stderr=completed.stderr.decode("utf-8", errors="replace"),
                executor_backend=EXECUTOR_BACKEND_HOST_STUB,
                isolation=False,
            )
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"").decode("utf-8", errors="replace")
            err = (exc.stderr or b"").decode("utf-8", errors="replace")
            return map_shell_exec_result(
                exit_code=-1,
                stdout=out,
                stderr=err,
                executor_backend=EXECUTOR_BACKEND_HOST_STUB,
                timed_out=True,
                isolation=False,
            )
        except OSError as exc:
            return ToolResult(
                ok=False,
                payload={"executor_backend": EXECUTOR_BACKEND_HOST_STUB},
                error_reason=f"os_error:{type(exc).__name__}",
            )
    finally:
        if args_path is not None:
            try:
                args_path.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Guest dispatch (isolation on)
# ---------------------------------------------------------------------------


def guest_dispatch(
    runner: _RunnerLike,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    package_dir: Path,
) -> ToolResult:
    """Stage package and exec inside the warm microsandbox (fail closed)."""
    life = get_sandbox_lifecycle()
    if life is None:
        return isolation_unavailable_result("lifecycle_unregistered")
    if getattr(life, "client_unusable", False):
        return isolation_unavailable_result(
            "client_unusable", anomaly="client_unusable"
        )

    paths = ctx.paths if ctx.paths is not None else resolve_paths()
    package_dir = Path(package_dir)
    name = package_dir.name
    timeout = clamp_tool_timeout(
        args.get("timeout") if isinstance(args, dict) else None
    )

    try:
        stage_package_for_guest(paths, package_dir)
    except OSError as exc:
        _LOG.warning("stage failed for %s: %s", name, exc)
        return ToolResult(
            ok=False,
            payload={"executor_backend": EXECUTOR_BACKEND_MICROSANDBOX},
            error_reason=f"stage_failed:{type(exc).__name__}",
        )

    if runner.kind == "sandbox_python":
        return _guest_python(
            life,
            runner,
            args,
            paths=paths,
            package_dir=package_dir,
            timeout=timeout,
        )
    if runner.kind == "sandbox_shell":
        return _guest_shell(
            life,
            runner,
            args,
            paths=paths,
            package_dir=package_dir,
            timeout=timeout,
        )
    return ToolResult(
        ok=False,
        payload={},
        error_reason=f"unknown_runner_kind:{runner.kind}",
    )


def _guest_python(
    life: Any,
    runner: _RunnerLike,
    args: dict[str, Any],
    *,
    paths: ElyraPaths,
    package_dir: Path,
    timeout: float,
) -> ToolResult:
    del paths
    module = (runner.module or "").strip()
    func_name = (runner.function or "run").strip() or "run"
    if not is_safe_module_rel(module):
        return ToolResult(ok=False, payload={}, error_reason="invalid_runner:module")
    if not is_public_function_name(func_name):
        return ToolResult(ok=False, payload={}, error_reason="invalid_runner:function")
    # Prefer resolved file name (may add .py) so guest path matches staged bytes.
    mod_file = resolve_module_file(package_dir, module)
    if mod_file is None:
        return ToolResult(
            ok=False,
            payload={
                "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
                "module": module,
                "tried": _module_rel_candidates(module),
            },
            error_reason="module_not_found",
        )
    rel = mod_file.relative_to(package_dir.resolve()).as_posix()
    guest_script = guest_module_path(package_dir.name, rel)
    runner_src = _guest_python_runner_source(
        guest_script=guest_script,
        func_name=func_name,
        args=args if isinstance(args, dict) else {},
    )
    env = {**guest_env(), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        result = _exec_with_one_reconnect(
            life,
            cmd="python3",
            argv=["-B", "-c", runner_src],
            cwd=GUEST_WORKSPACE_ROOT,
            env=env,
            timeout=timeout,
        )
    except _GuestTimeout as exc:
        return ToolResult(
            ok=False,
            payload={
                "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
                "timed_out": True,
            },
            error_reason="guest_timeout",
        )
    except _IsolationFailure as exc:
        return isolation_unavailable_result(exc.message, anomaly=exc.anomaly)

    return map_python_exec_result(
        exit_code=int(result.exit_code),
        stdout=str(result.stdout_text or ""),
        stderr=str(result.stderr_text or ""),
        executor_backend=EXECUTOR_BACKEND_MICROSANDBOX,
        isolation=True,
    )


def _guest_shell(
    life: Any,
    runner: _RunnerLike,
    args: dict[str, Any],
    *,
    paths: ElyraPaths,
    package_dir: Path,
    timeout: float,
) -> ToolResult:
    argv = list(runner.argv or [])
    if not argv or not str(argv[0]).strip():
        return ToolResult(ok=False, payload={}, error_reason="invalid_runner:argv")
    argv_s = [str(a) for a in argv]
    cmd, cmd_args = argv_s[0], argv_s[1:]

    host_root = ensure_host_tree(PRIMARY_NAME, paths)
    tmp_dir = host_root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    host_args_path = tmp_dir / f"elyra_tool_args_{token}.json"
    guest_args_path = f"{GUEST_WORKSPACE_ROOT}/tmp/elyra_tool_args_{token}.json"

    try:
        host_args_path.write_text(
            json.dumps(args if isinstance(args, dict) else {}),
            encoding="utf-8",
        )
        env = {
            **guest_env(),
            ENV_TOOL_ARGS: guest_args_path,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        guest_cwd = guest_tools_package_path(package_dir.name)
        try:
            result = _exec_with_one_reconnect(
                life,
                cmd=cmd,
                argv=cmd_args,
                cwd=guest_cwd,
                env=env,
                timeout=timeout,
            )
        except _GuestTimeout:
            return ToolResult(
                ok=False,
                payload={
                    "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
                    "timed_out": True,
                },
                error_reason="guest_timeout",
            )
        except _IsolationFailure as exc:
            return isolation_unavailable_result(exc.message, anomaly=exc.anomaly)

        return map_shell_exec_result(
            exit_code=int(result.exit_code),
            stdout=str(result.stdout_text or ""),
            stderr=str(result.stderr_text or ""),
            executor_backend=EXECUTOR_BACKEND_MICROSANDBOX,
            isolation=True,
        )
    finally:
        try:
            host_args_path.unlink(missing_ok=True)
        except OSError:
            pass


def _guest_python_runner_source(
    *,
    guest_script: str,
    func_name: str,
    args: dict[str, Any],
) -> str:
    """Build ``python3 -c`` body: ``result = fn(args)`` (single dict — KD21)."""
    payload = json.dumps(args)
    return f"""
import importlib.util
import json
from pathlib import Path

script = Path({guest_script!r})
spec = importlib.util.spec_from_file_location("_elyra_tool", script)
if spec is None or spec.loader is None:
    raise RuntimeError("unable to load tool module")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fn = getattr(module, {func_name!r})
payload = json.loads({payload!r})
result = fn(payload)
if isinstance(result, dict):
    print(json.dumps(result))
else:
    print(json.dumps({{"result": result}}))
""".strip()


# ---------------------------------------------------------------------------
# Exec + one reconnect (mid-exec death)
# ---------------------------------------------------------------------------


class _IsolationFailure(Exception):
    def __init__(self, message: str, *, anomaly: str = "sandbox_unavailable") -> None:
        super().__init__(message)
        self.message = message
        self.anomaly = anomaly


class _GuestTimeout(Exception):
    pass


class _SandboxDeathDuringExec(Exception):
    pass


def _exec_with_one_reconnect(
    life: Any,
    *,
    cmd: str,
    argv: list[str],
    cwd: str,
    env: Mapping[str, str],
    timeout: float,
) -> ExecResult:
    """Ensure ready, exec once; on mid-exec death invalidate + ensure + retry once.

    Tool timeouts do **not** reconnect (DESIGN).
    """
    last_iso: _IsolationFailure | None = None
    for attempt in range(2):
        if attempt > 0:
            time.sleep(_RECONNECT_BACKOFF_SECONDS)
        try:
            return _exec_once(
                life,
                cmd=cmd,
                argv=argv,
                cwd=cwd,
                env=env,
                timeout=timeout,
            )
        except _GuestTimeout:
            raise
        except _IsolationFailure as exc:
            last_iso = exc
            if attempt == 0:
                _force_ensure_reconnect(life)
            continue
        except _SandboxDeathDuringExec as exc:
            last_iso = _IsolationFailure(str(exc), anomaly="sandbox_unavailable")
            if attempt == 0:
                _force_ensure_reconnect(life)
            continue
    assert last_iso is not None
    raise last_iso


def _force_ensure_reconnect(life: Any) -> None:
    invalidate = getattr(life, "invalidate", None)
    if callable(invalidate):
        try:
            invalidate(PRIMARY_NAME)
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("invalidate failed: %s", exc)


def _exec_once(
    life: Any,
    *,
    cmd: str,
    argv: list[str],
    cwd: str,
    env: Mapping[str, str],
    timeout: float,
) -> ExecResult:
    bridge = life.bridge
    in_guest_exec = False
    try:
        with life.with_ready_sandbox(PRIMARY_NAME) as sb:
            in_guest_exec = True
            bridge_timeout = float(timeout) + _BRIDGE_SLACK_SECONDS
            try:
                result = bridge.run(
                    sb.exec(
                        cmd,
                        list(argv),
                        cwd=cwd,
                        timeout=float(timeout),
                        env=dict(env),
                    ),
                    timeout=bridge_timeout,
                )
            except BridgeTimeoutError as exc:
                raise _GuestTimeout(f"guest exec timeout ({timeout:.0f}s)") from exc
            except (BridgeShutdownError, BridgeReentrancyError) as exc:
                raise _IsolationFailure(
                    f"bridge error: {type(exc).__name__}",
                    anomaly="sandbox_unavailable",
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise _SandboxDeathDuringExec(
                    f"exec error: {type(exc).__name__}"
                ) from exc

            exit_code = int(getattr(result, "exit_code", 1))
            stdout = str(getattr(result, "stdout_text", "") or "")
            stderr = str(getattr(result, "stderr_text", "") or "")
            # Mid-exec crash heuristic: empty streams + non-zero.
            if exit_code != 0 and not stdout.strip() and not stderr.strip():
                raise _SandboxDeathDuringExec("empty guest failure (possible crash)")
            return ExecResult(
                exit_code=exit_code,
                stdout_text=stdout,
                stderr_text=stderr,
            )
    except (_IsolationFailure, _SandboxDeathDuringExec, _GuestTimeout):
        raise
    except EnsureLockTimeoutError as exc:
        raise _IsolationFailure(f"lock timeout: {exc}", anomaly="lock_timeout") from exc
    except SandboxClientUnusableError as exc:
        raise _IsolationFailure(
            f"client unusable: {exc}", anomaly="client_unusable"
        ) from exc
    except BridgeTimeoutError as exc:
        if not in_guest_exec:
            raise _IsolationFailure(
                f"bridge timeout during ensure: {exc}",
                anomaly="sandbox_unavailable",
            ) from exc
        raise _GuestTimeout(f"guest exec timeout ({timeout:.0f}s)") from exc
    except SandboxError as exc:
        raise _IsolationFailure(
            f"sandbox unavailable: {exc}",
            anomaly="sandbox_unavailable",
        ) from exc


# ---------------------------------------------------------------------------
# Builtin run guest helper (KD24)
# ---------------------------------------------------------------------------


def guest_exec_raw(
    cmd: str,
    argv: list[str],
    *,
    cwd: str = GUEST_WORKSPACE_ROOT,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> ExecResult:
    """Low-level guest exec with one reconnect. Raises isolation/timeout errors.

    Callers that need structured ToolResults should catch and map. Used by
    verify guest pytest and advanced helpers.
    """
    life = get_sandbox_lifecycle()
    if life is None:
        raise _IsolationFailure(
            "lifecycle_unregistered", anomaly="lifecycle_unregistered"
        )
    if getattr(life, "client_unusable", False):
        raise _IsolationFailure("client_unusable", anomaly="client_unusable")
    merged = {**guest_env(), "PYTHONDONTWRITEBYTECODE": "1"}
    if env:
        merged.update(dict(env))
    return _exec_with_one_reconnect(
        life,
        cmd=cmd,
        argv=list(argv),
        cwd=cwd,
        env=merged,
        timeout=float(timeout),
    )


def guest_run_argv(
    argv: list[str],
    *,
    timeout: float,
    cwd: str = GUEST_WORKSPACE_ROOT,
) -> ToolResult:
    """Execute argv in the warm guest (builtin ``run`` when isolation on).

    Non-zero exit and timeout are **payload data** with ``ok=True`` (parity with
    host ``Sandbox.run``). Isolation failures return ``ok=False``.
    """
    if not argv or not str(argv[0]).strip():
        return ToolResult(ok=False, payload={}, error_reason="empty_command")
    cmd, cmd_args = str(argv[0]), [str(a) for a in argv[1:]]
    try:
        result = guest_exec_raw(cmd, cmd_args, cwd=cwd, timeout=float(timeout))
    except _GuestTimeout:
        return ToolResult(
            ok=True,
            payload={
                "returncode": -1,
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
                "timed_out": True,
                "stdout_truncated": False,
                "stderr_truncated": False,
                "argv": list(argv),
                "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
            },
        )
    except _IsolationFailure as exc:
        return isolation_unavailable_result(exc.message, anomaly=exc.anomaly)

    exit_code = int(result.exit_code)
    return ToolResult(
        ok=True,
        payload={
            "returncode": exit_code,
            "exit_code": exit_code,
            "stdout": _tail(str(result.stdout_text or "")),
            "stderr": _tail(str(result.stderr_text or "")),
            "timed_out": False,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "argv": list(argv),
            "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
        },
    )


# Public aliases for verify / advanced callers that map timeouts themselves.
GuestTimeoutError = _GuestTimeout
GuestIsolationError = _IsolationFailure


# ---------------------------------------------------------------------------
# FS helpers (symlink-hardened stage)
# ---------------------------------------------------------------------------


def _tail(text: str, limit: int = _STREAM_TAIL_CHARS) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[-limit:]


def _scrubbed_host_env(
    *,
    home: Path,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Minimal child env matching Sandbox.run scrubbing (no secret inherit)."""
    env: dict[str, str] = {
        "PATH": _MINIMAL_PATH,
        "HOME": str(home),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "TERM": "dumb",
        GUEST_ENV_SANDBOX_ROOT: str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if extra:
        for k, v in extra.items():
            key = str(k)
            upper = key.upper()
            if upper in {
                "LD_PRELOAD",
                "LD_LIBRARY_PATH",
                "PYTHONPATH",
                "BASH_ENV",
            }:
                continue
            env[key] = str(v)
    return env


def _safe_path_component(part: str) -> str:
    if not part or part in {".", ".."} or "/" in part or "\\" in part:
        raise OSError(f"invalid stage path component: {part!r}")
    return part


def _ensure_real_subdir(sandbox_root: Path, *rel_parts: str) -> Path:
    """Ensure sandbox_root/rel_parts is a real directory (replace symlinks)."""
    if sandbox_root.is_symlink():
        raise OSError(f"sandbox root must not be a symlink: {sandbox_root}")
    root = sandbox_root.resolve()
    if not root.is_dir():
        raise OSError(f"sandbox root is not a directory: {root}")
    current = root
    for raw in rel_parts:
        part = _safe_path_component(str(raw))
        nxt = current / part
        if nxt.is_symlink():
            nxt.unlink(missing_ok=True)
        if nxt.exists():
            if nxt.is_symlink():
                raise OSError(f"path remained symlink after unlink: {nxt}")
            if not nxt.is_dir():
                nxt.unlink(missing_ok=True)
                nxt.mkdir(mode=0o755)
        else:
            nxt.mkdir(mode=0o755)
        try:
            nxt.resolve().relative_to(root)
        except ValueError as exc:
            raise OSError(f"stage component escapes sandbox: {nxt}") from exc
        current = nxt
    return current


def _safe_unlink(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)


def _safe_rmtree(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    for child in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_symlink():
            child.unlink(missing_ok=True)
    shutil.rmtree(path, ignore_errors=False)


def _safe_copy_file(src: Path, dest: Path) -> None:
    if src.is_symlink():
        raise OSError(f"refusing to stage symlink source: {src}")
    if not src.is_file():
        raise OSError(f"stage source is not a regular file: {src}")
    parent = dest.parent
    if parent.is_symlink() or not parent.is_dir():
        raise OSError(f"stage dest parent is not a real directory: {parent}")
    if dest.exists() or dest.is_symlink():
        if dest.is_dir() and not dest.is_symlink():
            _safe_rmtree(dest)
        else:
            _safe_unlink(dest)
    shutil.copyfile(src, dest, follow_symlinks=False)
    try:
        shutil.copystat(src, dest, follow_symlinks=False)
    except OSError:
        pass
    if dest.is_symlink():
        dest.unlink(missing_ok=True)
        raise OSError(f"stage produced unexpected symlink: {dest}")


def _is_elyra_tmp_name(name: str) -> bool:
    """True for per-file stage temps: ``<name>.elyra_tmp.<token>``."""
    return ".elyra_tmp." in name


def _safe_copy_file_replace(src: Path, dest: Path, *, token: str) -> None:
    """Copy ``src`` onto ``dest`` via sibling temp + ``os.replace`` (atomic)."""
    if src.is_symlink():
        raise OSError(f"refusing to stage symlink source: {src}")
    if not src.is_file():
        raise OSError(f"stage source is not a regular file: {src}")
    parent = dest.parent
    if parent.is_symlink() or not parent.is_dir():
        raise OSError(f"stage dest parent is not a real directory: {parent}")
    if dest.is_symlink():
        dest.unlink(missing_ok=True)
    elif dest.exists() and dest.is_dir() and not dest.is_symlink():
        _safe_rmtree(dest)

    tmp = parent / f"{dest.name}.elyra_tmp.{token}"
    try:
        if tmp.exists() or tmp.is_symlink():
            if tmp.is_dir() and not tmp.is_symlink():
                _safe_rmtree(tmp)
            else:
                _safe_unlink(tmp)
        shutil.copyfile(src, tmp, follow_symlinks=False)
        try:
            shutil.copystat(src, tmp, follow_symlinks=False)
        except OSError:
            pass
        if tmp.is_symlink():
            tmp.unlink(missing_ok=True)
            raise OSError(f"stage produced unexpected symlink: {tmp}")
        os.replace(tmp, dest)
    except BaseException:
        try:
            if tmp.exists() or tmp.is_symlink():
                if tmp.is_dir() and not tmp.is_symlink():
                    _safe_rmtree(tmp)
                else:
                    tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    if dest.is_symlink():
        dest.unlink(missing_ok=True)
        raise OSError(f"stage produced unexpected symlink: {dest}")


def _stage_should_skip_source_entry(child: Path, *, strip_verify_record: bool) -> bool:
    """True when a source entry is excluded from staged payload."""
    if child.name in _STAGE_IGNORE_NAMES:
        return True
    if child.suffix in _STAGE_IGNORE_SUFFIXES:
        return True
    if strip_verify_record and child.name == VERIFY_RECORD_NAME:
        return True
    return False


def _in_place_refresh(
    src: Path,
    dest: Path,
    *,
    sandbox_root: Path,
    strip_verify_record: bool = False,
) -> None:
    """Refresh ``dest`` from ``src`` without renaming the top-level package dir.

    Per-file: write to sibling ``*.elyra_tmp.<token>`` then ``os.replace``.
    After copy, prune dest paths absent from the source payload (keep-set:
    stage marker name only). Always prunes ``__pycache__`` / ``*.pyc`` /
    leftover temps. ``sandbox_root`` is accepted for call-site parity with
    ``_safe_copytree_into`` (escape checks live in callers that create dest).
    """
    del sandbox_root  # dest already under tools/; same contract as copytree
    if src.is_symlink() or not src.is_dir():
        raise OSError(f"stage source tree invalid: {src}")
    if dest.is_symlink() or not dest.is_dir():
        raise OSError(f"stage dest is not a real directory: {dest}")

    token = f"{os.getpid()}.{uuid.uuid4().hex[:8]}"
    desired_files: set[str] = set()
    desired_dirs: set[str] = set()

    def walk_copy(src_dir: Path, rel_prefix: str) -> None:
        for child in sorted(src_dir.iterdir(), key=lambda p: p.name):
            if _stage_should_skip_source_entry(
                child, strip_verify_record=strip_verify_record
            ):
                continue
            if child.is_symlink():
                raise OSError(f"refusing to stage symlink: {child}")
            rel = f"{rel_prefix}/{child.name}" if rel_prefix else child.name
            target = dest / rel
            if child.is_dir():
                desired_dirs.add(rel)
                if target.is_symlink():
                    target.unlink(missing_ok=True)
                if target.exists() and (not target.is_dir() or target.is_symlink()):
                    _safe_unlink(target)
                if not target.exists():
                    target.mkdir(mode=0o755)
                walk_copy(child, rel)
            elif child.is_file():
                desired_files.add(rel)
                parent = target.parent
                if parent.is_symlink() or not parent.is_dir():
                    raise OSError(
                        f"stage dest parent is not a real directory: {parent}"
                    )
                _safe_copy_file_replace(child, target, token=token)
            else:
                raise OSError(f"refusing to stage special file: {child}")

    walk_copy(src, "")
    _prune_stale_stage_payload(
        dest,
        desired_files=desired_files,
        desired_dirs=desired_dirs,
    )


def _prune_stale_stage_payload(
    dest: Path,
    *,
    desired_files: set[str],
    desired_dirs: set[str],
) -> None:
    """Delete dest entries not in the source payload (after in-place copy).

    Keep-set: never prune ``.elyra_stage.json`` as an orphan (caller rewrites
    after success; absent during refresh). Always prune ignore names/suffixes,
    leftover ``*.elyra_tmp.*``, and source-deleted modules/dirs.
    """
    dest = Path(dest)
    if not dest.is_dir() or dest.is_symlink():
        return
    # Bottom-up so files go before their parent dirs.
    for path in sorted(dest.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        try:
            rel = path.relative_to(dest).as_posix()
        except ValueError:
            continue
        # Keep-set: stage marker (rewritten by caller after success).
        if path.name == STAGE_MARKER_NAME:
            continue
        # Always prune stage ignore names / bytecode / refresh temps.
        if (
            path.name in _STAGE_IGNORE_NAMES
            or path.suffix in _STAGE_IGNORE_SUFFIXES
            or _is_elyra_tmp_name(path.name)
        ):
            if path.is_dir() and not path.is_symlink():
                _safe_rmtree(path)
            else:
                _safe_unlink(path)
            continue
        if path.is_symlink() or path.is_file():
            if rel not in desired_files:
                _safe_unlink(path)
            continue
        if path.is_dir():
            if rel not in desired_dirs:
                _safe_rmtree(path)


def _safe_copytree_into(
    src: Path,
    dest: Path,
    *,
    sandbox_root: Path,
    strip_verify_record: bool = False,
) -> None:
    if src.is_symlink() or not src.is_dir():
        raise OSError(f"stage source tree invalid: {src}")
    if dest.is_symlink() or not dest.is_dir():
        raise OSError(f"stage dest is not a real directory: {dest}")
    for child in sorted(src.iterdir(), key=lambda p: p.name):
        if _stage_should_skip_source_entry(
            child, strip_verify_record=strip_verify_record
        ):
            continue
        if child.is_symlink():
            raise OSError(f"refusing to stage symlink: {child}")
        target = dest / child.name
        if child.is_dir():
            if target.is_symlink():
                target.unlink(missing_ok=True)
            if not target.exists():
                target.mkdir(mode=0o755)
            elif not target.is_dir() or target.is_symlink():
                _safe_unlink(target)
                target.mkdir(mode=0o755)
            _safe_copytree_into(
                child,
                target,
                sandbox_root=sandbox_root,
                strip_verify_record=strip_verify_record,
            )
        elif child.is_file():
            _safe_copy_file(child, target)
        else:
            raise OSError(f"refusing to stage special file: {child}")


__all__ = [
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "ENV_TOOL_ARGS",
    "EXECUTOR_BACKEND_HOST_STUB",
    "EXECUTOR_BACKEND_MICROSANDBOX",
    "MAX_TOOL_TIMEOUT_SECONDS",
    "STAGE_MARKER_NAME",
    "clamp_tool_timeout",
    "GuestIsolationError",
    "GuestTimeoutError",
    "guest_dispatch",
    "guest_exec_raw",
    "guest_module_path",
    "guest_run_argv",
    "guest_tools_package_path",
    "host_stage_looks_complete",
    "host_stub_dispatch",
    "is_public_function_name",
    "is_safe_module_rel",
    "isolation_unavailable_result",
    "load_stage_marker",
    "map_python_exec_result",
    "map_shell_exec_result",
    "resolve_module_file",
    "stage_package_for_guest",
    "write_complete_stage_marker",
]
