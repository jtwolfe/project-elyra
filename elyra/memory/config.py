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
# KD-R4: sole rollback knob for main-leg vector search engine.
MEMORY_ANN_SEARCH_BACKENDS = frozenset({"lance_native", "python"})

# Wait-for-select absolute ceiling band (ms). Shared by settings validation,
# select_semantic, and the runtime glass toggle — keep memory free of runtime.
SEMANTIC_WAIT_MAX_MS_MIN = 1_000
SEMANTIC_WAIT_MAX_MS_MAX = 120_000
SEMANTIC_WAIT_MAX_MS_DEFAULT = 15_000

# Phase 2a traverse hard maxes (settings validation; design budgets table).
TRAVERSE_EXPAND_MAX_MS_MAX = 500
TRAVERSE_MAX_DEPTH_MAX = 6
TRAVERSE_MAX_NODES_MAX = 128
TRAVERSE_MAX_STEPS_MAX = 16
TRAVERSE_MAX_SEEDS_MAX = 16
TRAVERSE_FRONTIER_MAX_MAX = 32
TRAVERSE_MAX_EXPAND_PER_STEP_MAX = 8
TRAVERSE_KEEP_MAX_MAX = 32
TRAVERSE_SESSION_TTL_S_MAX = 3600
TRAVERSE_LABEL_CHARS_MAX = 160
TRAVERSE_PREVIEW_CHARS_MAX = 800
TRAVERSE_INSPECT_CHARS_PER_ID_MAX = 2000
TRAVERSE_INSPECT_MAX_IDS_MAX = 8
TRAVERSE_INSPECT_MAX_TOTAL_CHARS_MAX = 6000
TRAVERSE_SCRATCHPAD_CHARS_MAX = 400
TRAVERSE_SEMANTIC_K_MAX = 16
TRAVERSE_PARCEL_CHILD_CAP_MAX = 128
TRAVERSE_SAME_MOMENT_K_MAX = 16


def clamp_semantic_wait_max_ms(value: float | int) -> int:
    """Clamp wait-for-select ceiling to the product [1000, 120000] ms band."""
    v = int(value)
    if v < SEMANTIC_WAIT_MAX_MS_MIN:
        return SEMANTIC_WAIT_MAX_MS_MIN
    if v > SEMANTIC_WAIT_MAX_MS_MAX:
        return SEMANTIC_WAIT_MAX_MS_MAX
    return v


def is_directed_traversal_enabled(settings: MemorySettings | None) -> bool:
    """True when directed traversal tools/session may run."""
    if settings is None:
        return False
    return bool(getattr(settings, "directed_traversal_enabled", False))


