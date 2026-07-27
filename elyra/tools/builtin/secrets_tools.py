"""Builtin secrets tools: secrets_list, secrets_set, secrets_delete.

Scope: host entries for tools/bundled secrets_* packages.
In scope: SecretsStore via paths; never echo secret values in ToolResult.
Out of scope: inject (registry), Glass API, keyring backend.
"""

from __future__ import annotations

from typing import Any

from elyra.secrets.policy import MANAGED_BY_USER, normalize_grants, validate_secret_name
from elyra.secrets.store import SecretsStore
from elyra.tools.types import ToolContext, ToolResult


def _store(ctx: ToolContext) -> SecretsStore:
    existing = ctx.extras.get("secrets") if isinstance(ctx.extras, dict) else None
    if isinstance(existing, SecretsStore):
        return existing
    return SecretsStore(ctx.paths.data_dir)


def secrets_list(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """List named secrets metadata (names, grants, timestamps) — never values."""
    store = _store(ctx)
    rows = store.list_secrets()
    return ToolResult(
        ok=True,
        payload={"secrets": rows, "count": len(rows)},
    )


def secrets_set(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Write a named secret. Result omits the value (chain args scrubbed separately)."""
    name_raw = args.get("name")
    value = args.get("value")
    try:
        name = validate_secret_name(name_raw)
    except ValueError as exc:
        reason = str(exc) or "invalid_secret_name"
        return ToolResult(
            ok=False,
            payload={"name": name_raw if isinstance(name_raw, str) else None},
            error_reason=reason,
        )
    if not isinstance(value, str) or not value.strip():
        return ToolResult(
            ok=False,
            payload={"name": name},
            error_reason="empty_secret_value",
        )
    grants_arg = args.get("grants")
    grants: list[str] | None
    if grants_arg is None:
        grants = None
    else:
        try:
            grants = normalize_grants(grants_arg)
        except ValueError:
            return ToolResult(
                ok=False,
                payload={"name": name},
                error_reason="invalid_grants",
            )

    store = _store(ctx)
    try:
        meta = store.set_secret(
            name,
            value,
            grants=grants,
            managed_by=MANAGED_BY_USER,
        )
    except ValueError as exc:
        return ToolResult(
            ok=False,
            payload={"name": name},
            error_reason=str(exc) or "set_failed",
        )
    # Never include value / secret / token fields in the result.
    return ToolResult(
        ok=True,
        payload={
            "name": meta.get("name", name),
            "managed_by": meta.get("managed_by"),
            "grants": meta.get("grants") or [],
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
            "set": True,
        },
    )


def secrets_delete(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Delete a named secret. Result never includes a value."""
    name_raw = args.get("name")
    try:
        name = validate_secret_name(name_raw)
    except ValueError as exc:
        reason = str(exc) or "invalid_secret_name"
        return ToolResult(
            ok=False,
            payload={"name": name_raw if isinstance(name_raw, str) else None},
            error_reason=reason,
        )
    store = _store(ctx)
    try:
        deleted = store.delete_secret(name)
    except ValueError as exc:
        return ToolResult(
            ok=False,
            payload={"name": name},
            error_reason=str(exc) or "delete_failed",
        )
    if not deleted:
        return ToolResult(
            ok=False,
            payload={"name": name},
            error_reason="secret_not_found",
        )
    return ToolResult(
        ok=True,
        payload={"name": name, "deleted": True},
    )


__all__ = ["secrets_delete", "secrets_list", "secrets_set"]
