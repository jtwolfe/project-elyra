"""MemoryStore Protocol and factory.

Scope: swappable atom persistence interface + open_memory_store().
In scope: Protocol methods, jsonl default, lance guarded fall-back,
list_atoms + write-hook registration (Phase 2 PR2).
Out of scope: promote/meal/ladder, loop wiring.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

from elyra.config import ElyraPaths
from elyra.memory.config import MemorySettings
from elyra.memory.types import Atom, AtomKind, PeriodScale

_LOG = logging.getLogger(__name__)

# Glass / admin list hard cap (design: callers clamp limit ≤ 200).
LIST_ATOMS_MAX = 200

# Write hook: called after successful put_atom; must never raise to store callers.
AtomWriteHook = Callable[[Atom], None]


@runtime_checkable
class MemoryStore(Protocol):
    """Swappable atom persistence. Single-writer assumed (presence worker)."""

    def put_atom(self, atom: Atom, *, notify: bool = True) -> Atom:
        """Insert or replace by atom_id. Returns stored atom.

        ``notify`` (default True): when False, skip the write hook. Internal
        encode-status updates must use ``notify=False`` so they do not
        re-enqueue into the encode queue (see ``EncodeQueue._mark_atom_status``).
        """
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
        tips_only: bool = True,
    ) -> list[Atom]:
        """List period summaries for ``scale``.

        ``tips_only=True`` (default, KD-TIP): resolve via ladder index only —
        one tip per ``(scale, window_start)``. ``tips_only=False``: O(n) scan
        of summary atoms (version archaeology; no secondary index in #92).
        """
        ...

    def list_atoms(
        self,
        *,
        embedding_status: str | None = None,
        kinds: Sequence[AtomKind] | None = None,
        limit: int = 50,
        newest_first: bool = True,
    ) -> list[Atom]:
        """Glass/admin listing; filter by embedding_status / kinds.

        Hard-capped at LIST_ATOMS_MAX. Full-table scan is fine at dogfood scale.
        """
        ...

    def set_write_hook(self, hook: AtomWriteHook | None) -> None:
        """Register best-effort hook fired after successful put_atom (KD16)."""
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

    backend=lance requires optional ``lancedb`` (``elyra[memory-lance]``).

    Soft fall-back to jsonl + warning on:
    - ``ImportError`` (extra not installed)
    - construction / connect / open failures (OSError, RuntimeError, …)

    Native crashes (e.g. segfault in ``lancedb.connect`` on unsupported
    Python builds) are **fatal by design** — they cannot be caught in-process.
    Prefer a supported Python for the wheel, or set ``backend=jsonl``.
    Soft fall-back is logged loudly so operators notice dual-path risk
    (atoms may already exist under ``memory/lance/`` vs ``atoms.jsonl``).
    """
    cfg = settings or MemorySettings()
    backend = (cfg.backend or "jsonl").strip().lower()
    if backend == "lance":
        try:
            # Import lancedb first so missing extra is a clean ImportError
            # before constructing LanceMemoryStore.
            import lancedb  # noqa: F401, PLC0415
            from elyra.memory.lance_store import LanceMemoryStore  # noqa: PLC0415

            return LanceMemoryStore(paths, cfg)
        except ImportError:
            _LOG.warning(
                "memory backend=lance requested but lancedb not installed; "
                "using jsonl (pip install elyra[memory-lance])"
            )
        except Exception as exc:
            # Soft fall-back for non-segfault open failures (disk, schema, …).
            # Segfaults remain fatal (uncatchable). See docstring.
            _LOG.warning(
                "memory backend=lance open failed (%s: %s); using jsonl. "
                "Existing lance table data (if any) is not migrated.",
                type(exc).__name__,
                exc,
            )
    elif backend not in ("jsonl", "lance"):
        _LOG.warning("unknown memory backend %r; using jsonl", backend)

    from elyra.memory.jsonl_store import JsonlMemoryStore  # noqa: PLC0415

    return JsonlMemoryStore(paths, cfg)


__all__ = [
    "LIST_ATOMS_MAX",
    "AtomWriteHook",
    "MemoryStore",
    "open_memory_store",
]
