"""Builtin identity tools: get_identity, draft_identity, promote_identity.

Scope: host entries for tools/bundled identity packages.
In scope: IdentityStore / UsersStore via ToolContext.extras; pure promote gate;
  grant resolve→gate→consume→promote for self; medium gate for user.
Out of scope: Glass grant mint API, skills process playbooks.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from elyra.identity.gates import (
    PromoteContext,
    evaluate_promote_gate,
    should_name_nudge,
)
from elyra.identity.grants import consume_grant, load_active_token_set
from elyra.identity.layout import content_sha256, read_text_or_empty, validate_user_id
from elyra.tools.types import ToolContext, ToolResult

_LOG = logging.getLogger(__name__)

Actor = Literal["self", "user"]


def _identity_store(ctx: ToolContext):
    store = ctx.extras.get("identity")
    if store is None:
        return None, ToolResult(
            ok=False,
            payload={},
            error_reason="identity_not_configured",
        )
    return store, None


def _users_store(ctx: ToolContext):
    store = ctx.extras.get("users")
    if store is None:
        return None, ToolResult(
            ok=False,
            payload={},
            error_reason="users_not_configured",
        )
    return store, None


def _parse_actor(args: dict[str, Any]) -> tuple[Actor | None, ToolResult | None]:
    actor = args.get("actor")
    if actor not in ("self", "user"):
        return None, ToolResult(
            ok=False,
            payload={},
            error_reason="invalid_actor",
        )
    return actor, None  # type: ignore[return-value]


def _require_user_id(args: dict[str, Any]) -> tuple[str | None, ToolResult | None]:
    raw = args.get("user_id")
    if not isinstance(raw, str) or not raw.strip():
        return None, ToolResult(
            ok=False,
            payload={},
            error_reason="missing_user_id",
        )
    uid = raw.strip()
    try:
        validate_user_id(uid)
    except ValueError:
        return None, ToolResult(
            ok=False,
            payload={"user_id": uid},
            error_reason="invalid_user_id",
        )
    return uid, None


def _wake_kind(ctx: ToolContext) -> str | None:
    wk = ctx.extras.get("wake_kind")
    if isinstance(wk, str) and wk:
        return wk
    wake = ctx.extras.get("wake")
    if wake is not None:
        kind = getattr(wake, "kind", None)
        if isinstance(kind, str):
            return kind
    return None


def _user_exists(users, user_id: str) -> bool:
    """Best-effort existence without inventing users."""
    exists_fn = getattr(users, "_user_exists", None)
    if callable(exists_fn):
        try:
            return bool(exists_fn(user_id))
        except ValueError:
            return False
    # Fallback: list dirs
    try:
        return user_id in users.list_user_ids()
    except Exception:  # noqa: BLE001
        return False


def _draft_sha(identity_or_users, *, actor: Actor, user_id: str | None) -> str | None:
    if actor == "self":
        path = identity_or_users.draft_path()
    else:
        assert user_id is not None
        path = identity_or_users.draft_path(user_id)
    if not path.is_file():
        return None
    body = read_text_or_empty(path)
    if not body.strip():
        return None
    return content_sha256(body)


# ---------------------------------------------------------------------------
# get_identity
# ---------------------------------------------------------------------------


def get_identity(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Read current/draft/version identity body + meta (+ optional versions list)."""
    actor, err = _parse_actor(args)
    if err is not None:
        return err

    which = args.get("which") or "current"
    if which not in ("current", "draft", "version"):
        return ToolResult(ok=False, payload={}, error_reason="invalid_which")

    list_versions = bool(args.get("list_versions"))
    version_id = args.get("version_id")
    if which == "version":
        if not isinstance(version_id, str) or not version_id.strip():
            return ToolResult(
                ok=False,
                payload={},
                error_reason="version_not_found",
            )
        version_id = version_id.strip()

    if actor == "self":
        store, serr = _identity_store(ctx)
        if serr is not None:
            return serr
        result = store.get(
            which=which,  # type: ignore[arg-type]
            version_id=version_id,
            list_versions=list_versions,
        )
        if not result.get("ok"):
            return ToolResult(
                ok=False,
                payload={k: v for k, v in result.items() if k != "ok"},
                error_reason=str(result.get("error") or "get_failed"),
            )
        # Self: should_name_nudge omitted / false
        result.setdefault("should_name_nudge", False)
        return ToolResult(ok=True, payload=result)

    # actor == user
    users, uerr = _users_store(ctx)
    if uerr is not None:
        return uerr
    user_id, uid_err = _require_user_id(args)
    if uid_err is not None:
        return uid_err
    assert user_id is not None

    if not _user_exists(users, user_id):
        return ToolResult(
            ok=False,
            payload={"actor": "user", "user_id": user_id},
            error_reason="user_not_found",
        )

    result = users.get(
        user_id,
        which=which,  # type: ignore[arg-type]
        version_id=version_id,
        list_versions=list_versions,
    )
    if not result.get("ok"):
        return ToolResult(
            ok=False,
            payload={k: v for k, v in result.items() if k != "ok"},
            error_reason=str(result.get("error") or "get_failed"),
        )

    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    result["should_name_nudge"] = should_name_nudge(meta or {}, ctx.moment_id or "")
    return ToolResult(ok=True, payload=result)


