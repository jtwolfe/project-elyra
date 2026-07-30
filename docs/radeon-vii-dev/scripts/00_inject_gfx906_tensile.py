#!/usr/bin/env python3
"""00 — Inject gfx906 rocBLAS Tensile kernels into the project venv torch wheel.

Official PyTorch ``+rocm7.2`` wheels ship HIP and enumerate Radeon VII, but their
bundled rocBLAS Tensile library **omits gfx906**. Arch Linux ``rocblas`` 7.2.x
still packages ``TensileLibrary_lazy_gfx906.dat`` (+ kernels). This script
downloads that package (no root) and copies only ``*gfx906*`` assets into:

  ``.venv/.../site-packages/torch/lib/rocblas/library/``

Re-run after any ``pip install`` that replaces torch/rocm wheels.

Usage (repo root, project .venv optional for nothing except path resolution):

    python docs/radeon-vii-dev/scripts/00_inject_gfx906_tensile.py
    python docs/radeon-vii-dev/scripts/00_inject_gfx906_tensile.py --check-only
    python docs/radeon-vii-dev/scripts/00_inject_gfx906_tensile.py --pkg /path/to/rocblas-*.pkg.tar.zst

Exit: 0 = gfx906 present (or inject OK); 2 = torch library missing; 3 = inject failed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

# Arch extra (Omarchy mirror default used on LuxPrimata; override via --url)
DEFAULT_PKG_URL = (
    "https://stable-mirror.omarchy.org/extra/os/x86_64/"
    "rocblas-7.2.4-2-x86_64.pkg.tar.zst"
)
LAZY_NAME = "TensileLibrary_lazy_gfx906.dat"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _torch_rocblas_library() -> Path | None:
    try:
        import torch  # noqa: PLC0415
    except ImportError:
        return None
    p = Path(torch.__file__).resolve().parent / "lib" / "rocblas" / "library"
    return p if p.is_dir() else None


def _gfx906_present(lib: Path) -> bool:
    return (lib / LAZY_NAME).is_file()


def _extract_gfx906(pkg: Path, dest_lib: Path) -> int:
    """Extract *gfx906* members from Arch .pkg.tar.zst into dest_lib. Return count."""
    # bsdtar handles zst + multi-format; tarfile may not open zst on all Pythons
    with tempfile.TemporaryDirectory(prefix="rocblas-gfx906-") as td:
        td_path = Path(td)
        # List members
        list_out = subprocess.check_output(
            ["bsdtar", "-tf", str(pkg)], text=True
        )
        members = [
            line.strip()
            for line in list_out.splitlines()
            if "gfx906" in line and line.strip()
        ]
        if not members:
            raise RuntimeError(f"no gfx906 members in {pkg}")
        list_file = td_path / "members.txt"
        list_file.write_text("\n".join(members) + "\n", encoding="utf-8")
        subprocess.check_call(
            ["bsdtar", "-xvf", str(pkg), "-T", str(list_file), "-C", str(td_path)],
            stdout=subprocess.DEVNULL,
        )
        # Package layout: opt/rocm/lib/rocblas/library/*
        src_candidates = list(td_path.glob("**/rocblas/library/*gfx906*"))
        if not src_candidates:
            raise RuntimeError("extract produced no *gfx906* files under library/")
        dest_lib.mkdir(parents=True, exist_ok=True)
        n = 0
        for src in src_candidates:
            shutil.copy2(src, dest_lib / src.name)
            n += 1
        return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pkg",
        type=Path,
        default=None,
        help="Local Arch rocblas .pkg.tar.zst (skip download)",
    )
    ap.add_argument(
        "--url",
        default=DEFAULT_PKG_URL,
        help="Download URL for Arch rocblas package",
    )
    ap.add_argument(
        "--check-only",
        action="store_true",
        help="Only verify TensileLibrary_lazy_gfx906.dat is present",
    )
    ap.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Where to cache the downloaded package (default: /tmp)",
    )
    args = ap.parse_args(argv)

    lib = _torch_rocblas_library()
    if lib is None:
        print(
            "FAIL: torch not importable or missing lib/rocblas/library "
            "(activate project .venv with +rocm torch)",
            file=sys.stderr,
        )
        return 2

    print(f"torch rocblas library: {lib}")
    if _gfx906_present(lib):
        n = len(list(lib.glob("*gfx906*")))
        print(f"OK: {LAZY_NAME} present ({n} *gfx906* files)")
        if args.check_only:
            return 0
        print("Nothing to do (already injected). Use --check-only to verify only.")
        return 0

    if args.check_only:
        print(f"FAIL: missing {LAZY_NAME}", file=sys.stderr)
        return 3

    cache = args.cache_dir or Path("/tmp")
    cache.mkdir(parents=True, exist_ok=True)
    if args.pkg is not None:
        pkg = args.pkg.expanduser().resolve()
        if not pkg.is_file():
            print(f"FAIL: --pkg not found: {pkg}", file=sys.stderr)
            return 3
    else:
        pkg = cache / Path(args.url).name
        if not pkg.is_file():
            print(f"Downloading {args.url} → {pkg} …")
            urllib.request.urlretrieve(args.url, pkg)  # noqa: S310
        else:
            print(f"Using cached package {pkg}")

    try:
        n = _extract_gfx906(pkg, lib)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL inject: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    if not _gfx906_present(lib):
        print(f"FAIL: inject ran but {LAZY_NAME} still missing", file=sys.stderr)
        return 3

    print(f"PASS: injected {n} gfx906 rocBLAS/Tensile files into {lib}")
    print("Next: python docs/radeon-vii-dev/scripts/02_matmul_smoke.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
