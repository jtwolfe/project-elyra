"""Optional LanceDB MemoryStore backend (Phase 1 scalar + Phase 2 emb columns).

Scope: Protocol-complete Lance persistence under data/memory/lance/.
In scope: put/get/range/moment/links/walk/health, blob spill, meta.json,
          additive emb_* columns, migration, KD19 preserve, upsert_vectors.
Out of scope: ANN hybrid buffer (PR4), meal channel, torch.

Requires optional dependency: ``pip install elyra[memory-lance]`` (lancedb).
"""

from __future__ import annotations

import json
import logging
import math
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
from elyra.memory.embed.types import (
    CHANNELS,
    EMBED_DIM,
    EmbeddingSet,
    embeddings_are_ready,
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

# Physical vector layout epoch (meta.json only; Atom.schema_version stays 1).
VECTOR_SCHEMA_VERSION = 1

# Phase 1 scalar columns.
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

# Phase 2 emb columns (co-row with scalar; null when absent).
_EMB_VECTOR_COLS = tuple(f"emb_{c}" for c in CHANNELS)  # text/image/audio/video/joint
_EMB_META_COLS = ("embed_model", "encoded_at")
_EMB_ALL_COLS = _EMB_VECTOR_COLS + _EMB_META_COLS


def _require_lancedb():
    """Import lancedb or raise ImportError (factory catches for fall-back)."""
    import lancedb  # noqa: PLC0415 — optional dep

    return lancedb


def _emb_vector_type():
    """Fixed-size list float32[EMBED_DIM] (lancedb.vector / pa.list_)."""
    import pyarrow as pa  # noqa: PLC0415

    try:
        import lancedb  # noqa: PLC0415

        return lancedb.vector(EMBED_DIM)
    except Exception:  # noqa: BLE001
        return pa.list_(pa.float32(), EMBED_DIM)


def _atoms_schema(*, with_vectors: bool = True):
    import pyarrow as pa  # noqa: PLC0415 — pulled in with lancedb

    fields = [pa.field(name, pa.utf8()) for name in _STRING_COLS]
    fields.append(pa.field("schema_version", pa.int64()))
    if with_vectors:
        emb_type = _emb_vector_type()
        for name in _EMB_VECTOR_COLS:
            fields.append(pa.field(name, emb_type, nullable=True))
        fields.append(pa.field("embed_model", pa.utf8(), nullable=True))
        fields.append(pa.field("encoded_at", pa.utf8(), nullable=True))
    return pa.schema(fields)


def _sql_quote(value: str) -> str:
    """Single-quote a string for Lance SQL filter predicates."""
    return "'" + value.replace("'", "''") + "'"


def _vec_to_list(vec: Sequence[float] | None) -> list[float] | None:
    if vec is None:
        return None
    return [float(x) for x in vec]


def _vec_from_cell(raw: Any) -> tuple[float, ...] | None:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        if not raw:
            return None
        return tuple(float(x) for x in raw)
    # pyarrow scalar / numpy
    try:
        seq = list(raw)
    except TypeError:
        return None
    if not seq:
        return None
    return tuple(float(x) for x in seq)


class LanceMemoryStore:
    """Single-writer Lance atom store under ``data/memory/lance/``.

    In-memory indexes mirror JsonlMemoryStore so Protocol behaviour matches.
    Lance is the durable source of truth (reloaded on open).

    Phase 2: emb_* columns co-reside with scalar fields. Scalar put/update_links
    **preserve** emb columns (KD19 read-merge-write via ``_emb_by_id``).
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
        # atom_id -> emb column map (lists/str); not on Atom dataclass
        self._emb_by_id: dict[str, dict[str, Any]] = {}
        self._closed: bool = False
        self._db: Any = None
        self._table: Any = None
        self._lancedb = lancedb
        self._vector_schema_ok: bool = False
        self._vector_error: str | None = None
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

    @property
    def vector_schema_ok(self) -> bool:
        """True when emb columns are available for upsert/search."""
        return self._vector_schema_ok

    # ── lifecycle ────────────────────────────────────────────────────────

    def _read_meta(self) -> dict[str, Any]:
        if not self.meta_path.is_file():
            return {}
        try:
            existing = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return existing if isinstance(existing, dict) else {}

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
            existing = self._read_meta()
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

    def _mark_vector_meta(self, *, migrated: bool) -> None:
        """Write vector_schema_version / emb_dim into meta.json (best-effort)."""
        try:
            meta = self._read_meta()
            meta["schema_version"] = meta.get("schema_version", SCHEMA_VERSION)
            meta["backend"] = "lance"
            if migrated or meta.get("vector_schema_version") is None:
                meta["vector_schema_version"] = VECTOR_SCHEMA_VERSION
            meta["emb_dim"] = EMBED_DIM
            if not meta.get("embed_model"):
                meta["embed_model"] = (
                    getattr(self._settings, "embed_model_id", None) or ""
                )
            if migrated and "vector_migrated_at" not in meta:
                meta["vector_migrated_at"] = utc_now_iso()
            if "created_at" not in meta:
                meta["created_at"] = utc_now_iso()
            self._write_meta(meta)
        except Exception:  # noqa: BLE001
            _LOG.exception("failed to write vector fields to meta.json")

    def _open_db(self) -> None:
        import pyarrow as pa  # noqa: PLC0415

        uri = str(self.lance_dir)
        self._db = self._lancedb.connect(uri)
        names = list(self._db.table_names())
        if _ATOMS_TABLE not in names:
            empty = pa.Table.from_pylist([], schema=_atoms_schema(with_vectors=True))
            self._table = self._db.create_table(_ATOMS_TABLE, empty)
            self._vector_schema_ok = True
            self._vector_error = None
            self._mark_vector_meta(migrated=True)
        else:
            self._table = self._db.open_table(_ATOMS_TABLE)
            self._migrate_vector_schema()

    def _table_has_emb_columns(self) -> bool:
        if self._table is None:
            return False
        try:
            names = set(self._table.schema.names)
        except Exception:  # noqa: BLE001
            return False
        return all(c in names for c in _EMB_ALL_COLS)

    def _migrate_vector_schema(self) -> None:
        """Additive emb migration for Phase 1 tables (Gate A / KD19).

        Strategy for lancedb 0.20.x: recreate+copy. ``add_columns`` only accepts
        SQL expression maps and cannot reliably introduce fixed-size list
        vector columns with null defaults. Fail-closed: scalar path remains
        usable when migration throws.
        """
        import pyarrow as pa  # noqa: PLC0415

        if self._table_has_emb_columns():
            meta = self._read_meta()
            if int(meta.get("vector_schema_version") or 0) >= VECTOR_SCHEMA_VERSION:
                self._vector_schema_ok = True
                self._vector_error = None
                return
            self._vector_schema_ok = True
            self._vector_error = None
            self._mark_vector_meta(migrated=True)
            return

        _LOG.warning(
            "lance emb migration: Phase 1 table lacks emb columns; "
            "recreate+copy. Backup recommended: copy data/memory/lance before upgrade."
        )
        try:
            try:
                rows = self._table.to_arrow().to_pylist()
            except Exception:
                rows = []
            new_rows: list[dict[str, Any]] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                nr: dict[str, Any] = {}
                for col in _STRING_COLS:
                    nr[col] = r.get(col)
                nr["schema_version"] = int(r.get("schema_version") or SCHEMA_VERSION)
                for col in _EMB_VECTOR_COLS:
                    nr[col] = _vec_to_list(_vec_from_cell(r.get(col)))
                nr["embed_model"] = r.get("embed_model") or None
                nr["encoded_at"] = r.get("encoded_at") or None
                new_rows.append(nr)

            schema = _atoms_schema(with_vectors=True)
            # Drop + recreate (lancedb 0.20.x has no typed null add_columns).
            self._db.drop_table(_ATOMS_TABLE)
            if new_rows:
                table_data = pa.Table.from_pylist(new_rows, schema=schema)
            else:
                table_data = pa.Table.from_pylist([], schema=schema)
            self._table = self._db.create_table(_ATOMS_TABLE, table_data)
            self._vector_schema_ok = True
            self._vector_error = None
            self._mark_vector_meta(migrated=True)
            _LOG.info(
                "lance emb migration complete rows=%d vector_schema_version=%d",
                len(new_rows),
                VECTOR_SCHEMA_VERSION,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("lance emb migration failed")
            self._vector_schema_ok = False
            self._vector_error = f"migration_failed: {type(exc).__name__}: {exc}"
            # Best-effort reopen scalar table for Phase 1 path.
            try:
                if _ATOMS_TABLE in list(self._db.table_names()):
                    self._table = self._db.open_table(_ATOMS_TABLE)
            except Exception:  # noqa: BLE001
                _LOG.exception("lance reopen after failed migration also failed")

    def _load(self) -> None:
        """Rebuild in-memory indexes from the Lance table."""
        self._by_id.clear()
        self._by_moment.clear()
        self._ladder.clear()
        self._emb_by_id.clear()
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
            if self._vector_schema_ok:
                emb_map = self._emb_map_from_row(row)
                if emb_map is not None:
                    self._emb_by_id[atom.atom_id] = emb_map
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

    def _emb_map_from_row(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Extract emb columns from a Lance row into the side map shape."""
        has_any = False
        out: dict[str, Any] = {}
        for col in _EMB_VECTOR_COLS:
            vec = _vec_from_cell(row.get(col))
            out[col] = list(vec) if vec is not None else None
            if vec is not None:
                has_any = True
        model = row.get("embed_model")
        encoded = row.get("encoded_at")
        out["embed_model"] = str(model) if model else None
        out["encoded_at"] = str(encoded) if encoded else None
        if model or encoded:
            has_any = True
        return out if has_any else None

    def _embedding_set_from_map(
        self, atom_id: str, emb_map: dict[str, Any]
    ) -> EmbeddingSet | None:
        kwargs: dict[str, Any] = {}
        for col in _EMB_VECTOR_COLS:
            raw = emb_map.get(col)
            ch = col[len("emb_") :]
            kwargs[f"emb_{ch}"] = tuple(raw) if raw is not None else None
        if not any(kwargs.get(f"emb_{c}") is not None for c in CHANNELS):
            return None
        return EmbeddingSet(
            atom_id=atom_id,
            dim=EMBED_DIM,
            model_id=str(emb_map.get("embed_model") or ""),
            encoded_at=str(emb_map.get("encoded_at") or ""),
            **kwargs,
        )

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

    def _attach_emb_columns(self, row: dict[str, Any], atom_id: str) -> dict[str, Any]:
        """KD19: merge existing emb_* into scalar upsert row (read-merge-write)."""
        if not self._vector_schema_ok:
            return row
        emb = self._emb_by_id.get(atom_id)
        if emb:
            for col in _EMB_VECTOR_COLS:
                val = emb.get(col)
                row[col] = list(val) if val is not None else None
            row["embed_model"] = emb.get("embed_model")
            row["encoded_at"] = emb.get("encoded_at")
        else:
            for col in _EMB_VECTOR_COLS:
                row[col] = None
            row["embed_model"] = None
            row["encoded_at"] = None
        return row

    def _upsert_row(self, atom: Atom) -> None:
        row = self._row_for_disk(atom)
        row = self._attach_emb_columns(row, atom.atom_id)
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
        Scalar path preserves existing emb_* columns (KD19).
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
        """Patch sequential links only (preserves emb_* — KD19)."""
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

    def upsert_vectors(self, atom_id: str, embeddings: EmbeddingSet) -> bool:
        """Dedicated vector path: write emb_* + set ready when KD20 satisfied.

        Does not require promote to rewrite the full scalar atom. Returns False
        if atom missing, vectors unavailable, or embeddings not ready.
        """
        with self._lock:
            self._check_open()
            if not self._vector_schema_ok:
                return False
            atom = self._by_id.get(atom_id)
            if atom is None:
                return False
            if embeddings.atom_id and embeddings.atom_id != atom_id:
                _LOG.warning(
                    "upsert_vectors atom_id mismatch store=%s set=%s",
                    atom_id,
                    embeddings.atom_id,
                )
            if int(embeddings.dim) != EMBED_DIM:
                _LOG.warning(
                    "upsert_vectors dim mismatch atom_id=%s dim=%s expected=%s",
                    atom_id,
                    embeddings.dim,
                    EMBED_DIM,
                )
                return False
            if not embeddings_are_ready(embeddings):
                return False

            emb_map: dict[str, Any] = {
                "emb_text": _vec_to_list(embeddings.emb_text),
                "emb_image": _vec_to_list(embeddings.emb_image),
                "emb_audio": _vec_to_list(embeddings.emb_audio),
                "emb_video": _vec_to_list(embeddings.emb_video),
                "emb_joint": _vec_to_list(embeddings.emb_joint),
                "embed_model": embeddings.model_id or None,
                "encoded_at": embeddings.encoded_at or None,
            }
            self._emb_by_id[atom_id] = emb_map

            meta = dict(atom.meta or {})
            if embeddings.model_id:
                meta["embed_model"] = embeddings.model_id
            if embeddings.encoded_at:
                meta["embed_encoded_at"] = embeddings.encoded_at
            meta["embed_channels"] = list(embeddings.channels_present)
            meta["embed_encode_ok"] = True
            updated = atom_replace(
                atom,
                embedding_status="ready",
                meta=meta,
            )
            try:
                self._upsert_row(updated)
            except Exception:  # noqa: BLE001
                _LOG.exception("upsert_vectors lance write failed atom_id=%s", atom_id)
                # Roll back side map so scalar path does not re-attach stale.
                self._emb_by_id.pop(atom_id, None)
                return False
            self._index_put(updated)
            return True

    def get_vectors(self, atom_id: str) -> EmbeddingSet | None:
        """Return durable vectors for atom_id, or None."""
        with self._lock:
            self._check_open()
            emb_map = self._emb_by_id.get(atom_id)
            if not emb_map:
                return None
            return self._embedding_set_from_map(atom_id, emb_map)

    def search_vectors(
        self,
        query: Sequence[float],
        *,
        k: int = 12,
        channel: str = "joint",
        t_start: datetime | str | None = None,
        t_end: datetime | str | None = None,
        moment_id: str | None = None,
        kinds: Sequence[str] | None = None,
        exclude_atom_ids: Sequence[str] | None = None,
        exclude_moment_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """Brute-force cosine over ready emb columns (PR3 minimal search).

        Returns list of (atom_id, score) sorted by score desc. ANN / hybrid
        buffer is PR4.
        """
        with self._lock:
            self._check_open()
            if not self._vector_schema_ok or not query:
                return []
            col = f"emb_{channel}" if not channel.startswith("emb_") else channel
            if col not in _EMB_VECTOR_COLS:
                return []
            q = [float(x) for x in query]
            q_norm = math.sqrt(sum(x * x for x in q))
            if q_norm < 1e-12:
                return []
            q = [x / q_norm for x in q]

            start_s = to_iso_z(t_start) if t_start is not None else None
            end_s = to_iso_z(t_end) if t_end is not None else None
            kind_set = set(kinds) if kinds is not None else None
            exclude = set(exclude_atom_ids or ())

            scored: list[tuple[str, float]] = []
            for atom_id, emb_map in self._emb_by_id.items():
                if atom_id in exclude:
                    continue
                atom = self._by_id.get(atom_id)
                if atom is None:
                    continue
                if atom.embedding_status != "ready":
                    continue
                if kind_set is not None and atom.kind not in kind_set:
                    continue
                if moment_id is not None and atom.moment_id != moment_id:
                    continue
                if exclude_moment_id and atom.moment_id == exclude_moment_id:
                    continue
                at = to_iso_z(atom.t_start)
                if start_s is not None and at < start_s:
                    continue
                if end_s is not None and at >= end_s:
                    continue
                raw = emb_map.get(col)
                if raw is None:
                    continue
                vec = [float(x) for x in raw]
                if len(vec) != len(q):
                    continue
                # Vectors stored L2-normalized; still normalize defensively.
                v_norm = math.sqrt(sum(x * x for x in vec))
                if v_norm < 1e-12:
                    continue
                score = sum(a * b for a, b in zip(q, vec, strict=False)) / v_norm
                scored.append((atom_id, float(score)))

            scored.sort(key=lambda p: (-p[1], p[0]))
            return scored[: max(0, int(k))]

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
            self._emb_by_id.pop(atom_id, None)
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
        """``{ok, backend, atom_count, vectors, error?}``."""
        with self._lock:
            if self._closed:
                return {
                    "ok": False,
                    "backend": "lance",
                    "atom_count": 0,
                    "vectors": False,
                    "error": "closed",
                }
            vectors_ready = sum(
                1
                for a in self._by_id.values()
                if a.embedding_status == "ready" and a.atom_id in self._emb_by_id
            )
            out: dict[str, Any] = {
                "ok": True,
                "backend": "lance",
                "atom_count": len(self._by_id),
                "lance_dir": str(self.lance_dir),
                "vectors": bool(self._vector_schema_ok),
                "vector_schema_version": (
                    VECTOR_SCHEMA_VERSION if self._vector_schema_ok else 0
                ),
                "vectors_ready": vectors_ready,
            }
            if self._vector_error:
                out["vector_error"] = self._vector_error
            return out

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._table = None
            self._db = None


__all__ = [
    "VECTOR_SCHEMA_VERSION",
    "LanceMemoryStore",
]
