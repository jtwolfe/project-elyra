"""Load runner.json and dispatch tool execution by kind.

Scope: runner metadata, builtin entry import, sandbox_* stubs.
In scope: allowlisted kinds, ``module:attr`` builtin resolve, dispatch.
Out of scope: sandbox_shell/python full impl (PR7+), promote gates.
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


@dataclass(frozen=True)
class RunnerSpec:
    """Parsed runner.json."""

    kind: str
    entry: str | None = None  # module:attr for builtin
    # sandbox_shell / sandbox_python fields reserved for later PRs
    argv: list[str] | None = None
    module: str | None = None
    raw: dict[str, Any] | None = None


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
    if kind == "builtin" and not entry:
        raise ValueError(f"builtin runner requires entry (module:attr): {path}")
    return RunnerSpec(
        kind=kind,
        entry=entry,
        argv=list(argv) if isinstance(argv, list) else None,
        module=module,
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
) -> ToolResult:
    """Execute a tool via its runner kind.

    ``handler`` is the pre-resolved builtin callable (registry caches it).
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

    if runner.kind in {"sandbox_shell", "sandbox_python"}:
        # Full runners land with PR7 / create-tool; fail closed with clear reason.
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"runner_not_implemented:{runner.kind}",
        )

    return ToolResult(
        ok=False,
        payload={},
        error_reason=f"unknown_runner_kind:{runner.kind}",
    )
