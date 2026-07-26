"""Pure host promote gates for identity draft→current (no I/O).

Scope: evaluate_promote_gate + PromoteContext / GateResult; should_name_nudge.
In scope: self hard grant; user medium social/session checks; reason length.
Out of scope: grant file I/O, store.promote, Glass API handlers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from elyra.identity.layout import USER_ID_RE
from elyra.loop.continuous_policy import SOCIAL_WAKE_KINDS

Actor = Literal["self", "user"]


@dataclass(frozen=True)
class PromoteContext:
    """Inputs for evaluate_promote_gate (pure — host fills flags/tokens).

    Split target_user_id vs session_user_id (K15). Host-only flags must never
    come from model tool args — only worker/API construct this object.
    """

    actor: Actor
    # Identity profile being promoted; required if actor=user.
    target_user_id: str | None
    # ToolContext.user_id / Glass session (may be None on pure work).
    session_user_id: str | None
    wake_kind: str | None
    moment_id: str
    reason: str
    grant_token: str | None  # model path for self
    has_draft: bool
    draft_sha256: str | None
    expected_draft_sha256: str | None
    # Host-only flags — Glass/API only; model path always False.
    identity_promote_user_ok: bool = False
    identity_promote_any_user: bool = False
    # Host-provided snapshot for pure evaluation (active grant tokens).
    operator_grant_tokens: frozenset[str] = field(default_factory=frozenset)
    # Tests only — product entrypoints never set True.
    allow_self_promote_without_grant: bool = False
    # Host-provided existence for actor=user (pure; no disk I/O here).
    # None = not applicable (self) or unchecked; False → user_not_found.
    target_user_exists: bool | None = None


@dataclass(frozen=True)
class GateResult:
    """Outcome of evaluate_promote_gate."""

    allowed: bool
    error_reason: str | None  # machine code
    detail: str | None = None


def evaluate_promote_gate(ctx: PromoteContext) -> GateResult:
    """Fail closed. Order matters. Pure — no I/O, no grant consume."""
    # 1. reason non-empty
    if not isinstance(ctx.reason, str) or not ctx.reason.strip():
        return GateResult(False, "missing_reason")

    reason = ctx.reason.strip()

    # 2. draft present
    if not ctx.has_draft:
        return GateResult(False, "draft_missing")

    # 3. optional optimistic concurrency
    if (
        ctx.expected_draft_sha256 is not None
        and ctx.expected_draft_sha256 != ctx.draft_sha256
    ):
        return GateResult(False, "draft_hash_mismatch")

    actor = ctx.actor
    if actor == "self":
        return _gate_self(ctx, reason)
    if actor == "user":
        return _gate_user(ctx, reason)
    return GateResult(False, "invalid_actor", detail=f"actor={actor!r}")


def _gate_self(ctx: PromoteContext, reason: str) -> GateResult:
    if not ctx.allow_self_promote_without_grant:
        token = ctx.grant_token
        if not isinstance(token, str) or not token.strip():
            return GateResult(False, "self_grant_required")
        token = token.strip()
        if token not in ctx.operator_grant_tokens:
            return GateResult(False, "self_grant_required")
    if len(reason) < 8:
        return GateResult(False, "reason_too_short")
    return GateResult(True, None)


def _gate_user(ctx: PromoteContext, reason: str) -> GateResult:
    target = ctx.target_user_id
    if not isinstance(target, str) or not target.strip():
        return GateResult(False, "missing_user_id")
    target = target.strip()
    if not USER_ID_RE.fullmatch(target):
        return GateResult(False, "invalid_user_id")

    if ctx.target_user_exists is False:
        return GateResult(False, "user_not_found")

    social_ok = (
        isinstance(ctx.wake_kind, str) and ctx.wake_kind in SOCIAL_WAKE_KINDS
    ) or ctx.identity_promote_user_ok
    if not social_ok:
        return GateResult(False, "user_promote_context_required")

    if len(reason) < 4:
        return GateResult(False, "user_promote_context_required")

    session = ctx.session_user_id
    session_s = session.strip() if isinstance(session, str) else None
    if session_s != target and not ctx.identity_promote_any_user:
        return GateResult(False, "user_promote_wrong_user")

    return GateResult(True, None)


def should_name_nudge(
    meta: Mapping[str, Any],
    moment_id: str,
    *,
    max_nudges: int = 3,
) -> bool:
    """Pure. True when provisional/unknown name and not yet nudged this moment / under cap."""
    if meta.get("real_name_known") is True:
        return False
    nudge = meta.get("name_nudge") or {}
    if not isinstance(nudge, dict):
        nudge = {}
    if moment_id and nudge.get("last_moment_id") == moment_id:
        return False  # once per moment
    if int(nudge.get("count") or 0) >= max_nudges:
        return False
    return True
