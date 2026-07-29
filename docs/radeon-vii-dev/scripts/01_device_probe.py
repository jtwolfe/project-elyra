#!/usr/bin/env python3
"""01 — HIP torch + product probe_devices / select_device (A1–A4).

Fail closed (exit 2) when HIP is missing (e.g. cu130 host pre-swap).

Usage (from repo root, project .venv, PYTHONPATH=.):

    python docs/radeon-vii-dev/scripts/01_device_probe.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Allow running as script without installing package path setup.
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _common import (  # noqa: E402
    EXIT_OK,
    EXIT_ROCM_MISSING,
    RoCmMissingError,
    ensure_repo_on_syspath,
    exit_for_exception,
    import_torch,
    print_kv,
    require_hip,
    require_select_rocm,
)


def _optional_rocminfo_snippet(max_lines: int = 24) -> str | None:
    """Return a short rocminfo Marketing Name / gfx snippet, or None."""
    try:
        proc = subprocess.run(
            ["rocminfo"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    keep: list[str] = []
    keys = ("Marketing Name", "Name:", "gfx", "Agent", "Device Type")
    for line in proc.stdout.splitlines():
        if any(k in line for k in keys):
            keep.append(line.rstrip())
        if len(keep) >= max_lines:
            break
    return "\n".join(keep) if keep else proc.stdout[:800]


def main() -> int:
    ensure_repo_on_syspath()

    try:
        torch = import_torch()
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_ROCM_MISSING

    version = getattr(torch, "__version__", "?")
    hip = getattr(getattr(torch, "version", None), "hip", None)
    cuda_ver = getattr(getattr(torch, "version", None), "cuda", None)
    try:
        cuda_avail = bool(torch.cuda.is_available())
    except Exception as exc:  # noqa: BLE001
        cuda_avail = False
        cuda_err = f"{type(exc).__name__}: {exc}"
    else:
        cuda_err = None

    device_name = None
    if cuda_avail:
        try:
            device_name = torch.cuda.get_device_name(0)
        except Exception as exc:  # noqa: BLE001
            device_name = f"<error: {exc}>"

    print_kv(
        "torch",
        {
            "version": version,
            "hip": hip,
            "cuda_version_meta": cuda_ver,
            "cuda_is_available": cuda_avail,
            "device_name_0": device_name,
            "cuda_err": cuda_err,
        },
    )

    # Fail closed if no HIP (greppable: require_hip / torch.version.hip).
    try:
        require_hip(torch)
    except RoCmMissingError as exc:
        print(f"FAIL closed (exit 2): {exc}", file=sys.stderr)
        return EXIT_ROCM_MISSING

    from elyra.memory.embed.runtime import probe_devices, select_device

    caps = probe_devices()
    sel_rocm = select_device("rocm")
    sel_auto = select_device("auto")
    print_kv(
        "product probe",
        {
            "probe_devices": caps,
            'select_device("rocm")': sel_rocm,
            'select_device("auto")': sel_auto,
        },
    )

    # Product path must agree ROCm is visible.
    try:
        require_select_rocm()
    except RoCmMissingError as exc:
        print(f"FAIL closed (exit 2): {exc}", file=sys.stderr)
        return EXIT_ROCM_MISSING

    if not caps.get("rocm"):
        print(
            'FAIL closed (exit 2): probe_devices()["rocm"] is false',
            file=sys.stderr,
        )
        return EXIT_ROCM_MISSING

    snippet = _optional_rocminfo_snippet()
    if snippet:
        print("=== rocminfo (optional snippet) ===")
        print(snippet)
    else:
        print("=== rocminfo (optional snippet) ===")
        print("  (unavailable or empty)")

    print("PASS: HIP present; probe_devices rocm=True; select_device(rocm)=rocm")
    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(exit_for_exception(exc)) from exc
