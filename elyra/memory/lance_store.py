"""Optional LanceDB MemoryStore backend (Phase 1 atom fields only).

Scope: Protocol-complete Lance persistence under data/memory/lance/.
In scope: put/get/range/moment/links/walk/health, blob spill, meta.json.
Out of scope: vector columns, ANN, lance-graph, meal/promote rewrites.

Requires optional dependency: ``pip install elyra[memory-lance]`` (lancedb).
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
    blob_relpath_for_atom,
    ensure_memory_dirs,
    lance_root,
    memory_meta_path,
    memory_root,
)
from elyra.memory.errors import MemoryAtomNotFound, MemoryUnavailable
from elyra.memory.store import LIST_ATOMS_MAX, AtomWriteHook
from elyra.memory.types import (
    SCHEMA_VERSION,
    Atom,
    AtomKind,
    PeriodScale,
    atom_from_dict,
    atom_replace,
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

_ATOMS_TABLE = "atoms"

# Phase 1 scalar columns only — no vector / ANN fields.
_STRING_COLS = (
    "atom_id",
    "t_start",
    "kind",
    "content_text",
    "content_ref",
    "t_end",
    "moment_id",
    "media_ids_json",
    "prev_atom_id",
    "next_atom_id",
    "parent_atom_id",
    "scale",
    "window_start",
    "window_end",
    "source_beat_ts",
    "source_beat_type",
    "embedding_status",
    "meta_json",
)


def _require_lancedb():
    """Import lancedb or raise ImportError (factory catches for fall-back)."""
    import lancedb  # noqa: PLC0415 — optional dep

    return lancedb


def _atoms_schema():
    import pyarrow as pa  # noqa: PLC0415 — pulled in with lancedb

    fields = [pa.field(name, pa.utf8()) for name in _STRING_COLS]
    fields.append(pa.field("schema_version", pa.int64()))
    return pa.schema(fields)


def _sql_quote(value: str) -> str:
    """Single-quote a string for Lance SQL filter predicates."""
    return "'" + value.replace("'", "''") + "'"


class LanceMemoryStore:
    """Single-writer Lance atom store under ``data/memory/lance/``.

    In-memory indexes mirror JsonlMemoryStore so Protocol behaviour matches.
    Lance is the durable source of truth (reloaded on open).
    """

    def __init__(
        self,
        paths: ElyraPaths,
        settings: MemorySettings | None = None,
    ) -> None:
        lancedb = _require_lancedb()
        self._paths = paths
        self._settings = settings or MemorySettings()
        self._lock = threading.RLock()
        self._by_id: dict[str, Atom] = {}
        self._by_moment: dict[str, list[str]] = {}
        # (scale, window_start) -> atom_id for kind=summary ladder index
        self._ladder: dict[tuple[str, str], str] = {}
        self._closed: bool = False
        self._db: Any = None
        self._table: Any = None
        self._lancedb = lancedb
        # Phase 2 write hook (encode enqueue); best-effort, never raises out.
        self._write_hook: AtomWriteHook | None = None
        self._ensure_layout()
        self._open_db()
        self._load()

    # ── paths ────────────────────────────────────────────────────────────

    @property
    def memory_dir(self) -> Path:
        return memory_root(self._paths)

    @property
    def lance_dir(self) -> Path:
        return lance_root(self._paths)

    @property
    def meta_path(self) -> Path:
        return memory_meta_path(self._paths)

    @property
    def settings(self) -> MemorySettings:
        return self._settings

    # ── lifecycle ────────────────────────────────────────────────────────

    def _ensure_layout(self) -> None:
        ensure_memory_dirs(self._paths)
        self.lance_dir.mkdir(parents=True, exist_ok=True)
        if not self.meta_path.is_file():
            meta = {
                "schema_version": SCHEMA_VERSION,
                "backend": "lance",
                "created_at": utc_now_iso(),
            }
            self._write_meta(meta)
        else:
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
            # Record active backend when this store opens (operator switched).
            if existing.get("backend") != "lance":
                existing["backend"] = "lance"
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

    def _open_db(self) -> None:
        import pyarrow as pa  # noqa: PLC0415

        uri = str(self.lance_dir)
        self._db = self._lancedb.connect(uri)
        names = list(self._db.table_names())
        if _ATOMS_TABLE not in names:
            empty = pa.Table.from_pylist([], schema=_atoms_schema())
            self._table = self._db.create_table(_ATOMS_TABLE, empty)
        else:
            self._table = self._db.open_table(_ATOMS_TABLE)

    def _load(self) -> None:
        """Rebuild in-memory indexes from the Lance table."""
        self._by_id.clear()
        self._by_moment.clear()
        self._ladder.clear()
        if self._table is None:
            return
        try:
            rows = self._table.to_arrow().to_pylist()
        except Exception:
            _LOG.exception("lance load failed")
            raise
        for row in rows:
            try:
                atom = self._atom_from_row(row)
                atom = self._hydrate_content(atom)
            except (TypeError, ValueError):
                _LOG.warning("skipping corrupt lance row atom_id=%r", row.get("atom_id"))
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

    # ── row codec ────────────────────────────────────────────────────────

    def _atom_from_row(self, row: dict[str, Any]) -> Atom:
        media_raw = row.get("media_ids_json") or "[]"
        try:
            media_ids = json.loads(media_raw) if isinstance(media_raw, str) else media_raw
        except json.JSONDecodeError:
            media_ids = []
        meta_raw = row.get("meta_json") or "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        except json.JSONDecodeError:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}

        def _opt_str(key: str) -> str | None:
            val = row.get(key)
            if val is None or val == "":
                return None
            return str(val)

        data = {
            "atom_id": row.get("atom_id"),
            "t_start": row.get("t_start"),
            "t_end": _opt_str("t_end"),
            "moment_id": _opt_str("moment_id"),
            "kind": row.get("kind"),
            "content_ref": row.get("content_ref") or "inline",
            "content_text": row.get("content_text") or "",
            "media_ids": media_ids,
            "prev_atom_id": _opt_str("prev_atom_id"),
            "next_atom_id": _opt_str("next_atom_id"),
            "parent_atom_id": _opt_str("parent_atom_id"),
            "scale": _opt_str("scale"),
            "window_start": _opt_str("window_start"),
            "window_end": _opt_str("window_end"),
            "source_beat_ts": _opt_str("source_beat_ts"),
            "source_beat_type": _opt_str("source_beat_type"),
            "embedding_status": row.get("embedding_status") or "none",
            "meta": meta,
            "schema_version": int(row.get("schema_version") or SCHEMA_VERSION),
        }
        return atom_from_dict(data)

    def _row_for_disk(self, atom: Atom) -> dict[str, Any]:
        """Serialize atom for Lance; empty content_text when body is spilled."""
        content_text = atom.content_text
        if str(atom.content_ref).startswith("blob:"):
            content_text = ""
        return {
            "atom_id": atom.atom_id,
            "t_start": atom.t_start,
            "kind": str(atom.kind),
            "content_text": content_text if content_text is not None else "",
            "content_ref": atom.content_ref or "inline",
            "t_end": atom.t_end,
            "moment_id": atom.moment_id,
            "media_ids_json": json.dumps(list(atom.media_ids), ensure_ascii=False),
            "prev_atom_id": atom.prev_atom_id,
            "next_atom_id": atom.next_atom_id,
            "parent_atom_id": atom.parent_atom_id,
            "scale": atom.scale,
            "window_start": atom.window_start,
            "window_end": atom.window_end,
            "source_beat_ts": atom.source_beat_ts,
            "source_beat_type": atom.source_beat_type,
            "embedding_status": atom.embedding_status or "none",
            "meta_json": json.dumps(dict(atom.meta), ensure_ascii=False),
            "schema_version": int(atom.schema_version or SCHEMA_VERSION),
        }

    def _upsert_row(self, atom: Atom) -> None:
        row = self._row_for_disk(atom)
        (
            self._table.merge_insert("atom_id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute([row])
        )

    def _delete_row(self, atom_id: str) -> None:
        self._table.delete(f"atom_id = {_sql_quote(atom_id)}")

    # ── content spill ────────────────────────────────────────────────────

    def _unlink_blob_if_any(self, content_ref: str | None) -> None:
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
        """Cap content_text, derive content_ref, normalize timestamps, validate."""
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
            if prev_ref.startswith("blob:"):
                self._unlink_blob_if_any(prev_ref)
            atom = atom_replace(atom, content_ref="inline")

        if atom.embedding_status is None or atom.embedding_status == "":
            atom = atom_replace(atom, embedding_status="none")
        if not atom.schema_version:
            atom = atom_replace(atom, schema_version=SCHEMA_VERSION)

        return validate_atom(atom)

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

    def set_write_hook(self, hook: AtomWriteHook | None) -> None:
        """Register hook called after successful put_atom (KD16)."""
        with self._lock:
            self._write_hook = hook

    def _fire_write_hook(self, atom: Atom) -> None:
        """Best-effort write hook; must never raise to put_atom callers."""
        hook = self._write_hook
        if hook is None:
            return
        try:
            hook(atom)
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "memory write hook failed atom_id=%s", atom.atom_id
            )

    def put_atom(self, atom: Atom, *, notify: bool = True) -> Atom:
        """Insert or replace by atom_id. Returns stored atom (full content_text).

        ``notify=False`` skips the write hook (internal encode status updates).
        """
        with self._lock:
            self._check_open()
            prepared = self._prepare_for_put(atom)
            self._upsert_row(prepared)
            self._index_put(prepared)
        if notify:
            self._fire_write_hook(prepared)
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
        """Patch sequential links only."""
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
            self._upsert_row(updated)
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

    def list_atoms(
        self,
        *,
        embedding_status: str | None = None,
        kinds: Sequence[AtomKind | str] | None = None,
        limit: int = 50,
        newest_first: bool = True,
    ) -> list[Atom]:
        """Glass/admin listing by embedding_status / kinds (scan ``_by_id``)."""
        with self._lock:
            self._check_open()
            kind_set = set(kinds) if kinds is not None else None
            cap = max(0, min(int(limit), LIST_ATOMS_MAX))
            rows = list(self._by_id.values())
            out: list[Atom] = []
            for atom in rows:
                if (
                    embedding_status is not None
                    and atom.embedding_status != embedding_status
                ):
                    continue
                if kind_set is not None and atom.kind not in kind_set:
                    continue
                out.append(atom)
            out.sort(
                key=lambda a: (to_iso_z(a.t_start), a.atom_id),
                reverse=bool(newest_first),
            )
            return out[:cap]

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
        """Latest sequential atom in moment (excludes summary/parcel/moment_meta)."""
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
            tails = [a for a in chain if a.next_atom_id is None]
            pool = tails if tails else chain
            return max(pool, key=lambda a: (to_iso_z(a.t_start), a.atom_id))

    def global_tail(self) -> Atom | None:
        """Latest sequential-weave tip (excludes summary/parcel/moment_meta)."""
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
        with self._lock:
            self._check_open()
            return self._walk(atom_id, direction="next", n=n)

    def walk_prev(self, atom_id: str, *, n: int = 20) -> list[Atom]:
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
        """Remove atom (admin/tests). Deletes Lance row; unlinks blob."""
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
            self._delete_row(atom_id)
            return True

    def health(self) -> dict[str, Any]:
        """``{ok, backend, atom_count, error?}``."""
        with self._lock:
            if self._closed:
                return {
                    "ok": False,
                    "backend": "lance",
                    "atom_count": 0,
                    "error": "closed",
                }
            return {
                "ok": True,
                "backend": "lance",
                "atom_count": len(self._by_id),
                "lance_dir": str(self.lance_dir),
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._table = None
            self._db = None


__all__ = ["LanceMemoryStore"]
