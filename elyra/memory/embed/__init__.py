"""Memory embed package (Phase 2) — narrow public surface.

Public: open_encoder, encode_atom_inputs, EmbeddingSet, EMBED_DIM, CHANNELS,
MockEmbedder / mock_vector for tests. Core ``elyra.memory`` must not import
torch; this package keeps heavy deps behind future PR8 runtime paths.
"""

from elyra.memory.embed.mock import MOCK_MODEL_ID, MockEmbedder, mock_vector
from elyra.memory.embed.runtime import (
    Embedder,
    encode_atom_inputs,
    open_encoder,
    select_device,
)
from elyra.memory.embed.types import (
    CHANNELS,
    EMBED_BACKENDS,
    EMBED_DEVICE_PREFS,
    EMBED_DIM,
    DeviceKind,
    EmbeddingSet,
    EncodeResult,
    ModalityParts,
    l2_normalize,
    vector_l2_norm,
)

__all__ = [
    "CHANNELS",
    "EMBED_BACKENDS",
    "EMBED_DEVICE_PREFS",
    "EMBED_DIM",
    "MOCK_MODEL_ID",
    "DeviceKind",
    "Embedder",
    "EmbeddingSet",
    "EncodeResult",
    "MockEmbedder",
    "ModalityParts",
    "encode_atom_inputs",
    "l2_normalize",
    "mock_vector",
    "open_encoder",
    "select_device",
    "vector_l2_norm",
]
