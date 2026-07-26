"""Host media store: attachments, content-addressed blobs (KD1, KD14).

Stretch 2 embedding fields are stubs only (KD12). Meal-time vision expand
(KD6/KD20/KD25) lives in ``elyra.media.prompt``. STT and TTS host clients
(PR6/PR7) proxy xAI; never expose API keys to the browser.
"""

from elyra.media.activity import (
    clear_media_activity_for_tests,
    note_media_activity,
    recent_media_activity,
)
from elyra.media.gc import (
    UNBOUND_MAX_AGE,
    UNBOUND_MAX_BYTES,
    ensure_media,
    gc_unbound_attachments,
    media_stats,
    reconcile_mirrors,
)
from elyra.media.limits import (
    RATE_LIMITED_PAYLOAD,
    STT_MAX_PER_MINUTE,
    TTS_MAX_PER_MINUTE,
    allow_stt,
    allow_tts,
    reset_rate_limits_for_tests,
)
from elyra.media.ingest import (
    IngestError,
    clone_attachment,
    ingest_sandbox_path,
    prepare_speak_attachments,
)
from elyra.media.project import (
    clear_sandbox_media,
    project_attachment,
    projected_path_for,
    sandbox_media_root,
)
from elyra.media.prompt import (
    expand_meal_for_provider,
    index_glass,
    strip_meal_wire_fields,
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
from elyra.media.stt import (
    DEFAULT_STT_MODEL,
    SttError,
    SttResult,
    parse_stt_response,
    stt_enabled,
    stt_url,
    transcribe,
)
from elyra.media.tts import (
    DEFAULT_TTS_LANGUAGE,
    DEFAULT_TTS_PROFILE,
    DEFAULT_TTS_VOICE,
    TtsError,
    TtsResult,
    cache_path_for,
    load_cached_audio,
    store_cached_audio,
    synthesize,
    tts_enabled,
    tts_url,
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
    "IngestError",
    "clone_attachment",
    "prepare_speak_attachments",
    "ingest_sandbox_path",
    "ATTACHMENT_KINDS",
    "ATTACHMENT_ORIGINS",
    "DEFAULT_STT_MODEL",
    "DEFAULT_TTS_LANGUAGE",
    "DEFAULT_TTS_PROFILE",
    "DEFAULT_TTS_VOICE",
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
    "SttError",
    "SttResult",
    "TtsError",
    "TtsResult",
    "bind_attachment_message",
    "cache_path_for",
    "clear_sandbox_media",
    "ensure_media_dirs",
    "expand_meal_for_provider",
    "get_attachment",
    "index_glass",
    "load_cached_audio",
    "max_bytes_for_kind",
    "media_root",
    "parse_content_length",
    "parse_multipart_fields",
    "parse_multipart_files",
    "parse_stt_response",
    "project_attachment",
    "projected_path_for",
    "put_bytes",
    "safe_filename",
    "sandbox_media_root",
    "sniff_mime_and_kind",
    "sniff_mime_kind_source",
    "store_cached_audio",
    "stream_to_temp",
    "strip_meal_wire_fields",
    "stt_enabled",
    "stt_url",
    "synthesize",
    "transcribe",
    "tts_enabled",
    "tts_url",
    "validate_att_id",
    "IngestError",
]
