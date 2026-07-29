#!/usr/bin/env python3
"""02 — Tiny fp16 matmul on cuda:0 (**A5 HARD GATE**).

Proves gfx906 compute kernels before any Nemotron model load.
Fail closed (exit 2) when HIP missing; ISA/kernel errors → exit 3.

**Do not run 03 if this script fails.**

Usage (from repo root, project .venv, PYTHONPATH=.):

    python docs/radeon-vii-dev/scripts/02_matmul_smoke.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _common import (  # noqa: E402
    EXIT_LOAD_OR_KERNEL,
    EXIT_OK,
    EXIT_ROCM_MISSING,
    KernelOrLoadError,
    RoCmMissingError,
    classify_torch_error,
    ensure_repo_on_syspath,
    exit_for_exception,
    import_torch,
    print_kv,
    require_hip,
)


def main() -> int:
    ensure_repo_on_syspath()

    try:
        torch = import_torch()
        require_hip(torch)
    except RoCmMissingError as exc:
        print(f"FAIL closed (exit 2): {exc}", file=sys.stderr)
        return EXIT_ROCM_MISSING
    except RuntimeError as exc:
        print(f"FAIL closed (exit 2): {exc}", file=sys.stderr)
        return EXIT_ROCM_MISSING

    if not torch.cuda.is_available():
        print(
            "FAIL closed (exit 2): torch.cuda.is_available() is False "
            "(HIP present but no device)",
            file=sys.stderr,
        )
        return EXIT_ROCM_MISSING

    try:
        name = torch.cuda.get_device_name(0)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL closed (exit 2): cannot get device name: {exc}", file=sys.stderr)
        return EXIT_ROCM_MISSING

    print_kv(
        "matmul gate A5",
        {
            "torch": torch.__version__,
            "hip": torch.version.hip,
            "device": "cuda:0",
            "name": name,
            "dtype": "float16",
        },
    )

    # Tiny fp16 matmul — hard gate for gfx906 kernels.
    try:
        a = torch.randn(256, 256, device="cuda:0", dtype=torch.float16)
        b = torch.randn(256, 256, device="cuda:0", dtype=torch.float16)
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        c = a @ b
        torch.cuda.synchronize()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        # Touch result so compiler cannot elide.
        checksum = float(c.float().sum().item())
    except Exception as exc:  # noqa: BLE001
        code = classify_torch_error(exc)
        msg = f"{type(exc).__name__}: {exc}"
        if code == EXIT_LOAD_OR_KERNEL:
            print(
                f"FAIL A5 HARD GATE (exit {code}): ISA/kernel error — "
                f"STOP; do not run 03. {msg}",
                file=sys.stderr,
            )
            return EXIT_LOAD_OR_KERNEL
        print(f"FAIL A5 (exit {code}): {msg}", file=sys.stderr)
        return code

    if not torch.isfinite(torch.tensor(checksum)):
        print("FAIL A5 (exit 3): matmul result non-finite", file=sys.stderr)
        return EXIT_LOAD_OR_KERNEL

    print_kv(
        "matmul result",
        {
            "elapsed_ms": round(elapsed_ms, 3),
            "checksum": checksum,
            "shape": list(c.shape),
            "device": str(c.device),
        },
    )
    print("PASS A5: fp16 matmul on cuda:0 OK — safe to consider 03")
    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except RoCmMissingError as exc:
        print(f"FAIL closed (exit 2): {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ROCM_MISSING) from exc
    except KernelOrLoadError as exc:
        print(f"FAIL A5 (exit 3): {exc}", file=sys.stderr)
        raise SystemExit(EXIT_LOAD_OR_KERNEL) from exc
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(exit_for_exception(exc)) from exc
