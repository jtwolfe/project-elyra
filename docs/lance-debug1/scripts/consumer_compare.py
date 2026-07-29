#!/usr/bin/env python3
"""Optional R1 offline consumer compare for lance-debug1 (P06 / P09).

Materialize thin (bare to_arrow) vs full (head / to_lance) row sets, build
ephemeral dict-backed stores, run GraphView structural neighbors, and
optionally report prev/next weave edges that cross the thin-set boundary.

Does **not** open LanceMemoryStore (not W1). Prefer quarantine URI.
Does **not** import product paths that open live data by default.
Does **not** patch product _load.

Safety class: R1.
Deny-list: merge_insert, add, delete, drop_table, compact_files,
cleanup_old_versions, optimize.

Usage (from repo root):
  export PYTHONPATH=.
  python docs/lance-debug1/scripts/consumer_compare.py \\
    --uri \"$LANCE_DEBUG_URI\" \\
    --out docs/lance-debug1/evidence/YYYY-MM-DD-run-01/consumer-compare.json

  python docs/lance-debug1/scripts/consumer_compare.py \\
    --uri \"$LANCE_DEBUG_URI\" --weave-report --out /tmp/consumer-compare.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

_DENY_TABLE_METHODS = frozenset(
    {
        "merge_insert",
        "add",
        "delete",
        "drop_table",
        "compact_files",
        "cleanup_old_versions",
        "optimize",
        "create_table",
        "drop",
    }
)

_DEFAULT_HEAD_CAP = 100_000
_HAIKU_MARKERS = ("haiku", "funny-haiku", "haiku_collection")


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _assert_no_deny_calls_in_source() -> None:
    src = Path(__file__).read_text(encoding="utf-8")
    for name in sorted(_DENY_TABLE_METHODS):
        if f".{name}(" in src:
            raise RuntimeError(
                f"consumer_compare.py must not call deny-list method .{name}("
            )


def _pkg_versions() -> dict[str, Any]:
    out: dict[str, Any] = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
    }
    for name in ("lancedb", "lance", "pyarrow"):
        try:
            mod = __import__(name)
            out[name] = getattr(mod, "__version__", None)
        except Exception as exc:  # noqa: BLE001
            out[name] = f"import_error:{type(exc).__name__}"
    return out


def _column_ids(arrow_or_table: Any, col: str = "atom_id") -> list[str]:
    try:
        if hasattr(arrow_or_table, "column") and col in arrow_or_table.column_names:
            return [str(x.as_py() if hasattr(x, "as_py") else x) for x in arrow_or_table.column(col)]
        if hasattr(arrow_or_table, "to_pydict"):
            d = arrow_or_table.to_pydict()
            if col in d:
                return [str(x) for x in d[col]]
    except Exception:  # noqa: BLE001
        pass
    return []


def _rows_from_arrow(arrow_or_table: Any) -> list[dict[str, Any]]:
    """Best-effort list[dict] from pyarrow Table / RecordBatch."""
    try:
        if hasattr(arrow_or_table, "to_pylist"):
            return list(arrow_or_table.to_pylist())
    except Exception:  # noqa: BLE001
        pass
    try:
        if hasattr(arrow_or_table, "to_pydict"):
            d = arrow_or_table.to_pydict()
            n = len(next(iter(d.values()))) if d else 0
            keys = list(d.keys())
            return [{k: d[k][i] for k in keys} for i in range(n)]
    except Exception:  # noqa: BLE001
        pass
    return []


def _kind_hist(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for r in rows:
        c[str(r.get("kind") or "?")] += 1
    return dict(c.most_common())


def _looks_haiku(row: dict[str, Any]) -> bool:
    text = str(row.get("content_text") or "").lower()
    aid = str(row.get("atom_id") or "").lower()
    blob = text + " " + aid
    return any(m in blob for m in _HAIKU_MARKERS)


# ---------------------------------------------------------------------------
# Ephemeral dict-backed store (structural GraphView only; no disk I/O)
# ---------------------------------------------------------------------------


class _DictStore:
    """Minimal MemoryStore-shaped object for GraphView structural expand."""

    def __init__(self, atoms_by_id: dict[str, Any]) -> None:
        self._by_id = dict(atoms_by_id)
        self._by_moment: dict[str, list[str]] = {}
        for a in self._by_id.values():
            mid = getattr(a, "moment_id", None)
            if mid:
                self._by_moment.setdefault(str(mid), []).append(a.atom_id)
        for mid, ids in self._by_moment.items():
            ids.sort(
                key=lambda i: (
                    str(getattr(self._by_id[i], "t_start", "") or ""),
                    i,
                )
            )

    def get_atom(self, atom_id: str) -> Any | None:
        return self._by_id.get(atom_id)

    def list_by_moment(
        self,
        moment_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        ids = list(self._by_moment.get(str(moment_id), []))
        out: list[Any] = []
        for i in ids:
            a = self._by_id[i]
            if kinds is not None and str(getattr(a, "kind", "")) not in set(
                str(k) for k in kinds
            ):
                continue
            out.append(a)
            if limit is not None and len(out) >= limit:
                break
        return out

    def walk_next(self, atom_id: str, *, n: int = 20) -> list[Any]:
        out: list[Any] = []
        cur = self._by_id.get(atom_id)
        seen: set[str] = set()
        while cur is not None and len(out) < max(0, n) and cur.atom_id not in seen:
            seen |= {cur.atom_id}
            out.append(cur)
            nid = getattr(cur, "next_atom_id", None)
            cur = self._by_id.get(nid) if nid else None
        return out

    def walk_prev(self, atom_id: str, *, n: int = 20) -> list[Any]:
        out: list[Any] = []
        cur = self._by_id.get(atom_id)
        seen: set[str] = set()
        while cur is not None and len(out) < max(0, n) and cur.atom_id not in seen:
            seen |= {cur.atom_id}
            out.append(cur)
            pid = getattr(cur, "prev_atom_id", None)
            cur = self._by_id.get(pid) if pid else None
        return out

    def list_atoms(
        self,
        *,
        embedding_status: str | None = None,
        kinds: Sequence[str] | None = None,
        limit: int = 50,
        newest_first: bool = True,
    ) -> list[Any]:
        rows = list(self._by_id.values())
        if kinds is not None:
            kset = {str(k) for k in kinds}
            rows = [a for a in rows if str(getattr(a, "kind", "")) in kset]
        if embedding_status is not None:
            rows = [
                a
                for a in rows
                if str(getattr(a, "embedding_status", "")) == embedding_status
            ]
        rows.sort(
            key=lambda a: (str(getattr(a, "t_start", "") or ""), a.atom_id),
            reverse=bool(newest_first),
        )
        return rows[: max(0, min(int(limit), 200))]

    def moment_tail(self, moment_id: str) -> Any | None:
        rows = self.list_by_moment(moment_id)
        return rows[-1] if rows else None

    def global_tail(self) -> Any | None:
        rows = self.list_atoms(limit=1, newest_first=True)
        return rows[0] if rows else None

    # Unused write-side protocol stubs (GraphView structural path does not call)
    def put_atom(self, atom: Any, *, notify: bool = True) -> Any:
        raise RuntimeError("consumer_compare _DictStore is read-only")

    def update_links(self, atom_id: str, **kwargs: Any) -> Any:
        raise RuntimeError("consumer_compare _DictStore is read-only")

    def set_write_hook(self, hook: Any) -> None:
        return None

    def delete_atom(self, atom_id: str) -> bool:
        raise RuntimeError("consumer_compare _DictStore is read-only")

    def list_range(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def list_summaries(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


def _atom_from_row(row: dict[str, Any]) -> Any:
    """Build Atom via product helper when available; else minimal stand-in."""
    try:
        from elyra.memory.types import atom_from_dict

        def _opt_str(key: str) -> str | None:
            val = row.get(key)
            if val is None or val == "":
                return None
            return str(val)

        media = row.get("media_ids") or row.get("media_ids_json") or []
        if isinstance(media, str):
            try:
                media = json.loads(media)
            except json.JSONDecodeError:
                media = []
        meta = row.get("meta") or row.get("meta_json") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}

        t_start = row.get("t_start") or "1970-01-01T00:00:00Z"
        kind = row.get("kind") or "observation"
        data = {
            "atom_id": str(row.get("atom_id") or ""),
            "t_start": str(t_start),
            "t_end": _opt_str("t_end"),
            "moment_id": _opt_str("moment_id"),
            "kind": kind,
            "content_ref": row.get("content_ref") or "inline",
            "content_text": row.get("content_text") or "",
            "media_ids": media,
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
            "schema_version": int(row.get("schema_version") or 1),
        }
        if not data["atom_id"]:
            raise ValueError("missing atom_id")
        return atom_from_dict(data)
    except Exception:
        # Minimal namespace for structural fields only
        class _A:
            pass

        a = _A()
        a.atom_id = str(row.get("atom_id") or "")
        a.t_start = str(row.get("t_start") or "1970-01-01T00:00:00Z")
        a.kind = row.get("kind") or "observation"
        a.moment_id = row.get("moment_id")
        a.prev_atom_id = row.get("prev_atom_id") or None
        a.next_atom_id = row.get("next_atom_id") or None
        a.parent_atom_id = row.get("parent_atom_id") or None
        a.content_text = row.get("content_text") or ""
        a.embedding_status = row.get("embedding_status") or "none"
        a.scale = row.get("scale")
        a.window_start = row.get("window_start")
        a.window_end = row.get("window_end")
        a.meta = {}
        return a


def _build_store(rows: Sequence[dict[str, Any]]) -> _DictStore:
    by_id: dict[str, Any] = {}
    for r in rows:
        try:
            atom = _atom_from_row(r)
        except Exception:  # noqa: BLE001
            continue
        if not getattr(atom, "atom_id", None):
            continue
        by_id[str(atom.atom_id)] = atom
    return _DictStore(by_id)


def _neighbor_report(
    store: _DictStore,
    seed_ids: Sequence[str],
    *,
    k: int = 12,
) -> dict[str, Any]:
    """Run GraphView.neighbors structural-only when product import works."""
    report: dict[str, Any] = {
        "seeds_requested": list(seed_ids),
        "seeds_present": [],
        "neighbor_dst_ids": [],
        "neighbor_kinds": {},
        "edge_kinds": {},
        "non_haiku_dst": 0,
        "haiku_dst": 0,
        "errors": [],
        "graphview": False,
    }
    present = [s for s in seed_ids if store.get_atom(s) is not None]
    report["seeds_present"] = present
    if not present:
        return report

    try:
        from elyra.memory.config import MemorySettings
        from elyra.memory.graph import GraphView
        from elyra.memory.weights import (
            EDGE_CHILD_OF,
            EDGE_PARENT_OF,
            EDGE_SAME_MOMENT,
            EDGE_SEQUENTIAL,
        )

        gv = GraphView(
            store,  # type: ignore[arg-type]
            index=None,
            embedder=None,
            settings=MemorySettings(traverse_allow_semantic_hops=False),
        )
        report["graphview"] = True
        structural = (
            EDGE_SEQUENTIAL,
            EDGE_CHILD_OF,
            EDGE_PARENT_OF,
            EDGE_SAME_MOMENT,
        )
        dst_ids: list[str] = []
        edge_kinds: Counter[str] = Counter()
        for sid in present:
            try:
                edges = gv.neighbors(
                    sid,
                    kinds=structural,
                    k=k,
                    allow_semantic=False,
                    expand_deadline_ms=0,
                )
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"{sid}:{type(exc).__name__}:{exc}")
                continue
            for e in edges:
                dst_ids.append(e.dst_atom_id)
                edge_kinds[str(e.edge_kind)] += 1
        uniq_dst = list(dict.fromkeys(dst_ids))
        report["neighbor_dst_ids"] = sorted(uniq_dst)
        report["edge_kinds"] = dict(edge_kinds)
        # Kind hist on destinations
        k_hist: Counter[str] = Counter()
        non_h = 0
        h = 0
        for did in uniq_dst:
            atom = store.get_atom(did)
            if atom is None:
                continue
            k_hist[str(getattr(atom, "kind", "?"))] += 1
            text = str(getattr(atom, "content_text", "") or "").lower()
            if any(m in text for m in _HAIKU_MARKERS):
                h += 1
            else:
                non_h += 1
        report["neighbor_kinds"] = dict(k_hist)
        report["non_haiku_dst"] = non_h
        report["haiku_dst"] = h
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"graphview_import_or_run:{type(exc).__name__}:{exc}")
        # Fallback: pure structural prev/next among store without GraphView
        dst: list[str] = []
        for sid in present:
            a = store.get_atom(sid)
            if a is None:
                continue
            for attr in ("prev_atom_id", "next_atom_id", "parent_atom_id"):
                peer = getattr(a, attr, None)
                if peer and store.get_atom(str(peer)) is not None:
                    dst.append(str(peer))
        report["neighbor_dst_ids"] = sorted(dict.fromkeys(dst))
        report["fallback"] = "prev_next_parent_only"
    return report


def _weave_report(
    full_rows: Sequence[dict[str, Any]],
    thin_ids: set[str],
) -> dict[str, Any]:
    """Count prev/next edges whose endpoints leave the thin id set (P09 / H9)."""
    full_ids = {
        str(r.get("atom_id"))
        for r in full_rows
        if r.get("atom_id") is not None
    }
    edges_total = 0
    both_in_thin = 0
    one_outside_thin = 0
    both_outside_thin = 0
    missing_endpoint_on_disk = 0
    examples: list[dict[str, Any]] = []

    for r in full_rows:
        src = str(r.get("atom_id") or "")
        if not src:
            continue
        for field in ("prev_atom_id", "next_atom_id"):
            dst = r.get(field)
            if dst is None or dst == "":
                continue
            dst_s = str(dst)
            edges_total += 1
            src_in = src in thin_ids
            dst_in = dst_s in thin_ids
            if src_in and dst_in:
                both_in_thin += 1
            elif src_in != dst_in:
                one_outside_thin += 1
                if len(examples) < 15:
                    examples.append(
                        {
                            "src": src,
                            "dst": dst_s,
                            "field": field,
                            "src_in_thin": src_in,
                            "dst_in_thin": dst_in,
                        }
                    )
            else:
                both_outside_thin += 1
            if dst_s not in full_ids:
                missing_endpoint_on_disk += 1

    return {
        "thin_id_count": len(thin_ids),
        "full_id_count": len(full_ids),
        "edges_total": edges_total,
        "both_endpoints_in_thin": both_in_thin,
        "one_endpoint_outside_thin": one_outside_thin,
        "both_endpoints_outside_thin": both_outside_thin,
        "missing_endpoint_on_disk": missing_endpoint_on_disk,
        "h9_cascade_signal": one_outside_thin > 0,
        "examples": examples,
    }


def _materialize(uri: str, table_name: str) -> dict[str, Any]:
    import lancedb

    db = lancedb.connect(uri)
    table = db.open_table(table_name)

    n_full = int(table.count_rows())
    n_head_req = n_full if n_full <= _DEFAULT_HEAD_CAP else _DEFAULT_HEAD_CAP

    head_tbl = table.head(n_head_req)
    full_rows = _rows_from_arrow(head_tbl)
    # Prefer full via to_lance if head was capped or incomplete
    h1b_path = "head"
    if n_full > len(full_rows) or n_full > _DEFAULT_HEAD_CAP:
        try:
            lance_ds = table.to_lance()
            if hasattr(lance_ds, "to_table"):
                full_tbl = lance_ds.to_table()
                full_rows = _rows_from_arrow(full_tbl)
                h1b_path = "to_lance_to_table"
            elif hasattr(lance_ds, "count_rows"):
                # keep head rows; note count
                h1b_path = "head_partial_to_lance_count_only"
        except Exception as exc:  # noqa: BLE001
            h1b_path = f"head_fallback:{type(exc).__name__}"

    arrow = table.to_arrow()
    thin_rows = _rows_from_arrow(arrow)
    thin_ids = [str(r.get("atom_id")) for r in thin_rows if r.get("atom_id") is not None]
    head10 = table.head(10)
    head10_ids = _column_ids(head10)

    return {
        "n_full": n_full,
        "n_arrow": len(thin_rows),
        "n_full_materialized": len(full_rows),
        "h1b_path": h1b_path,
        "thin_rows": thin_rows,
        "full_rows": full_rows,
        "thin_ids": thin_ids,
        "head10_ids": head10_ids,
        "h1a_ok": thin_ids == head10_ids and len(thin_ids) > 0,
        "thin_kind_hist": _kind_hist(thin_rows),
        "full_kind_hist": _kind_hist(full_rows),
        "thin_haiku_rows": sum(1 for r in thin_rows if _looks_haiku(r)),
        "full_haiku_rows": sum(1 for r in full_rows if _looks_haiku(r)),
    }


def run(
    *,
    uri: str,
    table: str = "atoms",
    out: Path | None = None,
    weave_report: bool = False,
    neighbor_k: int = 12,
    seed_limit: int = 10,
) -> dict[str, Any]:
    _assert_no_deny_calls_in_source()
    mat = _materialize(uri, table)
    thin_rows: list[dict[str, Any]] = mat.pop("thin_rows")
    full_rows: list[dict[str, Any]] = mat.pop("full_rows")
    thin_ids: list[str] = mat["thin_ids"]

    thin_store = _build_store(thin_rows)
    full_store = _build_store(full_rows)

    # Seeds: all thin ids (capped) + sample of full-only ids for contrast
    seeds_thin = thin_ids[:seed_limit]
    full_id_list = [
        str(r.get("atom_id"))
        for r in full_rows
        if r.get("atom_id") is not None
    ]
    thin_set = set(thin_ids)
    seeds_full_extra = [i for i in full_id_list if i not in thin_set][:seed_limit]

    thin_nb = _neighbor_report(thin_store, seeds_thin, k=neighbor_k)
    # On full store, expand from same thin seeds (shows edges that become visible)
    full_nb_from_thin_seeds = _neighbor_report(full_store, seeds_thin, k=neighbor_k)
    full_nb_extra = _neighbor_report(full_store, seeds_full_extra, k=neighbor_k)

    # H8 framing: non-haiku kinds present in full but not thin
    thin_kinds = set(mat["thin_kind_hist"].keys())
    full_kinds = set(mat["full_kind_hist"].keys())
    kinds_only_full = sorted(full_kinds - thin_kinds)

    h8 = {
        "claim": (
            "Graph/traverse/meal independently filter non-haiku even when store is full"
        ),
        "kinds_only_on_full_corpus": kinds_only_full,
        "thin_kind_hist": mat["thin_kind_hist"],
        "full_kind_hist": mat["full_kind_hist"],
        "neighbor_dst_count_thin": len(thin_nb.get("neighbor_dst_ids") or []),
        "neighbor_dst_count_full_from_thin_seeds": len(
            full_nb_from_thin_seeds.get("neighbor_dst_ids") or []
        ),
        "full_only_seed_neighbors": len(full_nb_extra.get("neighbor_dst_ids") or []),
        "disconfirm_hint": (
            "If full corpus has kinds/neighbors absent from thin set, H8 as primary "
            "is disconfirmed — consumers reflect store contents."
        ),
        "h8_primary_disconfirmed": bool(kinds_only_full)
        or (
            len(full_nb_from_thin_seeds.get("neighbor_dst_ids") or [])
            > len(thin_nb.get("neighbor_dst_ids") or [])
        )
        or bool(seeds_full_extra),
    }

    result: dict[str, Any] = {
        "ts": _utc_now(),
        "safety_class": "R1",
        "script": "consumer_compare.py",
        "uri": uri,
        "table": table,
        "packages": _pkg_versions(),
        "materialize": mat,
        "split_expectation": {
            "A_to_arrow_prefix_kinds": mat["thin_kind_hist"],
            "note": (
                "Glass newest-first haiku among process maps is consumer order (C), "
                "not required to match bare to_arrow prefix kinds (A)."
            ),
        },
        "neighbors": {
            "thin_store": thin_nb,
            "full_store_from_thin_seeds": full_nb_from_thin_seeds,
            "full_store_from_full_only_seeds": full_nb_extra,
        },
        "h8": h8,
    }

    if weave_report:
        result["weave"] = _weave_report(full_rows, thin_set)
        result["h9"] = {
            "claim": "Post-restart promote weave only links among survivors (cascade)",
            "signal": result["weave"].get("h9_cascade_signal"),
            "one_endpoint_outside_thin": result["weave"].get(
                "one_endpoint_outside_thin"
            ),
            "note": "Cascade of B when mid-session promote was healthy (H3); not product fix",
        }

    if out is not None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
        result["wrote"] = str(out.resolve())

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--uri",
        default=os.environ.get("LANCE_DEBUG_URI"),
        help="lancedb URI (prefer quarantine); or $LANCE_DEBUG_URI",
    )
    parser.add_argument("--table", default="atoms")
    parser.add_argument("--out", type=Path, default=None, help="Write JSON report")
    parser.add_argument(
        "--weave-report",
        action="store_true",
        help="Include P09 prev/next cross-thin-set analysis (H9 cascade signal)",
    )
    parser.add_argument("--neighbor-k", type=int, default=12)
    parser.add_argument("--seed-limit", type=int, default=10)
    args = parser.parse_args(argv)

    if not args.uri:
        print(
            "error: --uri or LANCE_DEBUG_URI required (prefer quarantine)",
            file=sys.stderr,
        )
        return 2

    try:
        result = run(
            uri=str(args.uri),
            table=args.table,
            out=args.out,
            weave_report=bool(args.weave_report),
            neighbor_k=int(args.neighbor_k),
            seed_limit=int(args.seed_limit),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

    # Compact stdout summary
    mat = result.get("materialize") or {}
    h8 = result.get("h8") or {}
    summary = {
        "n_full": mat.get("n_full"),
        "n_arrow": mat.get("n_arrow"),
        "h1a_ok": mat.get("h1a_ok"),
        "h1b_path": mat.get("h1b_path"),
        "thin_kind_hist": mat.get("thin_kind_hist"),
        "h8_primary_disconfirmed": h8.get("h8_primary_disconfirmed"),
        "wrote": result.get("wrote"),
    }
    if "weave" in result:
        summary["weave_one_outside_thin"] = result["weave"].get(
            "one_endpoint_outside_thin"
        )
        summary["h9_cascade_signal"] = result["weave"].get("h9_cascade_signal")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
