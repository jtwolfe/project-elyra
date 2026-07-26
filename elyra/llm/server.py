"""llama-server launch argv (Vulkan-capable binary from project-elyra2).

Scope: pure function assembling argv.
In scope: model paths, jinja/embedding/reasoning, context/batch/slot knobs.
Out of scope: subprocess, health checks.
"""

from __future__ import annotations

from elyra.config import ElyraPaths
from elyra.llm.config import LocalClientConfig
from elyra.llm.constants import CONTEXT_WINDOW_TOKENS

DEFAULT_MODEL_FILENAME = "Gemma-4-12B-OBLITERATED-Q4_K_M.gguf"
DEFAULT_MMPROJ_FILENAME = "mmproj-BF16.gguf"
DEFAULT_SERVER_BINARY = "llama-server"
DEFAULT_BATCH_SIZE = 2048
DEFAULT_UBATCH_SIZE = 2048
DEFAULT_NGL = "99"
DEFAULT_PARALLEL_SLOTS = 1
DEFAULT_CACHE_RAM_MIB = 0


def build_server_command(
    paths: ElyraPaths,
    config: LocalClientConfig | None = None,
    *,
    model_filename: str = DEFAULT_MODEL_FILENAME,
    mmproj_filename: str = DEFAULT_MMPROJ_FILENAME,
    server_binary: str = DEFAULT_SERVER_BINARY,
    context_tokens: int = CONTEXT_WINDOW_TOKENS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    ubatch_size: int = DEFAULT_UBATCH_SIZE,
    ngl: str = DEFAULT_NGL,
    parallel_slots: int = DEFAULT_PARALLEL_SLOTS,
    cache_ram_mib: int = DEFAULT_CACHE_RAM_MIB,
) -> list[str]:
    cfg = config or LocalClientConfig()
    server_path = paths.model_dir / "llama.cpp" / server_binary
    model_path = paths.model_dir / model_filename
    mmproj_path = paths.model_dir / mmproj_filename

    cmd = [
        str(server_path),
        "-m",
        str(model_path),
        "--mmproj",
        str(mmproj_path),
        "--embedding",
        "--jinja",
        "--pooling",
        "mean",
        "-c",
        str(context_tokens),
        "-b",
        str(batch_size),
        "-ub",
        str(ubatch_size),
        "-ngl",
        ngl,
        "-np",
        str(parallel_slots),
        "--cache-ram",
        str(cache_ram_mib),
        "--host",
        cfg.host,
        "--port",
        str(cfg.port),
        "--no-webui",
        "--threads",
        "4",
    ]
    if cfg.use_reasoning:
        cmd.extend(["--reasoning", "on", "--reasoning-format", "auto"])
        if cfg.reasoning_budget is not None and cfg.reasoning_budget >= 0:
            cmd.extend(["--reasoning-budget", str(cfg.reasoning_budget)])
    return cmd


def validate_model_paths(paths: ElyraPaths) -> list[str]:
    """Return human-readable problems, or empty if model tree looks usable."""
    problems: list[str] = []
    server = paths.model_dir / "llama.cpp" / DEFAULT_SERVER_BINARY
    model = paths.model_dir / DEFAULT_MODEL_FILENAME
    mmproj = paths.model_dir / DEFAULT_MMPROJ_FILENAME
    if not paths.model_dir.is_dir():
        problems.append(
            f"model dir missing: {paths.model_dir} "
            f"(symlink to aurimago/project-elyra2/model — see docs/inference.md)"
        )
        return problems
    if not server.is_file():
        problems.append(f"llama-server binary missing: {server}")
    if not model.is_file():
        problems.append(f"model weights missing: {model}")
    if not mmproj.is_file():
        problems.append(f"mmproj missing: {mmproj}")
    return problems
