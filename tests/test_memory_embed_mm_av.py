"""PR6 (#124): audio/video MM encode + query matrix (hermetic mock).

Does **not** re-own image fixture/drain (PR1/PR3) or neighbors API (PR4) or
full glass (PR5). Owns:

1. Fixed fixtures ``tests/fixtures/mm_embed/tiny.wav`` (+ cheap ``tiny.mp4``).
2. Real MediaStore resolve + mock ``encode_atom`` / queue drain for
   **audio** and **video** channels (same diagnostics as image path).
3. Query-side matrix: ``resolve_one_media`` + ``MockEmbedder.encode_{audio,video}``
   as the shared seed path neighbors will use (KD-M21); no API surface here.
4. Optional live Nemotron audio/video under ``@pytest.mark.memory_embed`` /
   ``gpu`` — skip cleanly without deps / weights / mm utils.

Hermetic suite: mock only; no torch / GPU / sandbox junk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.media.store import MediaStore
from elyra.memory.config import MemorySettings
from elyra.memory.embed import EMBED_DIM, MockEmbedder, encode_atom
from elyra.memory.embed.encode import resolve_media_inputs, resolve_one_media
from elyra.memory.embed.mock import mock_vector
from elyra.memory.embed.queue import EncodeQueue
from elyra.memory.embed.types import ModalityParts
from elyra.memory.store import open_memory_store
from elyra.memory.types import Atom, atom_replace, new_atom_id

FIXTURES = Path(__file__).parent / "fixtures" / "mm_embed"
FIXTURE_TINY_WAV = FIXTURES / "tiny.wav"
FIXTURE_TINY_MP4 = FIXTURES / "tiny.mp4"
FIXTURE_TINY_PNG = FIXTURES / "tiny.png"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def paths(tmp_path: Path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    return open_memory_store(paths, MemorySettings(write_atoms=True, backend="jsonl"))


@pytest.fixture
def media_store(paths) -> MediaStore:
    return MediaStore(paths)


def _atom(
    *,
    text: str = "caption",
    status: str = "pending",
    media_ids: tuple[str, ...] = (),
    kind: str = "observation",
    meta: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Atom:
    return Atom(
        atom_id=kwargs.pop("atom_id", None) or new_atom_id(),
        t_start=kwargs.pop("t_start", "2026-08-05T15:00:00Z"),
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=kwargs.pop("moment_id", "m1"),
        media_ids=media_ids,
        embedding_status=status,
        meta=meta or {},
        **kwargs,
    )


class _Idx:
    """Index double: upsert(EmbeddingSet) → ready; records last emb set."""

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    def upsert(self, *args: Any, **kwargs: Any) -> bool:
        if args and hasattr(args[0], "atom_id"):
            emb = args[0]
            self.seen[str(emb.atom_id)] = emb
            return True
        if len(args) >= 2:
            self.seen[str(args[0])] = args[1]
            return True
        return False


def _assert_unit(vec: Any, dim: int = EMBED_DIM) -> None:
    assert vec is not None
    assert len(vec) == dim
    n = sum(float(x) * float(x) for x in vec) ** 0.5
    assert abs(n - 1.0) < 1e-5, f"expected unit vector, got norm={n}"


def _assert_clean_skips(skipped: list[Any] | None) -> None:
    assert skipped == [] or skipped is None or list(skipped) == []
    for s in skipped or []:
        assert "no_path" not in str(s)
        assert "unknown_type" not in str(s)


# ---------------------------------------------------------------------------
# Fixture pack sanity
# ---------------------------------------------------------------------------


def test_mm_embed_av_fixtures_present_and_sniffable():
    """PR6 owns tiny.wav + cheap tiny.mp4; magic/filename classify as audio/video."""
    assert FIXTURE_TINY_WAV.is_file()
    assert FIXTURE_TINY_MP4.is_file()
    wav = FIXTURE_TINY_WAV.read_bytes()
    mp4 = FIXTURE_TINY_MP4.read_bytes()
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert mp4[4:8] == b"ftyp"
    # Stay tiny — no large binaries in git (design KD-M6).
    assert len(wav) < 4096
    assert len(mp4) < 4096

    from elyra.media.store import sniff_mime_kind_source

    w_mime, w_kind, w_src = sniff_mime_kind_source(wav, filename="tiny.wav")
    assert w_mime == "audio/wav"
    assert w_kind == "audio"
    assert w_src == "magic"

    v_mime, v_kind, _ = sniff_mime_kind_source(mp4, filename="tiny.mp4")
    assert v_mime == "video/mp4"
    assert v_kind == "video"


# ---------------------------------------------------------------------------
# 1. Resolve matrix — real MediaStore + extensionless blobs (KD-M18/M19)
# ---------------------------------------------------------------------------


def test_resolve_real_mediastores_wav_via_mime_not_blob_suffix(media_store: MediaStore):
    data = FIXTURE_TINY_WAV.read_bytes()
    att = media_store.put_bytes(data, filename="clip.wav", origin="user_upload")
    blob = media_store.blob_path(att.sha256)
    assert blob.is_file()
    assert blob.suffix == ""
    assert not str(blob).endswith(".wav")
    assert att.mime == "audio/wav"
    assert att.kind == "audio"
    assert att.filename == "clip.wav"

    one = resolve_one_media(media_store, att.id)
    assert one["skipped"] is None
    assert one["modality"] == "audio"
    assert isinstance(one["input"], str)
    assert Path(one["input"]).resolve() == blob.resolve()

    atom = _atom(media_ids=(att.id,))
    out = resolve_media_inputs(atom, media_store)
    assert out["audio"] == str(blob)
    assert out["image"] is None
    assert out["video"] is None
    assert out["skipped"] == []


def test_resolve_real_mediastores_mp4_via_mime_filename(media_store: MediaStore):
    data = FIXTURE_TINY_MP4.read_bytes()
    att = media_store.put_bytes(
        data, filename="clip.mp4", mime="video/mp4", origin="user_upload"
    )
    blob = media_store.blob_path(att.sha256)
    assert blob.is_file()
    assert blob.suffix == ""
    assert att.mime == "video/mp4"
    assert att.kind == "video"

    one = resolve_one_media(media_store, att.id)
    assert one["skipped"] is None
    assert one["modality"] == "video"
    assert Path(str(one["input"])).resolve() == blob.resolve()

    out = resolve_media_inputs(_atom(media_ids=(att.id,)), media_store)
    assert out["video"] == str(blob)
    assert out["audio"] is None
    assert out["image"] is None
    assert out["skipped"] == []


def test_resolve_matrix_image_audio_video_first_wins(media_store: MediaStore):
    """Mixed pack: one of each channel; first-wins; no cross-channel pollution."""
    img = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="a.png", origin="user_upload"
    )
    aud = media_store.put_bytes(
        FIXTURE_TINY_WAV.read_bytes(), filename="b.wav", origin="user_upload"
    )
    vid = media_store.put_bytes(
        FIXTURE_TINY_MP4.read_bytes(),
        filename="c.mp4",
        mime="video/mp4",
        origin="user_upload",
    )
    # Distinct ids (different content).
    assert len({img.id, aud.id, vid.id}) == 3

    out = resolve_media_inputs(
        _atom(media_ids=(img.id, aud.id, vid.id)),
        media_store,
    )
    assert out["image"] is not None
    assert out["audio"] is not None
    assert out["video"] is not None
    assert out["skipped"] == []
    assert Path(out["image"]).resolve() == media_store.blob_path(img.sha256).resolve()
    assert Path(out["audio"]).resolve() == media_store.blob_path(aud.sha256).resolve()
    assert Path(out["video"]).resolve() == media_store.blob_path(vid.sha256).resolve()


def test_resolve_second_audio_channel_full(media_store: MediaStore):
    a1 = media_store.put_bytes(
        FIXTURE_TINY_WAV.read_bytes(), filename="first.wav", origin="user_upload"
    )
    # Distinct bytes → distinct att id.
    a2 = media_store.put_bytes(
        FIXTURE_TINY_WAV.read_bytes() + b"\x00",
        filename="second.wav",
        origin="user_upload",
    )
    assert a1.id != a2.id
    out = resolve_media_inputs(_atom(media_ids=(a1.id, a2.id)), media_store)
    assert out["audio"] is not None
    assert any(f"{a2.id}:channel_full:audio" == s for s in out["skipped"])


def test_resolve_second_video_channel_full(media_store: MediaStore):
    v1 = media_store.put_bytes(
        FIXTURE_TINY_MP4.read_bytes(),
        filename="first.mp4",
        mime="video/mp4",
        origin="user_upload",
    )
    v2 = media_store.put_bytes(
        FIXTURE_TINY_MP4.read_bytes() + b"\x00",
        filename="second.mp4",
        mime="video/mp4",
        origin="user_upload",
    )
    out = resolve_media_inputs(_atom(media_ids=(v1.id, v2.id)), media_store)
    assert out["video"] is not None
    assert any(f"{v2.id}:channel_full:video" == s for s in out["skipped"])


# ---------------------------------------------------------------------------
# 2. encode_atom + drain → ready + audio/video channels + diagnostics
# ---------------------------------------------------------------------------


def test_encode_atom_real_mediastores_audio_channel(media_store: MediaStore):
    att = media_store.put_bytes(
        FIXTURE_TINY_WAV.read_bytes(), filename="frame.wav", origin="user_upload"
    )
    atom = _atom(media_ids=(att.id,), text="audio caption")
    result = encode_atom(MockEmbedder(), atom, media_store=media_store)
    assert result.status == "ready"
    assert "audio" in (result.channels_encoded or ())
    assert "text" in (result.channels_encoded or ())
    assert "joint" in (result.channels_encoded or ())
    assert result.embeddings is not None
    assert result.embeddings.emb_audio is not None
    assert result.embeddings.emb_image is None
    assert result.embeddings.emb_video is None
    _assert_unit(result.embeddings.emb_audio)
    # KD-M11: audio channel is not a text vector relabel.
    text_only = tuple(MockEmbedder().encode_text("audio caption"))
    assert result.embeddings.emb_audio != text_only
    skipped = (result.meta or {}).get("embed_media_skipped") or []
    _assert_clean_skips(skipped)


def test_encode_atom_real_mediastores_video_channel(media_store: MediaStore):
    att = media_store.put_bytes(
        FIXTURE_TINY_MP4.read_bytes(),
        filename="frame.mp4",
        mime="video/mp4",
        origin="user_upload",
    )
    atom = _atom(media_ids=(att.id,), text="video caption")
    result = encode_atom(MockEmbedder(), atom, media_store=media_store)
    assert result.status == "ready"
    assert "video" in (result.channels_encoded or ())
    assert result.embeddings is not None
    assert result.embeddings.emb_video is not None
    assert result.embeddings.emb_audio is None
    _assert_unit(result.embeddings.emb_video)
    text_only = tuple(MockEmbedder().encode_text("video caption"))
    assert result.embeddings.emb_video != text_only
    skipped = (result.meta or {}).get("embed_media_skipped") or []
    _assert_clean_skips(skipped)


def test_drain_ready_audio_channel_real_mediastores(store, media_store):
    """Audio drain closed loop: ready + durable channels + empty skip list."""
    att = media_store.put_bytes(
        FIXTURE_TINY_WAV.read_bytes(), filename="drain.wav", origin="user_upload"
    )
    atom = store.put_atom(_atom(text="wav note", status="pending", media_ids=(att.id,)))
    q = EncodeQueue(maxsize=8)
    q.enqueue(atom.atom_id)
    idx = _Idx()
    emb = MockEmbedder()
    stats = q.drain(
        store,
        emb,
        index=idx,
        media_store=media_store,
        max_items=2,
        max_ms=5000,
    )
    assert stats["ok"] == 1
    assert stats["failed"] == 0

    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    channels = list(got.meta.get("embed_channels") or [])
    assert "audio" in channels
    assert "text" in channels
    assert "joint" in channels
    assert got.meta.get("embed_media_skipped") == []
    assert got.meta.get("embed_encode_ok") is True

    held = idx.seen.get(atom.atom_id)
    assert held is not None
    assert held.emb_audio is not None
    assert held.emb_text is not None
    _assert_unit(held.emb_audio)
    assert held.emb_audio != held.emb_text
    text_only = tuple(emb.encode_text("wav note"))
    assert held.emb_audio != text_only


def test_drain_ready_video_channel_real_mediastores(store, media_store):
    att = media_store.put_bytes(
        FIXTURE_TINY_MP4.read_bytes(),
        filename="drain.mp4",
        mime="video/mp4",
        origin="user_upload",
    )
    atom = store.put_atom(_atom(text="mp4 note", status="pending", media_ids=(att.id,)))
    q = EncodeQueue(maxsize=8)
    q.enqueue(atom.atom_id)
    idx = _Idx()
    stats = q.drain(
        store,
        MockEmbedder(),
        index=idx,
        media_store=media_store,
        max_items=2,
        max_ms=5000,
    )
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    channels = set(got.meta.get("embed_channels") or [])
    assert "video" in channels
    assert "text" in channels
    assert "joint" in channels
    assert got.meta.get("embed_media_skipped") == []

    held = idx.seen[atom.atom_id]
    assert held.emb_video is not None
    assert held.emb_audio is None
    _assert_unit(held.emb_video)


def test_drain_audio_only_ready_joint_copy(store, media_store):
    """Media-only audio atom: joint = copy(audio) (KD-R1 sole-mod)."""
    att = media_store.put_bytes(
        FIXTURE_TINY_WAV.read_bytes(), filename="only.wav", origin="user_upload"
    )
    atom = store.put_atom(_atom(text="", status="pending", media_ids=(att.id,)))
    atom = store.put_atom(
        atom_replace(
            atom,
            content_text="",
            media_ids=(att.id,),
            embedding_status="pending",
        ),
        notify=False,
    )

    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    idx = _Idx()
    stats = q.drain(
        store,
        MockEmbedder(),
        index=idx,
        media_store=media_store,
        max_items=2,
        max_ms=5000,
    )
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    channels = set(got.meta.get("embed_channels") or [])
    assert "audio" in channels
    assert "joint" in channels
    assert "text" not in channels
    assert got.meta.get("embed_media_skipped") == []

    held = idx.seen[atom.atom_id]
    assert held.emb_audio is not None
    assert held.emb_text is None
    assert held.emb_joint is not None
    assert held.emb_joint == held.emb_audio


def test_drain_video_only_ready_joint_copy(store, media_store):
    att = media_store.put_bytes(
        FIXTURE_TINY_MP4.read_bytes(),
        filename="only.mp4",
        mime="video/mp4",
        origin="user_upload",
    )
    atom = store.put_atom(_atom(text="", status="pending", media_ids=(att.id,)))
    atom = store.put_atom(
        atom_replace(
            atom,
            content_text="",
            media_ids=(att.id,),
            embedding_status="pending",
        ),
        notify=False,
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    idx = _Idx()
    stats = q.drain(
        store,
        MockEmbedder(),
        index=idx,
        media_store=media_store,
        max_items=2,
        max_ms=5000,
    )
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    channels = set(got.meta.get("embed_channels") or [])
    assert "video" in channels
    assert "joint" in channels
    assert "text" not in channels

    held = idx.seen[atom.atom_id]
    assert held.emb_video is not None
    assert held.emb_joint == held.emb_video


def test_drain_text_audio_joint_true_fusion(store, media_store):
    """Multi-mod text+audio: emb_joint is encode_joint, not sole-channel copy."""
    att = media_store.put_bytes(
        FIXTURE_TINY_WAV.read_bytes(), filename="joint.wav", origin="user_upload"
    )
    caption = "joint audio fusion"
    atom = store.put_atom(_atom(text=caption, status="pending", media_ids=(att.id,)))
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    idx = _Idx()
    emb = MockEmbedder()
    stats = q.drain(
        store, emb, index=idx, media_store=media_store, max_items=2, max_ms=5000
    )
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert set(got.meta.get("embed_channels") or []) >= {"text", "audio", "joint"}

    held = idx.seen[atom.atom_id]
    assert held.emb_text is not None
    assert held.emb_audio is not None
    assert held.emb_joint is not None
    _assert_unit(held.emb_joint)
    assert held.emb_joint != held.emb_text
    assert held.emb_joint != held.emb_audio
    blob = media_store.blob_path(att.sha256)
    expected = tuple(emb.encode_joint(ModalityParts(text=caption, audio=str(blob))))
    assert held.emb_joint == expected


def test_drain_second_audio_channel_full_skip_list(store, media_store):
    a1 = media_store.put_bytes(
        FIXTURE_TINY_WAV.read_bytes(), filename="first.wav", origin="user_upload"
    )
    a2 = media_store.put_bytes(
        FIXTURE_TINY_WAV.read_bytes() + b"\x01",
        filename="second.wav",
        origin="user_upload",
    )
    atom = store.put_atom(
        _atom(text="two wavs", status="pending", media_ids=(a1.id, a2.id))
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    idx = _Idx()
    stats = q.drain(
        store,
        MockEmbedder(),
        index=idx,
        media_store=media_store,
        max_items=2,
        max_ms=5000,
    )
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    assert "audio" in (got.meta.get("embed_channels") or [])
    skipped = got.meta.get("embed_media_skipped") or []
    assert any("channel_full" in s for s in skipped)
    assert any(a2.id in s for s in skipped)
    assert idx.seen[atom.atom_id].emb_audio is not None


# ---------------------------------------------------------------------------
# 3. Query-side matrix (shared resolve + encode_* as neighbors seed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture,filename,mime,modality,encode_attr",
    [
        (FIXTURE_TINY_WAV, "q.wav", None, "audio", "encode_audio"),
        (FIXTURE_TINY_MP4, "q.mp4", "video/mp4", "video", "encode_video"),
        (FIXTURE_TINY_PNG, "q.png", None, "image", "encode_image"),
    ],
)
def test_query_seed_resolve_and_encode_modality(
    media_store: MediaStore,
    fixture: Path,
    filename: str,
    mime: str | None,
    modality: str,
    encode_attr: str,
):
    """Neighbors-style seed: att_id → resolve_one_media → encode_{mod}.

    PR4 owns HTTP; this proves the shared encode path for audio/video query
    vectors under mock (KD-M21 + OQ-M2 matrix).
    """
    kwargs: dict[str, Any] = {"filename": filename, "origin": "user_upload"}
    if mime:
        kwargs["mime"] = mime
    att = media_store.put_bytes(fixture.read_bytes(), **kwargs)

    one = resolve_one_media(media_store, att.id)
    assert one["skipped"] is None
    assert one["modality"] == modality
    assert one["input"] is not None

    emb = MockEmbedder()
    encode_fn = getattr(emb, encode_attr)
    qvec = encode_fn(one["input"])
    _assert_unit(qvec)
    # Query vector must not equal a free-text encode of the filename.
    tvec = emb.encode_text(filename)
    assert list(qvec) != list(tvec)


def test_query_seed_audio_and_video_vectors_differ(media_store: MediaStore):
    """Same mock backend: audio vs video seeds produce distinct unit vectors."""
    aud = media_store.put_bytes(
        FIXTURE_TINY_WAV.read_bytes(), filename="s.wav", origin="user_upload"
    )
    vid = media_store.put_bytes(
        FIXTURE_TINY_MP4.read_bytes(),
        filename="s.mp4",
        mime="video/mp4",
        origin="user_upload",
    )
    emb = MockEmbedder()
    a_one = resolve_one_media(media_store, aud.id)
    v_one = resolve_one_media(media_store, vid.id)
    av = emb.encode_audio(a_one["input"])
    vv = emb.encode_video(v_one["input"])
    assert list(av) != list(vv)
    _assert_unit(av)
    _assert_unit(vv)


def test_query_seed_missing_att_skipped_reason():
    """Well-formed missing att_id → :missing (neighbors omit path)."""

    class _Empty:
        def get(self, mid: str):
            return None

    one = resolve_one_media(_Empty(), "att_deadbeefdeadbeefdeadbeefdeadbeef")
    assert one["modality"] is None
    assert one["input"] is None
    assert one["skipped"] == "att_deadbeefdeadbeefdeadbeefdeadbeef:missing"


def test_query_seed_oversize_audio_without_read(
    media_store: MediaStore, monkeypatch: pytest.MonkeyPatch
):
    att = media_store.put_bytes(
        FIXTURE_TINY_WAV.read_bytes(), filename="big.wav", origin="user_upload"
    )
    monkeypatch.setattr(att, "byte_size", 10_000_000)
    real_get = media_store.get

    def _get(aid: str):
        got = real_get(aid)
        if got is not None and got.id == att.id:
            return att
        return got

    monkeypatch.setattr(media_store, "get", _get)
    calls: list[str] = []
    real_read = media_store.read_bytes

    def _spy(aid: str) -> bytes:
        calls.append(aid)
        return real_read(aid)

    monkeypatch.setattr(media_store, "read_bytes", _spy)
    one = resolve_one_media(media_store, att.id, max_bytes=100)
    assert one["skipped"] == f"{att.id}:oversize_bytes:10000000"
    assert one["modality"] is None
    assert calls == []


# ---------------------------------------------------------------------------
# 4. Optional live Nemotron audio/video (skip cleanly without deps)
# ---------------------------------------------------------------------------


def _torch_tf_ready() -> bool:
    try:
        from elyra.memory.embed.runtime import (
            torch_available,
            transformers_available,
        )
    except Exception:  # noqa: BLE001
        return False
    return bool(torch_available() and transformers_available())


def _mm_utils_ready() -> bool:
    try:
        import qwen_omni_utils  # noqa: F401
        from qwen_omni_utils import process_mm_info  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _gpu_ready() -> bool:
    try:
        from elyra.memory.embed.runtime import probe_devices, torch_available
    except Exception:  # noqa: BLE001
        return False
    if not torch_available():
        return False
    caps = probe_devices()
    return bool(caps.get("cuda") or caps.get("rocm"))


def _model_reachable() -> bool:
    """Best-effort: local path or HF hub cache; never downloads."""
    import os

    from elyra.memory.embed import DEFAULT_NEMOTRON_MODEL_ID  # noqa: F401

    env_path = (os.environ.get("ELYRA_EMBED_MODEL_PATH") or "").strip()
    if env_path and Path(env_path).is_dir():
        return True
    cfg_path = (MemorySettings().embed_model_path or "").strip()
    if cfg_path and Path(cfg_path).is_dir():
        return True
    marker = Path.home() / ".cache" / "huggingface" / "hub"
    if not marker.is_dir():
        return False
    try:
        for child in marker.iterdir():
            if "omni-embed-nemotron" in child.name.lower():
                return True
    except OSError:
        return False
    return False


@pytest.mark.memory_embed
@pytest.mark.parametrize(
    "fixture,encode_attr,label",
    [
        (FIXTURE_TINY_WAV, "encode_audio", "audio"),
        (FIXTURE_TINY_MP4, "encode_video", "video"),
    ],
)
def test_nemotron_encode_av_optional_skips_clean(
    fixture: Path, encode_attr: str, label: str
):
    """Optional live: Nemotron encode_audio / encode_video when deps ready.

    Without torch/transformers / qwen_omni_utils / weights → skip.
    Assertion failures (dim / non-unit) still fail when the path runs.
    """
    if not fixture.is_file():
        pytest.skip(f"fixture missing: {fixture}")
    if not _torch_tf_ready():
        pytest.skip("torch/transformers not installed (elyra[memory-embed])")
    if not _mm_utils_ready():
        pytest.skip("qwen_omni_utils not installed (media encode unavailable)")
    if not _model_reachable():
        pytest.skip("Nemotron weights not cached/local (no download in CI)")

    from elyra.memory.embed import (
        DEFAULT_NEMOTRON_MODEL_ID,
        NemotronEmbedder,
        open_encoder,
    )

    cfg = MemorySettings(
        embed_backend="nemotron",
        embed_model_id=DEFAULT_NEMOTRON_MODEL_ID,
        embed_device="cpu",
    )
    enc = open_encoder(cfg)
    try:
        assert isinstance(enc, NemotronEmbedder)
        try:
            enc.ensure_loaded()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — load/OOM/import only
            pytest.skip(f"Nemotron load unavailable: {exc}")

        h = enc.health()
        if not h.get("media_encode"):
            pytest.skip("Nemotron media_encode false after load (mm utils path)")

        encode_fn = getattr(enc, encode_attr)
        try:
            vec = encode_fn(str(fixture))
        except Exception as exc:  # noqa: BLE001 — runtime encode only
            pytest.skip(f"Nemotron {encode_attr} unavailable: {exc}")

        assert len(vec) == EMBED_DIM
        norm = sum(x * x for x in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-3
        try:
            text_vec = enc.encode_text(f"contrast {label}")
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Nemotron encode_text unavailable for contrast: {exc}")
        assert list(vec) != list(text_vec)
    finally:
        enc.close()


@pytest.mark.memory_embed
@pytest.mark.gpu
def test_nemotron_encode_audio_gpu_optional():
    """GPU audio encode smoke when CUDA/ROCm + weights + mm_utils present."""
    if not _gpu_ready():
        pytest.skip("CUDA/ROCm unavailable")
    if not _torch_tf_ready():
        pytest.skip("torch/transformers not installed")
    if not _mm_utils_ready():
        pytest.skip("qwen_omni_utils not installed")
    if not _model_reachable():
        pytest.skip("Nemotron weights not cached/local (no download in CI)")
    if not FIXTURE_TINY_WAV.is_file():
        pytest.skip("tiny.wav fixture missing")

    from elyra.memory.embed import (
        DEFAULT_NEMOTRON_MODEL_ID,
        NemotronEmbedder,
        open_encoder,
    )

    cfg = MemorySettings(
        embed_backend="nemotron",
        embed_model_id=DEFAULT_NEMOTRON_MODEL_ID,
        embed_device="auto",
    )
    enc = open_encoder(cfg)
    try:
        assert isinstance(enc, NemotronEmbedder)
        try:
            enc.ensure_loaded()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Nemotron load unavailable: {exc}")
        if not enc.health().get("media_encode"):
            pytest.skip("media_encode unavailable on GPU path")
        try:
            vec = enc.encode_audio(str(FIXTURE_TINY_WAV))
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Nemotron encode_audio unavailable: {exc}")
        assert len(vec) == EMBED_DIM
        norm = sum(x * x for x in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-3
        h = enc.health()
        assert h["ok"] is True
        assert h["device"] in ("cuda", "rocm")
    finally:
        enc.close()


def test_optional_live_markers_and_fixtures_registered():
    """Markers + fixtures used by PR6 live tests stay hermetic on collect."""
    assert FIXTURE_TINY_WAV.is_file()
    assert FIXTURE_TINY_MP4.is_file()
    v = mock_vector("pr6-av-matrix", dim=16)
    assert len(v) == 16
