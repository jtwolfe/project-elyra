"""Orient social map blocks (Participants / Recently active / Active chats).

Scope: pure formatters + store-backed builders for multi-user orient (C12 PR5 / KD15).
In scope: conversation members → Participants; soft message-first recently-active;
ConversationsStore.list → Active chats; pure-work empty Participants/Active chats.
Out of scope: replace USER work-origin slot; full #131 presence product; HTTP.

Design: soft recently-active prefers glass messages first; client session
``activity_at`` (mutating put only) is secondary fill. Never claim \"online\".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from elyra.loop.context import estimate_tokens

if TYPE_CHECKING:
    from elyra.conversations import ConversationsStore
    from elyra.users import UsersStore

# Soft caps (chars) for one-line excerpts so a long profile cannot dominate.
_PROFILE_EXCERPT_CHARS = 80
_NAME_CHARS = 40
_RECENTLY_ACTIVE_DEFAULT_HOURS = 24
_RECENTLY_ACTIVE_DEFAULT_LIMIT = 8
_ACTIVE_CHATS_DEFAULT_LIMIT = 6
_PARTICIPANTS_DEFAULT_MAX_TOKENS = 800


def build_participants_block(
    *,
    social: bool,
    conversation_id: str | None = None,
    peer_user_id: str | None = None,
    conversations: "ConversationsStore | None" = None,
    users: "UsersStore",
    max_tokens: int = _PARTICIPANTS_DEFAULT_MAX_TOKENS,
) -> str:
    """Bullet list of conversation members for orient Participants.

    Pure work / non-social → empty string (USER may still carry work-origin).
    DM: single peer line with ``— peer DM``.
    Group: each member with goes_by + user_id (+ provisional / short excerpt).
    """
    if not social:
        return ""

    members: list[str] = []
    conv_type: str | None = None
    rec: dict[str, Any] | None = None

    if (
        conversations is not None
        and isinstance(conversation_id, str)
        and conversation_id.strip()
    ):
        try:
            rec = conversations.get(conversation_id.strip())
        except Exception:  # noqa: BLE001 — fail soft for orient
            rec = None
        if isinstance(rec, dict):
            raw_members = rec.get("members") or []
            if isinstance(raw_members, list):
                members = [
                    str(m).strip()
                    for m in raw_members
                    if isinstance(m, str) and m.strip()
                ]
            ctype = rec.get("type")
            if ctype in ("dm", "group"):
                conv_type = ctype

    cid_stripped = (
        conversation_id.strip()
        if isinstance(conversation_id, str) and conversation_id.strip()
        else None
    )
    is_group_id = bool(cid_stripped and cid_stripped.startswith("group:"))

    # Social without store hit: DM/legacy only. Never force DM peer fallback
    # when conversation_id is group:… (missing store would mislabel as peer DM).
    if not members and not is_group_id:
        if isinstance(peer_user_id, str) and peer_user_id.strip():
            members = [peer_user_id.strip()]
            conv_type = conv_type or "dm"

    if not members:
        return ""

    if conv_type is None:
        conv_type = "group" if is_group_id else "dm"

    lines: list[str] = []
    for uid in members:
        label = _display_label(users, uid)
        line = f"- {label} ({uid})"
        if conv_type == "dm" and len(members) == 1:
            line = f"{line} — peer DM"
        else:
            tag = _member_tag(users, uid)
            if tag:
                line = f"{line} — {tag}"
            elif conv_type == "group":
                excerpt = _profile_excerpt(users, uid)
                if excerpt:
                    line = f"{line} — {excerpt}"
        lines.append(line)

    return _budget_lines(lines, max_tokens=max_tokens)


def build_recently_active_block(
    *,
    glass_rows: Sequence[Mapping[str, Any]] | None = None,
    session_entries: Sequence[Mapping[str, Any]] | None = None,
    users: "UsersStore",
    hours: int = _RECENTLY_ACTIVE_DEFAULT_HOURS,
    limit: int = _RECENTLY_ACTIVE_DEFAULT_LIMIT,
    now: datetime | None = None,
) -> str:
    """Soft recently-active list (not full presence).

    Primary: users with a glass ``user`` message ``created_at`` within ``hours``.
    Secondary (optional fill): client session entries with ``activity_at`` within
    window, real UsersStore id only — mutating put stamps, not GET polls.
    Cap ``limit``; dedupe by user_id (message wins). Never claims \"online\".
    """
    if hours <= 0 or limit <= 0:
        return ""

    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)

    cutoff = now - timedelta(hours=hours)
    # uid → (best_ts, source) source is "glass" | "session"
    best: dict[str, tuple[datetime, str]] = {}

    for row in glass_rows or ():
        if not isinstance(row, Mapping):
            continue
        if row.get("role") not in ("user",):
            continue
        uid = row.get("user_id")
        if not isinstance(uid, str) or not uid.strip():
            continue
        uid = uid.strip()
        ts = _parse_iso(row.get("created_at"))
        if ts is None or ts < cutoff:
            continue
        prev = best.get(uid)
        if prev is None or ts > prev[0]:
            best[uid] = (ts, "glass")

    # Secondary: only fill users not already present from glass.
    for ent in session_entries or ():
        if not isinstance(ent, Mapping):
            continue
        uid = ent.get("user_id")
        if not isinstance(uid, str) or not uid.strip():
            continue
        uid = uid.strip()
        if uid in best:
            continue
        # Real user only — skip unknown / mint noise.
        if not _user_known(users, uid):
            continue
        # Prefer activity_at (mutating put); never fall back to updated_at alone
        # so GET-minted sessions without put do not pollute orient.
        ts = _parse_iso(ent.get("activity_at"))
        if ts is None or ts < cutoff:
            continue
        best[uid] = (ts, "session")

    if not best:
        return ""

    ordered = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
    lines: list[str] = []
    for uid, (ts, source) in ordered:
        label = _display_label(users, uid)
        rel = _relative_ago(now, ts)
        if source == "glass":
            lines.append(f"- {label} ({uid}) · last glass {rel}")
        else:
            lines.append(f"- {label} ({uid}) · session touch {rel}")
    return "\n".join(lines)


def build_active_chats_block(
    *,
    social: bool,
    conversations: "ConversationsStore | None" = None,
    users: "UsersStore",
    limit: int = _ACTIVE_CHATS_DEFAULT_LIMIT,
) -> str:
    """Top-K conversations for orient Active chats (informational).

    Pure work / non-social → empty (must-implement pure-work empty path).
    Social: ``ConversationsStore.list()`` order (last_message_at / updated_at).
    """
    if not social or conversations is None or limit <= 0:
        return ""

    try:
        rows = conversations.list()
    except Exception:  # noqa: BLE001 — fail soft
        return ""
    if not rows:
        return ""

    lines: list[str] = []
    for row in rows[:limit]:
        if not isinstance(row, Mapping):
            continue
        cid = row.get("id")
        if not isinstance(cid, str) or not cid.strip():
            continue
        cid = cid.strip()
        ctype = row.get("type") or "?"
        name = row.get("name")
        members = row.get("members") or []
        member_labels: list[str] = []
        if isinstance(members, list):
            for m in members[:4]:
                if isinstance(m, str) and m.strip():
                    member_labels.append(_display_label(users, m.strip()))
            if len(members) > 4:
                member_labels.append("…")
        members_s = ", ".join(member_labels) if member_labels else "—"
        if isinstance(name, str) and name.strip():
            lines.append(
                f"- {cid} ({ctype}) \"{_truncate(name.strip(), _NAME_CHARS)}\" · {members_s}"
            )
        else:
            lines.append(f"- {cid} ({ctype}) · {members_s}")
    return "\n".join(lines)


# ── helpers ──────────────────────────────────────────────────────────────


def _display_label(users: "UsersStore", user_id: str) -> str:
    try:
        label = users.display_label(user_id)
    except Exception:  # noqa: BLE001
        return user_id
    if isinstance(label, str) and label.strip():
        return label.strip()
    return user_id


def _user_known(users: "UsersStore", user_id: str) -> bool:
    """True when ``user_id`` has a durable UsersStore directory entry."""
    try:
        return user_id in set(users.list_user_ids())
    except Exception:  # noqa: BLE001
        return False


def _member_tag(users: "UsersStore", user_id: str) -> str | None:
    try:
        meta_fn = getattr(users, "get_meta", None)
        if not callable(meta_fn):
            return None
        meta = meta_fn(user_id)
        if isinstance(meta, dict) and meta.get("provisional") is True:
            return "provisional guest"
    except Exception:  # noqa: BLE001
        return None
    return None


def _profile_excerpt(users: "UsersStore", user_id: str) -> str:
    try:
        body = users.profile(user_id) or ""
    except Exception:  # noqa: BLE001
        return ""
    if not body.strip():
        return ""
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        return _truncate(line, _PROFILE_EXCERPT_CHARS)
    return ""


def _budget_lines(lines: list[str], *, max_tokens: int) -> str:
    if not lines:
        return ""
    if max_tokens <= 0:
        return ""
    kept = list(lines)
    while kept:
        text = "\n".join(kept)
        if estimate_tokens(text) <= max_tokens:
            return text
        kept.pop()
    return ""


def _truncate(text: str, max_chars: int) -> str:
    text = text.replace("\n", " ").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:max_chars]
    return text[: max_chars - 1].rstrip() + "…"


def _parse_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        ts = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _relative_ago(now: datetime, then: datetime) -> str:
    """Rough relative label: ``~5m ago`` / ``~2h ago`` / ``~1d ago``."""
    delta = now - then
    secs = max(0, int(delta.total_seconds()))
    if secs < 60:
        return "~just now"
    minutes = secs // 60
    if minutes < 60:
        return f"~{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"~{hours}h ago"
    days = hours // 24
    return f"~{days}d ago"


def coerce_orient_int(value: Any, default: int) -> int:
    """Parse an orient setting int; honor intentional 0 (disable).

    ``value or default`` is wrong: operator ``0`` must stay 0 so builders
    can short-circuit (empty Participants / RA / Active chats).
    """
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "build_active_chats_block",
    "build_participants_block",
    "build_recently_active_block",
    "coerce_orient_int",
]
