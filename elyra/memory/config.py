"""Memory path roots and settings helpers (Phase 1 + Phase 2 knobs).

Scope: data/memory layout constants, MemorySettings defaults used by the store
and nested under ``Settings.memory`` (elyra.toml / CLI merge in settings.py).
In scope: path helpers, frozen MemorySettings, backend/embed allowlists.
Out of scope: promote/meal/ladder/embed worker wiring (later PRs).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from elyra.config import ElyraPaths
from elyra.memory.embed.types import (
    EMBED_BACKENDS,
    EMBED_DEVICE_PREFS,
    SEARCH_CHANNEL_SET,
)

MEMORY_DIRNAME = "memory"
ATOMS_JSONL = "atoms.jsonl"
META_JSON = "meta.json"
ATOMS_BLOB_DIRNAME = "atoms"
LADDER_DIRNAME = "ladder"
LADDER_STATE = "state.json"
LANCE_DIRNAME = "lance"

# Inline body threshold for JSONL rows (spill to blob when longer).
# Kept below atom_max_chars (8000) so spill is reachable under default settings
# for bodies in (inline_max, atom_max] before cap truncates.
MEMORY_INLINE_MAX_CHARS = 4000
# Compaction triggers (idle path only; never mid-hop).
MEMORY_JSONL_COMPACT_BYTES = 8 * 1024 * 1024
MEMORY_JSONL_COMPACT_DIRTY = 256

# Valid ``MemorySettings.backend`` values (settings validation + factory).
MEMORY_BACKENDS = frozenset({"jsonl", "lance"})
_MEMORY_BACKENDS = MEMORY_BACKENDS  # alias for older call sites

# Phase 2 embed allowlists — single source: elyra.memory.embed.types.
MEMORY_EMBED_BACKENDS = EMBED_BACKENDS
MEMORY_EMBED_DEVICES = EMBED_DEVICE_PREFS
MEMORY_SEARCH_CHANNELS = SEARCH_CHANNEL_SET


@dataclass(frozen=True)
class MemorySettings:
    """Memory knobs (Phase 1 + Phase 2 semantic/embed; semantic defaults OFF).

    Nested under ``Settings.memory``; loaded from ``[memory]`` in elyra.toml.
    Store-only knobs (inline/compact) remain on this dataclass so factory and
    toml can tune them without a second type.

    Phase 2 flags (KD9 / KD23): ``semantic_enabled``, ``embed_enabled``, and
    ``parcels_enabled`` default **false** — zero behaviour change until
    operators opt in. No worker/meal wiring reads these in PR1.
    """

    enabled: bool = True  # outer meal uses labeled memory package (not full glass slide)
    write_atoms: bool = True  # promote beats/wakes into the atom store
    backend: str = "jsonl"  # jsonl | lance
    episodic_fraction: float = 0.20
    episodic_horizon_hours: float = 24.0
    ladder_enabled: bool = True  # runs if write_atoms or enabled
    ladder_max_ms_per_tick: int = 50
    regather_every_n_hops: int = 0  # 0 = off
    atom_max_chars: int = 8000
    compact_max_tokens: int = 400
    link_across_moments: bool = True
    model_promote_min_chars: int = 40
    protect_tail_atoms: int = 12
    tool_ok_preview_chars: int = 240
    max_tool_atoms_per_moment: int = 48
    # Store-only knobs.
    inline_max_chars: int = MEMORY_INLINE_MAX_CHARS
    jsonl_compact_bytes: int = MEMORY_JSONL_COMPACT_BYTES
    jsonl_compact_dirty: int = MEMORY_JSONL_COMPACT_DIRTY

    # --- Phase 2 semantic / embed (defaults OFF — KD9) ---
    semantic_enabled: bool = False  # meal channel + pending writes
    embed_enabled: bool = False  # allow load real/mock encoder drain
    embed_backend: str = "mock"  # mock | nemotron
    embed_model_id: str = "nvidia/omni-embed-nemotron-3b"
    embed_model_path: str = ""  # optional local path under ELYRA_HOME
    embed_device: str = "auto"  # auto | cuda | rocm | cpu
    embed_preload: bool = False
    embed_media_max_bytes: int = 8_000_000
    embed_media_max_seconds: int = 30
    encode_max_ms_per_tick: int = 100
    encode_max_items_per_tick: int = 4
    encode_max_attempts: int = 3
    encode_queue_max: int = 1024
    encode_query_max_ms: int = 30  # sub-budget of semantic_select_max_ms
    semantic_select_max_ms: int = 50  # total encode+search+pack in rebuild_outer
    # OQ4: flip historical none → pending when semantic+embed on (idle catch-up).
    embed_catchup_max: int = 500  # max none atoms marked pending per process life
    embed_catchup_horizon_hours: float = 168.0  # only t_start within this lookback
    embed_catchup_per_tick: int = 32  # max none→pending per idle tick
    parcels_enabled: bool = False  # KD23: off until operator enables
    parcel_threshold_chars: int = 8000
    semantic_fraction: float = 0.12  # of remaining when semantic on
    episodic_fraction_with_semantic: float = 0.18
    temporal_min_fraction: float = 0.55
    semantic_horizon_hours: float = 168.0
    semantic_top_k: int = 12
    semantic_min_score: float = 0.0  # 0 = off
    # KD-R2: product default auto (resolve joint-primary after repair).
    semantic_search_channel: str = "auto"  # auto | joint | text | image | audio | video
    # KD-R1: single-modality encode also writes emb_joint = copy(sole).
    embed_joint_for_single_modality: bool = True
    # KD-R11: eager joint-copy repair caps (open + idle; never hop path).
    joint_repair_max_per_open: int = 500
    joint_repair_max_per_tick: int = 64
    ann_recent_buffer_max: int = 256
    ann_full_search_below: int = 2000
    ann_optimize_every_n_encodes: int = 64
    ann_optimize_interval_s: int = 300
    ann_optimize_max_ms: int = 200


def memory_root(paths: ElyraPaths) -> Path:
    """Return ``{data_dir}/memory``."""
    return paths.data_dir / MEMORY_DIRNAME


def atoms_jsonl_path(paths: ElyraPaths) -> Path:
    return memory_root(paths) / ATOMS_JSONL


def memory_meta_path(paths: ElyraPaths) -> Path:
    return memory_root(paths) / META_JSON


def lance_root(paths: ElyraPaths) -> Path:
    """Return ``{data_dir}/memory/lance`` (LanceDB table root)."""
    return memory_root(paths) / LANCE_DIRNAME


def atoms_blob_root(paths: ElyraPaths) -> Path:
    return memory_root(paths) / ATOMS_BLOB_DIRNAME


def ladder_dir(paths: ElyraPaths) -> Path:
    return memory_root(paths) / LADDER_DIRNAME


def ensure_memory_dirs(paths: ElyraPaths) -> Path:
    """Create ``data/memory`` (+ atoms/ and ladder/); return memory root."""
    root = memory_root(paths)
    root.mkdir(parents=True, exist_ok=True)
    (root / ATOMS_BLOB_DIRNAME).mkdir(parents=True, exist_ok=True)
    (root / LADDER_DIRNAME).mkdir(parents=True, exist_ok=True)
    return root


def blob_relpath_for_atom(atom_id: str) -> str:
    """Relative path under memory root for spilled content: ``atoms/ab/a_….txt``."""
    safe = atom_id.replace("/", "_").replace("\\", "_")
    prefix = safe[:2] if len(safe) >= 2 else safe or "xx"
    return f"{ATOMS_BLOB_DIRNAME}/{prefix}/{safe}.txt"


__all__ = [
    "ATOMS_BLOB_DIRNAME",
    "ATOMS_JSONL",
    "LADDER_DIRNAME",
    "LADDER_STATE",
    "LANCE_DIRNAME",
    "MEMORY_BACKENDS",
    "MEMORY_DIRNAME",
    "MEMORY_EMBED_BACKENDS",
    "MEMORY_EMBED_DEVICES",
    "MEMORY_SEARCH_CHANNELS",
    "MEMORY_INLINE_MAX_CHARS",
    "MEMORY_JSONL_COMPACT_BYTES",
    "MEMORY_JSONL_COMPACT_DIRTY",
    "META_JSON",
    "MemorySettings",
    "atoms_blob_root",
    "atoms_jsonl_path",
    "blob_relpath_for_atom",
    "ensure_memory_dirs",
    "ladder_dir",
    "lance_root",
    "memory_meta_path",
    "memory_root",
]
