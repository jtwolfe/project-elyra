"""Sandbox path ingest + re-send clone for speak outbound media (PR8 / KD8, KD16).

Scope: read a sandbox-relative file into the content-addressed media store
(with RO projection via put_bytes), and clone existing attachment ids onto a
new att_id sharing the same blob sha for per-message inventory.

Out of scope: HTTP upload, GC, vision expand, speak transport wiring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths, resolve_paths
from elyra.media.store import MediaStore
from elyra.media.types import Attachment
from elyra.media.upload import max_bytes_for_kind
from elyra.sandbox.paths import PathEscapeError, resolve
from elyra.sandbox.sandbox import Sandbox, normalize_user_path
from elyra.sandbox.workspace_seed import ensure_primary_sandbox_tree


class IngestError(Exception):
    """Structured ingest failure; ``reason`` is a stable error code for tools."""

    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


def _sandbox_root(paths: ElyraPaths) -> Path:
    """Primary sandbox root (ensured). Prefer Sandbox construct for seed."""
    return ensure_primary_sandbox_tree(paths)


def resolve_sandbox_file(
    relpath: str,
    *,
    paths: ElyraPaths | None = None,
    sandbox: Sandbox | None = None,
) -> Path:
    """Resolve ``relpath`` under sandbox0; return absolute host path if a file.

    Raises:
        IngestError: path_escape | not_found | is_directory | invalid_path
    """
    if not isinstance(relpath, str) or not relpath.strip():
        raise IngestError("invalid_path", detail="path must be a non-empty string")
    layout = paths or resolve_paths()
    norm = normalize_user_path(relpath.strip())
    try:
        if sandbox is not None:
            host_path = sandbox.resolve(norm)
        else:
            root = _sandbox_root(layout)
            host_path = resolve(root, norm)
    except PathEscapeError as exc:
        raise IngestError("path_escape", detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise IngestError("invalid_path", detail=str(exc)) from exc

    if not host_path.exists():
        raise IngestError("not_found", detail=f"sandbox path not found: {relpath!r}")
    if host_path.is_dir():
        raise IngestError("is_directory", detail=f"sandbox path is a directory: {relpath!r}")
    if not host_path.is_file():
        raise IngestError("not_found", detail=f"sandbox path is not a file: {relpath!r}")
    return host_path


def ingest_sandbox_path(
    relpath: str,
    *,
    paths: ElyraPaths | None = None,
    sandbox: Sandbox | None = None,
    filename: str | None = None,
    kind: str | None = None,
    origin: str = "speak",
    uploader_user_id: str | None = "operator",
    mime: str | None = None,
) -> Attachment:
    """Copy a sandbox-relative file into the media store and project RO.

    Uses ``put_bytes`` (sha dedupe + try-hardlink/copy projection). Enforces
    per-kind size caps (KD15). Does not bind to a message.
    """
    layout = paths or resolve_paths()
    host_path = resolve_sandbox_file(relpath, paths=layout, sandbox=sandbox)
    fname = (filename or host_path.name or "file").strip() or "file"

    try:
        # Cap read by largest product limit first; refine after sniff.
        size = host_path.stat().st_size
    except OSError as exc:
        raise IngestError(
            f"os_error:{type(exc).__name__}", detail=str(exc)
        ) from exc

    # Pre-check against absolute max file budget before reading whole body.
    from elyra.media.upload import MAX_FILE_BYTES

    if size > MAX_FILE_BYTES:
        raise IngestError(
            "file_too_large",
            detail=f"{size} bytes exceeds max {MAX_FILE_BYTES}",
        )

    try:
        data = host_path.read_bytes()
    except OSError as exc:
        raise IngestError(
            f"os_error:{type(exc).__name__}", detail=str(exc)
        ) from exc

    store = MediaStore(layout)
    # Sniff via put_bytes; enforce kind cap after provisional kind known.
    # put_bytes raises ValueError for bad origin/kind — map to IngestError.
    try:
        # Provisional sniff for size gate before commit.
        from elyra.media.store import sniff_mime_and_kind

        _mime, sniffed_kind = sniff_mime_and_kind(
            data, filename=fname, claimed_mime=mime
        )
        final_kind = kind or sniffed_kind
        limit = max_bytes_for_kind(final_kind)
        if len(data) > limit:
            raise IngestError(
                "file_too_large",
                detail=f"{len(data)} bytes exceeds {final_kind} max {limit}",
            )
        att = store.put_bytes(
            data,
            filename=fname,
            mime=mime,
            kind=kind,
            origin=origin,
            uploader_user_id=uploader_user_id,
        )
    except IngestError:
        raise
    except ValueError as exc:
        raise IngestError("invalid_attachment", detail=str(exc)) from exc
    except OSError as exc:
        raise IngestError(
            f"os_error:{type(exc).__name__}", detail=str(exc)
        ) from exc
    return att


def clone_attachment(
    att_id: str,
    *,
    paths: ElyraPaths | None = None,
    origin: str = "speak",
    uploader_user_id: str | None = "operator",
) -> Attachment:
    """Create a new attachment id pointing at the same blob sha (KD16 re-send).

    Existing meta must be present and its blob readable. The new record is
    unbound (``bound_message_id`` null) until the speak transport binds it to
    the new assistant message. Prefer this over reusing a bound att_id so each
    glass row has a clear inventory entry.
    """
    layout = paths or resolve_paths()
    store = MediaStore(layout)
    from elyra.media.store import validate_att_id

    try:
        validate_att_id(att_id)
    except ValueError as exc:
        raise IngestError("invalid_attachment_ids", detail=str(exc)) from exc

    existing = store.get(att_id)
    if existing is None:
        raise IngestError(
            "attachment_not_found", detail=f"attachment not found: {att_id!r}"
        )
    try:
        data = store.read_bytes(att_id)
    except FileNotFoundError as exc:
        raise IngestError(
            "attachment_not_found", detail=f"blob missing for {att_id!r}"
        ) from exc

    try:
        att = store.put_bytes(
            data,
            filename=existing.filename,
            mime=existing.mime,
            kind=existing.kind,
            origin=origin,
            role_hint=existing.role_hint,
            uploader_user_id=uploader_user_id,
        )
    except ValueError as exc:
        raise IngestError("invalid_attachment", detail=str(exc)) from exc
    except OSError as exc:
        raise IngestError(
            f"os_error:{type(exc).__name__}", detail=str(exc)
        ) from exc

    # Link re-send lineage when the source was bound to a prior message.
    if existing.bound_message_id:
        att.source_message_id = existing.bound_message_id
        store._write_meta(att)  # noqa: SLF001 — same-package meta update
    return att


def prepare_speak_attachments(
    *,
    attachment_ids: list[str] | None = None,
    path_specs: list[dict[str, Any]] | None = None,
    paths: ElyraPaths | None = None,
    sandbox: Sandbox | None = None,
    uploader_user_id: str | None = "operator",
    max_attachments: int | None = None,
) -> list[Attachment]:
    """Resolve speak ``attachment_ids`` + sandbox path specs into Attachment list.

    Order: path specs first (tool-produced files), then id clones (re-send).
    Raises IngestError on validation / I/O failures.
    """
    from elyra.media.upload import MAX_ATTACHMENTS_PER_MESSAGE

    layout = paths or resolve_paths()
    cap = (
        MAX_ATTACHMENTS_PER_MESSAGE
        if max_attachments is None
        else int(max_attachments)
    )
    ids = list(attachment_ids or [])
    specs = list(path_specs or [])
    total = len(ids) + len(specs)
    if total > cap:
        raise IngestError(
            "too_many_attachments",
            detail=f"{total} attachments exceeds max {cap}",
        )

    out: list[Attachment] = []
    for spec in specs:
        if not isinstance(spec, dict):
            raise IngestError(
                "invalid_attachments", detail="attachment entry must be an object"
            )
        path = spec.get("path")
        if not isinstance(path, str) or not path.strip():
            raise IngestError(
                "invalid_attachments", detail="attachment.path is required"
            )
        fname = spec.get("filename")
        kind = spec.get("kind")
        if fname is not None and not isinstance(fname, str):
            raise IngestError(
                "invalid_attachments", detail="attachment.filename must be a string"
            )
        if kind is not None and not isinstance(kind, str):
            raise IngestError(
                "invalid_attachments", detail="attachment.kind must be a string"
            )
        out.append(
            ingest_sandbox_path(
                path.strip(),
                paths=layout,
                sandbox=sandbox,
                filename=fname.strip() if isinstance(fname, str) and fname.strip() else None,
                kind=kind if isinstance(kind, str) else None,
                origin="speak",
                uploader_user_id=uploader_user_id,
            )
        )

    seen_ids: set[str] = set()
    for aid in ids:
        if not isinstance(aid, str) or not aid.strip():
            raise IngestError(
                "invalid_attachment_ids", detail="attachment id must be a non-empty string"
            )
        key = aid.strip()
        if key in seen_ids:
            continue
        seen_ids.add(key)
        out.append(
            clone_attachment(
                key,
                paths=layout,
                origin="speak",
                uploader_user_id=uploader_user_id,
            )
        )
    return out
