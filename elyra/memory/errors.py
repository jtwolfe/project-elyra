"""Memory package error types.

Scope: structured errors for store open/IO and unavailable backends.
In scope: MemoryStoreError hierarchy used by elyra.memory.*.
Out of scope: promote/meal policy failures (logged, not raised into the loop).
"""

from __future__ import annotations


class MemoryStoreError(Exception):
    """Base for memory store failures."""


class MemoryUnavailable(MemoryStoreError):
    """Store cannot be opened or is closed / unusable."""


class MemoryAtomNotFound(MemoryStoreError, KeyError):
    """Requested atom_id is not present."""


__all__ = [
    "MemoryAtomNotFound",
    "MemoryStoreError",
    "MemoryUnavailable",
]
