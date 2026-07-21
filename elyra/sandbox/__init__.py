"""One persistent sandbox workspace under data/sandbox/.

Public API: path jail resolve, Sandbox FS ops, and shell=False run.

Trust boundary: FS methods are path-jailed; ``run`` is process-level only
(cwd + scrubbed env + shell=False) — not a container. See
``elyra.sandbox.sandbox`` module docstring.
"""

from elyra.sandbox.paths import PathEscapeError, resolve
from elyra.sandbox.sandbox import (
    DEFAULT_RUN_TIMEOUT_SECONDS,
    OUTPUT_CAP_BYTES,
    RunResult,
    Sandbox,
)

__all__ = [
    "DEFAULT_RUN_TIMEOUT_SECONDS",
    "OUTPUT_CAP_BYTES",
    "PathEscapeError",
    "RunResult",
    "Sandbox",
    "resolve",
]
