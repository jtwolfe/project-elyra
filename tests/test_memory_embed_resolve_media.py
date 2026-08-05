"""PR1: MediaStore resolve contract for encode (KD-M14/M18/M19/M21/M22).

Hermetic tests use a real MediaStore + tiny PNG fixture — not path_for doubles
alone. Path-for doubles remain covered in test_memory_embed_nemotron.py.

Skip-reason tokens (contract for drain meta + neighbors):
  {mid}:missing       — store.get returned None
  {mid}:no_path       — no resolvable blob path/bytes (incl. path_for miss)
  {mid}:unknown_type  — attachment present but modality not in matrix
  {mid}:oversize_bytes:{n}
  {mid}:channel_full:{modality}
  {mid}:error         — get/read raised (or empty att_id → ``:error``)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.media.store import MediaStore
from elyra.memory.embed.encode import (
    encode_atom,
    resolve_media_inputs,
    resolve_one_media,
)
from elyra.memory.embed.mock import MockEmbedder
from elyra.memory.types import Atom, new_atom_id, utc_now_iso

FIXTURE_TINY_PNG = Path(__file__).parent / "fixtures" / "mm_embed" / "tiny.png"


@pytest.fixture
def paths(tmp_path: Path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def media_store(paths) -> MediaStore:
    return MediaStore(paths)


def _obs(*, media_ids: tuple[str, ...] = (), text: str = "hi") -> Atom:
    return Atom(
        atom_id=new_atom_id(),
        t_start=utc_now_iso(),
        kind="observation",
        content_text=text,
        media_ids=media_ids,
    )


# ---------------------------------------------------------------------------
# Real MediaStore + extensionless blob path (KD-M14, M18, M19)
# ---------------------------------------------------------------------------


def test_resolve_real_mediastores_png_via_mime_not_blob_suffix(media_store: MediaStore):
    """put_bytes PNG → blob path has no .png; resolve still yields image (KD-M19)."""
    data = FIXTURE_TINY_PNG.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"

    att = media_store.put_bytes(data, filename="shot.png", origin="user_upload")
    blob = media_store.blob_path(att.sha256)
    assert blob.is_file()
    # Content-addressed path: …/blobs/<sha[:2]>/<sha> — no extension.
    assert blob.suffix == ""
    assert not str(blob).endswith(".png")
    assert att.mime == "image/png"
    assert att.filename == "shot.png"
    assert att.kind == "image"

    one = resolve_one_media(media_store, att.id)
    assert one["skipped"] is None
    assert one["modality"] == "image"
    assert isinstance(one["input"], str)
    assert Path(one["input"]).is_file()
    assert Path(one["input"]).resolve() == blob.resolve()

    atom = _obs(media_ids=(att.id,))
    out = resolve_media_inputs(atom, media_store)
    assert out["image"] == str(blob)
    assert out["audio"] is None
    assert out["video"] is None
    assert out["skipped"] == []


def test_resolve_blob_path_helper(media_store: MediaStore):
    att = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="x.png", origin="user_upload"
    )
    p = media_store.resolve_blob_path(att.id)
    assert p is not None
    assert p.is_file()
    assert p == media_store.blob_path(att.sha256)
    assert media_store.resolve_blob_path("att_does_not_exist") is None


def test_encode_atom_real_mediastores_image_channel(media_store: MediaStore):
    """End-to-end mock encode: real MediaStore PNG → image channel populated."""
    att = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="frame.png", origin="user_upload"
    )
    atom = _obs(media_ids=(att.id,), text="caption")
    result = encode_atom(MockEmbedder(), atom, media_store=media_store)
    assert result.status == "ready"
    assert "image" in (result.channels_encoded or ())
    assert result.embeddings is not None
    assert result.embeddings.emb_image is not None
    skipped = (result.meta or {}).get("embed_media_skipped") or []
    assert not any("no_path" in s for s in skipped)
    assert not any("unknown_type" in s for s in skipped)


def test_resolve_extensionless_blob_classifies_via_mime_only(media_store: MediaStore):
    """Even if filename lost an extension, att.mime alone must classify image."""
    att = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(),
        filename="noext",  # sanitized; magic still sets image/png
        origin="user_upload",
    )
    # Filename may lack .png; mime from magic must still win.
    assert att.mime == "image/png"
    assert media_store.blob_path(att.sha256).suffix == ""
    one = resolve_one_media(media_store, att.id)
    assert one["modality"] == "image"
    assert one["skipped"] is None


# ---------------------------------------------------------------------------
# Oversize without full read (KD-M22)
# ---------------------------------------------------------------------------


def test_oversize_skips_without_read_bytes(media_store: MediaStore, monkeypatch):
    """When att.byte_size > max_bytes, never call read_bytes (KD-M22)."""
    att = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="big.png", origin="user_upload"
    )
    # Force oversize via meta size without rewriting the tiny blob.
    monkeypatch.setattr(att, "byte_size", 10_000_000)

    # get returns the patched attachment (re-fetch would re-read meta).
    real_get = media_store.get

    def _get(aid: str):
        got = real_get(aid)
        if got is not None and got.id == att.id:
            return att
        return got

    monkeypatch.setattr(media_store, "get", _get)

    calls: list[str] = []
    real_read = media_store.read_bytes

    def _spy_read(aid: str) -> bytes:
        calls.append(aid)
        return real_read(aid)

    monkeypatch.setattr(media_store, "read_bytes", _spy_read)

    # Hide blob path so a naive impl might fall through to read_bytes.
    monkeypatch.setattr(
        media_store,
        "resolve_blob_path",
        lambda _aid: None,  # type: ignore[misc]
    )
    monkeypatch.setattr(
        media_store,
        "blob_path",
        lambda _sha: Path("/nonexistent/no/blob"),  # type: ignore[misc]
    )

    one = resolve_one_media(media_store, att.id, max_bytes=100)
    assert one["modality"] is None
    assert one["input"] is None
    assert one["skipped"] == f"{att.id}:oversize_bytes:10000000"
    assert calls == [], "read_bytes must not run when size exceeds max_bytes"


def test_oversize_via_blob_stat_when_meta_size_missing(
    media_store: MediaStore, monkeypatch
):
    """Size filled only via filesystem stat on blob path enforces cap (KD-M22)."""
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    att = media_store.put_bytes(data, filename="mid.png", mime="image/png")
    assert att.byte_size == len(data)
    # Clear meta size so resolve must re-stat the blob path.
    monkeypatch.setattr(att, "byte_size", None)
    real_get = media_store.get

    def _get(aid: str):
        got = real_get(aid)
        if got is not None and got.id == att.id:
            return att
        return got

    monkeypatch.setattr(media_store, "get", _get)

    calls: list[str] = []
    real_read = media_store.read_bytes

    def _spy_read(aid: str) -> bytes:
        calls.append(aid)
        return real_read(aid)

    monkeypatch.setattr(media_store, "read_bytes", _spy_read)

    one = resolve_one_media(media_store, att.id, max_bytes=50)
    assert one["modality"] is None
    assert one["input"] is None
    assert one["skipped"] == f"{att.id}:oversize_bytes:{len(data)}"
    assert calls == []


def test_oversize_underreported_meta_still_stats_path(
    media_store: MediaStore, monkeypatch
):
    """Under-reported att.byte_size cannot bypass cap when blob path exists."""
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    att = media_store.put_bytes(data, filename="lie.png", mime="image/png")
    # Meta claims 10 bytes; real file is ~208.
    monkeypatch.setattr(att, "byte_size", 10)
    real_get = media_store.get

    def _get(aid: str):
        got = real_get(aid)
        if got is not None and got.id == att.id:
            return att
        return got

    monkeypatch.setattr(media_store, "get", _get)

    calls: list[str] = []
    real_read = media_store.read_bytes

    def _spy_read(aid: str) -> bytes:
        calls.append(aid)
        return real_read(aid)

    monkeypatch.setattr(media_store, "read_bytes", _spy_read)

    one = resolve_one_media(media_store, att.id, max_bytes=50)
    assert one["skipped"] == f"{att.id}:oversize_bytes:{len(data)}"
    assert one["input"] is None
    assert calls == []


def test_oversize_real_store_path_visible_no_read(media_store: MediaStore, monkeypatch):
    """Product path still present: oversize skip without read_bytes spy empty."""
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    att = media_store.put_bytes(data, filename="vis.png", mime="image/png")
    assert media_store.blob_path(att.sha256).is_file()

    calls: list[str] = []
    real_read = media_store.read_bytes

    def _spy_read(aid: str) -> bytes:
        calls.append(aid)
        return real_read(aid)

    monkeypatch.setattr(media_store, "read_bytes", _spy_read)

    out = resolve_media_inputs(_obs(media_ids=(att.id,)), media_store, max_bytes=50)
    assert out["image"] is None
    assert any(f"{att.id}:oversize_bytes:" in s for s in out["skipped"])
    assert calls == []


# ---------------------------------------------------------------------------
# Missing / unknown / channel_full / bytes fallback / errors
# ---------------------------------------------------------------------------


def test_missing_attachment_skipped(media_store: MediaStore):
    one = resolve_one_media(media_store, "att_missing000")
    assert one["skipped"] == "att_missing000:missing"
    out = resolve_media_inputs(_obs(media_ids=("att_missing000",)), media_store)
    assert out["image"] is None
    assert "att_missing000:missing" in out["skipped"]


def test_unknown_type_skipped(media_store: MediaStore):
    att = media_store.put_bytes(
        b"%PDF-1.4 fake",
        filename="doc.pdf",
        mime="application/pdf",
        kind="file",
        origin="user_upload",
    )
    one = resolve_one_media(media_store, att.id)
    assert one["skipped"] == f"{att.id}:unknown_type"


def test_channel_full_keeps_first_image(media_store: MediaStore):
    a1 = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="a.png", origin="user_upload"
    )
    a2 = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes() + b"\x00",  # different sha
        filename="b.png",
        origin="user_upload",
    )
    out = resolve_media_inputs(_obs(media_ids=(a1.id, a2.id)), media_store)
    assert out["image"] is not None
    assert Path(out["image"]).resolve() == media_store.blob_path(a1.sha256).resolve()
    assert any(f"{a2.id}:channel_full:image" == s for s in out["skipped"])


def test_bytes_fallback_when_blob_path_unavailable(tmp_path: Path):
    """If no filesystem path but read_bytes works under cap, use bytes."""

    class _BytesStore:
        def __init__(self) -> None:
            self._data = FIXTURE_TINY_PNG.read_bytes()
            self.reads = 0

        def get(self, mid: str):
            if mid != "m1":
                return None

            class _Att:
                mime = "image/png"
                filename = "shot.png"
                kind = "image"
                byte_size = len(FIXTURE_TINY_PNG.read_bytes())
                sha256 = ""  # no blob_path route
                path = None
                local_path = None

            return _Att()

        def read_bytes(self, mid: str) -> bytes:
            self.reads += 1
            if mid != "m1":
                raise FileNotFoundError(mid)
            return self._data

    store = _BytesStore()
    one = resolve_one_media(store, "m1", max_bytes=8_000_000)
    assert one["modality"] == "image"
    assert one["input"] == store._data
    assert store.reads == 1
    assert one["skipped"] is None


def test_path_for_double_still_works(tmp_path: Path):
    """Legacy path_for test doubles remain supported (KD-M18 dual)."""

    class _Store:
        def __init__(self, mapping: dict[str, Path]) -> None:
            self._m = mapping

        def path_for(self, mid: str) -> str | None:
            p = self._m.get(mid)
            return str(p) if p else None

    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG" + b"\x00" * 16)
    out = resolve_media_inputs(
        _obs(media_ids=("m_img",)),
        _Store({"m_img": img}),
        max_bytes=1000,
    )
    assert out["image"] == str(img)
    assert out["skipped"] == []


def test_path_for_missing_is_no_path_not_unknown_type():
    """path_for-only miss → no_path (not unknown_type diagnostic)."""

    class _Store:
        def path_for(self, mid: str) -> str | None:
            return None

    one = resolve_one_media(_Store(), "m_gone")
    assert one["skipped"] == "m_gone:no_path"
    assert one["modality"] is None


def test_kind_soft_fallback_when_mime_ambiguous(tmp_path: Path):
    """kind in (image,audio,video) soft-fallback when mime/ext unknown."""

    class _Att:
        mime = "application/octet-stream"
        filename = "blobbin"
        kind = "image"
        byte_size = 4
        sha256 = ""
        path = None
        local_path = None

    class _Store:
        def get(self, mid: str):
            return _Att() if mid == "k1" else None

        def read_bytes(self, mid: str) -> bytes:
            return b"\x00\x01\x02\x03"

    one = resolve_one_media(_Store(), "k1")
    assert one["modality"] == "image"
    assert one["input"] == b"\x00\x01\x02\x03"


def test_empty_media_ids_and_none_store():
    """Zero-state: no store / no media_ids → empty channels, no skips."""
    atom = _obs(media_ids=())
    out = resolve_media_inputs(atom, None)
    assert out == {"image": None, "audio": None, "video": None, "skipped": []}
    out2 = resolve_media_inputs(_obs(media_ids=("x",)), None)
    assert out2["image"] is None
    assert out2["skipped"] == []


def test_empty_att_id_is_error_token():
    one = resolve_one_media(object(), "")
    assert one["skipped"] == ":error"
    assert one["modality"] is None
    assert one["input"] is None


def test_get_raising_is_error_token():
    class _Boom:
        def get(self, mid: str):
            raise RuntimeError("meta corrupt")

    one = resolve_one_media(_Boom(), "m1")
    assert one["skipped"] == "m1:error"


def test_read_bytes_generic_error_is_error_token():
    class _Att:
        mime = "image/png"
        filename = "x.png"
        kind = "image"
        byte_size = 4
        sha256 = ""
        path = None
        local_path = None

    class _Store:
        def get(self, mid: str):
            return _Att()

        def read_bytes(self, mid: str) -> bytes:
            raise OSError("disk failed")

    one = resolve_one_media(_Store(), "m1")
    assert one["skipped"] == "m1:error"


def test_meta_present_no_path_no_read_bytes_is_no_path():
    class _Att:
        mime = "image/png"
        filename = "x.png"
        kind = "image"
        byte_size = 4
        sha256 = ""
        path = None
        local_path = None

    class _Store:
        def get(self, mid: str):
            return _Att()

    one = resolve_one_media(_Store(), "m1")
    assert one["skipped"] == "m1:no_path"
    assert one["modality"] is None
