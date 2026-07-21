"""Append-only message log for chat glass.

Scope: JSONL store of user/assistant messages for UI and simple loop.
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


def _path(paths: ElyraPaths) -> Path:
    return paths.data_dir / MESSAGES_FILENAME


def _now() -> str:
    return datetime.now(UTC).isoformat()


def append_message(
    role: str,
    content: str,
    *,
    user_id: str | None = "operator",
    reasoning: str = "",
    moment_id: str | None = None,
    paths: ElyraPaths | None = None,
) -> Message:
    p = paths or resolve_paths()
    p.ensure_data_dirs()
    msg = Message(
        id=str(uuid.uuid4()),
        role=role,
        content=content,
        user_id=user_id,
        created_at=_now(),
        reasoning=reasoning or "",
        moment_id=moment_id,
    )
    with _path(p).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(msg), ensure_ascii=False) + "\n")
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
