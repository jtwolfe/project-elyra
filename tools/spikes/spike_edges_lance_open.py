#!/usr/bin/env python3
"""P0.5 spike: characterize open of dogfood edges.lance (copy-on-read preferred).

Does NOT touch operator data/memory for write. Default path is a /tmp copy.

Phases (subprocess + timeout so hang/segfault do not stick the parent):
  A  lance.dataset.Dataset open + count_rows on edges.lance path
  B  lancedb.connect(lance_root).open_table("edges") + count_rows
  C  elyra open_edge_store(backend=lance, fail_soft=False) + health parity

Usage:
  python tools/spikes/spike_edges_lance_open.py \\
      --table /tmp/grok-1000/edges-spike/edges.lance \\
      --timeout-s 90 \\
      --json-out /tmp/grok-1000/spike-edges-open-results.json
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path


def _phase_a(table: Path) -> dict:
    t0 = time.perf_counter()
    out: dict = {"phase": "A_lance_dataset", "table": str(table)}
    try:
        import lance  # type: ignore

        out["lance_version"] = getattr(lance, "__version__", "?")
        t_open0 = time.perf_counter()
        ds = lance.dataset(str(table))
        out["open_s"] = round(time.perf_counter() - t_open0, 3)
        t_c0 = time.perf_counter()
        try:
            n = ds.count_rows()
        except Exception:
            n = ds.scanner().count_rows() if hasattr(ds, "scanner") else None
        out["count_rows_s"] = round(time.perf_counter() - t_c0, 3)
        out["count_rows"] = int(n) if n is not None else None
        try:
            out["version"] = int(ds.version) if hasattr(ds, "version") else None
        except Exception as exc:  # noqa: BLE001
            out["version_error"] = f"{type(exc).__name__}: {exc}"
        try:
            frags = list(ds.get_fragments()) if hasattr(ds, "get_fragments") else None
            out["fragment_count"] = len(frags) if frags is not None else None
        except Exception as exc:  # noqa: BLE001
            out["fragment_error"] = f"{type(exc).__name__}: {exc}"
        out["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "error"
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()[-2000:]
    out["wall_s"] = round(time.perf_counter() - t0, 3)
    return out


def _phase_b(lance_root: Path) -> dict:
    t0 = time.perf_counter()
    out: dict = {"phase": "B_lancedb_open_table", "lance_root": str(lance_root)}
    try:
        import lancedb  # type: ignore

        out["lancedb_version"] = getattr(lancedb, "__version__", "?")
        t_open0 = time.perf_counter()
        db = lancedb.connect(str(lance_root))
        names = list(db.table_names())
        out["table_names"] = names
        if "edges" not in names:
            out["status"] = "missing_table"
            out["open_s"] = round(time.perf_counter() - t_open0, 3)
        else:
            table = db.open_table("edges")
            out["open_s"] = round(time.perf_counter() - t_open0, 3)
            t_c0 = time.perf_counter()
            n = int(table.count_rows()) if hasattr(table, "count_rows") else None
            out["count_rows_s"] = round(time.perf_counter() - t_c0, 3)
            out["count_rows"] = n
            out["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        out["status"] = "error"
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()[-2000:]
    out["wall_s"] = round(time.perf_counter() - t0, 3)
    return out


def _phase_c(data_home: Path) -> dict:
    """Open via Elyra factory with data_home pointing at parent of data/."""
    t0 = time.perf_counter()
    out: dict = {"phase": "C_open_edge_store", "data_home": str(data_home)}
    try:
        # Ensure repo import works when launched from tools/spikes
        repo = Path(__file__).resolve().parents[2]
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))

        from elyra.config import resolve_paths
        from elyra.memory.config import MemorySettings
        from elyra.memory.edges import UnavailableEdgeStore, open_edge_store

        paths = resolve_paths(data_home)
        paths.ensure_data_dirs()
        cfg = MemorySettings(backend="lance")
        t_open0 = time.perf_counter()
        store = open_edge_store(paths, cfg, fail_soft=False)
        out["open_s"] = round(time.perf_counter() - t_open0, 3)
        if isinstance(store, UnavailableEdgeStore):
            out["status"] = "unavailable"
            out["reason"] = getattr(store, "reason", None)
            out["health"] = store.health()
        else:
            t_h0 = time.perf_counter()
            health = store.health()
            out["health_s"] = round(time.perf_counter() - t_h0, 3)
            out["health"] = health
            out["ram_edge_count"] = health.get("edge_count")
            out["disk_edge_count"] = health.get("disk_edge_count")
            out["edge_count_parity"] = health.get("edge_count_parity")
            out["edges_by_kind"] = health.get("edges_by_kind")
            out["status"] = "ok" if health.get("ok") else "health_not_ok"
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        out["status"] = "error"
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()[-2000:]
    out["wall_s"] = round(time.perf_counter() - t0, 3)
    return out


def _run_child(phase: str, arg: str, timeout_s: float) -> dict:
    """Run one phase in a fresh process; classify timeout / segfault."""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    cmd = [sys.executable, __file__, "--child", phase, arg]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "phase": phase,
            "status": "timeout",
            "class_hint": "C1_open_hang_or_C6",
            "timeout_s": timeout_s,
            "wall_s": round(time.perf_counter() - t0, 3),
            "stdout_tail": (exc.stdout or "")[-1500:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-1500:] if isinstance(exc.stderr, str) else "",
        }

    wall = round(time.perf_counter() - t0, 3)
    rc = proc.returncode
    # Negative returncode on Unix = killed by signal (-11 = SIGSEGV)
    sig = -rc if rc is not None and rc < 0 else None
    base: dict = {
        "phase": phase,
        "returncode": rc,
        "signal": sig,
        "signal_name": signal.Signals(-rc).name if sig else None,
        "wall_s": wall,
        "stderr_tail": (proc.stderr or "")[-1500:],
    }
    if sig == signal.SIGSEGV:
        base["status"] = "segfault"
        base["class_hint"] = "C_segfault"
        return base
    if sig is not None:
        base["status"] = "signal"
        base["class_hint"] = f"C_signal_{base['signal_name']}"
        return base
    if rc != 0:
        base["status"] = "child_nonzero"
        base["stdout_tail"] = (proc.stdout or "")[-2000:]
        return base
    # Child prints one JSON object on stdout
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        payload["parent_wall_s"] = wall
        payload["returncode"] = rc
        return payload
    except Exception as exc:  # noqa: BLE001
        base["status"] = "bad_child_json"
        base["error"] = f"{type(exc).__name__}: {exc}"
        base["stdout_tail"] = (proc.stdout or "")[-2000:]
        return base


def _fs_stats(table: Path) -> dict:
    data_dir = table / "data"
    versions = table / "_versions"
    data_files = list(data_dir.glob("*")) if data_dir.is_dir() else []
    version_files = list(versions.glob("*")) if versions.is_dir() else []
    # du via walk
    total = 0
    for root, _dirs, files in os.walk(table):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return {
        "table": str(table),
        "exists": table.is_dir(),
        "size_bytes": total,
        "size_mb": round(total / (1024 * 1024), 1),
        "data_file_count": len(data_files),
        "version_file_count": len(version_files),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--table",
        type=Path,
        default=Path("/tmp/grok-1000/edges-spike/edges.lance"),
        help="Path to edges.lance directory (prefer /tmp copy)",
    )
    ap.add_argument("--timeout-s", type=float, default=90.0)
    ap.add_argument(
        "--json-out",
        type=Path,
        default=Path("/tmp/grok-1000/spike-edges-open-results.json"),
    )
    ap.add_argument(
        "--skip-elyra",
        action="store_true",
        help="Skip phase C (open_edge_store)",
    )
    ap.add_argument("--child", nargs=2, metavar=("PHASE", "ARG"), help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.child:
        phase, arg = args.child
        if phase == "A":
            result = _phase_a(Path(arg))
        elif phase == "B":
            result = _phase_b(Path(arg))
        elif phase == "C":
            result = _phase_c(Path(arg))
        else:
            result = {"status": "unknown_phase", "phase": phase}
        print(json.dumps(result), flush=True)
        return 0 if result.get("status") in ("ok", "unavailable", "health_not_ok", "missing_table") else 1

    table = args.table.resolve()
    lance_root = table.parent  # .../lance/
    # For Elyra: need ELYRA_HOME-like root whose data/memory/lance/edges.lance exists
    # table = <home>/data/memory/lance/edges.lance → home = parents[3]
    # If we only have /tmp/.../edges.lance, build a synthetic home.
    if table.name == "edges.lance" and table.parent.name == "lance":
        data_home = table.parents[2]  # .../data's parent? lance→memory→data→home
        # table.parent = lance, .parent = memory, .parent = data, .parent = home
        data_home = table.parents[3] if len(table.parents) >= 4 else table.parents[2]
    else:
        # Synthetic: /tmp/xxx/edges.lance → make /tmp/xxx_home/data/memory/lance/edges.lance
        synth = Path("/tmp/grok-1000/edges-spike-home")
        target = synth / "data" / "memory" / "lance" / "edges.lance"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                # Prefer symlink to avoid second 348MB copy
                try:
                    if target.is_symlink() or target.exists():
                        pass
                    else:
                        os.symlink(table, target)
                except OSError:
                    import shutil

                    shutil.copytree(table, target)
        data_home = synth
        # Also re-point lance_root/table for B to the linked path under synth if used
        table = target
        lance_root = table.parent

    # If operator-style layout not present under table path, always use synth home
    expected = data_home / "data" / "memory" / "lance" / "edges.lance"
    if not expected.is_dir():
        synth = Path("/tmp/grok-1000/edges-spike-home")
        target = synth / "data" / "memory" / "lance" / "edges.lance"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            try:
                os.symlink(table if table.exists() else args.table.resolve(), target)
            except OSError:
                import shutil

                shutil.copytree(args.table.resolve(), target)
        data_home = synth
        table = target.resolve() if target.is_symlink() else target
        # Keep using the real path for open
        table = Path(os.path.realpath(target))
        lance_root = table.parent

    report: dict = {
        "spike": "P0.5 dogfood edges.lance open class",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version.split()[0],
        "timeout_s": args.timeout_s,
        "fs": _fs_stats(table),
        "layout": {
            "table": str(table),
            "lance_root": str(lance_root),
            "data_home": str(data_home),
        },
        "phases": {},
    }

    print(f"[spike] fs={json.dumps(report['fs'])}", flush=True)
    print(f"[spike] phase A timeout={args.timeout_s}s …", flush=True)
    report["phases"]["A"] = _run_child("A", str(table), args.timeout_s)
    print(f"[spike] A → {report['phases']['A'].get('status')}", flush=True)

    print(f"[spike] phase B timeout={args.timeout_s}s …", flush=True)
    report["phases"]["B"] = _run_child("B", str(lance_root), args.timeout_s)
    print(f"[spike] B → {report['phases']['B'].get('status')}", flush=True)

    if not args.skip_elyra:
        print(f"[spike] phase C timeout={args.timeout_s}s …", flush=True)
        report["phases"]["C"] = _run_child("C", str(data_home), args.timeout_s)
        print(f"[spike] C → {report['phases']['C'].get('status')}", flush=True)

    report["classification"] = _classify(report)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["classification"], indent=2), flush=True)
    print(f"[spike] wrote {args.json_out}", flush=True)
    return 0


def _classify(report: dict) -> dict:
    """Map phase outcomes to failure/success class from warm-on-start design."""
    phases = report.get("phases") or {}
    a = phases.get("A") or {}
    b = phases.get("B") or {}
    c = phases.get("C") or {}
    classes: list[str] = []
    notes: list[str] = []

    for label, p in (("A", a), ("B", b), ("C", c)):
        st = p.get("status")
        if st == "segfault":
            classes.append("C_segfault")
            notes.append(f"{label}: segfault on open/load")
        elif st == "timeout":
            classes.append("C1_open_hang")  # design C6 open hang; inventory also C1 sticky
            notes.append(f"{label}: timeout after {p.get('timeout_s')}s (open hang)")
        elif st == "error":
            err = (p.get("error") or "").lower()
            if "materialize" in err:
                classes.append("C2_materialize_fail")
                notes.append(f"{label}: materialize fail: {p.get('error')}")
            else:
                classes.append("C_open_error")
                notes.append(f"{label}: error: {p.get('error')}")
        elif st == "unavailable":
            classes.append("C1_unavailable_soft_fail")
            notes.append(f"{label}: UnavailableEdgeStore reason={p.get('reason')}")
        elif st in ("ok", "health_not_ok"):
            ram = p.get("ram_edge_count")
            disk = p.get("disk_edge_count")
            parity = p.get("edge_count_parity")
            if ram is not None and disk is not None:
                if ram == 0 and disk and disk > 0:
                    classes.append("C3_empty_RAM_disk_mismatch")
                    notes.append(f"{label}: RAM=0 disk={disk}")
                elif parity is False:
                    classes.append("C8_parity_mismatch")
                    notes.append(f"{label}: parity false ram={ram} disk={disk}")
                elif ram and ram > 0 and parity is True:
                    classes.append("C4_ok")
                    notes.append(f"{label}: open ok ram={ram} disk={disk} parity=true")
                else:
                    classes.append("C4_ok_or_empty")
                    notes.append(f"{label}: open status={st} ram={ram} disk={disk}")
            elif p.get("count_rows") is not None:
                n = p.get("count_rows")
                if n and n > 0:
                    classes.append("C4_ok_dataset")
                    notes.append(f"{label}: count_rows={n} open_s={p.get('open_s')}")
                else:
                    classes.append("C_empty_table")
                    notes.append(f"{label}: count_rows={n}")
            else:
                notes.append(f"{label}: status={st} without counts")

    # Fragment scale note
    fs = report.get("fs") or {}
    frag = fs.get("data_file_count")
    if frag and frag > 500:
        classes.append("C3_fragment_explosion")
        notes.append(
            f"fs: data_files={frag} versions={fs.get('version_file_count')} "
            f"size_mb={fs.get('size_mb')} (fragment explosion risk)"
        )

    # Primary class: first hard failure, else ok
    primary = "unknown"
    priority = [
        "C_segfault",
        "C1_open_hang",
        "C2_materialize_fail",
        "C1_unavailable_soft_fail",
        "C3_empty_RAM_disk_mismatch",
        "C8_parity_mismatch",
        "C_open_error",
        "C4_ok",
        "C4_ok_dataset",
        "C4_ok_or_empty",
        "C_empty_table",
    ]
    for cand in priority:
        if cand in classes:
            primary = cand
            break

    return {
        "primary_class": primary,
        "all_classes": sorted(set(classes)),
        "notes": notes,
        "open_times_s": {
            "A_open": a.get("open_s"),
            "A_wall": a.get("wall_s") or a.get("parent_wall_s"),
            "B_open": b.get("open_s"),
            "B_wall": b.get("wall_s") or b.get("parent_wall_s"),
            "C_open": c.get("open_s"),
            "C_wall": c.get("wall_s") or c.get("parent_wall_s"),
        },
        "counts": {
            "A_count_rows": a.get("count_rows"),
            "A_fragments": a.get("fragment_count"),
            "B_count_rows": b.get("count_rows"),
            "C_ram": c.get("ram_edge_count"),
            "C_disk": c.get("disk_edge_count"),
            "C_parity": c.get("edge_count_parity"),
            "C_by_kind": c.get("edges_by_kind"),
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
