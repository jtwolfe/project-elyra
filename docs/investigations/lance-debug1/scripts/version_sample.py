#!/usr/bin/env python3
"""R1 version archaeology sampler for lance-debug1 (P08).

Sample Lance table versions safely. **Read-only only.**

Allowed:
  - Table.list_versions
  - Read-only checkout (if non-mutating)
  - Row-count via count_rows / head / to_lance at sampled versions
  - Optional subprocess lance.dataset for row counts (segfault isolation)

Forbidden (never invoked):
  compact_files, cleanup_old_versions, optimize, delete, drop_table,
  merge_insert, add, create_table (except fixtures elsewhere).

When to run: **after** H1a/H1b (P01). Optional polish if default-limit proven.
H10 residual: only claim historical rewrite if samples show non-monotonic
row-count **collapse**. If latest full APIs already show a large corpus, H10
does **not** explain today's process-thin bug — residual risk for future
migrate/reopen paths that still use bare ``to_arrow``.

Usage (from repo root):
  python docs/investigations/lance-debug1/scripts/version_sample.py \\
    --uri \"$LANCE_DEBUG_URI\" --samples 5 \\
    --out docs/investigations/lance-debug1/evidence/YYYY-MM-DD-run-01/version-sample.json

  # design alias:
  python docs/investigations/lance-debug1/scripts/version_sample.py --dataset PATH --samples 5 --out PATH.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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

# Sample fractions: first, 25%, 50%, 75%, latest (design P08).
_DEFAULT_FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _assert_no_deny_calls_in_source() -> None:
    src = Path(__file__).read_text(encoding="utf-8")
    for name in sorted(_DENY_TABLE_METHODS):
        if f".{name}(" in src:
            raise RuntimeError(
                f"version_sample.py must not call deny-list method .{name}("
            )


def _pkg_versions() -> dict[str, Any]:
    out: dict[str, Any] = {"python": sys.version.split()[0], "executable": sys.executable}
    for name in ("lancedb", "lance", "pyarrow"):
        try:
            mod = __import__(name)
            out[name] = getattr(mod, "__version__", None)
        except Exception as exc:  # noqa: BLE001
            out[name] = f"import_error:{type(exc).__name__}"
    return out


def _jsonable(obj: Any) -> Any:
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


def _version_id(v: Any) -> Any:
    """Extract a version identifier from list_versions entry."""
    if v is None:
        return None
    if isinstance(v, (int, float, str)):
        return v
    if isinstance(v, dict):
        for key in ("version", "id", "version_id", "num"):
            if key in v:
                return v[key]
        return v
    for attr in ("version", "id", "version_id", "num"):
        if hasattr(v, attr):
            try:
                return getattr(v, attr)
            except Exception:  # noqa: BLE001
                pass
    return str(v)


def _version_sort_key(v: Any) -> tuple[int, Any]:
    """Sort key for ascending chronological order (int version preferred).

    Returns (tier, key) so integers sort before non-int fallbacks of the same
    numeric string, and unparseable entries stay stable at the end of their tier.
    """
    vid = _version_id(v)
    if isinstance(vid, bool):
        return (2, str(vid))
    if isinstance(vid, int):
        return (0, vid)
    if isinstance(vid, float):
        return (0, int(vid))
    if isinstance(vid, str):
        try:
            return (0, int(vid))
        except ValueError:
            # Timestamp-ish strings sort lexicographically within tier 1.
            return (1, vid)
    # dict leftovers / unknown
    if isinstance(vid, dict):
        for key in ("timestamp", "ts", "created_at", "mtime"):
            if key in vid:
                return (1, str(vid[key]))
    return (2, str(vid))


def _sort_versions_ascending(versions: list[Any]) -> tuple[list[Any], dict[str, Any]]:
    """Sort list_versions entries ascending by version id/timestamp.

    Design assumes first→latest is chronological. Some lancedb builds may return
    newest-first; sorting avoids labeling growth as H10 collapse.
    """
    meta: dict[str, Any] = {
        "sort_key": "version_id int asc, then str, then repr",
        "n_in": len(versions),
    }
    if not versions:
        meta["sorted"] = True
        meta["order_was"] = "empty"
        return [], meta
    # Detect pre-sort direction (best-effort) for evidence.
    first_k = _version_sort_key(versions[0])
    last_k = _version_sort_key(versions[-1])
    if first_k < last_k:
        meta["order_was"] = "ascending_or_mixed"
    elif first_k > last_k:
        meta["order_was"] = "descending_or_mixed"
    else:
        meta["order_was"] = "equal_endpoints"
    sorted_v = sorted(versions, key=_version_sort_key)
    meta["sorted"] = True
    meta["n_out"] = len(sorted_v)
    meta["first_version_id"] = _jsonable(_version_id(sorted_v[0]))
    meta["latest_version_id"] = _jsonable(_version_id(sorted_v[-1]))
    return sorted_v, meta


def _pick_indices(n: int, samples: int) -> list[int]:
    """Pick sample indices: first, evenly spaced, latest. Always include 0 and n-1."""
    if n <= 0:
        return []
    if n == 1:
        return [0]
    k = max(2, min(samples, n))
    if k >= n:
        return list(range(n))
    # Use design fractions when samples == 5; otherwise linspace.
    if k == 5:
        fracs = _DEFAULT_FRACTIONS
    else:
        fracs = tuple(i / (k - 1) for i in range(k))
    idxs = sorted({min(n - 1, max(0, int(round(f * (n - 1))))) for f in fracs})
    # Ensure endpoints
    if 0 not in idxs:
        idxs = [0] + idxs
    if (n - 1) not in idxs:
        idxs = idxs + [n - 1]
    return sorted(set(idxs))


def _count_at_version_subprocess(
    dataset_path: Path, version: Any, *, timeout: float = 60.0
) -> dict[str, Any]:
    """Optional native lance.dataset row count at version in a child process."""
    code = (
        "import json, sys\n"
        f"path = {str(dataset_path)!r}\n"
        f"ver = {version!r}\n"
        "try:\n"
        "    import lance\n"
        "    kwargs = {}\n"
        "    if ver is not None:\n"
        "        try:\n"
        "            kwargs['version'] = int(ver)\n"
        "        except (TypeError, ValueError):\n"
        "            kwargs['version'] = ver\n"
        "    ds = lance.dataset(path, **kwargs)\n"
        "    n = int(ds.count_rows())\n"
        "    print(json.dumps({'ok': True, 'num_rows': n, 'version': ver}))\n"
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
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
        return json.loads(line) if line else {"ok": False, "error": proc.stderr or "empty"}
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": f"bad_json rc={proc.returncode}",
            "stderr": (proc.stderr or "")[:500],
        }


def _row_count_current(table: Any) -> dict[str, Any]:
    """Best-effort full row count on the currently checked-out table view."""
    out: dict[str, Any] = {}
    try:
        if hasattr(table, "count_rows"):
            out["count_rows"] = int(table.count_rows())
    except Exception as exc:  # noqa: BLE001
        out["count_rows_error"] = f"{type(exc).__name__}: {exc}"
    try:
        if hasattr(table, "to_lance"):
            ds = table.to_lance()
            if hasattr(ds, "count_rows"):
                out["to_lance_count_rows"] = int(ds.count_rows())
            elif hasattr(ds, "to_table"):
                out["to_lance_to_table"] = int(ds.to_table().num_rows)
    except Exception as exc:  # noqa: BLE001
        out["to_lance_error"] = f"{type(exc).__name__}: {exc}"
    # Prefer count_rows; never use bare to_arrow as full count here.
    if "count_rows" in out:
        out["num_rows"] = out["count_rows"]
        out["path"] = "count_rows"
    elif "to_lance_count_rows" in out:
        out["num_rows"] = out["to_lance_count_rows"]
        out["path"] = "to_lance_count_rows"
    elif "to_lance_to_table" in out:
        out["num_rows"] = out["to_lance_to_table"]
        out["path"] = "to_lance_to_table"
    else:
        out["num_rows"] = None
        out["path"] = None
    return out


def _try_checkout(table: Any, version: Any) -> dict[str, Any]:
    """Attempt read-only checkout if available. Never compact/optimize."""
    if not hasattr(table, "checkout"):
        return {"present": False, "note": "checkout not on table"}
    try:
        # Some APIs: table.checkout(version); others return a new handle.
        result = table.checkout(version)
        handle = result if result is not None else table
        counts = _row_count_current(handle)
        return {"present": True, "ok": True, "counts": counts}
    except TypeError:
        # Maybe keyword form
        try:
            result = table.checkout(version=version)
            handle = result if result is not None else table
            counts = _row_count_current(handle)
            return {"present": True, "ok": True, "counts": counts}
        except Exception as exc:  # noqa: BLE001
            return {"present": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"present": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _max_t_start_sample(table: Any, cap: int = 1000) -> str | None:
    """Cheap max t_start sample via head only (not full scan)."""
    try:
        ht = table.head(min(cap, 10_000))
        names = list(ht.schema.names)
        if "t_start" not in names:
            return None
        vals = [v for v in ht.column("t_start").to_pylist() if v]
        return max(str(v) for v in vals) if vals else None
    except Exception:  # noqa: BLE001
        return None


def run_sample(
    uri: Path,
    *,
    table_name: str = "atoms",
    samples: int = 5,
    subprocess_native: bool = False,
    count_versions: bool = True,
) -> dict[str, Any]:
    """List versions and sample row counts. R1 only."""
    import lancedb  # noqa: PLC0415

    t0 = time.perf_counter()
    result: dict[str, Any] = {
        "probe": "version_sample",
        "safety_class": "R1",
        "started_at": _utc_now(),
        "packages": _pkg_versions(),
        "uri": str(uri.resolve()),
        "table": table_name,
        "samples_requested": samples,
        "deny_list": sorted(_DENY_TABLE_METHODS),
        "errors": [],
        "note": (
            "Read-only sampling. Never compact/optimize/cleanup. "
            "H10 residual: non-monotonic historical collapse only; "
            "not active process-thin when latest full APIs already large."
        ),
    }

    # Presence of deny methods on Table (documented; never called).
    deny_presence: dict[str, bool] = {}

    try:
        db = lancedb.connect(str(uri))
        names = list(db.table_names())
        result["table_names"] = names
        if table_name not in names:
            result["errors"].append(f"table {table_name!r} not in {names}")
            result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            return result
        table = db.open_table(table_name)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"connect: {type(exc).__name__}: {exc}")
        result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        return result

    for name in sorted(_DENY_TABLE_METHODS):
        deny_presence[name] = hasattr(table, name)
    result["deny_list_presence"] = deny_presence

    # list_versions
    versions_raw: list[Any] = []
    if not hasattr(table, "list_versions"):
        result["list_versions"] = {"present": False}
        result["errors"].append("list_versions not available on table")
        result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        return result

    try:
        versions_raw = list(table.list_versions() or [])
    except Exception as exc:  # noqa: BLE001
        result["list_versions"] = {"present": True, "error": f"{type(exc).__name__}: {exc}"}
        result["errors"].append(f"list_versions: {type(exc).__name__}: {exc}")
        result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        return result

    # Sort ascending by version id so first/25/50/75/latest fractions are chronological
    # (avoids labeling growth as H10 collapse when API returns newest-first).
    versions_raw, sort_meta = _sort_versions_ascending(versions_raw)
    n_versions = len(versions_raw)
    result["list_versions"] = {
        "present": True,
        "n_versions": n_versions,
        "sort": sort_meta,
        "first": _jsonable(versions_raw[0]) if versions_raw else None,
        "latest": _jsonable(versions_raw[-1]) if versions_raw else None,
    }

    # Current (latest) full count
    current_counts = _row_count_current(table)
    result["current"] = {
        "counts": current_counts,
        "max_t_start_sample": _max_t_start_sample(table),
    }

    idxs = _pick_indices(n_versions, samples)
    dataset_path = uri / f"{table_name}.lance"
    if not dataset_path.exists():
        dataset_path = uri  # some layouts

    samples_out: list[dict[str, Any]] = []
    row_counts: list[int | None] = []

    for idx in idxs:
        entry = versions_raw[idx]
        vid = _version_id(entry)
        sample: dict[str, Any] = {
            "index": idx,
            "fraction": round(idx / max(1, n_versions - 1), 4) if n_versions > 1 else 0.0,
            "version_id": _jsonable(vid),
            "version_entry": _jsonable(entry),
        }

        if count_versions:
            # Prefer checkout if available; else optional subprocess native.
            checkout = _try_checkout(table, vid)
            sample["checkout"] = checkout
            num_rows = None
            if checkout.get("ok") and isinstance(checkout.get("counts"), dict):
                num_rows = checkout["counts"].get("num_rows")

            if num_rows is None and subprocess_native:
                sub = _count_at_version_subprocess(dataset_path, vid)
                sample["subprocess_native"] = sub
                if sub.get("ok"):
                    num_rows = sub.get("num_rows")

            sample["num_rows"] = num_rows
            row_counts.append(int(num_rows) if num_rows is not None else None)
        else:
            sample["num_rows"] = None
            row_counts.append(None)

        samples_out.append(sample)

    result["samples"] = samples_out

    # Monotonicity / H10 residual framing
    known = [c for c in row_counts if c is not None]
    h10: dict[str, Any] = {
        "residual_framing": (
            "H10 is residual risk for future migrate/reopen using bare to_arrow. "
            "It is NOT the active process-thin bug when latest full APIs already "
            "show a large corpus. Supported only for non-monotonic historical "
            "row-count collapse."
        ),
        "migration_sites_still_use_bare_to_arrow": [
            "elyra/memory/lance_store.py:_migrate_vector_schema",
            "elyra/memory/lance_store.py:_promote_staging_table",
        ],
        "latest_num_rows": current_counts.get("num_rows"),
        "sampled_num_rows": known,
    }
    if len(known) >= 2:
        collapses = []
        for i in range(1, len(known)):
            if known[i] < known[i - 1]:
                collapses.append(
                    {"from": known[i - 1], "to": known[i], "drop": known[i - 1] - known[i]}
                )
        h10["non_monotonic_collapses"] = collapses
        h10["historical_collapse"] = bool(collapses)
        # Active process-thin is not H10 if latest is already large.
        latest = current_counts.get("num_rows")
        h10["explains_active_process_thin"] = bool(
            collapses and latest is not None and int(latest) <= 10
        )
        h10["note"] = (
            "If historical_collapse and latest still large → H10 residual only "
            "(past rewrite risk), not today's thin process."
            if collapses and latest is not None and int(latest) > 10
            else (
                "No collapse observed in samples → H10 unsupported for history; "
                "residual bare-to_arrow migrate risk remains for future ops."
                if not collapses
                else "Collapse to thin latest — investigate H10 further."
            )
        )
    else:
        h10["historical_collapse"] = None
        h10["note"] = (
            "Insufficient per-version row counts (checkout/subprocess may be "
            "unavailable). list_versions alone still useful for H3 version growth."
        )

    result["h10"] = h10

    # H3 growth (version count / growth of manifests)
    result["h3"] = {
        "n_versions": n_versions,
        "note": (
            "H3: healthy write / version growth under merge_insert. "
            "Large n_versions with large latest full count supports durable disk."
        ),
        "large_version_history": n_versions >= 10,
    }

    result["summary"] = {
        "n_versions": n_versions,
        "samples": len(samples_out),
        "latest_num_rows": current_counts.get("num_rows"),
        "historical_collapse": h10.get("historical_collapse"),
        "h10_explains_active_process_thin": h10.get("explains_active_process_thin"),
    }
    result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    result["finished_at"] = _utc_now()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--uri",
        default=None,
        help="lancedb connect URI (dir containing atoms table). Default: $LANCE_DEBUG_URI",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help="Alias for --uri (design CLI: version_sample.py --dataset PATH)",
    )
    parser.add_argument("--table", default="atoms", help="Table name (default: atoms)")
    parser.add_argument(
        "--samples",
        type=int,
        default=5,
        help="Number of versions to sample (default 5: first/25/50/75/latest)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON result to this path",
    )
    parser.add_argument(
        "--subprocess-native",
        action="store_true",
        help="Also count rows via lance.dataset in a subprocess (per sample)",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only list_versions metadata; skip per-version row counts",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print human summary",
    )
    args = parser.parse_args(argv)

    try:
        _assert_no_deny_calls_in_source()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    uri_s = args.uri or args.dataset or os.environ.get("LANCE_DEBUG_URI")
    if not uri_s:
        print("error: --uri / --dataset or LANCE_DEBUG_URI required", file=sys.stderr)
        return 2

    uri = Path(uri_s)
    if not uri.exists():
        print(f"error: URI path does not exist: {uri}", file=sys.stderr)
        return 2

    try:
        result = run_sample(
            uri,
            table_name=args.table,
            samples=args.samples,
            subprocess_native=args.subprocess_native,
            count_versions=not args.list_only,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: probe crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(result, indent=2, sort_keys=True, default=_jsonable) + "\n",
            encoding="utf-8",
        )
        if not args.quiet:
            print(f"wrote {args.out}")

    if not args.quiet:
        s = result.get("summary") or {}
        print("# version_sample summary (R1)")
        print(f"uri: {result.get('uri')}")
        print(f"n_versions={s.get('n_versions')} samples={s.get('samples')}")
        print(f"latest_num_rows={s.get('latest_num_rows')}")
        print(
            f"historical_collapse={s.get('historical_collapse')} "
            f"h10_active_thin={s.get('h10_explains_active_process_thin')}"
        )
        if result.get("errors"):
            print(f"errors: {result['errors']}")

    if result.get("errors") and not (result.get("list_versions") or {}).get("n_versions"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
