"""Discover and execute disk tool packages (bundled + local).

Scope: scan roots, local-over-bundled priority, execute → ToolResult.
In scope: BUNDLED_TOOLS_ROOT assert, drafts never scanned, openai_tools().
Out of scope: promote gates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from elyra.config import ElyraPaths, resolve_paths
from elyra.tools.policy import (
    CONTROL_TOOL_KINDS,
    BundledToolsRootError,
    assert_callable_root,
    is_under_drafts_tree,
    is_valid_tool_name,
    normalize_tool_name,
    resolve_bundled_tools_root,
)
from elyra.tools.runner import (
    BuiltinHandler,
    RunnerSpec,
    dispatch,
    load_runner_json,
    resolve_builtin_handler,
)
from elyra.tools.schema import ToolMeta, load_tool_meta, to_openai_tool
from elyra.tools.types import ToolContext, ToolResult

_LOG = logging.getLogger(__name__)

# Sources for ToolPackage.source
SOURCE_BUNDLED = "bundled"
SOURCE_LOCAL = "local"


@dataclass(frozen=True)
class ToolPackage:
    """One discovered, callable tool package."""

    meta: ToolMeta
    runner: RunnerSpec
    source: str  # bundled | local
    package_dir: Path
    handler: BuiltinHandler | None = None  # resolved builtin only


class ToolRegistry:
    """In-memory catalog of callable tools (bundled + local; never drafts)."""

    def __init__(
        self,
        paths: ElyraPaths | None = None,
        *,
        bundled_root: Path | str | None = None,
        local_root: Path | str | None = None,
    ) -> None:
        """Build registry and scan roots.

        Parameters
        ----------
        paths:
            Elyra home paths (local tools under ``paths.tools_dir / "local"``).
        bundled_root:
            Override for BUNDLED_TOOLS_ROOT (tests / elyra.toml). When None,
            resolve from project tree; missing dir raises BundledToolsRootError.
        local_root:
            Override local tools directory (default ``$ELYRA_HOME/tools/local``).
        """
        self._paths = paths or resolve_paths()
        # Assert bundled root exists at init (S1 editable/repo requirement).
        self._bundled_root = resolve_bundled_tools_root(bundled_root)
        if local_root is not None:
            self._local_root = Path(local_root).expanduser().resolve()
            assert_callable_root(self._local_root, label="local_tools_root")
        else:
            self._local_root = (self._paths.tools_dir / "local").resolve()
        self._by_key: dict[str, ToolPackage] = {}
        self._override_logged: set[str] = set()
        self.reload()

    @property
    def bundled_root(self) -> Path:
        return self._bundled_root

    @property
    def local_root(self) -> Path:
        return self._local_root

    @property
    def paths(self) -> ElyraPaths:
        return self._paths

    def reload(self) -> None:
        """Rescan bundled + local; local names win over bundled (log once)."""
        found: dict[str, ToolPackage] = {}
        # Bundled first; local overwrites.
        for pkg in self._scan_root(self._bundled_root, source=SOURCE_BUNDLED):
            key = normalize_tool_name(pkg.meta.name)
            found[key] = pkg
        for pkg in self._scan_root(self._local_root, source=SOURCE_LOCAL):
            key = normalize_tool_name(pkg.meta.name)
            if key in found and found[key].source == SOURCE_BUNDLED:
                if key not in self._override_logged:
                    _LOG.info(
                        "local tool %r overrides bundled package at %s",
                        pkg.meta.name,
                        found[key].package_dir,
                    )
                    self._override_logged.add(key)
            found[key] = pkg
        self._by_key = found

    def names(self) -> list[str]:
        """Sorted callable tool names."""
        return sorted(p.meta.name for p in self._by_key.values())

    def get(self, name: str) -> ToolPackage | None:
        return self._by_key.get(normalize_tool_name(name))

    def has(self, name: str) -> bool:
        return normalize_tool_name(name) in self._by_key

    def openai_tools(self) -> list[dict[str, Any]]:
        """OpenAI function-tools schema list for chat completions."""
        packages = sorted(self._by_key.values(), key=lambda p: p.meta.name)
        return [to_openai_tool(p.meta) for p in packages]

    def execute(
        self,
        name: str,
        args: dict[str, Any] | None,
        ctx: ToolContext,
    ) -> ToolResult:
        """Run a callable tool; unknown/invalid names return error ToolResult (no raise)."""
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return ToolResult(
                ok=False,
                payload={},
                error_reason="invalid_arguments_type",
            )
        # Fail closed on non-str / malformed names (never AttributeError).
        if not isinstance(name, str) or not is_valid_tool_name(name):
            return ToolResult(ok=False, payload={}, error_reason="invalid_name")
        key = normalize_tool_name(name)
        if not key:
            return ToolResult(ok=False, payload={}, error_reason="invalid_name")
        # Drafts are never in the catalog; explicit name still fails closed.
        pkg = self._by_key.get(key)
        if pkg is None:
            return ToolResult(ok=False, payload={}, error_reason="unknown_tool")

        # Local packages can be deleted out-of-band (operator rm). Rescan once
        # so the ghost entry drops instead of failing mid-stage with OSError.
        if pkg.source == SOURCE_LOCAL and not Path(pkg.package_dir).is_dir():
            try:
                self.reload()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("registry.reload after missing package: %s", exc)
            pkg = self._by_key.get(key)
            if pkg is None:
                return ToolResult(
                    ok=False,
                    payload={"name": name},
                    error_reason="unknown_tool",
                )
            if pkg.source == SOURCE_LOCAL and not Path(pkg.package_dir).is_dir():
                return ToolResult(
                    ok=False,
                    payload={"name": name, "package_dir": str(pkg.package_dir)},
                    error_reason="package_missing",
                )

        # Secrets inject (call-local only). Never merge into guest/host-stub env.
        # Registry does not invent auth_unavailable when secrets are missing.
        secret_env: dict[str, str] = {}
        known_secret_values: list[str] = []
        try:
            from elyra.secrets.inject import (
                redact_tool_result_payload,
                resolve_for_tool,
            )
            from elyra.secrets.policy import TOOL_SECRET_REQUIREMENTS
            from elyra.secrets.store import SecretsStore

            tool_name = pkg.meta.name
            secrets_store = None
            if isinstance(ctx.extras, dict):
                existing = ctx.extras.get("secrets")
                if isinstance(existing, SecretsStore):
                    secrets_store = existing
            if secrets_store is None:
                secrets_store = SecretsStore(self._paths.data_dir)
            if tool_name in TOOL_SECRET_REQUIREMENTS:
                secret_env = resolve_for_tool(tool_name, secrets_store)
            try:
                known_secret_values = list(secrets_store.known_values())
            except Exception as exc:  # noqa: BLE001 — redaction best-effort
                _LOG.debug("secrets known_values failed: %s", exc)
            # Union reserved auth secrets (api key + oauth access/refresh).
            try:
                from elyra.llm.auth import auth_secret_values_for_redaction

                auth_vals = auth_secret_values_for_redaction(self._paths.data_dir)
                if auth_vals:
                    # Prefer provider snapshot when live runtime is present.
                    if isinstance(ctx.extras, dict):
                        provider = ctx.extras.get("provider")
                        snap_fn = getattr(provider, "auth_redaction_values", None)
                        if callable(snap_fn):
                            try:
                                snap = snap_fn()
                                if snap:
                                    auth_vals = list(snap)
                            except Exception:  # noqa: BLE001
                                pass
                    known_secret_values = list(
                        dict.fromkeys([*known_secret_values, *auth_vals])
                    )
            except Exception as exc:  # noqa: BLE001 — redaction best-effort
                _LOG.debug("auth secret redaction union failed: %s", exc)
            if isinstance(ctx.extras, dict):
                ctx.extras["secret_env"] = secret_env
        except Exception as exc:  # noqa: BLE001 — inject must not block tools
            _LOG.warning("secrets inject setup failed: %s", exc)
            if isinstance(ctx.extras, dict):
                ctx.extras["secret_env"] = {}

        result = dispatch(
            pkg.runner,
            args,
            ctx,
            handler=pkg.handler,
            package_dir=pkg.package_dir,
        )

        # Post-dispatch: scrub known secret values from model-visible payload.
        if known_secret_values and isinstance(result.payload, dict):
            try:
                from elyra.secrets.inject import redact_tool_result_payload

                redacted = redact_tool_result_payload(
                    result.payload, known_secret_values
                )
                if redacted != result.payload:
                    result = replace(result, payload=redacted)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("secrets result redaction failed: %s", exc)

        return self._enforce_control_policy(pkg, result)

    def _enforce_control_policy(
        self, pkg: ToolPackage, result: ToolResult
    ) -> ToolResult:
        """Strip loop-control flags unless the tool kind is allowlisted.

        - ``ends_moment`` / ``stop_reason`` / ``arm_wait``: only ``control`` or
          ``speak`` kinds may set them (design: loop trusts execute flags only).
        - ``counts_as_speak``: only ``kind=speak``.

        Applied whenever any restricted flag is set — not only when
        ``ends_moment`` is true — so buggy handlers cannot smuggle wait/speak
        side effects through ordinary read/mutate tools.
        """
        kind = (pkg.meta.kind or "").lower()
        ends_moment = result.ends_moment
        stop_reason = result.stop_reason
        arm_wait = result.arm_wait
        counts_as_speak = result.counts_as_speak

        if kind not in CONTROL_TOOL_KINDS:
            ends_moment = False
            stop_reason = None
            arm_wait = None
        if kind != "speak":
            counts_as_speak = False

        if (
            ends_moment == result.ends_moment
            and stop_reason == result.stop_reason
            and arm_wait is result.arm_wait
            and counts_as_speak == result.counts_as_speak
        ):
            return result
        return replace(
            result,
            ends_moment=ends_moment,
            stop_reason=stop_reason,
            arm_wait=arm_wait,
            counts_as_speak=counts_as_speak,
        )

    def _scan_root(self, root: Path, *, source: str) -> Iterable[ToolPackage]:
        if not root.is_dir():
            return
        # Never treat a drafts tree as a scan root (defense in depth).
        if root.name.casefold() == "drafts":
            _LOG.warning("refusing to scan drafts as callable tools root: %s", root)
            return
        if is_under_drafts_tree(root, tools_dir=self._paths.tools_dir):
            _LOG.warning("refusing to scan path under drafts: %s", root)
            return
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if child.name.casefold() == "drafts":
                continue
            if not is_valid_tool_name(child.name):
                _LOG.warning("skip tool package with invalid name: %s", child)
                continue
            # Symlink (or hard path) into tools/drafts is never callable.
            if is_under_drafts_tree(child, tools_dir=self._paths.tools_dir):
                _LOG.warning(
                    "skip tool package resolving under drafts (not callable): %s",
                    child,
                )
                continue
            # Require schema + runner for a complete package.
            if not (child / "schema.json").is_file():
                continue
            if not (child / "runner.json").is_file():
                continue
            try:
                yield self._load_package(child, source=source)
            except Exception as exc:  # noqa: BLE001 — skip bad packages
                _LOG.warning("skip broken tool package %s: %s", child, exc)

    def _load_package(self, package_dir: Path, *, source: str) -> ToolPackage:
        meta = load_tool_meta(package_dir, default_name=package_dir.name)
        # Directory basename is the canonical callable / OpenAI function name
        # (dogfood: folder name = tool name). Always rewrite so case-only
        # frontmatter mismatches cannot advertise a different casing.
        if meta.name != package_dir.name:
            if normalize_tool_name(meta.name) != normalize_tool_name(package_dir.name):
                _LOG.warning(
                    "tool package dir %s name %r differs; using directory name",
                    package_dir.name,
                    meta.name,
                )
            meta = ToolMeta(
                name=package_dir.name,
                description=meta.description,
                kind=meta.kind,
                package_dir=meta.package_dir,
                parameters=meta.parameters,
            )
        runner = load_runner_json(package_dir)
        # Builtin only allowed for bundled (design: forbid for local promote).
        # Local packages may still load if hand-placed; warn and refuse execute
        # via missing handler? Design: kind builtin forbidden for drafts/local
        # promote — hand-placed local builtin is operator escape hatch. Allow
        # resolve for both so sample tests / overrides work.
        handler: BuiltinHandler | None = None
        if runner.kind == "builtin" and runner.entry:
            try:
                handler = resolve_builtin_handler(runner.entry)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning(
                    "builtin entry resolve failed for %s (%s): %s",
                    package_dir.name,
                    runner.entry,
                    exc,
                )
                # Keep package visible; execute will return error.
        return ToolPackage(
            meta=meta,
            runner=runner,
            source=source,
            package_dir=package_dir,
            handler=handler,
        )


def drafts_dir(paths: ElyraPaths) -> Path:
    """Path to non-callable drafts root (never scanned by ToolRegistry)."""
    return paths.tools_dir / "drafts"


# Re-export for callers that only import registry
__all__ = [
    "SOURCE_BUNDLED",
    "SOURCE_LOCAL",
    "BundledToolsRootError",
    "ToolPackage",
    "ToolRegistry",
    "drafts_dir",
]
