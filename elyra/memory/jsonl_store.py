"""Hermetic JSONL MemoryStore backend (default / CI).

Scope: append-only atoms.jsonl with in-memory indexes, blob spill, idle compact.
In scope: put/get/range/moment/links/walk/health, schema v1 meta.json.
Out of scope: promote, meal, ladder refresh, Lance backend.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from elyra.config import ElyraPaths
from elyra.memory.config import (
    MemorySettings,
    atoms_jsonl_path,
    blob_relpath_for_atom,
    ensure_memory_dirs,
    memory_meta_path,
    memory_root,
)
from elyra.memory.errors import MemoryAtomNotFound, MemoryUnavailable
from elyra.memory.types import (
    SCHEMA_VERSION,
    Atom,
    AtomKind,
    PeriodScale,
    atom_from_dict,
    atom_replace,
    atom_to_dict,
    to_iso_z,
    utc_now_iso,
    validate_atom,
)

_LOG = logging.getLogger(__name__)

_UNSET: Any = object()

# Kinds that are not part of the sequential experience weave (R7 tails).
_CHAIN_EXCLUDE_KINDS: frozenset[str] = frozenset(
    {"summary", "parcel", "moment_meta"}
)


class JsonlMemoryStore:
    """Single-writer JSONL atom store under ``data/memory/``.

    Thread safety: one ``threading.RLock`` per instance (mirror GoalsStore).
    Compaction is explicit (``compact`` / ``maybe_compact``) — never mid-hop.
    """

    def __init__(
        self,
        paths: ElyraPaths,
        settings: MemorySettings | None = None,
    ) -> None:
        self._paths = paths
        self._settings = settings or MemorySettings()
        self._lock = threading.RLock()
        self._by_id: dict[str, Atom] = {}
        self._by_moment: dict[str, list[str]] = {}
        # (scale, window_start) -> atom_id for kind=summary ladder index
        self._ladder: dict[tuple[str, str], str] = {}
        self._line_count: int = 0
        self._closed: bool = False
        self._corrupt_lines: int = 0
        self._ensure_layout()
        self._load()

    # ── paths ────────────────────────────────────────────────────────────

    @property
    def memory_dir(self) -> Path:
        return memory_root(self._paths)

    @property
    def atoms_path(self) -> Path:
        return atoms_jsonl_path(self._paths)

    @property
    def meta_path(self) -> Path:
        return memory_meta_path(self._paths)

    @property
    def settings(self) -> MemorySettings:
        return self._settings

    # ── lifecycle ────────────────────────────────────────────────────────

    def _ensure_layout(self) -> None:
        ensure_memory_dirs(self._paths)
        if not self.meta_path.is_file():
            meta = {
                "schema_version": SCHEMA_VERSION,
                "backend": "jsonl",
                "created_at": utc_now_iso(),
            }
            self._write_meta(meta)
        else:
            # Heal missing keys without clobbering unknown future fields.
            try:
                existing = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            changed = False
            if "schema_version" not in existing:
                existing["schema_version"] = SCHEMA_VERSION
                changed = True
            if "backend" not in existing:
                existing["backend"] = "jsonl"
                changed = True
            if "created_at" not in existing:
                existing["created_at"] = utc_now_iso()
                changed = True
            if changed:
                self._write_meta(existing)

    def _write_meta(self, meta: dict[str, Any]) -> None:
        path = self.meta_path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _load(self) -> None:
        """Rebuild in-memory indexes from atoms.jsonl (latest-wins by atom_id)."""
        path = self.atoms_path
        self._by_id.clear()
        self._by_moment.clear()
        self._ladder.clear()
        self._line_count = 0
        self._corrupt_lines = 0
        if not path.is_file():
            return
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                self._line_count += 1
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    self._corrupt_lines += 1
                    continue
                if not isinstance(row, dict):
                    self._corrupt_lines += 1
                    continue
                if row.get("_deleted") is True:
                    atom_id = row.get("atom_id")
                    if isinstance(atom_id, str) and atom_id:
                        self._by_id.pop(atom_id, None)
                    continue
                try:
                    atom = atom_from_dict(row)
                    atom = self._hydrate_content(atom)
                except (TypeError, ValueError):
                    self._corrupt_lines += 1
                    continue
                self._by_id[atom.atom_id] = atom
        self._rebuild_secondary_indexes()

    def _rebuild_secondary_indexes(self) -> None:
        self._by_moment.clear()
        self._ladder.clear()
        for atom in self._by_id.values():
            if atom.moment_id:
                self._by_moment.setdefault(atom.moment_id, []).append(atom.atom_id)
            if atom.kind == "summary" and atom.scale and atom.window_start:
                self._ladder[(atom.scale, atom.window_start)] = atom.atom_id
        for mid, ids in self._by_moment.items():
            ids.sort(key=lambda i: (self._by_id[i].t_start, i))

    def _hydrate_content(self, atom: Atom) -> Atom:
        """Load content_text from blob when content_ref is a blob locator."""
        ref = atom.content_ref or "inline"
        if not ref.startswith("blob:"):
            return atom
        rel = ref[5:]
        if not rel or ".." in Path(rel).parts:
            return atom
        path = (self.memory_dir / rel).resolve()
        root = self.memory_dir.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return atom
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return atom
        if text == atom.content_text:
            return atom
        return atom_replace(atom, content_text=text)

    def _check_open(self) -> None:
        if self._closed:
            raise MemoryUnavailable("memory store is closed")

    # ── content spill ────────────────────────────────────────────────────

    def _unlink_blob_if_any(self, content_ref: str | None) -> None:
        """Best-effort remove a spilled blob file (orphans on shrink/delete)."""
        if not content_ref or not content_ref.startswith("blob:"):
            return
        rel = content_ref[5:]
        if not rel or ".." in Path(rel).parts:
            return
        path = (self.memory_dir / rel).resolve()
        root = self.memory_dir.resolve()
        if not path.is_relative_to(root):
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _prepare_for_put(self, atom: Atom) -> Atom:
        """Cap content_text, derive content_ref from body length, validate.

        Locator is always re-derived from current body length (never trust a
        stale ``blob:`` ref when the body has shrunk under inline_max).
        Timestamps are normalized to UTC ``Z`` for consistent range compares.
        """
        # Normalize timestamps so Z / +00:00 never diverge in indexes.
        ts_changes: dict[str, Any] = {}
        try:
            ts_changes["t_start"] = to_iso_z(atom.t_start)
        except (TypeError, ValueError):
            pass
        if atom.t_end:
            try:
                ts_changes["t_end"] = to_iso_z(atom.t_end)
            except (TypeError, ValueError):
                pass
        if atom.window_start:
            try:
                ts_changes["window_start"] = to_iso_z(atom.window_start)
            except (TypeError, ValueError):
                pass
        if atom.window_end:
            try:
                ts_changes["window_end"] = to_iso_z(atom.window_end)
            except (TypeError, ValueError):
                pass
        if atom.source_beat_ts:
            try:
                ts_changes["source_beat_ts"] = to_iso_z(atom.source_beat_ts)
            except (TypeError, ValueError):
                pass
        if ts_changes:
            atom = atom_replace(atom, **ts_changes)

        max_chars = int(self._settings.atom_max_chars)
        text = atom.content_text if atom.content_text is not None else ""
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars]
            meta = dict(atom.meta)
            meta["truncated"] = True
            atom = atom_replace(atom, content_text=text, meta=meta)

        inline_max = int(self._settings.inline_max_chars)
        prev_ref = atom.content_ref or "inline"
        if inline_max > 0 and len(atom.content_text) > inline_max:
            rel = blob_relpath_for_atom(atom.atom_id)
            blob_path = self.memory_dir / rel
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            blob_path.write_text(atom.content_text, encoding="utf-8")
            atom = atom_replace(atom, content_ref=f"blob:{rel}")
        else:
            # Force inline when body fits — do not keep a stale blob locator.
            if prev_ref.startswith("blob:"):
                self._unlink_blob_if_any(prev_ref)
            atom = atom_replace(atom, content_ref="inline")

        if atom.embedding_status is None or atom.embedding_status == "":
            atom = atom_replace(atom, embedding_status="none")
        if not atom.schema_version:
            atom = atom_replace(atom, schema_version=SCHEMA_VERSION)

        return validate_atom(atom)

    def _row_for_disk(self, atom: Atom) -> dict[str, Any]:
        row = atom_to_dict(atom)
        # Keep JSONL lines small when body lives in a blob (KD18 locator).
        if str(atom.content_ref).startswith("blob:"):
            row["content_text"] = ""
        return row

    def _append_row(self, row: dict[str, Any]) -> None:
        path = self.atoms_path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_trailing_newline(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._line_count += 1

    def _ensure_trailing_newline(self, path: Path) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            return
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            last = handle.read(1)
        if last != b"\n":
            with path.open("ab") as handle:
                handle.write(b"\n")

    def _index_put(self, atom: Atom) -> None:
        old = self._by_id.get(atom.atom_id)
        if old is not None:
            if old.moment_id and old.moment_id in self._by_moment:
                ids = self._by_moment[old.moment_id]
                try:
                    ids.remove(atom.atom_id)
                except ValueError:
                    pass
                if not ids:
                    del self._by_moment[old.moment_id]
            if old.kind == "summary" and old.scale and old.window_start:
                key = (old.scale, old.window_start)
                if self._ladder.get(key) == atom.atom_id:
                    del self._ladder[key]

        self._by_id[atom.atom_id] = atom
        if atom.moment_id:
            ids = self._by_moment.setdefault(atom.moment_id, [])
            if atom.atom_id not in ids:
                ids.append(atom.atom_id)
            ids.sort(key=lambda i: (self._by_id[i].t_start, i))
        if atom.kind == "summary" and atom.scale and atom.window_start:
            self._ladder[(atom.scale, atom.window_start)] = atom.atom_id

    # ── Protocol methods ─────────────────────────────────────────────────

    def put_atom(self, atom: Atom) -> Atom:
        """Insert or replace by atom_id. Returns stored atom (full content_text)."""
        with self._lock:
            self._check_open()
            prepared = self._prepare_for_put(atom)
            self._append_row(self._row_for_disk(prepared))
            self._index_put(prepared)
            return prepared

    def get_atom(self, atom_id: str) -> Atom | None:
        with self._lock:
            self._check_open()
            return self._by_id.get(atom_id)

    def update_links(
        self,
        atom_id: str,
        *,
        prev_atom_id: str | None | object = _UNSET,
        next_atom_id: str | None | object = _UNSET,
    ) -> Atom:
        """Patch sequential links only; appends an update line."""
        with self._lock:
            self._check_open()
            existing = self._by_id.get(atom_id)
            if existing is None:
                raise MemoryAtomNotFound(atom_id)
            changes: dict[str, Any] = {}
            if prev_atom_id is not _UNSET:
                changes["prev_atom_id"] = prev_atom_id
            if next_atom_id is not _UNSET:
                changes["next_atom_id"] = next_atom_id
            if not changes:
                return existing
            updated = atom_replace(existing, **changes)
            self._append_row(self._row_for_disk(updated))
            self._index_put(updated)
            return updated

    def list_by_moment(
        self,
        moment_id: str,
        *,
        kinds: Sequence[AtomKind | str] | None = None,
        limit: int | None = None,
    ) -> list[Atom]:
        """Atoms in moment order (t_start asc, then atom_id)."""
        with self._lock:
            self._check_open()
            ids = list(self._by_moment.get(moment_id, ()))
            kind_set = set(kinds) if kinds is not None else None
            out: list[Atom] = []
            for aid in ids:
                atom = self._by_id.get(aid)
                if atom is None:
                    continue
                if kind_set is not None and atom.kind not in kind_set:
                    continue
                out.append(atom)
            if limit is not None:
                out = out[: max(0, int(limit))]
            return out

    def list_range(
        self,
        t_start: datetime | str,
        t_end: datetime | str,
        *,
        kinds: Sequence[AtomKind | str] | None = None,
        exclude_moment_id: str | None = None,
        limit: int = 200,
    ) -> list[Atom]:
        """Half-open [t_start, t_end) by atom.t_start; oldest first."""
        with self._lock:
            self._check_open()
            start = to_iso_z(t_start)
            end = to_iso_z(t_end)
            kind_set = set(kinds) if kinds is not None else None
            rows = sorted(
                self._by_id.values(),
                key=lambda a: (to_iso_z(a.t_start), a.atom_id),
            )
            out: list[Atom] = []
            for atom in rows:
                # Normalize atom times so mixed Z/+00:00 rows compare correctly.
                at = to_iso_z(atom.t_start)
                if at < start or at >= end:
                    continue
                if exclude_moment_id and atom.moment_id == exclude_moment_id:
                    continue
                if kind_set is not None and atom.kind not in kind_set:
                    continue
                out.append(atom)
                if limit is not None and len(out) >= max(0, int(limit)):
                    break
            return out

    def list_summaries(
        self,
        scale: PeriodScale | str,
        *,
        overlapping: tuple[datetime | str, datetime | str] | None = None,
        limit: int = 50,
    ) -> list[Atom]:
        with self._lock:
            self._check_open()
            out: list[Atom] = []
            for (sc, _ws), aid in self._ladder.items():
                if sc != scale:
                    continue
                atom = self._by_id.get(aid)
                if atom is None:
                    continue
                if overlapping is not None:
                    o_start = to_iso_z(overlapping[0])
                    o_end = to_iso_z(overlapping[1])
                    # Overlap if window_start < o_end and window_end > o_start
                    ws = to_iso_z(atom.window_start) if atom.window_start else ""
                    we = to_iso_z(atom.window_end) if atom.window_end else ""
                    if not (ws < o_end and we > o_start):
                        continue
                out.append(atom)
            out.sort(
                key=lambda a: (
                    to_iso_z(a.window_start) if a.window_start else "",
                    a.atom_id,
                )
            )
            return out[: max(0, int(limit))]

    def moment_tail(self, moment_id: str) -> Atom | None:
        """Latest sequential atom in moment by (t_start, atom_id).

        Excludes summary/parcel/moment_meta so ladder rows do not become
        chain tips for R7 sequential linking.
        """
        with self._lock:
            self._check_open()
            ids = self._by_moment.get(moment_id) or []
            if not ids:
                return None
            chain = [
                self._by_id[i]
                for i in ids
                if i in self._by_id
                and self._by_id[i].kind not in _CHAIN_EXCLUDE_KINDS
            ]
            if not chain:
                return None
            # Prefer chain tip: next_atom_id is None among sequential atoms.
            tails = [a for a in chain if a.next_atom_id is None]
            pool = tails if tails else chain
            return max(pool, key=lambda a: (to_iso_z(a.t_start), a.atom_id))

    def global_tail(self) -> Atom | None:
        """Latest sequential-weave tip (excludes summary/parcel/moment_meta).

        R7 sequential linking must not attach to ladder summary atoms.
        """
        with self._lock:
            self._check_open()
            if not self._by_id:
                return None
            chain = [
                a
                for a in self._by_id.values()
                if a.kind not in _CHAIN_EXCLUDE_KINDS
            ]
            if not chain:
                return None
            tails = [a for a in chain if a.next_atom_id is None]
            pool = tails if tails else chain
            return max(pool, key=lambda a: (to_iso_z(a.t_start), a.atom_id))

    def walk_next(self, atom_id: str, *, n: int = 20) -> list[Atom]:
        """Follow next_atom_id up to n steps (including start)."""
        with self._lock:
            self._check_open()
            return self._walk(atom_id, direction="next", n=n)

    def walk_prev(self, atom_id: str, *, n: int = 20) -> list[Atom]:
        """Follow prev_atom_id up to n steps (including start)."""
        with self._lock:
            self._check_open()
            return self._walk(atom_id, direction="prev", n=n)

    def _walk(self, atom_id: str, *, direction: str, n: int) -> list[Atom]:
        n = max(0, int(n))
        if n == 0:
            return []
        start = self._by_id.get(atom_id)
        if start is None:
            return []
        out: list[Atom] = [start]
        seen = {atom_id}
        cur = start
        while len(out) < n:
            nxt_id = cur.next_atom_id if direction == "next" else cur.prev_atom_id
            if not nxt_id or nxt_id in seen:
                break
            nxt = self._by_id.get(nxt_id)
            if nxt is None:
                break
            out.append(nxt)
            seen.add(nxt_id)
            cur = nxt
        return out

    def delete_atom(self, atom_id: str) -> bool:
        """Remove atom (admin/tests). Appends a tombstone line; unlinks blob."""
        with self._lock:
            self._check_open()
            if atom_id not in self._by_id:
                return False
            old = self._by_id.pop(atom_id)
            if old.moment_id and old.moment_id in self._by_moment:
                ids = self._by_moment[old.moment_id]
                try:
                    ids.remove(atom_id)
                except ValueError:
                    pass
                if not ids:
                    del self._by_moment[old.moment_id]
            if old.kind == "summary" and old.scale and old.window_start:
                key = (old.scale, old.window_start)
                if self._ladder.get(key) == atom_id:
                    del self._ladder[key]
            self._unlink_blob_if_any(old.content_ref)
            self._append_row({"atom_id": atom_id, "_deleted": True})
            return True

    def health(self) -> dict[str, Any]:
        """``{ok, backend, atom_count, line_count, corrupt_lines?, error?}``."""
        with self._lock:
            if self._closed:
                return {
                    "ok": False,
                    "backend": "jsonl",
                    "atom_count": 0,
                    "error": "closed",
                }
            return {
                "ok": True,
                "backend": "jsonl",
                "atom_count": len(self._by_id),
                "line_count": self._line_count,
                "corrupt_lines": self._corrupt_lines,
                "dirty_ratio": (
                    self._line_count / max(1, len(self._by_id))
                    if self._by_id
                    else float(self._line_count)
                ),
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True

    # ── compaction (idle only) ───────────────────────────────────────────

    def needs_compact(self) -> bool:
        """True when dirty line count or file size suggests compact."""
        with self._lock:
            dirty_thresh = int(self._settings.jsonl_compact_dirty)
            if self._line_count - len(self._by_id) >= dirty_thresh:
                return True
            path = self.atoms_path
            if path.is_file():
                try:
                    if path.stat().st_size >= int(self._settings.jsonl_compact_bytes):
                        return True
                except OSError:
                    pass
            return False

    def maybe_compact(self) -> bool:
        """Compact if ``needs_compact``; return True when a rewrite ran."""
        with self._lock:
            if not self.needs_compact():
                return False
            self._compact_unlocked()
            return True

    def compact(self) -> None:
        """Rewrite atoms.jsonl with one latest line per atom_id."""
        with self._lock:
            self._check_open()
            self._compact_unlocked()

    def _compact_unlocked(self) -> None:
        path = self.atoms_path
        path.parent.mkdir(parents=True, exist_ok=True)
        atoms = sorted(
            self._by_id.values(),
            key=lambda a: (a.t_start, a.atom_id),
        )
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                for atom in atoms:
                    handle.write(
                        json.dumps(self._row_for_disk(atom), ensure_ascii=False)
                        + "\n"
                    )
            tmp.replace(path)
            self._line_count = len(atoms)
            self._corrupt_lines = 0
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise


__all__ = ["JsonlMemoryStore"]
