"""Memory embed package (Phase 2) — narrow public surface.

Public: open_encoder, encode_atom_inputs, encode_atom, EncodeQueue,
EmbeddingSet, EMBED_DIM, CHANNELS, MockEmbedder / mock_vector for tests.
Core ``elyra.memory`` must not import torch; heavy deps live behind
``open_encoder`` / ``NemotronEmbedder`` lazy imports (PR8).
"""

from elyra.memory.embed.encode import content_fingerprint, encode_atom, is_embeddable
from elyra.memory.embed.gate import EmbedderGate
from elyra.memory.embed.mock import MOCK_MODEL_ID, MockEmbedder, mock_vector
from elyra.memory.embed.queue import (
    EncodePriority,
    EncodeQueue,
    catchup_none_atoms_for_encode,
    scan_pending_into_queue,
)
from elyra.memory.embed.worker import EncodeWorker
from elyra.memory.embed.runtime import (
    DEFAULT_NEMOTRON_MODEL_ID,
    Embedder,
    NemotronEmbedder,
    encode_atom_inputs,
    open_encoder,
    probe_devices,
    select_device,
    torch_available,
    transformers_available,
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
    "DEFAULT_NEMOTRON_MODEL_ID",
    "EMBED_BACKENDS",
    "EMBED_DEVICE_PREFS",
    "EMBED_DIM",
    "MOCK_MODEL_ID",
    "DeviceKind",
    "Embedder",
    "EmbeddingSet",
    "EmbedderGate",
    "EncodePriority",
    "EncodeQueue",
    "EncodeResult",
    "EncodeWorker",
    "MockEmbedder",
    "ModalityParts",
    "NemotronEmbedder",
    "content_fingerprint",
    "encode_atom",
    "encode_atom_inputs",
    "is_embeddable",
    "l2_normalize",
    "mock_vector",
    "open_encoder",
    "probe_devices",
    "catchup_none_atoms_for_encode",
    "scan_pending_into_queue",
    "select_device",
    "torch_available",
    "transformers_available",
    "vector_l2_norm",
]
