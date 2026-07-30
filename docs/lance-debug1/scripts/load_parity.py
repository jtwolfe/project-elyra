#!/usr/bin/env python3
"""W1 load-path parity probe for lance-debug1 (P02).

Open LanceMemoryStore against a **marked quarantine** data_dir and compare
process health / ``_by_id`` size to offline ``n_arrow`` / ``n_full``.

Marker (canonical only — KD15):
  Path(data_dir).resolve().parent / ".lance-debug1-quarantine"
  i.e. {data_dir}/../.lance-debug1-quarantine when data_dir ends in …/data

Safety class: **W1** (store open may write meta.json + joint repair).
Never open the live operator store — marker is **required** (never optional).

Prove H2: process atom_count / len(_by_id) tracks bare to_arrow, not n_full.
Disconfirm H5: skip-corrupt log count ≪ gap.

Deny-list (never invoked): merge_insert (except product open side-effects),
add, delete, drop_table, compact_files, cleanup_old_versions, optimize.

Usage (from repo root):
  export LANCE_DEBUG_DATA_DIR=/tmp/lance-q-YYYYMMDD/data
  # marker must exist at $LANCE_DEBUG_DATA_DIR/../.lance-debug1-quarantine
  python docs/lance-debug1/scripts/load_parity.py \\
    --data-dir \"$LANCE_DEBUG_DATA_DIR\" \\
    --api-matrix docs/lance-debug1/evidence/YYYY-MM-DD-run-01/api-matrix.json \\
    --out docs/lance-debug1/evidence/YYYY-MM-DD-run-01/load-parity.json

  # Or let the script take its own R1 counts (still on quarantine URI only):
  python docs/lance-debug1/scripts/load_parity.py --data-dir \"$LANCE_DEBUG_DATA_DIR\" \\
    --out /tmp/load-parity.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Deny-list: names that must never appear as call sites in this module.
# (Product open may internally merge_insert for joint repair — we do not call
# those APIs from this script.)
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

_MARKER_NAME = ".lance-debug1-quarantine"
_ID_SAMPLE = 20


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _assert_no_deny_calls_in_source() -> None:
    """Static self-check: this module must not invoke deny-list APIs."""
    src = Path(__file__).read_text(encoding="utf-8")
    for name in sorted(_DENY_TABLE_METHODS):
        if f".{name}(" in src:
            raise RuntimeError(f"load_parity.py must not call deny-list method .{name}(")


def _pkg_versions() -> dict[str, Any]:
    out: dict[str, Any] = {"python": sys.version.split()[0], "executable": sys.executable}
    for name in ("lancedb", "lance", "pyarrow"):
        try:
            mod = __import__(name)
            out[name] = getattr(mod, "__version__", None)
        except Exception as exc:  # noqa: BLE001
            out[name] = f"import_error:{type(exc).__name__}"
    return out


def _is_bare_thin(n_full: int, n_arrow: int) -> bool:
    """Same thinness rule as api_matrix (n_arrow ≪ n_full)."""
    if n_full <= 0 or n_arrow < 0 or n_arrow >= n_full:
        return False
    if n_arrow <= 10:
        return True
    return n_arrow <= max(1, n_full // 10)


def _jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_jsonable(x) for x in obj]
    return str(obj)


def require_quarantine_marker(data_dir: Path) -> Path:
    """Resolve and require the canonical quarantine marker (KD15).

    Marker path: ``Path(data_dir).resolve().parent / ".lance-debug1-quarantine"``
    i.e. ``{data_dir}/../.lance-debug1-quarantine``.

    Marker is **never** optional for W1 — even with ELYRA_LANCE_ALLOW_WRITE=1.
    """
    data_dir = data_dir.resolve()
    marker = data_dir.parent / _MARKER_NAME
    if not marker.is_file():
        raise SystemExit(
            f"error: W1 quarantine marker required at:\n"
            f"  {marker}\n"
            f"(canonical only — not data/.lance-debug1-quarantine or "
            f"data/memory/.lance-debug1-quarantine)\n"
            f"Run quarantine_copy.sh first; see SAFETY.md KD15."
        )
    return marker


def refuse_live_workspace_data_dir(data_dir: Path) -> None:
    """Refuse if data_dir looks like live workspace data/ (not under /tmp)."""
    resolved = str(data_dir.resolve())
    # Allow explicit /tmp and /var/tmp quarantine layouts.
    if resolved.startswith("/tmp/") or resolved.startswith("/var/tmp/"):
        return
    # Heuristic: ends with /data and lives under a repo-ish tree with elyra.
    if resolved.endswith("/data") or resolved.endswith("/data/"):
        parent = data_dir.resolve().parent
        if (parent / "elyra").is_dir() or (parent / "docs" / "lance-debug1").is_dir():
            raise SystemExit(
                f"error: refusing live workspace data_dir for W1 open:\n"
                f"  {resolved}\n"
                f"Use quarantine: quarantine_copy.sh → /tmp/lance-q-…/data"
            )


def _read_marker(marker: Path) -> dict[str, Any] | None:
    try:
        raw = marker.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"raw": raw.strip()}
    except (OSError, json.JSONDecodeError):
        try:
            return {"raw": marker.read_text(encoding="utf-8").strip()}
        except OSError as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}


def _load_api_matrix(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    if not path.is_file():
        raise SystemExit(f"error: --api-matrix not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"error: failed to read api-matrix: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("error: api-matrix JSON must be an object")
    return data


def _pick_id_list(
    candidates: list[list[Any] | None],
    *,
    expected_len: int | None,
) -> list[str] | None:
    """Prefer an id list whose length matches ``expected_len`` (full materialization).

    api_matrix caps ``atom_ids_all`` at 50; truncated lists must not win over
    full ``h1a.arrow_ids`` or direct R1 when lengths disagree.
    """
    lists: list[list[str]] = []
    for raw in candidates:
        if not raw:
            continue
        lists.append([str(x) for x in raw])
    if not lists:
        return None
    if expected_len is not None and expected_len >= 0:
        for lst in lists:
            if len(lst) == expected_len:
                return lst
    # No exact match: prefer longest (least truncated) candidate.
    return max(lists, key=len)


def _counts_from_api_matrix(am: dict[str, Any]) -> dict[str, Any]:
    summary = am.get("summary") or {}
    probes = am.get("probes") or {}
    n_full = summary.get("n_full")
    n_arrow = summary.get("n_arrow")
    n_head = summary.get("n_head")
    arrow_ids = None
    head_ids = None
    ta = probes.get("to_arrow") if isinstance(probes.get("to_arrow"), dict) else {}
    hd = probes.get("head") if isinstance(probes.get("head"), dict) else {}
    if ta:
        if n_arrow is None and ta.get("n_arrow") is not None:
            n_arrow = ta.get("n_arrow")
    if hd:
        if n_head is None and hd.get("n_head") is not None:
            n_head = hd.get("n_head")
    # Fallback nested shapes from api_matrix
    if n_full is None and isinstance(probes.get("count_rows"), dict):
        cr = probes["count_rows"]
        n_full = cr.get("n_full") or cr.get("n") or cr.get("num_rows")

    try:
        n_arrow_i = int(n_arrow) if n_arrow is not None else None
    except (TypeError, ValueError):
        n_arrow_i = None

    h1a_ids = None
    if isinstance(am.get("h1a"), dict):
        h1a_ids = (am["h1a"] or {}).get("arrow_ids")

    # Prefer full-length lists: h1a.arrow_ids (full when H1a ran) over capped
    # atom_ids_all (PR2 stores [:50] when n_arrow > 50).
    arrow_ids = _pick_id_list(
        [
            h1a_ids,
            ta.get("atom_ids_all") if ta else None,
            ta.get("atom_ids") if ta else None,
            ta.get("ids") if ta else None,
        ],
        expected_len=n_arrow_i,
    )
    head_ids = _pick_id_list(
        [
            hd.get("atom_ids_prefix") if hd else None,
            hd.get("atom_ids") if hd else None,
            hd.get("ids") if hd else None,
        ],
        expected_len=None,  # prefix samples are intentionally short
    )
    # Mark when arrow_ids still look truncated vs n_arrow (caller may fill direct_r1).
    arrow_ids_truncated = bool(
        arrow_ids is not None
        and n_arrow_i is not None
        and n_arrow_i > 0
        and len(arrow_ids) < n_arrow_i
    )
    return {
        "source": "api_matrix",
        "n_full": n_full,
        "n_arrow": n_arrow,
        "n_head": n_head,
        "arrow_ids": list(arrow_ids) if arrow_ids else None,
        "head_ids": list(head_ids) if head_ids else None,
        "arrow_ids_truncated": arrow_ids_truncated,
        "h1_ok": (am.get("h1") or {}).get("ok"),
        "h1a_ok": (am.get("h1a") or {}).get("ok"),
        "h1b_ok": (am.get("h1b") or {}).get("ok"),
        "h1b_path": (am.get("h1b") or {}).get("path"),
    }


def _direct_r1_counts(uri: Path, *, table_name: str = "atoms") -> dict[str, Any]:
    """R1 counts on quarantine URI only — no store open, no deny-list ops."""
    import lancedb  # noqa: PLC0415

    db = lancedb.connect(str(uri))
    names = list(db.table_names())
    if table_name not in names:
        return {
            "source": "direct_r1",
            "error": f"table {table_name!r} not in {names}",
            "n_full": None,
            "n_arrow": None,
            "n_head": None,
            "arrow_ids": None,
            "head_ids": None,
        }
    table = db.open_table(table_name)
    n_full: int | None = None
    n_arrow: int | None = None
    n_head: int | None = None
    arrow_ids: list[str] | None = None
    head_ids: list[str] | None = None
    errors: list[str] = []

    try:
        n_full = int(table.count_rows())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"count_rows: {type(exc).__name__}: {exc}")

    try:
        arr = table.to_arrow()
        n_arrow = int(arr.num_rows)
        names_cols = list(arr.schema.names)
        key = "atom_id" if "atom_id" in names_cols else (names_cols[0] if names_cols else None)
        if key is not None:
            arrow_ids = [str(x) for x in arr.column(key).to_pylist()]
    except Exception as exc:  # noqa: BLE001
        errors.append(f"to_arrow: {type(exc).__name__}: {exc}")

    try:
        req = n_full if n_full is not None else 10_000
        ht = table.head(req)
        n_head = int(ht.num_rows)
        names_cols = list(ht.schema.names)
        key = "atom_id" if "atom_id" in names_cols else (names_cols[0] if names_cols else None)
        if key is not None:
            head_ids = [str(x) for x in ht.column(key).to_pylist()[:_ID_SAMPLE]]
    except Exception as exc:  # noqa: BLE001
        errors.append(f"head: {type(exc).__name__}: {exc}")

    return {
        "source": "direct_r1",
        "n_full": n_full,
        "n_arrow": n_arrow,
        "n_head": n_head,
        "arrow_ids": arrow_ids,
        "head_ids": head_ids,
        "errors": errors or None,
    }


def _build_elyra_paths(data_dir: Path):
    """Build ElyraPaths with data_dir = quarantine data dir (W1)."""
    from elyra.config import ElyraPaths  # noqa: PLC0415

    data_dir = data_dir.resolve()
    # Synthetic home: parent of data_dir (quarantine root layout).
    home = data_dir.parent
    return ElyraPaths(
        home=home,
        model_dir=home / "model",
        data_dir=data_dir,
        skills_dir=home / "skills",
        tools_dir=home / "tools",
        prompts_dir=home / "prompts",
    )


def _open_store(data_dir: Path):
    """Open LanceMemoryStore only (no soft fall-back to jsonl)."""
    from elyra.memory.config import MemorySettings  # noqa: PLC0415
    from elyra.memory.lance_store import LanceMemoryStore  # noqa: PLC0415

    paths = _build_elyra_paths(data_dir)
    # joint_repair_max_per_open left at product default — open is classified W1
    # precisely because repair may write; quarantine only.
    settings = MemorySettings(backend="lance")
    store = LanceMemoryStore(paths, settings)
    return store, paths, settings


class _SkipCounter(logging.Handler):
    """Count 'skipping corrupt lance row' warnings from LanceMemoryStore._load.

    Attach **only** to ``logging.getLogger("elyra.memory.lance_store")`` — not
    also to the root logger. Product loggers propagate by default; dual-attach
    double-counts each warning and can corrupt H5 thresholds.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "skipping corrupt lance row" in msg:
            self.count += 1
            if len(self.messages) < 50:
                self.messages.append(msg)


