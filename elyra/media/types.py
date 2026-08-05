"""Attachment record types and embedding stubs (KD1, KD12).

Scope: normative Attachment dataclass + kind/origin/embedding vocabularies.
In scope: serializable fields for meta JSON and message.attachments[].
Out of scope: blob I/O, HTTP, vision expand, real embedding pipeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping

# Stretch 2 stubs only (KD12) — no message-level embedding fields this train.
EmbeddingStatus = Literal["none", "pending", "ready", "failed"]
EMBEDDING_STATUSES: frozenset[str] = frozenset(
    {"none", "pending", "ready", "failed"}
)

AttachmentKind = Literal["image", "audio", "video", "file", "tts_cache"]
ATTACHMENT_KINDS: frozenset[str] = frozenset(
    {"image", "audio", "video", "file", "tts_cache"}
)

AttachmentOrigin = Literal[
    "user_upload",
    "user_recording",
    "tool",
    "speak",
    "view",
    "stt_source",
    "tts_cache",
    "system",
]
ATTACHMENT_ORIGINS: frozenset[str] = frozenset(
    {
        "user_upload",
        "user_recording",
        "tool",
        "speak",
        "view",
        "stt_source",
        "tts_cache",
        "system",
    }
)

RoleHint = Literal["primary", "inline", "source", "derived"]
ROLE_HINTS: frozenset[str] = frozenset(
    {"primary", "inline", "source", "derived"}
)


@dataclass
class Attachment:
    """Durable attachment record (host meta + message inventory shape).

    Blobs live content-addressed under ``data/media/blobs/``; this record is
    stored as ``data/media/meta/<id>.json`` and may be copied onto a Message
    row's ``attachments[]``. ``bound_message_id`` is null until bind.
    """

    id: str
    kind: str
    origin: str
    filename: str
    mime: str
    byte_size: int
    sha256: str
    created_at: str
    role_hint: str = "primary"
    sandbox_relpath: str | None = None
    xai_file_id: str | None = None
    xai_file_expires_at: str | None = None
    source_message_id: str | None = None
    voice_id: str | None = None
    transcript_of: str | None = None
    embedding_status: str = "none"
    embedding_ref: str | None = None
    bound_message_id: str | None = None
    uploader_user_id: str | None = "operator"
    # Free-form extras reserved for later PRs; not written unless set.
    extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable meta / message.attachments entry (no ``extra``)."""
        d = asdict(self)
        d.pop("extra", None)
        return d

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Attachment:
        """Build from meta JSON; unknown keys ignored; stubs defaulted."""
        data = dict(raw)
        status = data.get("embedding_status") or "none"
        if status not in EMBEDDING_STATUSES:
            status = "none"
        return cls(
            id=str(data["id"]),
            kind=str(data.get("kind") or "file"),
            origin=str(data.get("origin") or "system"),
            filename=str(data.get("filename") or "file"),
            mime=str(data.get("mime") or "application/octet-stream"),
            byte_size=int(data.get("byte_size") or 0),
            sha256=str(data.get("sha256") or ""),
            created_at=str(data.get("created_at") or ""),
            role_hint=str(data.get("role_hint") or "primary"),
            sandbox_relpath=data.get("sandbox_relpath"),
            xai_file_id=data.get("xai_file_id"),
            xai_file_expires_at=data.get("xai_file_expires_at"),
            source_message_id=data.get("source_message_id"),
            voice_id=data.get("voice_id"),
            transcript_of=data.get("transcript_of"),
            embedding_status=status,
            embedding_ref=data.get("embedding_ref"),
            bound_message_id=data.get("bound_message_id"),
            uploader_user_id=data.get("uploader_user_id", "operator"),
        )
