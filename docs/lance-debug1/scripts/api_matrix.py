#!/usr/bin/env python3
"""R1 read-only API matrix probe for lance-debug1 (P01).

Preferred probe order (safe full-read first):
  count_rows → head(n) → bare to_arrow → H1a → H1b fallback chain → to_lance
  → optional subprocess native → list_versions / schema

H1b fallback chain (stop at first success; do NOT require public table.query()):
  H1b-1 public query().limit(n_full).to_arrow() if present
  H1b-2 optional private/async table._table.query().limit(n_full).to_arrow()
  H1b-3 head(n_full) primary public proof on lancedb 0.20.0
  H1b-4 to_lance().to_table() / count_rows corroboration

Deny-list (never invoked): merge_insert, add, delete, drop_table, compact_files,
cleanup_old_versions, optimize.

Usage (from repo root):
  python docs/lance-debug1/scripts/api_matrix.py --uri PATH --out PATH.json
  python docs/lance-debug1/scripts/api_matrix.py --uri \"$LANCE_DEBUG_URI\" \\
      --table atoms --out docs/lance-debug1/evidence/YYYY-MM-DD-run-01/api-matrix.json

Safety class: R1. Prefer quarantine URI. Does not open LanceMemoryStore.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import subprocess
import sys
import time
import traceback
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Deny-list: names that must never be called on Table (documented + asserted).
# ---------------------------------------------------------------------------
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

# Cap for materializing head when n_full is huge (still prefer full when feasible).
_DEFAULT_HEAD_CAP = 100_000
_ID_SAMPLE = 20


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pkg_versions() -> dict[str, Any]:
    out: dict[str, Any] = {"python": sys.version.split()[0], "executable": sys.executable}
    for name in ("lancedb", "lance", "pyarrow"):
        try:
            mod = __import__(name)
            out[name] = getattr(mod, "__version__", None)
        except Exception as exc:  # noqa: BLE001
            out[name] = f"import_error:{type(exc).__name__}"
    return out


def _column_ids(arrow_or_table: Any, col: str = "atom_id") -> list[str]:
    """Extract atom_id (or fallback) list from pyarrow Table / RecordBatch."""
    try:
        names = list(arrow_or_table.schema.names)
    except Exception:  # noqa: BLE001
        names = []
    key = col if col in names else (names[0] if names else None)
    if key is None:
        return []
    try:
        return [str(x) for x in arrow_or_table.column(key).to_pylist()]
    except Exception:  # noqa: BLE001
        return []


def _kind_hist(arrow_or_table: Any) -> dict[str, int]:
    try:
        names = list(arrow_or_table.schema.names)
    except Exception:  # noqa: BLE001
        return {}
    if "kind" not in names:
        return {}
    try:
        vals = [str(x) if x is not None else "null" for x in arrow_or_table.column("kind").to_pylist()]
    except Exception:  # noqa: BLE001
        return {}
    return dict(sorted(Counter(vals).items()))


def _embedding_status_hist(arrow_or_table: Any) -> dict[str, int] | None:
    try:
        names = list(arrow_or_table.schema.names)
    except Exception:  # noqa: BLE001
        return None
    if "embedding_status" not in names:
        return None
    try:
        vals = [
            str(x) if x is not None else "null"
            for x in arrow_or_table.column("embedding_status").to_pylist()
        ]
    except Exception:  # noqa: BLE001
        return None
    return dict(sorted(Counter(vals).items()))


def _schema_has_emb(schema_names: list[str]) -> bool:
    return any(n.startswith("emb_") for n in schema_names)


def _assert_no_deny_calls_in_source() -> None:
    """Static self-check: this module's source must not invoke deny-list APIs."""
    src = Path(__file__).read_text(encoding="utf-8")
    # Allow listing the names; forbid call-site patterns.
    for name in sorted(_DENY_TABLE_METHODS):
        if f".{name}(" in src:
            raise RuntimeError(f"api_matrix.py must not call deny-list method .{name}(")


def _maybe_await(result: Any) -> Any:
    if inspect.iscoroutine(result):
        return asyncio.run(result)
    return result


