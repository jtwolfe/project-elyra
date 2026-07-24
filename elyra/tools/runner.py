"""Load runner.json and dispatch tool execution by kind.

Scope: runner metadata, builtin entry import, sandbox_python / sandbox_shell
dispatch (guest when isolation on; host stub when ELYRA_SANDBOX=0).
In scope: allowlisted kinds, ``module:attr`` builtin resolve, shape validation,
``package_dir`` through dispatch, return map via guest_exec.
Out of scope: promote gates.
"""

from __future__ import annotations

import importlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from elyra.tools.policy import ALLOWED_RUNNER_KINDS
from elyra.tools.types import ToolContext, ToolResult

_LOG = logging.getLogger(__name__)

BuiltinHandler = Callable[[dict[str, Any], ToolContext], ToolResult]

_SANDBOX_KINDS = frozenset({"sandbox_shell", "sandbox_python"})


@dataclass(frozen=True)
class RunnerSpec:
    """Parsed runner.json."""

    kind: str
    entry: str | None = None  # module:attr for builtin
    argv: list[str] | None = None  # sandbox_shell
    module: str | None = None  # sandbox_python path under package_dir
    function: str | None = None  # sandbox_python; default "run" at dispatch if None
    raw: dict[str, Any] | None = None


def validate_runner_fields(kind: str, data: dict[str, Any]) -> str | None:
    """Return ``invalid_runner:*`` reason if sandbox runner shape is illegal.

    Used by ``load_runner_json`` (raises) and ``validate_draft_package``.
    """
    kind_n = (kind or "").strip().lower()
    if kind_n == "sandbox_python":
        module = data.get("module")
        if module is None or (isinstance(module, str) and not module.strip()):
            return "invalid_runner:module_missing"
        if not isinstance(module, str):
            return "invalid_runner:module_type"
        from elyra.tools.guest_exec import is_safe_module_rel

        if not is_safe_module_rel(module):
            # Absolute / .. / empty after strip
            path = Path(str(module).strip())
            if path.is_absolute():
                return "invalid_runner:module_absolute"
            if ".." in path.parts:
                return "invalid_runner:module_dotdot"
            return "invalid_runner:module"
        func = data.get("function")
        if func is not None:
            if not isinstance(func, str):
                return "invalid_runner:function_type"
            func_s = func.strip()
            if func_s:
                from elyra.tools.guest_exec import is_public_function_name

                if not is_public_function_name(func_s):
                    return "invalid_runner:function"
        return None

    if kind_n == "sandbox_shell":
        argv = data.get("argv")
        if argv is None:
            return "invalid_runner:argv_missing"
        if not isinstance(argv, list):
            return "invalid_runner:argv_type"
        if not argv:
            return "invalid_runner:argv_empty"
        if not all(isinstance(a, str) for a in argv):
            return "invalid_runner:argv_not_strings"
        if not str(argv[0]).strip():
            return "invalid_runner:argv0_empty"
        return None

    return None


def load_runner_json(package_dir: Path) -> RunnerSpec:
    """Load and validate ``runner.json`` for a package."""
    path = package_dir / "runner.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing runner.json in {package_dir}")
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"runner.json must be a JSON object: {path}")
    kind = str(data.get("kind") or "").strip().lower()
    if kind not in ALLOWED_RUNNER_KINDS:
        raise ValueError(
            f"runner kind {kind!r} not in {sorted(ALLOWED_RUNNER_KINDS)}: {path}"
        )
    entry = data.get("entry")
    if entry is not None:
        entry = str(entry).strip() or None
    argv = data.get("argv")
    if argv is not None and not isinstance(argv, list):
        raise ValueError(f"runner.json argv must be a list: {path}")
    module = data.get("module")
    if module is not None:
        module = str(module).strip() or None
    function_raw = data.get("function")
    function: str | None = None
    if function_raw is not None:
        function = str(function_raw).strip() or None

    if kind == "builtin" and not entry:
        raise ValueError(f"builtin runner requires entry (module:attr): {path}")

    if kind in _SANDBOX_KINDS:
        shape_err = validate_runner_fields(kind, data)
        if shape_err:
            raise ValueError(f"{shape_err}: {path}")
        if kind == "sandbox_python":
            # Default function name is applied at load so RunnerSpec is complete.
            if not function:
                function = "run"
        if kind == "sandbox_shell" and argv is not None:
            argv = [str(a) for a in argv]

    return RunnerSpec(
        kind=kind,
        entry=entry,
        argv=list(argv) if isinstance(argv, list) else None,
        module=module,
        function=function,
        raw=data,
    )


def resolve_builtin_handler(entry: str) -> BuiltinHandler:
    """Import ``module.path:callable_name`` and return the callable."""
    if ":" not in entry:
        raise ValueError(
            f"builtin entry must be 'module.path:attr', got {entry!r}"
        )
    module_name, _, attr = entry.partition(":")
    module_name = module_name.strip()
    attr = attr.strip()
    if not module_name or not attr:
        raise ValueError(f"builtin entry must be 'module.path:attr', got {entry!r}")
    if attr.startswith("_"):
        raise ValueError(f"builtin entry attr must be public (no leading _): {entry!r}")
    mod = importlib.import_module(module_name)
    handler = getattr(mod, attr, None)
    if handler is None or not callable(handler):
        raise ValueError(f"builtin entry not a callable: {entry!r}")
    return handler  # type: ignore[return-value]


def dispatch(
    runner: RunnerSpec,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    handler: BuiltinHandler | None = None,
    package_dir: Path | None = None,
) -> ToolResult:
    """Execute a tool via its runner kind.

    ``handler`` is the pre-resolved builtin callable (registry caches it).
    ``package_dir`` is required for sandbox_python / sandbox_shell.
    """
    if runner.kind == "builtin":
        if handler is None:
            if not runner.entry:
                return ToolResult(
                    ok=False,
                    payload={},
                    error_reason="builtin_entry_missing",
                )
            try:
                handler = resolve_builtin_handler(runner.entry)
            except Exception as exc:  # noqa: BLE001 — surface as tool error
                _LOG.warning("builtin resolve failed for %s: %s", runner.entry, exc)
                return ToolResult(
                    ok=False,
                    payload={},
                    error_reason="builtin_resolve_failed",
                )
        try:
            result = handler(args, ctx)
        except Exception as exc:  # noqa: BLE001 — never raise out of execute
            _LOG.exception("builtin handler error: %s", runner.entry)
            return ToolResult(
                ok=False,
                payload={},
                error_reason=f"handler_error:{type(exc).__name__}",
            )
        if not isinstance(result, ToolResult):
            return ToolResult(
                ok=False,
                payload={},
                error_reason="handler_bad_return",
            )
        return result

    if runner.kind in _SANDBOX_KINDS:
        if package_dir is None:
            return ToolResult(
                ok=False,
                payload={},
                error_reason="package_dir_missing",
            )
        # Lazy import keeps runner loadable without sandbox stack at import time.
        from elyra.sandbox.paths import isolation_enabled
        from elyra.tools.guest_exec import guest_dispatch, host_stub_dispatch

        if not isolation_enabled():
            return host_stub_dispatch(
                runner, args, ctx, package_dir=Path(package_dir)
            )
        return guest_dispatch(runner, args, ctx, package_dir=Path(package_dir))

    return ToolResult(
        ok=False,
        payload={},
        error_reason=f"unknown_runner_kind:{runner.kind}",
    )
