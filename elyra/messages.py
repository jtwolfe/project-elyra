"""Append-only message log for chat glass.

Scope: JSONL store of user/assistant messages for UI and simple loop.
In scope: Message schema with optional attachments/meta (KD1) and optional
conversation_id (C12); get_message; list_messages filter-then-last-N (KD17)
with lazy legacy DM inclusion; optional eager migrate helper.
Out of scope: media blob store (elyra.media), HTTP, vision expand, social
write-invariant enforcement (KD16 — callers / later PRs).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths, resolve_paths

MESSAGES_FILENAME = "messages.jsonl"

# Process-local serialize for append vs full-file rewrite (migrate). Does not
# coordinate multi-process writers — see migrate_legacy_conversation_ids.
_messages_io_lock = threading.Lock()
_LOG = logging.getLogger(__name__)


@dataclass
class Message:
    id: str
    role: str  # user | assistant | system
    content: str
    user_id: str | None
    created_at: str
    reasoning: str = ""
    moment_id: str | None = None
    # C12: social address; null = solo / legacy-unscoped.
    conversation_id: str | None = None
    # KD1: optional structured media inventory; missing/null on load → [].
    attachments: list[dict[str, Any]] | None = None
    # Optional per-message meta (stt, input_mode, …); missing/null → {}.
    meta: dict[str, Any] | None = None


def _path(paths: ElyraPaths) -> Path:
    return paths.data_dir / MESSAGES_FILENAME


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_attachments(
    attachments: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Return a list of plain dicts, or None when caller omitted attachments."""
    if attachments is None:
        return None
    out: list[dict[str, Any]] = []
    for item in attachments:
        if hasattr(item, "to_dict"):
            out.append(item.to_dict())  # type: ignore[union-attr]
        elif isinstance(item, dict):
            out.append(dict(item))
        else:
            raise TypeError(
                f"attachment must be dict or Attachment, got {type(item)!r}"
            )
    return out


def _message_to_row(msg: Message) -> dict[str, Any]:
    """Serialize Message; omit optional fields when unset (legacy-shaped rows)."""
    row = asdict(msg)
    if row.get("attachments") is None:
        del row["attachments"]
    if row.get("meta") is None:
        del row["meta"]
    if row.get("conversation_id") is None:
        del row["conversation_id"]
    return row


def append_message(
    role: str,
    content: str,
    *,
    user_id: str | None = "operator",
    reasoning: str = "",
    moment_id: str | None = None,
    conversation_id: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
    paths: ElyraPaths | None = None,
) -> Message:
    """Append a glass row.

    Empty ``content`` is allowed when attachments present (enforced at
    API/speak layers; this store persists what it is given).

    ``user_id`` default remains ``\"operator\"`` for legacy call sites;
    **explicit** ``user_id=None`` is persisted as null (no coerce to
    ``\"operator\"``) — required for group assistant rows (KD20).

    ``conversation_id`` is persisted when non-None; omitted when None
    (legacy-shaped rows OK for load). Social write invariant (KD16) is
    enforced by callers, not this store.
    """
    p = paths or resolve_paths()
    p.ensure_data_dirs()
    atts = _normalize_attachments(attachments)
    cid = conversation_id
    if isinstance(cid, str):
        cid = cid.strip() or None
    msg = Message(
        id=str(uuid.uuid4()),
        role=role,
        content=content,
        user_id=user_id,
        created_at=_now(),
        reasoning=reasoning or "",
        moment_id=moment_id,
        conversation_id=cid,
        attachments=atts,
        meta=dict(meta) if meta is not None else None,
    )
    line = json.dumps(_message_to_row(msg), ensure_ascii=False) + "\n"
    with _messages_io_lock:
        with _path(p).open("a", encoding="utf-8") as handle:
            handle.write(line)
    return msg


def _row_conversation_id(row: dict[str, Any]) -> str | None:
    """Normalize row conversation_id: missing / null / blank → None."""
    if "conversation_id" not in row:
        return None
    val = row.get("conversation_id")
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        return s or None
    return None


def _matches_conversation(row: dict[str, Any], conversation_id: str) -> bool:
    """Predicate for list_messages conversation filter (KD4 / §2.4).

    - Always include rows with explicit matching conversation_id.
    - Legacy DM fill only: for ``dm:<uid>``, also include rows with
      missing/null conversation_id and ``user_id == uid``.
    - Groups: **no** legacy fill by member user_id.
    """
    row_cid = _row_conversation_id(row)
    if row_cid == conversation_id:
        return True
    if conversation_id.startswith("dm:") and row_cid is None:
        peer = conversation_id[3:]
        if row.get("user_id") == peer:
            return True
    return False


def _matches_user(row: dict[str, Any], user_id: str) -> bool:
    return row.get("user_id") == user_id


