#!/usr/bin/env python3
"""03 — Nemotron text encode on ROCm with hard G1–G9 asserts (A6–A7).

**Construct:** ``NemotronEmbedder(device="rocm")`` ONLY — never ``open_encoder``.
**Gate:** run only after ``02_matmul_smoke.py`` exits 0 (A5 hard gate).

Fail closed (exit 2) when HIP / select_device(rocm) missing.
Anti-game fails → exit 6; encode/dim → exit 4; OOM → exit 5.

Usage (from repo root, project .venv, PYTHONPATH=.):

    python docs/radeon-vii-dev/scripts/03_nemotron_encode.py \\
      --text-a "passage: a red cube on a table" \\
      --text-b "passage: a blue sphere in space"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _common import (  # noqa: E402
    EXIT_ANTIGAME,
    EXIT_ENCODE_ASSERT,
    EXIT_OK,
    EXIT_OOM,
    EXIT_ROCM_MISSING,
    VRAM_FLOOR_BYTES,
    AntiGameError,
    EncodeAssertError,
    OomError,
    RoCmMissingError,
    assert_gpu_nemotron,
    classify_torch_error,
    cosine_similarity,
    ensure_repo_on_syspath,
    exit_for_exception,
    import_torch,
    max_memory_allocated,
    print_kv,
    require_hip,
    require_select_rocm,
    reset_peak_memory,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Standalone Nemotron GPU encode smoke (G1–G9)."
    )
    p.add_argument(
        "--text-a",
        default="passage: a red cube on a table",
        help="Primary text to encode",
    )
    p.add_argument(
        "--text-b",
        default=None,
        help="Optional second text for cosine similarity",
    )
    p.add_argument(
        "--model-id",
        default=None,
        help="HF model id (default: product DEFAULT_NEMOTRON_MODEL_ID)",
    )
    p.add_argument(
        "--model-path",
        default="",
        help="Optional local model directory",
    )
    p.add_argument(
        "--dtype",
        default="float16",
        help="Dtype name (acceptance is float16 only; other values experimental)",
    )
    p.add_argument(
        "--vram-floor",
        type=int,
        default=VRAM_FLOOR_BYTES,
        help=f"G6 VRAM floor bytes (default {VRAM_FLOOR_BYTES})",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print final report as JSON",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    ensure_repo_on_syspath()

    # --- G1: hip truthy inside 03 (not only in 01) ---
    try:
        torch = import_torch()
        require_hip(torch)  # greppable: torch.version.hip / require_hip
    except RoCmMissingError as exc:
        print(f"FAIL closed G1 (exit 2): {exc}", file=sys.stderr)
        return EXIT_ROCM_MISSING
    except RuntimeError as exc:
        print(f"FAIL closed (exit 2): {exc}", file=sys.stderr)
        return EXIT_ROCM_MISSING

    # --- G2: select_device("rocm") == "rocm" BEFORE construct ---
    try:
        require_select_rocm()
    except RoCmMissingError as exc:
        print(f"FAIL closed G2 (exit 2): {exc}", file=sys.stderr)
        return EXIT_ROCM_MISSING

    from elyra.memory.embed.runtime import (
        DEFAULT_NEMOTRON_MODEL_ID,
        NemotronEmbedder,
        select_device,
    )
    from elyra.memory.embed.types import EMBED_DIM

    # G2 explicit greppable assert
    _sel = select_device("rocm")
    assert _sel == "rocm", f'G2: select_device("rocm")={_sel!r}'  # noqa: S101

    model_id = args.model_id or DEFAULT_NEMOTRON_MODEL_ID
    dtype_name = args.dtype
    if dtype_name != "float16":
        print(
            f"WARN: dtype={dtype_name!r} is experimental / non-acceptance "
            f"(design: fp16 only for A7)",
            file=sys.stderr,
        )

    # G9: reset peak memory BEFORE load so floor measures this run.
    if torch.cuda.is_available():
        reset_peak_memory(torch, 0)
    else:
        print(
            "FAIL closed (exit 2): cuda not available after HIP check",
            file=sys.stderr,
        )
        return EXIT_ROCM_MISSING

    # --- G3: NemotronEmbedder(device="rocm") ONLY — never open_encoder ---
    # Grep guard: this file must not reference open_encoder on acceptance path.
    requested_device = "rocm"
    embedder = NemotronEmbedder(
        model_id=model_id,
        model_path=args.model_path or "",
        device="rocm",
        dim=EMBED_DIM,
        dtype_name=dtype_name,
    )

    t_load0 = time.perf_counter()
    try:
        embedder.ensure_loaded()
    except Exception as exc:  # noqa: BLE001
        code = classify_torch_error(exc)
        peak = max_memory_allocated(torch, 0)
        print(
            f"FAIL load (exit {code}): {type(exc).__name__}: {exc} "
            f"vram_peak={peak}",
            file=sys.stderr,
        )
        if code == EXIT_OOM:
            return EXIT_OOM
        return code
    load_s = time.perf_counter() - t_load0

    # Post-load GPU proof (G4–G6, G8; G7 after encode).
    try:
        mid_report = assert_gpu_nemotron(
            embedder,
            torch=torch,
            vec=None,
            expected_dim=EMBED_DIM,
            requested_device=requested_device,
            floor_bytes=args.vram_floor,
            check_hip=True,
            check_select=False,
        )
    except AntiGameError as exc:
        print(f"FAIL anti-game (exit 6): {exc}", file=sys.stderr)
        embedder.close()
        return EXIT_ANTIGAME

    # Encode text A
    t_enc0 = time.perf_counter()
    try:
        vec_a = embedder.encode_text(args.text_a)
    except Exception as exc:  # noqa: BLE001
        code = classify_torch_error(exc)
        if code == EXIT_OOM:
            print(
                f"FAIL OOM encode (exit 5): {exc} "
                f"vram_peak={max_memory_allocated(torch, 0)}",
                file=sys.stderr,
            )
            embedder.close()
            return EXIT_OOM
        print(f"FAIL encode (exit 4): {type(exc).__name__}: {exc}", file=sys.stderr)
        embedder.close()
        return EXIT_ENCODE_ASSERT
    enc_a_s = time.perf_counter() - t_enc0

    try:
        full_report = assert_gpu_nemotron(
            embedder,
            torch=torch,
            vec=vec_a,
            expected_dim=EMBED_DIM,
            requested_device=requested_device,
            floor_bytes=args.vram_floor,
            check_hip=True,
            check_select=False,
        )
    except AntiGameError as exc:
        print(f"FAIL anti-game (exit 6): {exc}", file=sys.stderr)
        embedder.close()
        return EXIT_ANTIGAME
    except EncodeAssertError as exc:
        print(f"FAIL encode assert (exit 4): {exc}", file=sys.stderr)
        embedder.close()
        return EXIT_ENCODE_ASSERT

    cos = None
    enc_b_s = None
    if args.text_b:
        t_b0 = time.perf_counter()
        try:
            vec_b = embedder.encode_text(args.text_b)
        except Exception as exc:  # noqa: BLE001
            code = classify_torch_error(exc)
            print(
                f"FAIL encode B (exit {code if code == EXIT_OOM else 4}): {exc}",
                file=sys.stderr,
            )
            embedder.close()
            return EXIT_OOM if code == EXIT_OOM else EXIT_ENCODE_ASSERT
        enc_b_s = time.perf_counter() - t_b0
        try:
            cos = cosine_similarity(vec_a, vec_b)
        except EncodeAssertError as exc:
            print(f"FAIL cosine (exit 4): {exc}", file=sys.stderr)
            embedder.close()
            return EXIT_ENCODE_ASSERT

    peak = max_memory_allocated(torch, 0)
    health = embedder.health()
    report = {
        "ok": True,
        "model_id": model_id,
        "model_path": args.model_path or None,
        "device": requested_device,
        "param_device": full_report.get("g5_param_device"),
        "dtype": health.get("dtype"),
        "load_s": round(load_s, 3),
        "encode_a_s": round(enc_a_s, 3),
        "encode_b_s": round(enc_b_s, 3) if enc_b_s is not None else None,
        "dim": full_report.get("g7", {}).get("dim"),
        "l2_norm": full_report.get("g7", {}).get("l2_norm"),
        "cosine_a_b": cos,
        "vram_peak_bytes": peak,
        "vram_floor_bytes": args.vram_floor,
        "hip": str(torch.version.hip),
        "torch": torch.__version__,
        "g1_g9": full_report,
        "post_load_mid": mid_report,
        "text_a_preview": args.text_a[:80],
        "note": "acceptance is scripts-only; BUG-mem-gpu-01 stays Open",
    }

    embedder.close()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_kv(
            "03_nemotron_encode PASS",
            {
                "model_id": report["model_id"],
                "param_device": report["param_device"],
                "dtype": report["dtype"],
                "load_s": report["load_s"],
                "encode_a_s": report["encode_a_s"],
                "encode_b_s": report["encode_b_s"],
                "dim": report["dim"],
                "l2_norm": report["l2_norm"],
                "cosine_a_b": report["cosine_a_b"],
                "vram_peak_bytes": report["vram_peak_bytes"],
                "hip": report["hip"],
            },
        )
        print("PASS A6–A7: G1–G9 satisfied (param cuda:0 + VRAM floor + 2048-d)")

    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except RoCmMissingError as exc:
        print(f"FAIL closed (exit 2): {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ROCM_MISSING) from exc
    except AntiGameError as exc:
        print(f"FAIL anti-game (exit 6): {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ANTIGAME) from exc
    except EncodeAssertError as exc:
        print(f"FAIL encode assert (exit 4): {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ENCODE_ASSERT) from exc
    except OomError as exc:
        print(f"FAIL OOM (exit 5): {exc}", file=sys.stderr)
        raise SystemExit(EXIT_OOM) from exc
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(exit_for_exception(exc)) from exc
