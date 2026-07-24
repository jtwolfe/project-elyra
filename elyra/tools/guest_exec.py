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
_STAGE_IGNORE_NAMES = frozenset({"__pycache__", ".stage", ".verify"})
_STAGE_IGNORE_SUFFIXES = frozenset({".pyc", ".pyo"})
_VERIFY_RECORD_NAME = ".verify.json"

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
    """True when ``module`` is a package-relative path (no abs / ``..``)."""
    raw = (module or "").strip()
    if not raw:
        return False
    path = Path(raw)
    if path.is_absolute():
        return False
    if ".." in path.parts or any(p in {"", "."} for p in path.parts if p == ".."):
        return False
    if any(part == ".." for part in path.parts):
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


def resolve_module_file(package_dir: Path, module: str) -> Path | None:
    """Resolve ``module`` under ``package_dir``; None if missing or escapes."""
    if not is_safe_module_rel(module):
        return None
    root = package_dir.resolve()
    candidate = (package_dir / module).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    # Allow omitting ``.py`` when the file exists with that suffix.
    if not module.endswith(".py"):
        with_py = (package_dir / f"{module}.py").resolve()
        try:
            with_py.relative_to(root)
        except ValueError:
            return None
        if with_py.is_file():
            return with_py
    return None


# ---------------------------------------------------------------------------
# Atomic stage into guest-visible tools/
# ---------------------------------------------------------------------------


def stage_package_for_guest(
    paths: ElyraPaths,
    package_dir: Path,
    *,
    strip_verify_record: bool = False,
) -> Path:
    """Copy package into ``sandboxes/sandbox0/tools/<name>/`` (atomic-ish).

    Writes under ``tools/.stage/<name>.<pid>.<token>/`` then renames into place.
    Excludes ``__pycache__`` / ``.pyc``. Optionally strips ``.verify.json``.
    Returns the host destination directory.
    """
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        raise OSError(f"package_dir is not a directory: {package_dir}")
    name = package_dir.name
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise OSError(f"invalid package name for stage: {name!r}")

    host_root = ensure_host_tree(PRIMARY_NAME, paths)
    tools_dir = _ensure_real_subdir(host_root, "tools")
    stage_root = _ensure_real_subdir(host_root, "tools", ".stage")
    token = f"{os.getpid()}.{uuid.uuid4().hex[:8]}"
    work = stage_root / f"{name}.{token}"
    dest = tools_dir / name

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
        # Swap into place: move old dest aside, rename work → dest, drop old.
        backup: Path | None = None
        if dest.exists() or dest.is_symlink():
            backup = stage_root / f"{name}.old.{token}"
            if backup.exists() or backup.is_symlink():
                _safe_rmtree(backup)
            os.rename(dest, backup)
        try:
            os.rename(work, dest)
        except OSError:
            if backup is not None and backup.exists():
                try:
                    os.rename(backup, dest)
                except OSError:
                    pass
            raise
        if backup is not None:
            _safe_rmtree(backup)
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
            error_reason=(
                "guest_nonzero_exit" if isolation else "host_nonzero_exit"
            ),
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
            error_reason=(
                "guest_nonzero_exit" if isolation else "host_nonzero_exit"
            ),
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
    timeout = clamp_tool_timeout(args.get("timeout") if isinstance(args, dict) else None)
    if runner.kind == "sandbox_python":
        return _host_stub_python(runner, args, ctx, package_dir=package_dir, timeout=timeout)
    if runner.kind == "sandbox_shell":
        return _host_stub_shell(runner, args, ctx, package_dir=package_dir, timeout=timeout)
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
        return ToolResult(
            ok=False, payload={}, error_reason="invalid_runner:module"
        )
    if not is_public_function_name(func_name):
        return ToolResult(
            ok=False, payload={}, error_reason="invalid_runner:function"
        )
    mod_path = resolve_module_file(package_dir, module)
    if mod_path is None:
        return ToolResult(
            ok=False,
            payload={"executor_backend": EXECUTOR_BACKEND_HOST_STUB},
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
        return ToolResult(
            ok=False, payload={}, error_reason="invalid_runner:argv"
        )
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
    timeout = clamp_tool_timeout(args.get("timeout") if isinstance(args, dict) else None)

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
        return ToolResult(
            ok=False, payload={}, error_reason="invalid_runner:module"
        )
    if not is_public_function_name(func_name):
        return ToolResult(
            ok=False, payload={}, error_reason="invalid_runner:function"
        )
    # Prefer resolved file name (may add .py) so guest path matches staged bytes.
    mod_file = resolve_module_file(package_dir, module)
    if mod_file is None:
        return ToolResult(
            ok=False,
            payload={"executor_backend": EXECUTOR_BACKEND_MICROSANDBOX},
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
        return ToolResult(
            ok=False, payload={}, error_reason="invalid_runner:argv"
        )
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
            last_iso = _IsolationFailure(
                str(exc), anomaly="sandbox_unavailable"
            )
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
                raise _GuestTimeout(
                    f"guest exec timeout ({timeout:.0f}s)"
                ) from exc
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
                raise _SandboxDeathDuringExec(
                    "empty guest failure (possible crash)"
                )
            return ExecResult(
                exit_code=exit_code,
                stdout_text=stdout,
                stderr_text=stderr,
            )
    except (_IsolationFailure, _SandboxDeathDuringExec, _GuestTimeout):
        raise
    except EnsureLockTimeoutError as exc:
        raise _IsolationFailure(
            f"lock timeout: {exc}", anomaly="lock_timeout"
        ) from exc
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
        if child.name in _STAGE_IGNORE_NAMES:
            continue
        if child.suffix in _STAGE_IGNORE_SUFFIXES:
            continue
        if strip_verify_record and child.name == _VERIFY_RECORD_NAME:
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
            if strip_verify_record and child.name == _VERIFY_RECORD_NAME:
                continue
            _safe_copy_file(child, target)
        else:
            raise OSError(f"refusing to stage special file: {child}")


__all__ = [
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "ENV_TOOL_ARGS",
    "EXECUTOR_BACKEND_HOST_STUB",
    "EXECUTOR_BACKEND_MICROSANDBOX",
    "MAX_TOOL_TIMEOUT_SECONDS",
    "clamp_tool_timeout",
    "GuestIsolationError",
    "GuestTimeoutError",
    "guest_dispatch",
    "guest_exec_raw",
    "guest_module_path",
    "guest_run_argv",
    "guest_tools_package_path",
    "host_stub_dispatch",
    "is_public_function_name",
    "is_safe_module_rel",
    "isolation_unavailable_result",
    "map_python_exec_result",
    "map_shell_exec_result",
    "resolve_module_file",
    "stage_package_for_guest",
]
