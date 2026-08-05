"""Memory path roots and settings helpers (Phase 1 + Phase 2 knobs).

Scope: data/memory layout constants, MemorySettings defaults used by the store
and nested under ``Settings.memory`` (elyra.toml / CLI merge in settings.py).
In scope: path helpers, frozen MemorySettings, backend/embed allowlists.
Out of scope: promote/meal/ladder/embed worker wiring (later PRs).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from elyra.config import ElyraPaths
from elyra.memory.embed.types import (
    EMBED_BACKENDS,
    EMBED_DEVICE_PREFS,
    SEARCH_CHANNEL_SET,
)

# Long-path / snappy ANN sites (polish1 unified wait — design §1.1).
SemanticAnnSite = Literal["meal", "traverse", "recalls", "http"]

MEMORY_DIRNAME = "memory"
ATOMS_JSONL = "atoms.jsonl"
EDGES_JSONL = "edges.jsonl"
META_JSON = "meta.json"
ATOMS_BLOB_DIRNAME = "atoms"
LADDER_DIRNAME = "ladder"
LADDER_STATE = "state.json"
LANCE_DIRNAME = "lance"
# Write-time cap for meta.source_atom_ids (PR-C summary edge fabric).
LADDER_SOURCE_EDGE_K_DEFAULT = 24
LADDER_SOURCE_EDGE_K_MAX = 48

# Durable EdgeStore schema / budgets (design-memory-edges-and-traversal).
EDGE_SCHEMA_VERSION = 1
EDGE_MAX_PER_ATOM_DEFAULT = 150
EDGE_MAX_PER_ATOM_MAX = 256
EDGE_CREATED_WITH_MAX_DEFAULT = 100
EDGE_CREATED_WITH_MAX_MAX = 150
EDGE_CREATED_WITH_WRITE_CAP_DEFAULT = 32
EDGE_CREATED_WITH_WRITE_CAP_MAX = 100
EDGE_RECALLS_MAX_DEFAULT = 8
EDGE_RECALLS_MAX_MAX = 10
EDGE_RECALLS_ANN_K_DEFAULT = 15
EDGE_RECALLS_ANN_K_MAX = 32
EDGE_RECALLS_KEEP_DEFAULT = 5
EDGE_RECALLS_KEEP_MAX = 10
# Deprecated no-op for live ANN ceiling (polish1 KD-P0-deprec). Kept for toml
# compat / validation only — write_speak_recalls uses semantic wait helper.
EDGE_RECALLS_MAX_MS_DEFAULT = 40
EDGE_RECALLS_MAX_MS_MAX = 500
EDGE_RECALLS_SKIP_QUEUE_DEPTH_DEFAULT = 64
EDGE_RECALLS_SKIP_QUEUE_DEPTH_MAX = 4096
# Product default: deferred recalls on idle tick (KD-P0-defer). Inline is
# tests/emergency only (edge_recalls_inline=true).
EDGE_RECALLS_INLINE_DEFAULT = False
# Presence-worker deferred recalls queue (OQ-P7: 32 drop-new).
EDGE_RECALLS_DEFERRED_QUEUE_DEPTH_DEFAULT = 32
# Dev force edge backfill (polish1 KD-P-backfill). Factory ON for dogfood era;
# writes still require durable_edges_enabled (Gate B stays off).
EDGE_BACKFILL_MAX_ATOMS_DEFAULT = 2000
EDGE_BACKFILL_MAX_ATOMS_MAX = 10_000
EDGE_BACKFILL_MAX_MS_DEFAULT = 30_000
EDGE_BACKFILL_MAX_MS_MAX = 120_000

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

# Phase 2a / PR6 traverse hard maxes (settings validation; design §5.1 table).
# Product defaults live on MemorySettings; request clamp uses these as hi.
TRAVERSE_EXPAND_MAX_MS_MAX = 500
TRAVERSE_MAX_DEPTH_MAX = 8  # was 6
TRAVERSE_MAX_NODES_MAX = 160  # was 128
TRAVERSE_MAX_STEPS_MAX = 24  # was 16
TRAVERSE_MAX_SEEDS_MAX = 16
TRAVERSE_FRONTIER_MAX_MAX = 48  # was 32
TRAVERSE_MAX_EXPAND_PER_STEP_MAX = 10  # was 8
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
TRAVERSE_SAME_MOMENT_K_MAX = 24  # was 16
TRAVERSE_NEIGHBOR_K_MAX = 32  # step expand + GraphView default k hard max
# PR5 dual temporal anchors reserved before semantic fill (#105 seed half).
TRAVERSE_DUAL_START_N_MAX = 4
TRAVERSE_SEED_MODES = frozenset(
    {"auto", "semantic_only", "temporal_only", "temporal", "explicit_only"}
)


def clamp_semantic_wait_max_ms(value: float | int) -> int:
    """Clamp wait-for-select ceiling to the product [1000, 120000] ms band."""
    v = int(value)
    if v < SEMANTIC_WAIT_MAX_MS_MIN:
        return SEMANTIC_WAIT_MAX_MS_MIN
    if v > SEMANTIC_WAIT_MAX_MS_MAX:
        return SEMANTIC_WAIT_MAX_MS_MAX
    return v


def semantic_wait_enabled(
    settings: MemorySettings | None,
    *,
    runtime_state: Any | None = None,
) -> bool:
    """True when long-path semantic wait is on (runtime overlays settings).

    Prefer ``runtime_state.enabled`` when a process-level SemanticWaitState is
    available; otherwise read ``settings.semantic_wait_for_select`` (product
    default True).
    """
    if runtime_state is not None:
        return bool(getattr(runtime_state, "enabled", False))
    if settings is None:
        return True
    return bool(getattr(settings, "semantic_wait_for_select", True))


def effective_semantic_wait_max_ms(
    settings: MemorySettings | None,
    *,
    runtime_state: Any | None = None,
) -> int:
    """Clamped long-path ANN/embed ceiling (ms).

    Prefer ``runtime_state.max_ms`` when provided, else
    ``settings.semantic_wait_max_ms``, else product default. Always clamped to
    ``[SEMANTIC_WAIT_MAX_MS_MIN, SEMANTIC_WAIT_MAX_MS_MAX]``.

    Callers should use this as the ANN deadline **only when**
    :func:`semantic_wait_enabled` is True; when wait is off use
    :func:`snappy_ann_max_ms` for the site. Normative: no long-path call site
    hardcodes 40 / 120 / 250 as the ANN ceiling.
    """
    if runtime_state is not None:
        raw = getattr(runtime_state, "max_ms", SEMANTIC_WAIT_MAX_MS_DEFAULT)
    elif settings is not None:
        raw = getattr(settings, "semantic_wait_max_ms", SEMANTIC_WAIT_MAX_MS_DEFAULT)
    else:
        raw = SEMANTIC_WAIT_MAX_MS_DEFAULT
    try:
        return clamp_semantic_wait_max_ms(int(raw))
    except (TypeError, ValueError):
        return SEMANTIC_WAIT_MAX_MS_DEFAULT


def snappy_ann_max_ms(
    settings: MemorySettings | None,
    site: SemanticAnnSite,
) -> int:
    """Snappy ANN budget (ms) when wait is **disabled** — per-site table.

    | site     | budget |
    |----------|--------|
    | meal     | ``semantic_select_max_ms`` |
    | traverse | ``min(traverse_expand_max_ms, semantic_select_max_ms)`` |
    | recalls  | **0 = skip ANN** (product default; never inline snappy under promote) |
    | http     | ``min(traverse_expand_max_ms, semantic_select_max_ms)`` |

    Wait-off recalls soft-skip ANN entirely (design §1.1 / KD-P0-defer). PR1b
    deferred jobs use the wait helper when wait is on; wait-off stays skip.
    """
    select_ms = 50
    expand_ms = 120
    if settings is not None:
        try:
            select_ms = max(0, int(getattr(settings, "semantic_select_max_ms", 50) or 0))
        except (TypeError, ValueError):
            select_ms = 50
        try:
            expand_ms = max(0, int(getattr(settings, "traverse_expand_max_ms", 120) or 0))
        except (TypeError, ValueError):
            expand_ms = 120
    site_key = str(site or "").strip().lower()
    if site_key == "meal":
        return select_ms
    if site_key == "recalls":
        # Product default: skip ANN when wait is off (not min(select, 100)).
        return 0
    # traverse + http (and unknown → traverse/http snappy)
    return min(expand_ms, select_ms)


def semantic_ann_deadline_ms(
    settings: MemorySettings | None,
    site: SemanticAnnSite,
    *,
    runtime_state: Any | None = None,
) -> int:
    """ANN deadline for a call site: wait ceiling when on, else snappy table.

    Convenience over :func:`semantic_wait_enabled` +
    :func:`effective_semantic_wait_max_ms` / :func:`snappy_ann_max_ms`.
    """
    if semantic_wait_enabled(settings, runtime_state=runtime_state):
        return effective_semantic_wait_max_ms(settings, runtime_state=runtime_state)
    return snappy_ann_max_ms(settings, site)


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


def is_durable_edges_enabled(settings: MemorySettings | None) -> bool:
    """True when promote / encode may write durable EdgeStore rows.

    Default false (Gate B non-goal). EdgeStore itself can still open and
    serve put/list for tests and admin when the flag is off.
    """
    if settings is None:
        return False
    return bool(getattr(settings, "durable_edges_enabled", False))


def is_edge_backfill_dev_enabled(settings: MemorySettings | None) -> bool:
    """True when Graph dev force-edge-backfill button/API may run.

    Factory default **on** for dogfood era (KD-P-backfill). Write path still
    requires ``durable_edges_enabled``; this flag only gates the dev surface.
    """
    if settings is None:
        return False
    return bool(getattr(settings, "edge_backfill_dev_enabled", True))


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
    episodic_fraction: float = 0.24
    episodic_horizon_hours: float = 24.0
    ladder_enabled: bool = True  # runs if write_atoms or enabled
    ladder_max_ms_per_tick: int = 200  # nibble / repair (template)
    # --- Episodic ladder LLM + hourly schedule (#92 PR-A) ---
    summary_mode: str = "template"  # template | llm (CI default hermetic)
    ladder_write_legacy_scales: bool = False  # reject new 15m/6h writes
    # When True, soft age/tip gates unlock 1w/1m/1y gradually. Default False:
    # all write scales (1h→1y) are always allowed for cascade + status.
    ladder_age_gates_enabled: bool = False
    ladder_hourly_max_ms: int = 12000  # hourly + cascade wall-clock
    ladder_catchup_max_hours: int = 24  # closed 1h per hourly tick
    ladder_llm_max_calls_per_tick: int = 3
    ladder_llm_max_calls_per_hour: int = 40
    ladder_skip_empty: bool = True  # skip put when window has no sources
    ladder_recent_1h_meal: int = 6  # meal band (PR-D consumes)
    # Operator rebuild (Context button): closed hours to force-refresh + cascade.
    ladder_rebuild_max_hours: int = 48
    ladder_rebuild_max_ms: int = 120_000
    ladder_rebuild_max_llm_calls: int = 80
    # Write cap for source edges (PR-C); settings reject outside [0, MAX].
    ladder_source_edge_k: int = LADDER_SOURCE_EDGE_K_DEFAULT
    # GraphView summary fabric expand depth (PR-C). lite = default; deep stub #103.
    traverse_summary_expand: str = "lite"  # lite | deep
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
    # Continuous encode worker (KD-E1 / KD-E6). false → owner=idle rollback only.
    encode_worker_enabled: bool = True
    encode_worker_poll_s: float = 0.35  # Event wait timeout between ticks
    encode_worker_max_restarts: int = 3  # per-window thrash budget (not permanent give-up)
    encode_worker_restart_window_s: float = 60.0
    encode_worker_restart_backoff_max_s: float = 30.0
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
    episodic_fraction_with_semantic: float = 0.22
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
    # KD-V8 conservative outer rebalance: tip thickened without cutting meal
    # fraction 0.5 or temporal_min_fraction 0.55.
    glass_tail_fraction: float = 0.10  # soft % of residual R (5–12% band)
    glass_tail_floor_messages: int = 6  # social wakes only; ≥2 full turns
    glass_tail_max_messages: int = 20  # hard cap — prevent unbounded dump
    glass_tail_list_limit: int = 80  # align with rebuild_outer list_messages

    # Per-step expand compute (NOT multi-hop session wall-clock — KD-A18).
    # PR6 raised product defaults (§5.1): expand 120ms; same_moment 8; semantic 10.
    traverse_expand_max_ms: int = 120  # soft wall for neighbors / seed_from_text
    # Start seed_from_query budget (PR5 / #103); 0 = same as traverse_expand_max_ms.
    traverse_start_expand_max_ms: int = 250
    traverse_parcel_child_cap: int = 32  # parent_of reverse chain / moment cap
    traverse_same_moment_k: int = 8  # OQ-A4 same_moment soft edge cap
    traverse_semantic_k: int = 10  # semantic_hop / seed_from_text top-k
    traverse_neighbor_k: int = 16  # step expand + GraphView neighbors top-k
    traverse_allow_semantic_hops: bool = True  # no-ops without index / cold encoder
    traverse_temporal_half_life_hours: float = 72.0  # weight model half-life
    traverse_min_expand_weight: float = 0.05  # drop edges below this floor

    # Session budgets (hard maxes enforced in settings validation + request clamp).
    # PR6 raised product defaults (design §5.1).
    traverse_max_depth: int = 5
    traverse_max_nodes: int = 80
    traverse_max_steps: int = 12
    # PR5: dual reserve + semantic top (#105 seed half); hard max still 16.
    traverse_max_seeds: int = 10
    traverse_frontier_max: int = 24
    traverse_max_expand_per_step: int = 5
    traverse_keep_max: int = 20
    traverse_keep_adjacent: bool = True  # finish: sequential ±1 if slots remain
    traverse_session_ttl_s: int = 900  # idle TTL for active only (KD-A18)

    # Pure semantic start + dual temporal anchors (PR5 / #103 / #105 seed).
    # dual_start reserves N temporal slots BEFORE semantic fill so anchors
    # are not starved when ANN returns a full semantic top-k.
    traverse_dual_start: bool = True
    traverse_dual_start_n: int = 2
    # auto | semantic_only | temporal_only | explicit_only ("temporal" alias).
    traverse_default_seed_mode: str = "auto"

    # Thin surface / inspect caps (KD-A17).
    traverse_label_chars: int = 80
    traverse_preview_chars: int = 400
    traverse_inspect_chars_per_id: int = 800
    traverse_inspect_max_ids: int = 4
    traverse_inspect_max_total_chars: int = 2400
    traverse_scratchpad_chars: int = 200
    # Host-assembled ~d2.5 local map on start/step focus (polish1 KD-P2).
    # Off → frontier-only thin surface (rollback).
    traverse_local_map_enabled: bool = True

    # --- Durable EdgeStore (default OFF writes — KD-E / edges design) ---
    # Store open is independent of this flag; promote write path gates on it.
    durable_edges_enabled: bool = False
    edge_max_per_atom: int = EDGE_MAX_PER_ATOM_DEFAULT
    edge_created_with_max: int = EDGE_CREATED_WITH_MAX_DEFAULT
    edge_created_with_write_cap: int = EDGE_CREATED_WITH_WRITE_CAP_DEFAULT
    edge_recalls_max: int = EDGE_RECALLS_MAX_DEFAULT
    edge_recalls_ann_k: int = EDGE_RECALLS_ANN_K_DEFAULT
    edge_recalls_keep: int = EDGE_RECALLS_KEEP_DEFAULT
    # Deprecated: ignored as live ANN ceiling (use semantic wait helper).
    edge_recalls_max_ms: int = EDGE_RECALLS_MAX_MS_DEFAULT
    edge_recalls_skip_queue_depth: int = EDGE_RECALLS_SKIP_QUEUE_DEPTH_DEFAULT
    # When True, promote runs write_speak_recalls inline (tests / emergency).
    # Product default False: enqueue deferred job on presence worker.
    edge_recalls_inline: bool = EDGE_RECALLS_INLINE_DEFAULT
    edge_retarget_enabled: bool = True
    edge_retarget_ensure_vertical: bool = True
    # When True, default GraphView expand includes has_channel kind filter
    # (virtual channel destinations are still never walkable — Option A).
    traverse_expand_channels: bool = False
    # Dev Graph force edge backfill (polish1 KD-P-backfill). ON for dogfood;
    # UI hides when false. Writes still require durable_edges_enabled.
    edge_backfill_dev_enabled: bool = True
    edge_backfill_max_atoms: int = EDGE_BACKFILL_MAX_ATOMS_DEFAULT
    edge_backfill_max_ms: int = EDGE_BACKFILL_MAX_MS_DEFAULT


def memory_root(paths: ElyraPaths) -> Path:
    """Return ``{data_dir}/memory``."""
    return paths.data_dir / MEMORY_DIRNAME


def atoms_jsonl_path(paths: ElyraPaths) -> Path:
    return memory_root(paths) / ATOMS_JSONL


def edges_jsonl_path(paths: ElyraPaths) -> Path:
    """Return ``{data_dir}/memory/edges.jsonl`` (JSONL EdgeStore)."""
    return memory_root(paths) / EDGES_JSONL


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
    "EDGE_BACKFILL_MAX_ATOMS_DEFAULT",
    "EDGE_BACKFILL_MAX_ATOMS_MAX",
    "EDGE_BACKFILL_MAX_MS_DEFAULT",
    "EDGE_BACKFILL_MAX_MS_MAX",
    "EDGE_CREATED_WITH_MAX_DEFAULT",
    "EDGE_CREATED_WITH_MAX_MAX",
    "EDGE_CREATED_WITH_WRITE_CAP_DEFAULT",
    "EDGE_CREATED_WITH_WRITE_CAP_MAX",
    "EDGE_MAX_PER_ATOM_DEFAULT",
    "EDGE_MAX_PER_ATOM_MAX",
    "EDGE_RECALLS_ANN_K_DEFAULT",
    "EDGE_RECALLS_ANN_K_MAX",
    "EDGE_RECALLS_KEEP_DEFAULT",
    "EDGE_RECALLS_KEEP_MAX",
    "EDGE_RECALLS_MAX_DEFAULT",
    "EDGE_RECALLS_MAX_MAX",
    "EDGE_RECALLS_DEFERRED_QUEUE_DEPTH_DEFAULT",
    "EDGE_RECALLS_INLINE_DEFAULT",
    "EDGE_RECALLS_MAX_MS_DEFAULT",
    "EDGE_RECALLS_MAX_MS_MAX",
    "EDGE_RECALLS_SKIP_QUEUE_DEPTH_DEFAULT",
    "EDGE_RECALLS_SKIP_QUEUE_DEPTH_MAX",
    "EDGE_SCHEMA_VERSION",
    "EDGES_JSONL",
    "LADDER_DIRNAME",
    "LADDER_SOURCE_EDGE_K_DEFAULT",
    "LADDER_SOURCE_EDGE_K_MAX",
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
    "TRAVERSE_DUAL_START_N_MAX",
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
    "TRAVERSE_NEIGHBOR_K_MAX",
    "TRAVERSE_PARCEL_CHILD_CAP_MAX",
    "TRAVERSE_PREVIEW_CHARS_MAX",
    "TRAVERSE_SAME_MOMENT_K_MAX",
    "TRAVERSE_SCRATCHPAD_CHARS_MAX",
    "TRAVERSE_SEED_MODES",
    "TRAVERSE_SEMANTIC_K_MAX",
    "TRAVERSE_SESSION_TTL_S_MAX",
    "MemorySettings",
    "SemanticAnnSite",
    "atoms_blob_root",
    "atoms_jsonl_path",
    "blob_relpath_for_atom",
    "clamp_semantic_wait_max_ms",
    "edges_jsonl_path",
    "effective_semantic_wait_max_ms",
    "ensure_memory_dirs",
    "is_directed_keep_enabled",
    "is_directed_traversal_enabled",
    "is_durable_edges_enabled",
    "is_edge_backfill_dev_enabled",
    "ladder_dir",
    "lance_root",
    "memory_meta_path",
    "memory_root",
    "semantic_ann_deadline_ms",
    "semantic_wait_enabled",
    "snappy_ann_max_ms",
]
