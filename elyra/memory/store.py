"""MemoryStore Protocol and factory.

Scope: swappable atom persistence interface + open_memory_store().
In scope: Protocol methods, jsonl default, lance guarded fall-back.
Out of scope: promote/meal/ladder, loop wiring.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol, Sequence, runtime_checkable

from elyra.config import ElyraPaths
from elyra.memory.config import MemorySettings
from elyra.memory.types import Atom, AtomKind, PeriodScale

_LOG = logging.getLogger(__name__)


@runtime_checkable
class MemoryStore(Protocol):
    """Swappable atom persistence. Single-writer assumed (presence worker)."""

    def put_atom(self, atom: Atom) -> Atom:
        """Insert or replace by atom_id. Returns stored atom."""
        ...

    def get_atom(self, atom_id: str) -> Atom | None:
        ...

    def update_links(
        self,
        atom_id: str,
        *,
        prev_atom_id: str | None = ...,
        next_atom_id: str | None = ...,
    ) -> Atom:
        """Patch sequential links only."""
        ...

    def list_by_moment(
        self,
        moment_id: str,
        *,
        kinds: Sequence[AtomKind] | None = None,
        limit: int | None = None,
    ) -> list[Atom]:
        """Atoms in moment order (t_start asc, then atom_id)."""
        ...

    def list_range(
        self,
        t_start: datetime | str,
        t_end: datetime | str,
        *,
        kinds: Sequence[AtomKind] | None = None,
        exclude_moment_id: str | None = None,
        limit: int = 200,
    ) -> list[Atom]:
        """Half-open [t_start, t_end) by t_start; oldest first."""
        ...

    def list_summaries(
        self,
        scale: PeriodScale,
        *,
        overlapping: tuple[datetime | str, datetime | str] | None = None,
        limit: int = 50,
    ) -> list[Atom]:
        ...

    def moment_tail(self, moment_id: str) -> Atom | None:
        """Latest atom in moment by time/chain."""
        ...

    def global_tail(self) -> Atom | None:
        ...

    def walk_next(self, atom_id: str, *, n: int = 20) -> list[Atom]:
        """Follow next_atom_id up to n steps (including start)."""
        ...

    def walk_prev(self, atom_id: str, *, n: int = 20) -> list[Atom]:
        ...

    def delete_atom(self, atom_id: str) -> bool:
        """Phase 1: optional; meal must not call this. Admin/tests only."""
        ...

    def health(self) -> dict[str, Any]:
        """{ok, backend, atom_count?, error?} — for glass/status."""
        ...

    def close(self) -> None:
        ...


def open_memory_store(
    paths: ElyraPaths,
    settings: MemorySettings | None = None,
) -> MemoryStore:
    """Factory. backend=jsonl always available.

    backend=lance requires optional dependency; falls back to jsonl + log if
    missing (no lance implementation in Phase 1 PR1 — always jsonl for now).
    """
    cfg = settings or MemorySettings()
    backend = (cfg.backend or "jsonl").strip().lower()
    if backend == "lance":
        # Phase 1 optional path — not implemented yet; hermetic fall-back.
        _LOG.warning(
            "memory backend=lance requested but not available; using jsonl"
        )
    elif backend not in ("jsonl", "lance"):
        _LOG.warning("unknown memory backend %r; using jsonl", backend)

    from elyra.memory.jsonl_store import JsonlMemoryStore

    return JsonlMemoryStore(paths, cfg)


__all__ = [
    "MemoryStore",
    "open_memory_store",
]
