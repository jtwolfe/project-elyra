"""Shared helpers for Radeon VII / ROCm standalone GPU embed smoke scripts.

Isolation: docs-only. Product ``open_encoder`` / presence must never import this.
Scripts may import product ``elyra.memory.embed.runtime`` one-way.

Exit codes (normative — see scripts/README.md):

| Code | Meaning |
|------|---------|
| 0 | Pass |
| 2 | ROCm / device missing |
| 3 | Load or kernel failure (or ISA) |
| 4 | Encode / dim / assert fail |
| 5 | OOM |
| 6 | Anti-game / GPU-proof assert fail (A7) |
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Sequence

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_ROCM_MISSING = 2
EXIT_LOAD_OR_KERNEL = 3
EXIT_ENCODE_ASSERT = 4
EXIT_OOM = 5
EXIT_ANTIGAME = 6

# Hard VRAM floor for G6 (1 GiB). Gate, not target — tune after first real load.
VRAM_FLOOR_BYTES = 1_000_000_000

# L2 norm tolerance for G7.
L2_NORM_TOL = 1e-3


# ---------------------------------------------------------------------------
# Repo / path
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    """Discover repo root (directory containing ``elyra/`` package).

    Walks parents of this file; also accepts cwd if it contains ``elyra/``.
    """
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "elyra").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    cwd = Path.cwd().resolve()
    if (cwd / "elyra").is_dir() and (cwd / "pyproject.toml").is_file():
        return cwd
    raise RuntimeError(
        "cannot find repo root (expected elyra/ + pyproject.toml); "
        "run from repo root with PYTHONPATH=."
    )


def ensure_repo_on_syspath() -> Path:
    """Insert repo root at front of sys.path if missing; return root."""
    root = repo_root()
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return root


# ---------------------------------------------------------------------------
# Torch / HIP / ROCm gates
# ---------------------------------------------------------------------------


def import_torch() -> Any:
    """Import torch or raise RuntimeError."""
    try:
        import torch  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"torch not importable: {type(exc).__name__}: {exc}") from exc
    return torch


def require_hip(torch: Any | None = None) -> Any:
    """Require ``torch.version.hip`` truthy. On failure raise with exit-2 semantics.

    Greppable assert site for fail-closed cu130 hosts.
    """
    if torch is None:
        torch = import_torch()
    hip = getattr(getattr(torch, "version", None), "hip", None)
    # Fail closed: no HIP → not a ROCm build (e.g. cu130).
    if not hip:
        raise RoCmMissingError(
            f"torch.version.hip is {hip!r} — ROCm/HIP torch required "
            f"(got torch={getattr(torch, '__version__', '?')})"
        )
    return torch


def require_select_rocm() -> str:
    """Require product ``select_device("rocm") == "rocm"``. Return device kind.

    Greppable assert: fail closed when probe/select cannot see ROCm.
    """
    ensure_repo_on_syspath()
    from elyra.memory.embed.runtime import select_device

    kind = select_device("rocm")
    if kind != "rocm":
        raise RoCmMissingError(
            f'select_device("rocm") returned {kind!r}, expected "rocm"'
        )
    return kind


class RoCmMissingError(RuntimeError):
    """ROCm / HIP / device missing — scripts should exit 2."""

    exit_code = EXIT_ROCM_MISSING


class AntiGameError(RuntimeError):
    """G1–G9 / GPU-proof assert failed — scripts should exit 6."""

    exit_code = EXIT_ANTIGAME


class EncodeAssertError(RuntimeError):
    """Encode / dim / contract assert failed — scripts should exit 4."""

    exit_code = EXIT_ENCODE_ASSERT


class KernelOrLoadError(RuntimeError):
    """ISA / kernel / load failure — scripts should exit 3."""

    exit_code = EXIT_LOAD_OR_KERNEL


class OomError(RuntimeError):
    """Out of memory — scripts should exit 5."""

    exit_code = EXIT_OOM


def exit_for_exception(exc: BaseException) -> int:
    """Map known exception types to exit codes; unknown → 3."""
    code = getattr(exc, "exit_code", None)
    if isinstance(code, int):
        return code
    # Heuristic OOM
    msg = f"{type(exc).__name__}: {exc}".lower()
    if "out of memory" in msg or "oom" in msg or "hipoutOfMemory".lower() in msg:
        return EXIT_OOM
    if "arch" in msg or "gfx906" in msg or "no binary" in msg or "isa" in msg:
        return EXIT_LOAD_OR_KERNEL
    return EXIT_LOAD_OR_KERNEL


# ---------------------------------------------------------------------------
# Parameter device (documented private access — no product public API)
# ---------------------------------------------------------------------------


def parameter_device(embedder: Any) -> Any:
    """Return torch.device of first parameter.

    # noqa: SLF001 — NemotronEmbedder has no public param-device accessor.
    Operator-script only; do not copy into product package this phase.
    """
    model = embedder._model  # noqa: SLF001
    if model is None:
        raise RuntimeError("model not loaded (embedder._model is None)")
    return next(model.parameters()).device


def parameter_device_str(embedder: Any) -> str:
    """Return str(device) of first parameter (expect ``cuda:0`` on ROCm)."""
    return str(parameter_device(embedder))


def assert_params_on_cuda0(embedder: Any) -> str:
    """G5: first parameter must live on CUDA device index 0.

    # noqa: SLF001 — private _model access documented in design §4.3.1.
    """
    dev = next(embedder._model.parameters()).device  # noqa: SLF001
    if dev.type != "cuda":
        raise AntiGameError(f"G5: params not on cuda (got {dev!r})")
    # index None can mean current device 0 depending on torch build.
    if dev.index not in (0, None):
        raise AntiGameError(f"G5: expected cuda:0, got {dev!r}")
    return str(dev)


# ---------------------------------------------------------------------------
# VRAM
# ---------------------------------------------------------------------------


def reset_peak_memory(torch: Any, device_index: int = 0) -> None:
    """G9: reset peak memory stats so floor measures this run."""
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats(device_index)
        torch.cuda.synchronize(device_index)


def max_memory_allocated(torch: Any, device_index: int = 0) -> int:
    """Return peak allocated bytes on device (0 if unavailable)."""
    if not torch.cuda.is_available():
        return 0
    return int(torch.cuda.max_memory_allocated(device_index))


def assert_vram_floor(
    torch: Any,
    *,
    floor_bytes: int = VRAM_FLOOR_BYTES,
    device_index: int = 0,
) -> int:
    """G6: peak allocated VRAM must be >= floor (default 1 GiB)."""
    peak = max_memory_allocated(torch, device_index)
    if peak < floor_bytes:
        raise AntiGameError(
            f"G6: VRAM peak {peak} < floor {floor_bytes} "
            f"({peak / 1e9:.3f} GiB < {floor_bytes / 1e9:.3f} GiB)"
        )
    return peak


# ---------------------------------------------------------------------------
# Vector contract
# ---------------------------------------------------------------------------


def vector_l2(vec: Sequence[float]) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in vec))


def assert_embed_contract(
    vec: Sequence[float],
    *,
    expected_dim: int,
    l2_tol: float = L2_NORM_TOL,
) -> dict[str, Any]:
    """G7: len(vec)==EMBED_DIM and L2 ≈ 1.0."""
    n = len(vec)
    if n != expected_dim:
        raise EncodeAssertError(
            f"G7: len(vec)={n}, expected EMBED_DIM={expected_dim}"
        )
    norm = vector_l2(vec)
    if abs(norm - 1.0) > l2_tol:
        raise EncodeAssertError(
            f"G7: L2 norm {norm:.6f} not within {l2_tol} of 1.0"
        )
    return {"dim": n, "l2_norm": norm}


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise EncodeAssertError(
            f"cosine: length mismatch {len(a)} vs {len(b)}"
        )
    # Vectors expected unit — still compute properly.
    na = vector_l2(a)
    nb = vector_l2(b)
    if na < 1e-12 or nb < 1e-12:
        raise EncodeAssertError("cosine: near-zero vector")
    dot = sum(float(x) * float(y) for x, y in zip(a, b, strict=True))
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Composite A7 helper
# ---------------------------------------------------------------------------


def assert_gpu_nemotron(
    embedder: Any,
    *,
    torch: Any,
    vec: Sequence[float] | None = None,
    expected_dim: int,
    requested_device: str = "rocm",
    floor_bytes: int = VRAM_FLOOR_BYTES,
    check_hip: bool = True,
    check_select: bool = False,
) -> dict[str, Any]:
    """Run greppable G1–G9 checks; return a report dict.

    Call after ``ensure_loaded()`` (and after encode when ``vec`` is provided).

    G1 hip — when check_hip
    G2 select_device — when check_select (usually done before construct in 03)
    G3 construct path — caller's responsibility (NemotronEmbedder only); we
       verify health backend and that device was never cpu (G8)
    G4 health loaded/error/backend
    G5 param cuda:0
    G6 VRAM floor
    G7 dim + L2 (if vec given)
    G8 no cpu placement
    G9 peak reset is caller's pre-load duty; we only document in report
    """
    report: dict[str, Any] = {}

    # G1
    if check_hip:
        hip = getattr(getattr(torch, "version", None), "hip", None)
        if not hip:
            raise AntiGameError(f"G1: torch.version.hip is {hip!r} (not truthy)")
        report["g1_hip"] = str(hip)

    # G2 (optional here — 03 does it pre-construct)
    if check_select:
        ensure_repo_on_syspath()
        from elyra.memory.embed.runtime import select_device

        kind = select_device("rocm")
        if kind != "rocm":
            raise AntiGameError(f'G2: select_device("rocm")={kind!r}')
        report["g2_select_rocm"] = kind

    # G8 — requested device must not have been cpu
    if requested_device == "cpu":
        raise AntiGameError('G8: requested device was "cpu" (anti-fallback)')
    report["g8_requested_device"] = requested_device

    # G4
    health = embedder.health()
    if health.get("loaded") is not True:
        raise AntiGameError(f"G4: health loaded is not True: {health!r}")
    if health.get("error") is not None:
        raise AntiGameError(f"G4: health error is set: {health.get('error')!r}")
    if health.get("backend") != "nemotron":
        raise AntiGameError(f"G4: backend={health.get('backend')!r}, expected nemotron")
    report["g4_health"] = {
        "loaded": health.get("loaded"),
        "error": health.get("error"),
        "backend": health.get("backend"),
        "device": health.get("device"),
        "dtype": health.get("dtype"),
    }

    # G5 + G8 param placement
    dev_s = assert_params_on_cuda0(embedder)
    report["g5_param_device"] = dev_s
    dev = parameter_device(embedder)
    if dev.type == "cpu":
        raise AntiGameError("G8: parameters silently placed on cpu")

    # G6
    peak = assert_vram_floor(torch, floor_bytes=floor_bytes)
    report["g6_vram_peak_bytes"] = peak
    report["g6_vram_floor_bytes"] = floor_bytes

    # G7
    if vec is not None:
        report["g7"] = assert_embed_contract(vec, expected_dim=expected_dim)

    # G3 note (construct path enforced by 03; record health device)
    report["g3_construct"] = "NemotronEmbedder(device=rocm) — caller enforced"
    report["g9_peak_reset"] = "caller must reset_peak_memory before load"

    return report


def classify_torch_error(exc: BaseException) -> int:
    """Map torch/runtime failures to exit codes (3 ISA/kernel, 5 OOM, else 3)."""
    text = f"{type(exc).__name__}: {exc}".lower()
    oom_markers = (
        "out of memory",
        "oom",
        "hipoutofmemory",
        "cuda out of memory",
        "insufficient memory",
    )
    if any(m in text for m in oom_markers):
        return EXIT_OOM
    isa_markers = (
        "no binary for",
        "gfx906",
        "invalid device function",
        "arch not supported",
        "hiperror",
        "no kernel image",
        "cudaerrorillegaladdress",
    )
    if any(m in text for m in isa_markers):
        return EXIT_LOAD_OR_KERNEL
    return EXIT_LOAD_OR_KERNEL


def print_kv(title: str, mapping: dict[str, Any]) -> None:
    """Human-readable key=value report block."""
    print(f"=== {title} ===")
    for k, v in mapping.items():
        print(f"  {k}={v!r}")
