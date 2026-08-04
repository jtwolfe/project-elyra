#!/usr/bin/env python3
"""R0/R1 helper: print package versions, Python, and debug path env.

Usage (from repo root):
  python docs/investigations/lance-debug1/scripts/env_check.py
  python docs/investigations/lance-debug1/scripts/env_check.py --json

Does not open product stores or mutate any Lance URI.
Inspection-only (SAFETY R0 for pure print; paths may be R1 context).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _pkg_version(name: str) -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover
        return None
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _try_import_version(mod_name: str) -> tuple[bool, str | None, str | None]:
    """Return (import_ok, version_attr_or_pkg, error)."""
    try:
        mod = __import__(mod_name)
    except Exception as exc:  # noqa: BLE001 — report any import failure
        return False, None, f"{type(exc).__name__}: {exc}"
    ver = getattr(mod, "__version__", None)
    if ver is None:
        ver = _pkg_version(mod_name)
    return True, str(ver) if ver is not None else None, None


def collect_env() -> dict[str, Any]:
    lancedb_ok, lancedb_ver, lancedb_err = _try_import_version("lancedb")
    lance_ok, lance_ver, lance_err = _try_import_version("lance")
    pa_ok, pa_ver, pa_err = _try_import_version("pyarrow")

    # Prefer package metadata when import works but __version__ missing.
    if lancedb_ok and not lancedb_ver:
        lancedb_ver = _pkg_version("lancedb")
    if lance_ok and not lance_ver:
        lance_ver = _pkg_version("pylance") or _pkg_version("lance")

    data_dir = os.environ.get("LANCE_DEBUG_DATA_DIR") or os.environ.get("ELYRA_DATA_DIR")
    uri = os.environ.get("LANCE_DEBUG_URI")
    marker_expected: str | None = None
    if data_dir:
        # Canonical: {data_dir}/../.lance-debug1-quarantine
        marker_expected = str(
            (Path(data_dir).resolve().parent / ".lance-debug1-quarantine")
        )

    marker_present = bool(marker_expected and Path(marker_expected).is_file())

    return {
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
            "implementation": sys.implementation.name,
        },
        "packages": {
            "lancedb": {
                "import_ok": lancedb_ok,
                "version": lancedb_ver,
                "error": lancedb_err,
            },
            "lance": {
                "import_ok": lance_ok,
                "version": lance_ver,
                "error": lance_err,
            },
            "pyarrow": {
                "import_ok": pa_ok,
                "version": pa_ver,
                "error": pa_err,
            },
        },
        "paths": {
            "LANCE_DEBUG_DATA_DIR": os.environ.get("LANCE_DEBUG_DATA_DIR"),
            "LANCE_DEBUG_URI": uri,
            "ELYRA_DATA_DIR": os.environ.get("ELYRA_DATA_DIR"),
            "ELYRA_LANCE_ALLOW_WRITE": os.environ.get("ELYRA_LANCE_ALLOW_WRITE"),
            "data_dir_resolved": str(Path(data_dir).resolve()) if data_dir else None,
            "uri_exists": bool(uri and Path(uri).exists()),
            "marker_expected": marker_expected,
            "marker_present": marker_present,
        },
        "safety": {
            "class_note": "env_check is R0 (versions/paths); does not connect or write",
            "deny_list": [
                "merge_insert",
                "add",
                "delete",
                "drop_table",
                "compact_files",
                "cleanup_old_versions",
                "optimize",
            ],
        },
    }


def _print_human(info: dict[str, Any]) -> None:
    py = info["python"]
    print("# env_check (lance-debug1)")
    print()
    print(f"Python: {py['version']} ({py['implementation']})")
    print(f"Executable: {py['executable']}")
    print()
    print("## Packages")
    for name, meta in info["packages"].items():
        if meta["import_ok"]:
            print(f"- {name}: {meta['version'] or '(version unknown)'}")
        else:
            print(f"- {name}: IMPORT FAILED — {meta['error']}")
    print()
    print("## Paths / env")
    paths = info["paths"]
    for key in (
        "LANCE_DEBUG_DATA_DIR",
        "LANCE_DEBUG_URI",
        "ELYRA_DATA_DIR",
        "ELYRA_LANCE_ALLOW_WRITE",
        "data_dir_resolved",
        "uri_exists",
        "marker_expected",
        "marker_present",
    ):
        print(f"- {key}: {paths.get(key)}")
    print()
    print("## Notes")
    print("- Prefer quarantine URI for R1 probes (see SAFETY.md).")
    print("- On Python 3.14, native lancedb/lance may segfault; use 3.12 when probing.")
    print("- Marker for W1 must be only at $QUARANTINE_ROOT/.lance-debug1-quarantine.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable text",
    )
    args = parser.parse_args(argv)
    info = collect_env()
    if args.json:
        print(json.dumps(info, indent=2, sort_keys=True))
    else:
        _print_human(info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
