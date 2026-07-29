#!/usr/bin/env python3
"""Build a tiny synthetic lancedb atoms table for hermetic api_matrix tests.

Safety: fixture builder only — create_table is allowed here (SAFETY deny-list
exception for scripts/fixtures/). Never run against live operator data.

Usage:
  python docs/lance-debug1/scripts/fixtures/build_tiny_atoms.py --out /tmp/tiny-lance
  # writes /tmp/tiny-lance/ (lancedb URI with table "atoms", 25 rows)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build(out_dir: Path, *, n_rows: int = 25) -> dict:
    import lancedb
    import pyarrow as pa

    out_dir = out_dir.resolve()
    if out_dir.exists():
        # Only wipe if it looks like our fixture (has marker) or is empty-ish.
        marker = out_dir / ".lance-debug1-fixture"
        if out_dir.is_dir() and not any(out_dir.iterdir()):
            pass
        elif marker.is_file():
            import shutil

            shutil.rmtree(out_dir)
        else:
            raise SystemExit(
                f"refusing to overwrite non-fixture dir: {out_dir} "
                f"(no .lance-debug1-fixture marker)"
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(out_dir))
    ids = [f"fix{i:03d}" for i in range(n_rows)]
    kinds = (["summary"] * 15 + ["tool"] * 10)[:n_rows]
    if len(kinds) < n_rows:
        kinds = kinds + ["observation"] * (n_rows - len(kinds))
    table = pa.table(
        {
            "atom_id": ids,
            "kind": kinds,
            "content_text": [f"row-{i}" for i in range(n_rows)],
        }
    )
    db.create_table("atoms", table)
    marker = {
        "kind": "lance-debug1-fixture",
        "n_rows": n_rows,
        "table": "atoms",
        "note": "synthetic; for tests only",
    }
    (out_dir / ".lance-debug1-fixture").write_text(
        json.dumps(marker, indent=2) + "\n", encoding="utf-8"
    )
    return {"uri": str(out_dir), "n_rows": n_rows, "table": "atoms"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output lancedb URI directory",
    )
    parser.add_argument("--rows", type=int, default=25, help="Row count (default 25)")
    args = parser.parse_args(argv)
    try:
        info = build(args.out, n_rows=args.rows)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
