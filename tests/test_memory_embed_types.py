"""Embed pure types: EmbeddingSet, channels, EmbeddingStatus.skipped."""

from __future__ import annotations

import math

import pytest

from elyra.memory.embed.types import (
    CHANNELS,
    CHANNEL_SET,
    EMBED_BACKENDS,
    EMBED_DEVICE_PREFS,
    EMBED_DIM,
    EmbeddingSet,
    EncodeResult,
    ModalityParts,
    embedding_set_from_mapping,
    l2_normalize,
    vector_l2_norm,
)
from elyra.memory.types import (
    EMBEDDING_STATUSES,
    Atom,
    atom_from_dict,
    atom_to_dict,
    new_atom_id,
    validate_atom,
)


def test_channels_and_dim_contract():
    assert CHANNELS == ("text", "image", "audio", "video", "joint")
    assert CHANNEL_SET == frozenset(CHANNELS)
    assert EMBED_DIM == 2048
    assert EMBED_BACKENDS == frozenset({"mock", "nemotron"})
    assert EMBED_DEVICE_PREFS == frozenset({"auto", "cuda", "rocm", "cpu"})


def test_embedding_status_includes_skipped():
    """KD8: EmbeddingStatus adds skipped; keep none/pending/ready/failed."""
    assert EMBEDDING_STATUSES == frozenset(
        {"none", "pending", "ready", "failed", "skipped"}
    )


def test_validate_atom_accepts_skipped():
    atom = Atom(
        atom_id=new_atom_id(),
        t_start="2026-07-28T12:00:00Z",
        kind="moment_meta",
        content_text="",
        embedding_status="skipped",
    )
    assert validate_atom(atom) is atom
    row = atom_to_dict(atom)
    assert row["embedding_status"] == "skipped"
    restored = atom_from_dict(row)
    assert restored.embedding_status == "skipped"


def test_validate_atom_rejects_unknown_embedding_status():
    atom = Atom(
        atom_id="a_x",
        t_start="2026-07-28T12:00:00Z",
        kind="speak",
        content_text="x",
        embedding_status="bogus",
    )
    with pytest.raises(ValueError, match="invalid embedding_status"):
        validate_atom(atom)


def test_atom_from_dict_unknown_status_becomes_none():
    """Unknown statuses degrade to none (tolerant load)."""
    atom = atom_from_dict(
        {
            "atom_id": "a_1",
            "t_start": "2026-07-28T12:00:00Z",
            "kind": "speak",
            "content_text": "hi",
            "embedding_status": "not_a_status",
        }
    )
    assert atom.embedding_status == "none"


def test_embedding_set_dim_and_channels_present():
    vec = l2_normalize([1.0] + [0.0] * (EMBED_DIM - 1))
    emb = EmbeddingSet(
        atom_id="a_1",
        emb_text=vec,
        emb_joint=vec,
    )
    assert emb.dim == EMBED_DIM
    assert emb.channels_present == ("text", "joint")
    assert emb.has_any_vector() is True
    assert emb.channel_vector("text") is vec
    assert emb.channel_vector("image") is None
    with pytest.raises(ValueError, match="unknown embed channel"):
        emb.channel_vector("smell")


def test_embedding_set_empty_has_no_channels():
    emb = EmbeddingSet(atom_id="a_empty")
    assert emb.channels_present == ()
    assert emb.has_any_vector() is False


def test_embedding_set_rejects_wrong_dim():
    short = (1.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="emb_text: expected length"):
        EmbeddingSet(atom_id="a_bad", emb_text=short)


def test_embedding_set_rejects_non_finite():
    bad = (float("nan"),) + (0.0,) * (EMBED_DIM - 1)
    with pytest.raises(ValueError, match="non-finite"):
        EmbeddingSet(atom_id="a_nan", emb_text=bad)
    inf = (float("inf"),) + (0.0,) * (EMBED_DIM - 1)
    with pytest.raises(ValueError, match="non-finite"):
        EmbeddingSet(atom_id="a_inf", emb_text=inf)


def test_embedding_set_has_any_vector_ignores_declared_only():
    """Phantom channels_present without vectors is rejected / not trusted."""
    with pytest.raises(ValueError, match="channels_present"):
        EmbeddingSet(
            atom_id="a_phantom",
            channels_present=("text",),
            emb_text=None,
        )
    empty = EmbeddingSet(atom_id="a_empty", channels_present=())
    assert empty.has_any_vector() is False


def test_l2_normalize_unit_and_zero():
    v = l2_normalize([3.0, 4.0])
    assert abs(vector_l2_norm(v) - 1.0) < 1e-9
    assert abs(v[0] - 0.6) < 1e-9
    assert abs(v[1] - 0.8) < 1e-9

    z = l2_normalize([0.0, 0.0, 0.0])
    assert abs(vector_l2_norm(z) - 1.0) < 1e-9
    assert z[0] == 1.0

    with pytest.raises(ValueError, match="non-empty"):
        l2_normalize([])


def test_encode_result_derives_channels():
    vec = l2_normalize([1.0] * EMBED_DIM)
    emb = EmbeddingSet(atom_id="a_1", emb_text=vec)
    result = EncodeResult(status="ready", embeddings=emb)
    assert result.channels_encoded == ("text",)
    assert result.meta == {}


def test_modality_parts_present():
    p = ModalityParts(text="  hi  ", image=b"\x89PNG", audio=None, video=None)
    assert p.present_modalities() == ("text", "image")
    empty = ModalityParts(text="   ", image=None)
    assert empty.present_modalities() == ()
    # Empty media bytes / blank path are absent (skip policy alignment).
    blank_media = ModalityParts(text=None, image=b"", audio="", video="  ")
    assert blank_media.present_modalities() == ()


def test_embedding_set_from_mapping():
    vec = tuple(float(i) for i in range(4))
    emb = embedding_set_from_mapping(
        "a_x",
        {"text": vec, "joint": vec},
        dim=4,
        model_id="m",
        encoded_at="2026-01-01T00:00:00Z",
    )
    assert emb.atom_id == "a_x"
    assert emb.dim == 4
    assert emb.emb_text == vec
    assert emb.emb_joint == vec
    assert emb.model_id == "m"
    assert math.isclose(sum(emb.emb_text), sum(vec))