def is_directed_keep_enabled(settings: MemorySettings | None) -> bool:
    """Effective directed_keep flag (OQ-A1: follows traversal when on).

    Both flags default false out of box. When ``directed_traversal_enabled``
    is true, keep is treated as on for dogfood (single operator knob).
    Explicit ``directed_keep_enabled=true`` also activates keep without
    requiring traversal tools.
    """
    if settings is None:
        return False
    if bool(getattr(settings, "directed_keep_enabled", False)):
        return True
    return bool(getattr(settings, "directed_traversal_enabled", False))


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
    ladder_max_ms_per_tick: int = 200  # nibble / repair (template)
    # --- Episodic ladder LLM + hourly schedule (#92 PR-A) ---
    summary_mode: str = "template"  # template | llm (CI default hermetic)
    ladder_write_legacy_scales: bool = False  # reject new 15m/6h writes
    ladder_hourly_max_ms: int = 12000  # hourly + cascade wall-clock
    ladder_catchup_max_hours: int = 24  # closed 1h per hourly tick
    ladder_llm_max_calls_per_tick: int = 3
    ladder_llm_max_calls_per_hour: int = 40
    ladder_skip_empty: bool = True  # skip put when window has no sources
    ladder_recent_1h_meal: int = 6  # meal band (PR-D consumes)
    ladder_source_edge_k: int = 24  # write cap for source edges (PR-C)
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
    # Wait-for-select: when on, raise ceiling and keep slow encode results
    # (CPU Nemotron dogfood). Product band [SEMANTIC_WAIT_MAX_MS_MIN,
    # SEMANTIC_WAIT_MAX_MS_MAX]; settings validation enforces the same band.
    # Runtime JSON (if present) overlays on the worker path; missing JSON
    # seeds from these settings so elyra.toml affects live meal until glass
    # writes semantic_wait.json.
    semantic_wait_for_select: bool = True
    semantic_wait_max_ms: int = SEMANTIC_WAIT_MAX_MS_DEFAULT
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
    # KD-R3: skip IVF/create_index when channel vector count is below this.
    # Full scan remains correct — not a product error. 0 = always attempt IVF.
    ann_ivf_min_vectors: int = 256
    # KD-R3: columns to build ANN on (channel names ⊂ CHANNEL_SET). Default joint only.
    ann_index_channels: tuple[str, ...] = ("joint",)
    # KD-R4 / OQ-R6: primary main-leg engine. Sole rollback is ``python``.
    # Small-N under lance_native reports search_mode=full_lance (not full_python).
    ann_search_backend: str = "lance_native"  # lance_native | python

    # --- Phase 2a directed traversal (PR-A1 GraphView + PR-A2 session) ---
    # Feature flags default OFF (KD-A10). OQ-A1: directed_keep follows
    # directed_traversal when the latter is on (helper; both still false OOB).
    directed_traversal_enabled: bool = False
    directed_keep_enabled: bool = False
    directed_keep_fraction: float = 0.08  # meal residual share when channel active
    # Sticky directed-keep tray TTL / cap (S3 / #93 B5+B5b). Host-owned.
    directed_keep_hard_ttl_hours: float = 24.0  # hard drop on load + compose
    directed_keep_soft_ttl_hours: float = 3.0  # prefer cut under meal pressure
    directed_keep_entry_cap: int = 32  # hard safety LRU cap

    # --- Glass-tail band (S1 / #93 instance continuity) ---
    # Soft residual share + absolute message floor for social wakes (KD-SOC).
    glass_tail_fraction: float = 0.08  # soft % of residual R (5–12% band)
    glass_tail_floor_messages: int = 4  # social wakes only; ≥2 full turns
    glass_tail_max_messages: int = 16  # hard cap — prevent unbounded dump
    glass_tail_list_limit: int = 80  # align with rebuild_outer list_messages

    # Per-step expand compute (NOT multi-hop session wall-clock — KD-A18).
    traverse_expand_max_ms: int = 80  # soft wall for neighbors / seed_from_text
    # Start seed_from_text budget; 0 = same as traverse_expand_max_ms.
    traverse_start_expand_max_ms: int = 0
    traverse_parcel_child_cap: int = 32  # parent_of reverse chain / moment cap
    traverse_same_moment_k: int = 4  # OQ-A4 same_moment soft edge cap
    traverse_semantic_k: int = 8  # semantic_hop / seed_from_text top-k
    traverse_allow_semantic_hops: bool = True  # no-ops without index / cold encoder
    traverse_temporal_half_life_hours: float = 72.0  # weight model half-life
    traverse_min_expand_weight: float = 0.05  # drop edges below this floor

    # Session budgets (hard maxes enforced in settings validation).
    traverse_max_depth: int = 3
    traverse_max_nodes: int = 48
    traverse_max_steps: int = 8
    traverse_max_seeds: int = 8
    traverse_frontier_max: int = 16
    traverse_max_expand_per_step: int = 3
    traverse_keep_max: int = 16
    traverse_keep_adjacent: bool = True  # finish: sequential ±1 if slots remain
    traverse_session_ttl_s: int = 900  # idle TTL for active only (KD-A18)

    # Thin surface / inspect caps (KD-A17).
    traverse_label_chars: int = 80
    traverse_preview_chars: int = 400
    traverse_inspect_chars_per_id: int = 800
    traverse_inspect_max_ids: int = 4
    traverse_inspect_max_total_chars: int = 2400
    traverse_scratchpad_chars: int = 200


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
    "MEMORY_ANN_SEARCH_BACKENDS",
    "MEMORY_BACKENDS",
    "MEMORY_DIRNAME",
    "MEMORY_EMBED_BACKENDS",
    "MEMORY_EMBED_DEVICES",
    "MEMORY_SEARCH_CHANNELS",
    "MEMORY_INLINE_MAX_CHARS",
    "MEMORY_JSONL_COMPACT_BYTES",
    "MEMORY_JSONL_COMPACT_DIRTY",
    "META_JSON",
    "SEMANTIC_WAIT_MAX_MS_DEFAULT",
    "SEMANTIC_WAIT_MAX_MS_MAX",
    "SEMANTIC_WAIT_MAX_MS_MIN",
    "TRAVERSE_EXPAND_MAX_MS_MAX",
    "TRAVERSE_FRONTIER_MAX_MAX",
    "TRAVERSE_INSPECT_CHARS_PER_ID_MAX",
    "TRAVERSE_INSPECT_MAX_IDS_MAX",
    "TRAVERSE_INSPECT_MAX_TOTAL_CHARS_MAX",
    "TRAVERSE_KEEP_MAX_MAX",
    "TRAVERSE_LABEL_CHARS_MAX",
    "TRAVERSE_MAX_DEPTH_MAX",
    "TRAVERSE_MAX_EXPAND_PER_STEP_MAX",
    "TRAVERSE_MAX_NODES_MAX",
    "TRAVERSE_MAX_SEEDS_MAX",
    "TRAVERSE_MAX_STEPS_MAX",
    "TRAVERSE_PARCEL_CHILD_CAP_MAX",
    "TRAVERSE_PREVIEW_CHARS_MAX",
    "TRAVERSE_SAME_MOMENT_K_MAX",
    "TRAVERSE_SCRATCHPAD_CHARS_MAX",
    "TRAVERSE_SEMANTIC_K_MAX",
    "TRAVERSE_SESSION_TTL_S_MAX",
    "MemorySettings",
    "atoms_blob_root",
    "atoms_jsonl_path",
    "blob_relpath_for_atom",
    "clamp_semantic_wait_max_ms",
    "ensure_memory_dirs",
    "is_directed_keep_enabled",
    "is_directed_traversal_enabled",
    "ladder_dir",
    "lance_root",
    "memory_meta_path",
    "memory_root",
]