def _smoke_skip_counter_once() -> None:
    """Sanity: one warning → count == 1 when handler is on lance_store only."""
    handler = _SkipCounter()
    lance_logger = logging.getLogger("elyra.memory.lance_store")
    # Avoid dual-count if root also has handlers that re-emit (we only attach once).
    lance_logger.addHandler(handler)
    try:
        lance_logger.warning("skipping corrupt lance row atom_id=%r", "smoke")
        if handler.count != 1:
            raise RuntimeError(
                f"_SkipCounter smoke failed: expected count=1, got {handler.count}"
            )
    finally:
        lance_logger.removeHandler(handler)


def run_parity(
    data_dir: Path,
    *,
    api_matrix_path: Path | None = None,
    table_name: str = "atoms",
    take_direct_counts: bool = True,
) -> dict[str, Any]:
    """Run W1 load parity. Caller must have already checked marker."""
    t0 = time.perf_counter()
    data_dir = data_dir.resolve()
    marker = require_quarantine_marker(data_dir)
    refuse_live_workspace_data_dir(data_dir)

    result: dict[str, Any] = {
        "probe": "load_parity",
        "safety_class": "W1",
        "started_at": _utc_now(),
        "packages": _pkg_versions(),
        "data_dir": str(data_dir),
        "marker": str(marker),
        "marker_body": _read_marker(marker),
        "table": table_name,
        "errors": [],
    }

    # Offline counts: prefer api_matrix; optionally also direct R1 on quarantine URI.
    am = _load_api_matrix(api_matrix_path)
    offline: dict[str, Any]
    if am is not None:
        offline = _counts_from_api_matrix(am)
        offline["api_matrix_path"] = str(api_matrix_path)
        result["api_matrix_summary"] = {
            "h1_ok": offline.get("h1_ok"),
            "h1a_ok": offline.get("h1a_ok"),
            "h1b_ok": offline.get("h1b_ok"),
            "h1b_path": offline.get("h1b_path"),
        }
    else:
        offline = {
            "source": None,
            "n_full": None,
            "n_arrow": None,
            "n_head": None,
            "arrow_ids": None,
            "head_ids": None,
        }

    lance_uri = data_dir / "memory" / "lance"
    result["lance_uri"] = str(lance_uri)
    if take_direct_counts and lance_uri.is_dir():
        try:
            direct = _direct_r1_counts(lance_uri, table_name=table_name)
            result["direct_r1"] = direct
            # Fill gaps from direct when api_matrix missing fields.
            # Also replace truncated arrow_ids (api_matrix atom_ids_all cap 50)
            # when direct provides a full-length list.
            for key in ("n_full", "n_arrow", "n_head", "head_ids"):
                if offline.get(key) is None and direct.get(key) is not None:
                    offline[key] = direct[key]
                    if offline.get("source") is None:
                        offline["source"] = "direct_r1"
                    elif offline["source"] == "api_matrix":
                        offline["source"] = "api_matrix+direct_r1"
            # arrow_ids: prefer full-length over truncated api_matrix sample
            try:
                n_arrow_i = (
                    int(offline["n_arrow"]) if offline.get("n_arrow") is not None else None
                )
            except (TypeError, ValueError):
                n_arrow_i = None
            direct_ids = direct.get("arrow_ids")
            offline_ids = offline.get("arrow_ids")
            need_ids = offline_ids is None or bool(offline.get("arrow_ids_truncated"))
            if need_ids and direct_ids:
                chosen = _pick_id_list(
                    [direct_ids, offline_ids],
                    expected_len=n_arrow_i,
                )
                if chosen is not None and (
                    offline_ids is None or len(chosen) > len(offline_ids)
                ):
                    offline["arrow_ids"] = chosen
                    offline["arrow_ids_truncated"] = bool(
                        n_arrow_i is not None and len(chosen) < n_arrow_i
                    )
                    if offline.get("source") is None:
                        offline["source"] = "direct_r1"
                    elif offline["source"] == "api_matrix":
                        offline["source"] = "api_matrix+direct_r1"
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"direct_r1: {type(exc).__name__}: {exc}")

    result["offline"] = offline

    # --- W1 open ---
    # Attach skip handler ONLY to the product logger (not root): product uses
    # getLogger(__name__) with propagate=True; dual-attach double-counts H5.
    skip_handler = _SkipCounter()
    lance_logger = logging.getLogger("elyra.memory.lance_store")
    lance_logger.addHandler(skip_handler)
    store = None
    try:
        store, paths, settings = _open_store(data_dir)
        health = store.health()
        by_id = getattr(store, "_by_id", {}) or {}
        process_ids = sorted(str(k) for k in by_id.keys())
        process_count = int(health.get("atom_count", len(process_ids)))

        result["process"] = {
            "health": health,
            "atom_count": process_count,
            "by_id_len": len(process_ids),
            "id_sample": process_ids[:_ID_SAMPLE],
            "id_set_sha256": hashlib.sha256(
                ("\n".join(process_ids)).encode("utf-8")
            ).hexdigest()
            if process_ids
            else None,
            "vectors_ready": health.get("vectors_ready"),
            "lance_dir": health.get("lance_dir"),
            "backend": health.get("backend"),
        }
        result["paths"] = {
            "data_dir": str(paths.data_dir),
            "memory_dir": str(paths.data_dir / "memory"),
            "lance_dir": str(paths.data_dir / "memory" / "lance"),
            "settings_backend": settings.backend,
        }
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"open_store: {type(exc).__name__}: {exc}")
        result["open_traceback"] = traceback.format_exc()
        result["process"] = None
        process_count = -1
        process_ids = []
    finally:
        lance_logger.removeHandler(skip_handler)
        if store is not None:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass

    result["skip_corrupt"] = {
        "count": skip_handler.count,
        "messages": skip_handler.messages,
        "note": "H5: expect skip count ≪ gap between n_full and process count",
    }

    # --- H2 / H5 verdicts ---
    n_full = offline.get("n_full")
    n_arrow = offline.get("n_arrow")
    try:
        n_full_i = int(n_full) if n_full is not None else None
    except (TypeError, ValueError):
        n_full_i = None
    try:
        n_arrow_i = int(n_arrow) if n_arrow is not None else None
    except (TypeError, ValueError):
        n_arrow_i = None

    h2: dict[str, Any] = {
        "ok": False,
        "process_atom_count": process_count if process_count >= 0 else None,
        "n_arrow": n_arrow_i,
        "n_full": n_full_i,
        "note": (
            "H2: process _by_id / health.atom_count tracks bare to_arrow (n_arrow), "
            "not n_full (count_rows / head full)"
        ),
    }
    if process_count >= 0 and n_arrow_i is not None and n_full_i is not None:
        # Allow small tolerance (joint repair / skip) vs exact arrow count.
        tracks_arrow = abs(process_count - n_arrow_i) <= max(2, n_arrow_i // 10)
        thin_vs_full = _is_bare_thin(n_full_i, process_count) or (
            process_count < n_full_i and n_arrow_i <= 10 and process_count <= n_arrow_i + 2
        )
        # Strong H2: process ≈ n_arrow AND process ≪ n_full
        h2["tracks_arrow"] = tracks_arrow
        h2["thin_vs_full"] = bool(thin_vs_full and process_count < n_full_i)
        h2["ok"] = bool(tracks_arrow and process_count < n_full_i and _is_bare_thin(n_full_i, n_arrow_i))
        h2["gap_full_minus_process"] = n_full_i - process_count
        h2["gap_process_minus_arrow"] = process_count - n_arrow_i

    # Id-set compare (when arrow ids available)
    arrow_ids = offline.get("arrow_ids") or []
    if process_ids and arrow_ids:
        pset = set(process_ids)
        aset = set(str(x) for x in arrow_ids)
        h2["id_compare"] = {
            "process_only": sorted(pset - aset)[:_ID_SAMPLE],
            "arrow_only": sorted(aset - pset)[:_ID_SAMPLE],
            "intersection_size": len(pset & aset),
            "process_size": len(pset),
            "arrow_size": len(aset),
            "process_equals_arrow": pset == aset,
        }

    result["h2"] = h2

    # H5: skip path is not the mass-drop explanation
    gap = h2.get("gap_full_minus_process")
    skip_n = skip_handler.count
    h5: dict[str, Any] = {
        "skip_count": skip_n,
        "gap_full_minus_process": gap,
        "note": "H5 disconfirm if skip_count ≪ gap (mass missing rows not corrupt-skip)",
    }
    if gap is not None and gap > 0:
        h5["disconfirmed"] = skip_n < max(1, gap // 10)
        h5["ok_as_mass_drop"] = skip_n >= max(1, gap // 2)  # would support H5
    else:
        h5["disconfirmed"] = None
        h5["ok_as_mass_drop"] = False
    result["h5"] = h5

    result["summary"] = {
        "process_atom_count": h2.get("process_atom_count"),
        "n_arrow": n_arrow_i,
        "n_full": n_full_i,
        "h2_ok": h2.get("ok"),
        "h5_disconfirmed": h5.get("disconfirmed"),
        "skip_corrupt": skip_n,
        "marker": str(marker),
        "safety_class": "W1",
    }
    result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    result["finished_at"] = _utc_now()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Quarantine data_dir (…/data). Default: $LANCE_DEBUG_DATA_DIR",
    )
    parser.add_argument(
        "--paths-root",
        default=None,
        help="Quarantine root; data_dir becomes PATHS_ROOT/data (alias for design CLI)",
    )
    parser.add_argument(
        "--api-matrix",
        type=Path,
        default=None,
        help="Optional prior api_matrix.json for n_full / n_arrow / ids",
    )
    parser.add_argument(
        "--table",
        default="atoms",
        help="Table name (default: atoms)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON result to this path",
    )
    parser.add_argument(
        "--no-direct-counts",
        action="store_true",
        help="Do not take direct R1 counts on quarantine URI (api_matrix only)",
    )
    parser.add_argument(
        "--fail-on-h2-signature",
        action="store_true",
        help=(
            "Exit 2 when H2 signature holds (process ≈ n_arrow ≪ n_full). "
            "Useful for CI fixture smoking-gun; default is exit 0 on probe success."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print human summary",
    )
    args = parser.parse_args(argv)

    try:
        _assert_no_deny_calls_in_source()
        _smoke_skip_counter_once()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Resolve data_dir: --data-dir > --paths-root/data > $LANCE_DEBUG_DATA_DIR only.
    # Do NOT fall back to ELYRA_DATA_DIR (often live operator tree; footgun).
    if args.data_dir:
        data_dir = Path(args.data_dir)
    elif args.paths_root:
        data_dir = Path(args.paths_root) / "data"
    else:
        env_dd = os.environ.get("LANCE_DEBUG_DATA_DIR")
        if not env_dd:
            print(
                "error: --data-dir / --paths-root or LANCE_DEBUG_DATA_DIR required",
                file=sys.stderr,
            )
            return 2
        data_dir = Path(env_dd)

    if not data_dir.is_dir():
        print(f"error: data_dir is not a directory: {data_dir}", file=sys.stderr)
        return 2

    # Early marker check with clear error (also re-checked in run_parity)
    try:
        require_quarantine_marker(data_dir)
        refuse_live_workspace_data_dir(data_dir)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        result = run_parity(
            data_dir,
            api_matrix_path=args.api_matrix,
            table_name=args.table,
            take_direct_counts=not args.no_direct_counts,
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
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
        print("# load_parity summary (W1)")
        print(f"data_dir: {result.get('data_dir')}")
        print(f"marker: {result.get('marker')}")
        print(
            f"process={s.get('process_atom_count')} "
            f"n_arrow={s.get('n_arrow')} n_full={s.get('n_full')}"
        )
        print(
            f"H2={s.get('h2_ok')} H5_disconfirmed={s.get('h5_disconfirmed')} "
            f"skip_corrupt={s.get('skip_corrupt')}"
        )
        if result.get("errors"):
            print(f"errors: {result['errors']}")

    if result.get("process") is None and result.get("errors"):
        return 1

    if args.fail_on_h2_signature and (result.get("h2") or {}).get("ok"):
        if not args.quiet:
            print(
                "H2 signature confirmed (process ≈ n_arrow ≪ n_full); "
                "exiting 2 (--fail-on-h2-signature)",
                file=sys.stderr,
            )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