def list_messages(
    *,
    limit: int = 200,
    conversation_id: str | None = None,
    user_id: str | None = None,
    paths: ElyraPaths | None = None,
) -> list[dict[str, Any]]:
    """Scan all → filter → last-N (KD17). Never limit-then-filter.

    When ``conversation_id`` is set, apply §2.4 legacy DM inclusion rules.
    When both filters null: global tail (forensic view=all).
    ``limit <= 0`` returns all matching rows (unlimited contract).
    """
    p = paths or resolve_paths()
    file = _path(p)
    if not file.is_file():
        return []

    cid_filter: str | None = None
    if isinstance(conversation_id, str) and conversation_id.strip():
        cid_filter = conversation_id.strip()
    uid_filter: str | None = None
    if isinstance(user_id, str) and user_id.strip():
        uid_filter = user_id.strip()

    rows: list[dict[str, Any]] = []
    with file.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if cid_filter is not None and not _matches_conversation(row, cid_filter):
                continue
            if uid_filter is not None and not _matches_user(row, uid_filter):
                continue
            rows.append(row)
    if limit > 0:
        rows = rows[-limit:]
    return rows


def list_messages_for_conversation(
    conversation_id: str,
    *,
    limit: int = 200,
    paths: ElyraPaths | None = None,
) -> list[dict[str, Any]]:
    """Convenience: list_messages filtered to one conversation (KD17)."""
    return list_messages(
        limit=limit,
        conversation_id=conversation_id,
        paths=paths,
    )


def get_message(
    message_id: str,
    *,
    paths: ElyraPaths | None = None,
) -> dict[str, Any] | None:
    """Return the first JSONL row whose ``id`` matches, or None.

    v1: full-file scan (acceptable for glass-scale logs). Large-log index later.
    """
    if not message_id:
        return None
    p = paths or resolve_paths()
    file = _path(p)
    if not file.is_file():
        return None
    with file.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("id") == message_id:
                return row
    return None


def migrate_legacy_conversation_ids(
    *,
    paths: ElyraPaths | None = None,
) -> dict[str, Any]:
    """Eager rewrite: stamp ``conversation_id=dm:<user_id>`` on legacy rows.

    Rows already carrying conversation_id are left unchanged. Rows with null
    conversation_id and a **path-jail-safe** non-null user_id get
    ``dm:{user_id}``. Rows with malformed user_id are left unstamped (lazy
    list_messages fill still matches them by user_id).

    **Quiesce requirement:** stop PresenceWorker and any other process that
    may append to ``messages.jsonl`` before calling. A process-local lock
    serializes with ``append_message`` in *this* process only; multi-process
    writers are not coordinated. Before replace, size/mtime are re-checked;
    if the log grew (e.g. another process appended), migrate **aborts**
    without replacing so concurrent appends are not lost
    (``ok=False``, ``error="messages_changed"``).

    Prefer lazy ``list_messages`` fill for dogfood; this is an optional
    helper for small logs / operators under quiesced writers.
    """
    from elyra.identity.layout import validate_user_id

    p = paths or resolve_paths()
    file = _path(p)

    with _messages_io_lock:
        if not file.is_file():
            return {
                "ok": True,
                "rewritten": 0,
                "total": 0,
                "skipped_invalid_user_id": 0,
                "path": str(file),
            }

        try:
            st0 = file.stat()
        except OSError as exc:
            return {
                "ok": False,
                "error": "stat_failed",
                "detail": str(exc),
                "path": str(file),
            }
        size0, mtime0 = st0.st_size, st0.st_mtime_ns

        rewritten = 0
        total = 0
        skipped_invalid = 0
        out_lines: list[str] = []
        with file.open(encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    out_lines.append(raw)
                    continue
                if not isinstance(row, dict):
                    out_lines.append(raw)
                    continue
                total += 1
                if _row_conversation_id(row) is None:
                    uid = row.get("user_id")
                    if isinstance(uid, str) and uid.strip():
                        try:
                            safe_uid = validate_user_id(uid.strip())
                        except ValueError:
                            skipped_invalid += 1
                            _LOG.warning(
                                "migrate_legacy_conversation_ids: skip invalid "
                                "user_id=%r on row id=%r",
                                uid,
                                row.get("id"),
                            )
                        else:
                            row = dict(row)
                            row["conversation_id"] = f"dm:{safe_uid}"
                            rewritten += 1
                out_lines.append(json.dumps(row, ensure_ascii=False))

        text = ("\n".join(out_lines) + "\n") if out_lines else ""
        tmp = file.with_name(
            f"{file.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            tmp.write_text(text, encoding="utf-8")
            # Detect concurrent multi-process growth before replace.
            try:
                st1 = file.stat()
            except OSError as exc:
                tmp.unlink(missing_ok=True)
                return {
                    "ok": False,
                    "error": "stat_failed",
                    "detail": str(exc),
                    "path": str(file),
                }
            if st1.st_size != size0 or st1.st_mtime_ns != mtime0:
                tmp.unlink(missing_ok=True)
                return {
                    "ok": False,
                    "error": "messages_changed",
                    "detail": "messages.jsonl changed during migrate; abort",
                    "rewritten": 0,
                    "total": total,
                    "skipped_invalid_user_id": skipped_invalid,
                    "path": str(file),
                }
            tmp.replace(file)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return {
            "ok": True,
            "rewritten": rewritten,
            "total": total,
            "skipped_invalid_user_id": skipped_invalid,
            "path": str(file),
        }
