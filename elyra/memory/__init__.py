"""Durable episodic memory atoms (Stretch 2 Phase 1).

Public surface is intentionally narrow: types, errors, store Protocol/factory.
Promote / meal / ladder land in later PRs and are not re-exported here yet.
"""

from elyra.memory.config import MemorySettings, ensure_memory_dirs, memory_root
from elyra.memory.errors import MemoryStoreError, MemoryUnavailable
from elyra.memory.store import MemoryStore, open_memory_store
from elyra.memory.types import (
    ATOM_KINDS,
    PERIOD_SCALES,
    SCHEMA_VERSION,
    Atom,
    new_atom_id,
    stable_summary_id,
    validate_atom,
    window_bounds,
)

__all__ = [
    "ATOM_KINDS",
    "Atom",
    "MemorySettings",
    "MemoryStore",
    "MemoryStoreError",
    "MemoryUnavailable",
    "PERIOD_SCALES",
    "SCHEMA_VERSION",
    "ensure_memory_dirs",
    "memory_root",
    "new_atom_id",
    "open_memory_store",
    "stable_summary_id",
    "validate_atom",
    "window_bounds",
]
