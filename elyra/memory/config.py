"""Memory path roots and Phase 1 settings helpers.

Scope: data/memory layout constants, MemorySettings defaults used by the store.
In scope: path helpers, frozen MemorySettings (not yet wired into Settings).
Out of scope: elyra.toml / Settings.memory merge (later PR), promote flags in loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from elyra.config import ElyraPaths

MEMORY_DIRNAME = "memory"
ATOMS_JSONL = "atoms.jsonl"
META_JSON = "meta.json"
ATOMS_BLOB_DIRNAME = "atoms"
LADDER_DIRNAME = "ladder"
LADDER_STATE = "state.json"

# Inline body threshold for JSONL rows (spill to blob when longer).
MEMORY_INLINE_MAX_CHARS = 8000
# Compaction triggers (idle path only; never mid-hop).
MEMORY_JSONL_COMPACT_BYTES = 8 * 1024 * 1024
MEMORY_JSONL_COMPACT_DIRTY = 256

_MEMORY_BACKENDS = frozenset({"jsonl", "lance"})


@dataclass(frozen=True)
class MemorySettings:
    """Phase 1 memory knobs (store/factory defaults; Settings wiring is later)."""

    enabled: bool = False  # meal path
    write_atoms: bool = False  # promote path
    backend: str = "jsonl"  # jsonl | lance
    episodic_fraction: float = 0.20
    episodic_horizon_hours: float = 24.0
    ladder_enabled: bool = True
    ladder_max_ms_per_tick: int = 50
    regather_every_n_hops: int = 0  # 0 = off
    atom_max_chars: int = 8000
    compact_max_tokens: int = 400
    link_across_moments: bool = True
    model_promote_min_chars: int = 40
    protect_tail_atoms: int = 12
    tool_ok_preview_chars: int = 240
    max_tool_atoms_per_moment: int = 48
    # Store-only knobs (not all on Settings surface yet).
    inline_max_chars: int = MEMORY_INLINE_MAX_CHARS
    jsonl_compact_bytes: int = MEMORY_JSONL_COMPACT_BYTES
    jsonl_compact_dirty: int = MEMORY_JSONL_COMPACT_DIRTY


def memory_root(paths: ElyraPaths) -> Path:
    """Return ``{data_dir}/memory``."""
    return paths.data_dir / MEMORY_DIRNAME


def atoms_jsonl_path(paths: ElyraPaths) -> Path:
    return memory_root(paths) / ATOMS_JSONL


def memory_meta_path(paths: ElyraPaths) -> Path:
    return memory_root(paths) / META_JSON


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
    "MEMORY_DIRNAME",
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
    "memory_meta_path",
    "memory_root",
]
