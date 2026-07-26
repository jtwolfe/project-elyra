"""Append-only message log for chat glass.

Scope: JSONL store of user/assistant messages for UI and simple loop.
In scope: Message schema with optional attachments/meta (KD1); get_message;
backward-compatible load of legacy rows without those fields.
Out of scope: media blob store (elyra.media), HTTP, vision expand.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths, resolve_paths

MESSAGES_FILENAME = "messages.jsonl"


@dataclass
class Message:
    id: str
    role: str  # user | assistant | system
    content: str
    user_id: str | None
    created_at: str
    reasoning: str = ""
    moment_id: str | None = None
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
    """Serialize Message; omit attachments/meta when unset (legacy-shaped rows)."""
    row = asdict(msg)
    if row.get("attachments") is None:
        del row["attachments"]
    if row.get("meta") is None:
        del row["meta"]
    return row


def append_message(
    role: str,
    content: str,
    *,
    user_id: str | None = "operator",
    reasoning: str = "",
    moment_id: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
    paths: ElyraPaths | None = None,
) -> Message:
    """Append a glass row. Empty ``content`` is allowed when attachments present
    (enforced at API/speak layers; this store persists what it is given).
    """
    p = paths or resolve_paths()
    p.ensure_data_dirs()
    atts = _normalize_attachments(attachments)
    msg = Message(
        id=str(uuid.uuid4()),
        role=role,
        content=content,
        user_id=user_id,
        created_at=_now(),
        reasoning=reasoning or "",
        moment_id=moment_id,
        attachments=atts,
        meta=dict(meta) if meta is not None else None,
    )
    with _path(p).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_message_to_row(msg), ensure_ascii=False) + "\n")
    return msg


def list_messages(
    *,
    limit: int = 200,
    paths: ElyraPaths | None = None,
) -> list[dict[str, Any]]:
    p = paths or resolve_paths()
    file = _path(p)
    if not file.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with file.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit > 0:
        rows = rows[-limit:]
    return rows


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
