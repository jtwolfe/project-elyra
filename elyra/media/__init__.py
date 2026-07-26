"""Host media store: attachments, content-addressed blobs (KD1, KD14).

Stretch 2 embedding fields are stubs only (KD12). HTTP / STT / TTS / vision
expand land in later PRs.
"""

from elyra.media.project import (
    clear_sandbox_media,
    project_attachment,
    projected_path_for,
    sandbox_media_root,
)
from elyra.media.store import (
    MediaStore,
    bind_attachment_message,
    ensure_media_dirs,
    get_attachment,
    media_root,
    put_bytes,
    safe_filename,
    sniff_mime_and_kind,
    sniff_mime_kind_source,
    validate_att_id,
)
from elyra.media.types import (
    ATTACHMENT_KINDS,
    ATTACHMENT_ORIGINS,
    EMBEDDING_STATUSES,
    ROLE_HINTS,
    Attachment,
)
from elyra.media.upload import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    MAX_AUDIO_BYTES,
    MAX_CONCURRENT_UPLOADS,
    MAX_FILE_BYTES,
    MAX_IMAGE_BYTES,
    MAX_JSON_BODY_BYTES,
    MAX_MEDIA_REQUEST_BYTES,
    FormFile,
    max_bytes_for_kind,
    parse_content_length,
    parse_multipart_fields,
    parse_multipart_files,
    stream_to_temp,
)

__all__ = [
    "ATTACHMENT_KINDS",
    "ATTACHMENT_ORIGINS",
    "EMBEDDING_STATUSES",
    "MAX_ATTACHMENTS_PER_MESSAGE",
    "MAX_AUDIO_BYTES",
    "MAX_CONCURRENT_UPLOADS",
    "MAX_FILE_BYTES",
    "MAX_IMAGE_BYTES",
    "MAX_JSON_BODY_BYTES",
    "MAX_MEDIA_REQUEST_BYTES",
    "ROLE_HINTS",
    "Attachment",
    "FormFile",
    "MediaStore",
    "bind_attachment_message",
    "clear_sandbox_media",
    "ensure_media_dirs",
    "get_attachment",
    "max_bytes_for_kind",
    "media_root",
    "parse_content_length",
    "parse_multipart_fields",
    "parse_multipart_files",
    "project_attachment",
    "projected_path_for",
    "put_bytes",
    "safe_filename",
    "sandbox_media_root",
    "sniff_mime_and_kind",
    "sniff_mime_kind_source",
    "stream_to_temp",
    "validate_att_id",
]