# ---------------------------------------------------------------------------
# draft_identity
# ---------------------------------------------------------------------------


def draft_identity(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Write draft.md + draft_meta; never touch current body."""
    actor, err = _parse_actor(args)
    if err is not None:
        return err

    reason = args.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return ToolResult(ok=False, payload={}, error_reason="missing_reason")

    body = args.get("body")
    meta_patch = args.get("meta_patch")
    if meta_patch is not None and not isinstance(meta_patch, dict):
        return ToolResult(ok=False, payload={}, error_reason="invalid_meta_patch")

    if actor == "self":
        store, serr = _identity_store(ctx)
        if serr is not None:
            return serr
        result = store.write_draft(
            body if isinstance(body, str) or body is None else None,
            meta_patch=meta_patch,
            reason=reason,
        )
        if not result.get("ok"):
            return ToolResult(
                ok=False,
                payload={k: v for k, v in result.items() if k != "ok"},
                error_reason=str(result.get("error") or "draft_failed"),
            )
        return ToolResult(ok=True, payload=result)

    users, uerr = _users_store(ctx)
    if uerr is not None:
        return uerr
    user_id, uid_err = _require_user_id(args)
    if uid_err is not None:
        return uid_err
    assert user_id is not None

    result = users.write_draft(
        user_id,
        body if isinstance(body, str) or body is None else None,
        meta_patch=meta_patch,
        reason=reason,
        moment_id=ctx.moment_id or None,
    )
    if not result.get("ok"):
        return ToolResult(
            ok=False,
            payload={k: v for k, v in result.items() if k != "ok"},
            error_reason=str(result.get("error") or "draft_failed"),
        )
    return ToolResult(ok=True, payload=result)


# ---------------------------------------------------------------------------
# promote_identity
# ---------------------------------------------------------------------------


def promote_identity(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Host-gated promote: resolve → gate → consume (self) → store.promote."""
    actor, err = _parse_actor(args)
    if err is not None:
        return err

    reason = args.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return ToolResult(ok=False, payload={}, error_reason="missing_reason")

    expected = args.get("expected_draft_sha256")
    if expected is not None and not isinstance(expected, str):
        return ToolResult(
            ok=False,
            payload={},
            error_reason="invalid_expected_draft_sha256",
        )

    grant_token = args.get("grant_token")
    if grant_token is not None and not isinstance(grant_token, str):
        grant_token = None

    wake_kind = _wake_kind(ctx)

    if actor == "self":
        return _promote_self(
            ctx,
            reason=reason.strip(),
            grant_token=grant_token,
            expected_draft_sha256=expected,
            wake_kind=wake_kind,
        )

    return _promote_user(
        args,
        ctx,
        reason=reason.strip(),
        expected_draft_sha256=expected,
        wake_kind=wake_kind,
    )


def _promote_self(
    ctx: ToolContext,
    *,
    reason: str,
    grant_token: str | None,
    expected_draft_sha256: str | None,
    wake_kind: str | None,
) -> ToolResult:
    store, serr = _identity_store(ctx)
    if serr is not None:
        return serr

    has_draft = bool(store.has_draft())
    draft_sha = _draft_sha(store, actor="self", user_id=None)

    # Resolve token (model path: args.grant_token required)
    resolved = grant_token.strip() if isinstance(grant_token, str) and grant_token.strip() else None
    active = load_active_token_set(ctx.paths)
    operator_tokens = active
    if resolved:
        operator_tokens = frozenset(set(active) | {resolved})

    gate = evaluate_promote_gate(
        PromoteContext(
            actor="self",
            target_user_id=None,
            session_user_id=ctx.user_id,
            wake_kind=wake_kind,
            moment_id=ctx.moment_id or "",
            reason=reason,
            grant_token=resolved,
            has_draft=has_draft and draft_sha is not None,
            draft_sha256=draft_sha,
            expected_draft_sha256=expected_draft_sha256,
            identity_promote_user_ok=False,
            identity_promote_any_user=False,
            operator_grant_tokens=operator_tokens,
            allow_self_promote_without_grant=False,
        )
    )
    if not gate.allowed:
        return ToolResult(
            ok=False,
            payload={"actor": "self", "detail": gate.detail},
            error_reason=gate.error_reason or "promote_denied",
        )

    assert resolved is not None  # gate required non-empty token
    consumed = consume_grant(ctx.paths, resolved)
    if not consumed.get("ok"):
        return ToolResult(
            ok=False,
            payload={"actor": "self"},
            error_reason=str(consumed.get("error") or "grant_exhausted"),
        )

    # Promote after successful consume — store is transactional under lock.
    try:
        result = store.promote(
            reason=reason,
            expected_draft_sha256=expected_draft_sha256,
        )
    except Exception as exc:  # noqa: BLE001 — surface I/O failure after consume
        _LOG.exception("identity promote self failed after grant consume")
        return ToolResult(
            ok=False,
            payload={"actor": "self", "grant_consumed": True},
            error_reason=f"promote_failed:{type(exc).__name__}",
        )

    if not result.get("ok"):
        return ToolResult(
            ok=False,
            payload={
                **{k: v for k, v in result.items() if k != "ok"},
                "grant_consumed": True,
            },
            error_reason=str(result.get("error") or "promote_failed"),
        )
    return ToolResult(ok=True, payload=result)


def _promote_user(
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    reason: str,
    expected_draft_sha256: str | None,
    wake_kind: str | None,
) -> ToolResult:
    users, uerr = _users_store(ctx)
    if uerr is not None:
        return uerr
    user_id, uid_err = _require_user_id(args)
    if uid_err is not None:
        return uid_err
    assert user_id is not None

    exists = _user_exists(users, user_id)
    has_draft = bool(users.has_draft(user_id)) if exists else False
    draft_sha = (
        _draft_sha(users, actor="user", user_id=user_id) if has_draft else None
    )

    # Model path: host-only flags stay False (cannot be set from args).
    gate = evaluate_promote_gate(
        PromoteContext(
            actor="user",
            target_user_id=user_id,
            session_user_id=ctx.user_id,
            wake_kind=wake_kind,
            moment_id=ctx.moment_id or "",
            reason=reason,
            grant_token=None,
            has_draft=has_draft and draft_sha is not None,
            draft_sha256=draft_sha,
            expected_draft_sha256=expected_draft_sha256,
            identity_promote_user_ok=False,
            identity_promote_any_user=False,
            operator_grant_tokens=frozenset(),
            allow_self_promote_without_grant=False,
            target_user_exists=exists,
        )
    )
    if not gate.allowed:
        return ToolResult(
            ok=False,
            payload={"actor": "user", "user_id": user_id, "detail": gate.detail},
            error_reason=gate.error_reason or "promote_denied",
        )

    try:
        result = users.promote(
            user_id,
            reason=reason,
            expected_draft_sha256=expected_draft_sha256,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("identity promote user failed user_id=%s", user_id)
        return ToolResult(
            ok=False,
            payload={"actor": "user", "user_id": user_id},
            error_reason=f"promote_failed:{type(exc).__name__}",
        )

    if not result.get("ok"):
        return ToolResult(
            ok=False,
            payload={k: v for k, v in result.items() if k != "ok"},
            error_reason=str(result.get("error") or "promote_failed"),
        )
    return ToolResult(ok=True, payload=result)