def _jsonable(obj: Any) -> Any:
    """Best-effort conversion of list_versions samples / nested structures."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    # dataclasses / simple objects with __dict__
    if hasattr(obj, "_asdict"):
        try:
            return _jsonable(obj._asdict())
        except Exception:  # noqa: BLE001
            pass
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        try:
            return _jsonable(vars(obj))
        except Exception:  # noqa: BLE001
            pass
    return str(obj)


def _run_h1b_chain(
    table: Any,
    *,
    n_full: int,
    n_arrow: int,
    n_head: int | None,
) -> dict[str, Any]:
    """H1b fallback chain: stop at first success; record attempts.

    Overall pass: any step yields full row count while bare to_arrow stays thin.
    Missing public table.query is NOT a failure.
    """
    attempts: list[str] = []
    path: str | None = None
    details: dict[str, Any] = {}
    bare_thin = n_full > 0 and n_arrow < n_full and (
        n_arrow <= 10 or n_arrow <= max(1, n_full // 10)
    )
    # Typical smoking-gun: n_arrow == 10 and n_full >> 10
    if n_full > 10 and n_arrow == 10:
        bare_thin = True
    if n_full > 0 and n_arrow < n_full:
        bare_thin = True

    # --- H1b-1: public query if present ---
    public_query = getattr(table, "query", None)
    if public_query is None or not callable(public_query):
        attempts.append("query_public_missing")
        details["query_public"] = {"present": False}
    else:
        attempts.append("query_public")
        try:
            q = public_query()
            if hasattr(q, "limit"):
                q = q.limit(n_full)
            arr = _maybe_await(q.to_arrow())
            n = int(arr.num_rows)
            details["query_public"] = {"present": True, "num_rows": n}
            if n == n_full and bare_thin:
                path = "query_public"
                return {
                    "ok": True,
                    "path": path,
                    "n_full": n_full,
                    "n_arrow": n_arrow,
                    "attempts": attempts,
                    "details": details,
                    "bare_thin": bare_thin,
                }
        except Exception as exc:  # noqa: BLE001
            details["query_public"] = {
                "present": True,
                "error": f"{type(exc).__name__}: {exc}",
            }

    # --- H1b-2: optional private/async inner query (discovery only; do not prefer as path) ---
    # Record result for library confirmation; continue to public head/to_lance so
    # h1b.path on 0.20.0 is typically head_n_full (primary public proof).
    private_async_ok = False
    attempts.append("private_async")
    try:
        inner = getattr(table, "_table", None)
        if inner is not None and hasattr(inner, "query"):
            q = inner.query()
            if hasattr(q, "limit"):
                q = q.limit(n_full)
            arr = _maybe_await(q.to_arrow())
            n = int(arr.num_rows)
            details["private_async"] = {
                "present": True,
                "num_rows": n,
                "note": "discovery only; not preferred h1b.path",
            }
            private_async_ok = n == n_full and bare_thin
        else:
            details["private_async"] = {"present": False}
            attempts[-1] = "private_async_missing"
    except Exception as exc:  # noqa: BLE001
        details["private_async"] = {"error": f"{type(exc).__name__}: {exc}"}

    # --- H1b-3: primary public proof on 0.20.0 — head(n_full) ---
    attempts.append("head_n_full")
    try:
        if n_head is not None and n_head == n_full and bare_thin:
            details["head_n_full"] = {
                "num_rows": n_head,
                "note": "bare to_arrow is default-limit query; head(n) is explicit full/limited read",
            }
            path = "head_n_full"
            return {
                "ok": True,
                "path": path,
                "n_full": n_full,
                "n_arrow": n_arrow,
                "attempts": attempts,
                "details": details,
                "bare_thin": bare_thin,
            }
        # Re-probe if n_head not already full
        ht = table.head(n_full)
        n = int(ht.num_rows)
        details["head_n_full"] = {
            "num_rows": n,
            "note": "bare to_arrow is default-limit query; head(n) is explicit full/limited read",
        }
        if n == n_full and bare_thin:
            path = "head_n_full"
            return {
                "ok": True,
                "path": path,
                "n_full": n_full,
                "n_arrow": n_arrow,
                "attempts": attempts,
                "details": details,
                "bare_thin": bare_thin,
            }
    except Exception as exc:  # noqa: BLE001
        details["head_n_full"] = {"error": f"{type(exc).__name__}: {exc}"}

    # --- H1b-4: to_lance corroboration ---
    attempts.append("to_lance")
    try:
        ds = table.to_lance()
        n = None  # type: int | None
        if hasattr(ds, "count_rows"):
            try:
                n = int(ds.count_rows())
            except Exception:  # noqa: BLE001
                n = None
        if n is None and hasattr(ds, "to_table"):
            n = int(ds.to_table().num_rows)
        details["to_lance"] = {"num_rows": n}
        if n == n_full and bare_thin:
            path = "to_lance"
            return {
                "ok": True,
                "path": path,
                "n_full": n_full,
                "n_arrow": n_arrow,
                "attempts": attempts,
                "details": details,
                "bare_thin": bare_thin,
            }
    except Exception as exc:  # noqa: BLE001
        details["to_lance"] = {"error": f"{type(exc).__name__}: {exc}"}

    # Fall back to private_async success if public paths did not pass.
    if private_async_ok:
        return {
            "ok": True,
            "path": "private_async",
            "n_full": n_full,
            "n_arrow": n_arrow,
            "attempts": attempts,
            "details": details,
            "bare_thin": bare_thin,
        }

    return {
        "ok": False,
        "path": path,
        "n_full": n_full,
        "n_arrow": n_arrow,
        "attempts": attempts,
        "details": details,
        "bare_thin": bare_thin,
    }


def _subprocess_native(uri: str, table_name: str, timeout: float = 60.0) -> dict[str, Any]:
    """Optional native lance.dataset in a subprocess (segfault isolation)."""
    code = (
        "import sys, json\n"
        f"uri = {uri!r}\n"
        f"name = {table_name!r}\n"
        "try:\n"
        "    import lance\n"
        "    # lancedb URI is a dir of tables; dataset is uri/name.lance\n"
        "    from pathlib import Path\n"
        "    p = Path(uri) / f'{name}.lance'\n"
        "    if not p.exists():\n"
        "        p = Path(uri)\n"
        "    ds = lance.dataset(str(p))\n"
        "    n = ds.count_rows()\n"
        "    print(json.dumps({'ok': True, 'num_rows': int(n), 'path': str(p)}))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'ok': False, 'error': f'{type(e).__name__}: {e}'}))\n"
        "    sys.exit(1)\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except OSError as exc:
        return {"ok": False, "error": f"OSError: {exc}"}
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return {
            "ok": False,
            "error": f"empty stdout rc={proc.returncode} stderr={(proc.stderr or '')[:300]}",
        }
    try:
        return json.loads(line[-1])
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": f"bad json rc={proc.returncode}: {line[-1][:200]}",
        }


def run_matrix(
    uri: str,
    *,
    table_name: str = "atoms",
    head_cap: int = _DEFAULT_HEAD_CAP,
    subprocess_native: bool = False,
) -> dict[str, Any]:
    """Connect read-only and run preferred probe order. Never mutates table."""
    _assert_no_deny_calls_in_source()

    t0 = time.perf_counter()
    result: dict[str, Any] = {
        "utc": _utc_now(),
        "safety_class": "R1",
        "uri": uri,
        "table": table_name,
        "packages": _pkg_versions(),
        "deny_list": sorted(_DENY_TABLE_METHODS),
        "probes": {},
        "h1": {},
        "h1a": {},
        "h1b": {},
        "errors": [],
    }

    try:
        import lancedb
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"import lancedb failed: {type(exc).__name__}: {exc}")
        result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        return result

    try:
        db = lancedb.connect(uri)
        names = list(db.table_names())
        result["probes"]["table_names"] = names
        if table_name not in names:
            result["errors"].append(
                f"table {table_name!r} not in {names!r}; open failed"
            )
            result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            return result
        table = db.open_table(table_name)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"connect/open failed: {type(exc).__name__}: {exc}")
        result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        return result

    # Document deny-list presence without calling.
    result["probes"]["deny_methods_present"] = {
        name: hasattr(table, name) for name in sorted(_DENY_TABLE_METHODS)
    }
    result["probes"]["has_public_query"] = bool(
        callable(getattr(table, "query", None))
    )
    result["probes"]["has_scanner"] = hasattr(table, "scanner")

    # 1) count_rows → n_full
    n_full: int | None = None
    try:
        n_full = int(table.count_rows())
        result["probes"]["count_rows"] = {"n_full": n_full}
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"count_rows: {type(exc).__name__}: {exc}")
        result["probes"]["count_rows"] = {"error": str(exc)}

    if n_full is None:
        result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        return result

    head_n = n_full if n_full <= head_cap else min(head_cap, 10_000)
    result["probes"]["head_n_requested"] = head_n

    # 2) head(n_full or cap)
    n_head: int | None = None
    head_ids: list[str] = []
    head_tbl: Any = None
    try:
        head_tbl = table.head(head_n)
        n_head = int(head_tbl.num_rows)
        head_ids = _column_ids(head_tbl)
        result["probes"]["head"] = {
            "n_head": n_head,
            "requested": head_n,
            "atom_ids_prefix": head_ids[:_ID_SAMPLE],
            "kind_hist": _kind_hist(head_tbl),
            "embedding_status_hist": _embedding_status_hist(head_tbl),
        }
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"head: {type(exc).__name__}: {exc}")
        result["probes"]["head"] = {"error": str(exc)}

    # 3) head(10) prefix
    prefix_10: list[str] = []
    try:
        h10 = table.head(10)
        prefix_10 = _column_ids(h10)
        result["probes"]["head_10"] = {
            "atom_ids": prefix_10,
            "num_rows": int(h10.num_rows),
            "kind_hist": _kind_hist(h10),
        }
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"head(10): {type(exc).__name__}: {exc}")
        result["probes"]["head_10"] = {"error": str(exc)}

    # 4) bare to_arrow
    n_arrow: int | None = None
    arrow_ids: list[str] = []
    arrow_tbl: Any = None
    try:
        arrow_tbl = table.to_arrow()
        n_arrow = int(arrow_tbl.num_rows)
        arrow_ids = _column_ids(arrow_tbl)
        result["probes"]["to_arrow"] = {
            "n_arrow": n_arrow,
            "atom_ids": arrow_ids[:_ID_SAMPLE],
            "atom_ids_all": arrow_ids if len(arrow_ids) <= 50 else arrow_ids[:50],
            "kind_hist": _kind_hist(arrow_tbl),
            "embedding_status_hist": _embedding_status_hist(arrow_tbl),
        }
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"to_arrow: {type(exc).__name__}: {exc}")
        result["probes"]["to_arrow"] = {"error": str(exc)}

    # Schema
    try:
        schema_names = list(table.schema.names)
        result["probes"]["schema"] = {
            "names": schema_names,
            "has_emb_columns": _schema_has_emb(schema_names),
        }
    except Exception as exc:  # noqa: BLE001
        result["probes"]["schema"] = {"error": str(exc)}

    # 5) H1a: arrow_ids == head(10) order-sensitive
    h1a_ok = (
        n_arrow is not None
        and prefix_10 is not None
        and arrow_ids == prefix_10
    )
    result["h1a"] = {
        "ok": bool(h1a_ok),
        "arrow_ids": arrow_ids,
        "prefix_10": prefix_10,
        "note": "order-sensitive equality; haiku skew not required",
    }

    # 6) H1b fallback chain (uses n_head from step 2 when full)
    if n_arrow is not None:
        h1b = _run_h1b_chain(
            table, n_full=n_full, n_arrow=n_arrow, n_head=n_head
        )
    else:
        h1b = {
            "ok": False,
            "path": None,
            "n_full": n_full,
            "n_arrow": None,
            "attempts": [],
            "details": {"error": "to_arrow failed"},
            "bare_thin": False,
        }
    result["h1b"] = h1b

    # 7) to_lance (always record; may also satisfy H1b-4)
    try:
        ds = table.to_lance()
        n_lance_count: int | None = None
        n_lance_table: int | None = None
        if hasattr(ds, "count_rows"):
            try:
                n_lance_count = int(ds.count_rows())
            except Exception as exc:  # noqa: BLE001
                result["probes"]["to_lance_count_error"] = str(exc)
        if hasattr(ds, "to_table"):
            try:
                lt = ds.to_table()
                n_lance_table = int(lt.num_rows)
                result["probes"]["to_lance"] = {
                    "count_rows": n_lance_count,
                    "to_table_num_rows": n_lance_table,
                    "atom_ids_prefix": _column_ids(lt)[:_ID_SAMPLE],
                    "kind_hist": _kind_hist(lt),
                }
            except Exception as exc:  # noqa: BLE001
                result["probes"]["to_lance"] = {
                    "count_rows": n_lance_count,
                    "to_table_error": str(exc),
                }
        else:
            result["probes"]["to_lance"] = {"count_rows": n_lance_count}
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"to_lance: {type(exc).__name__}: {exc}")
        result["probes"]["to_lance"] = {"error": str(exc)}

    # 8) list_versions (R1 only)
    try:
        if hasattr(table, "list_versions"):
            versions = table.list_versions()
            n_versions = len(versions) if versions is not None else None
            sample = None
            if versions:
                try:
                    raw = (
                        [versions[0], versions[-1]]
                        if len(versions) > 1
                        else list(versions)[:1]
                    )
                    sample = _jsonable(raw)
                except Exception:  # noqa: BLE001
                    sample = None
            result["probes"]["list_versions"] = {
                "n_versions": n_versions,
                "sample": sample,
            }
        else:
            result["probes"]["list_versions"] = {"present": False}
    except Exception as exc:  # noqa: BLE001
        result["probes"]["list_versions"] = {"error": str(exc)}

    # 9) optional subprocess native
    if subprocess_native:
        result["probes"]["subprocess_native"] = _subprocess_native(uri, table_name)

    # 10) scanner only if present
    if hasattr(table, "scanner"):
        result["probes"]["scanner"] = {
            "present": True,
            "note": "not exercised; allowlist optional",
        }
    else:
        result["probes"]["scanner"] = {
            "present": False,
            "note": "not on sync LanceTable 0.20.0 — skipped",
        }

    # H1 overall
    n_a = n_arrow if n_arrow is not None else -1
    h1_ok = n_full > 0 and n_a >= 0 and n_a < n_full
    result["h1"] = {
        "ok": h1_ok,
        "n_full": n_full,
        "n_arrow": n_arrow,
        "n_head": n_head,
        "note": "bare to_arrow thin vs count_rows/full APIs",
    }

    # Summary convenience fields
    result["summary"] = {
        "n_full": n_full,
        "n_head": n_head,
        "n_arrow": n_arrow,
        "h1_ok": result["h1"]["ok"],
        "h1a_ok": result["h1a"]["ok"],
        "h1b_ok": result["h1b"]["ok"],
        "h1b_path": result["h1b"].get("path"),
        "h4_demoted_if_h1a_h1b": bool(result["h1a"]["ok"] and result["h1b"]["ok"]),
    }

    result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--uri",
        default=os.environ.get("LANCE_DEBUG_URI"),
        help="lancedb connect URI (dir containing atoms table). Default: $LANCE_DEBUG_URI",
    )
    parser.add_argument("--table", default="atoms", help="Table name (default: atoms)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON result to this path (parent dirs created)",
    )
    parser.add_argument(
        "--head-cap",
        type=int,
        default=_DEFAULT_HEAD_CAP,
        help=f"Max rows for head(n_full) materialize (default {_DEFAULT_HEAD_CAP})",
    )
    parser.add_argument(
        "--subprocess-native",
        action="store_true",
        help="Also probe lance.dataset in a subprocess",
    )
    parser.add_argument(
        "--print",
        dest="do_print",
        action="store_true",
        default=True,
        help="Print summary to stdout (default)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print human summary (JSON to --out only)",
    )
    args = parser.parse_args(argv)

    if not args.uri:
        print(
            "error: --uri or LANCE_DEBUG_URI required",
            file=sys.stderr,
        )
        return 2

    uri_path = Path(args.uri)
    if not uri_path.exists():
        print(f"error: URI path does not exist: {args.uri}", file=sys.stderr)
        return 2

    try:
        result = run_matrix(
            str(uri_path),
            table_name=args.table,
            head_cap=args.head_cap,
            subprocess_native=args.subprocess_native,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: probe crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            result, indent=2, sort_keys=True, default=_jsonable
        )
        args.out.write_text(payload + "\n", encoding="utf-8")
        if not args.quiet:
            print(f"wrote {args.out}")

    if not args.quiet:
        s = result.get("summary") or {}
        print("# api_matrix summary")
        print(f"uri: {result.get('uri')}")
        print(f"packages: {result.get('packages')}")
        print(
            f"n_full={s.get('n_full')} n_head={s.get('n_head')} n_arrow={s.get('n_arrow')}"
        )
        print(
            f"H1={s.get('h1_ok')} H1a={s.get('h1a_ok')} "
            f"H1b={s.get('h1b_ok')} path={s.get('h1b_path')}"
        )
        if result.get("errors"):
            print(f"errors: {result['errors']}")
        h1b = result.get("h1b") or {}
        print(f"h1b.attempts={h1b.get('attempts')}")

    # Exit 0 even when H1 holds (probe success ≠ hypothesis fail). Non-zero only on hard errors.
    if result.get("errors") and not (result.get("summary") or {}).get("n_full"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
