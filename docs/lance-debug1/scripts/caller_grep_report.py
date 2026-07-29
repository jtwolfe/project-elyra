#!/usr/bin/env python3
"""R0 helper: grep to_arrow / related Lance call sites for TO-ARROW-CALLERS.md.

Usage (from repo root):
  python docs/lance-debug1/scripts/caller_grep_report.py
  python docs/lance-debug1/scripts/caller_grep_report.py --root /path/to/repo

Does not import elyra or open any Lance URI. Inspection-only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Patterns of interest for the inspection package.
_LINE_RE = re.compile(
    r"to_arrow|count_rows|\.head\s*\(|to_lance|\.scanner\s*\(|merge_insert"
)

# Paths relative to repo root to search (memory product surface).
_DEFAULT_GLOBS = (
    "elyra/memory/**/*.py",
    "elyra/presence/worker.py",
    "elyra/runtime/api.py",
    "elyra/memory/store.py",
)


def _repo_root_from_script() -> Path:
    # docs/lance-debug1/scripts/this_file.py → repo root
    return Path(__file__).resolve().parents[3]


def _iter_py_files(root: Path) -> list[Path]:
    files: list[Path] = []
    candidates = [
        root / "elyra" / "memory",
        root / "elyra" / "presence" / "worker.py",
        root / "elyra" / "runtime" / "api.py",
    ]
    for c in candidates:
        if c.is_file() and c.suffix == ".py":
            files.append(c)
        elif c.is_dir():
            files.extend(sorted(c.rglob("*.py")))
    return files


def _scan(path: Path, root: Path) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"# warn: cannot read {path}: {exc}", file=sys.stderr)
        return out
    rel = path.relative_to(root).as_posix()
    for i, line in enumerate(text.splitlines(), start=1):
        if _LINE_RE.search(line):
            out.append((rel, i, line.rstrip()))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root (default: inferred from script location)",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        default=True,
        help="Emit markdown table (default)",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Emit plain path:line:text lines instead of markdown",
    )
    args = parser.parse_args(argv)
    root = (args.root or _repo_root_from_script()).resolve()
    if not (root / "elyra").is_dir():
        print(f"error: no elyra/ under {root}", file=sys.stderr)
        return 2

    rows: list[tuple[str, int, str]] = []
    for path in _iter_py_files(root):
        rows.extend(_scan(path, root))

    # Prefer lance_store.py first, then other memory, then open path.
    def sort_key(r: tuple[str, int, str]) -> tuple[int, str, int]:
        rel = r[0]
        if rel.endswith("lance_store.py"):
            pri = 0
        elif rel.startswith("elyra/memory/"):
            pri = 1
        else:
            pri = 2
        return (pri, rel, r[1])

    rows.sort(key=sort_key)

    if args.plain:
        for rel, line_no, text in rows:
            print(f"{rel}:{line_no}:{text}")
        return 0

    print("# caller_grep_report (auto)")
    print()
    print(f"Root: `{root}`")
    print(f"Matches: **{len(rows)}**")
    print()
    print("| File | Line | Snippet |")
    print("|------|------|---------|")
    for rel, line_no, text in rows:
        snippet = text.strip().replace("|", "\\|")
        if len(snippet) > 120:
            snippet = snippet[:117] + "..."
        print(f"| `{rel}` | {line_no} | `{snippet}` |")
    print()
    print("Paste into `docs/lance-debug1/TO-ARROW-CALLERS.md` after review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
