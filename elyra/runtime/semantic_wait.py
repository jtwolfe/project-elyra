"""Semantic wait-for-select — keep slow query encodes for meal packing.

Default ON for CPU dogfood: Nemotron encode often exceeds the snappy
``semantic_select_max_ms`` / ``encode_query_max_ms`` budgets; wait mode raises
the ceiling and keeps a finished encode when the vector is usable.
Persisted in ``data/runtime/semantic_wait.json`` (like continuous / dev_speed).

When the JSON file is missing, state seeds from ``MemorySettings`` (elyra.toml)
so library defaults and operator toml affect the live worker path until glass
or API writes the runtime override.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyra.memory.config import (
    SEMANTIC_WAIT_MAX_MS_DEFAULT,
    SEMANTIC_WAIT_MAX_MS_MAX,
    SEMANTIC_WAIT_MAX_MS_MIN,
    MemorySettings,
    clamp_semantic_wait_max_ms,
)

logger = logging.getLogger(__name__)

SEMANTIC_WAIT_RUNTIME_REL = Path("runtime") / "semantic_wait.json"

# Re-export product constants (single source: memory.config).
DEFAULT_ENABLED = True
DEFAULT_MAX_MS = SEMANTIC_WAIT_MAX_MS_DEFAULT
MIN_MAX_MS = SEMANTIC_WAIT_MAX_MS_MIN
MAX_MAX_MS = SEMANTIC_WAIT_MAX_MS_MAX


def clamp_wait_max_ms(value: float | int) -> int:
    """Clamp to the allowed product band (ms)."""
    return clamp_semantic_wait_max_ms(value)


@dataclass
class SemanticWaitState:
    """Runtime toggle + absolute ceiling for select_semantic wait mode."""

    enabled: bool = DEFAULT_ENABLED
    max_ms: int = DEFAULT_MAX_MS


def effective_select_max_ms(
    state: SemanticWaitState,
    *,
    snappy_max_ms: int = 50,
) -> int:
    """Wall-clock ceiling for select_semantic given runtime wait state.

    When wait is on, use clamped ``state.max_ms``; when off, use the snappy
    ``semantic_select_max_ms`` budget (caller-supplied from settings).
    """
    if not state.enabled:
        return max(0, int(snappy_max_ms))
    return clamp_wait_max_ms(state.max_ms)


def semantic_wait_status_block(
    state: SemanticWaitState,
    *,
    snappy_max_ms: int = 50,
) -> dict[str, Any]:
    """Build the ``semantic_wait`` object for /api/status.

    ``snappy_max_ms`` should be ``settings.memory.semantic_select_max_ms`` so
    glass “off” copy matches the live snappy budget.
    """
    max_ms = clamp_wait_max_ms(state.max_ms)
    snappy = max(0, int(snappy_max_ms))
    return {
        "enabled": bool(state.enabled),
        "max_ms": max_ms,
        "min_max_ms": MIN_MAX_MS,
        "max_max_ms": MAX_MAX_MS,
        "snappy_select_max_ms": snappy,
        "effective_select_max_ms": effective_select_max_ms(
            state, snappy_max_ms=snappy
        ),
    }


def semantic_wait_runtime_path(data_dir: Path) -> Path:
    return Path(data_dir) / SEMANTIC_WAIT_RUNTIME_REL


def _state_from_defaults(defaults: MemorySettings | None) -> SemanticWaitState:
    """Seed state from MemorySettings / product defaults (no JSON)."""
    if defaults is None:
        return SemanticWaitState()
    enabled = bool(getattr(defaults, "semantic_wait_for_select", DEFAULT_ENABLED))
    raw_max = getattr(defaults, "semantic_wait_max_ms", DEFAULT_MAX_MS)
    try:
        max_ms = clamp_wait_max_ms(int(raw_max))
    except (TypeError, ValueError):
        max_ms = DEFAULT_MAX_MS
    return SemanticWaitState(enabled=enabled, max_ms=max_ms)


def load_semantic_wait_runtime(
    data_dir: Path,
    *,
    defaults: MemorySettings | None = None,
) -> SemanticWaitState:
    """Load state: settings/product defaults, then data/runtime/semantic_wait.json.

    Missing or corrupt JSON → ``defaults`` (MemorySettings) when provided,
    else product defaults. JSON overrides enabled/max_ms when present.
    """
    state = _state_from_defaults(defaults)
    path = semantic_wait_runtime_path(data_dir)
    if not path.is_file():
        return state
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("semantic_wait runtime load failed (%s): %s", path, exc)
        return state
    if not isinstance(raw, dict):
        return state
    if "enabled" in raw:
        state.enabled = bool(raw["enabled"])
    if "max_ms" in raw and raw["max_ms"] is not None:
        try:
            state.max_ms = clamp_wait_max_ms(raw["max_ms"])
        except (TypeError, ValueError):
            pass
    return state


def save_semantic_wait_runtime(
    data_dir: Path,
    *,
    enabled: bool,
    max_ms: int | None = None,
    defaults: MemorySettings | None = None,
) -> Path:
    """Persist enabled + max_ms; creates parent dirs.

    When ``max_ms`` is None and a prior file exists, preserve its max_ms.
    When no file and max_ms is None, seed from ``defaults`` or product default.
    """
    path = semantic_wait_runtime_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if max_ms is not None:
        ceiling = clamp_wait_max_ms(max_ms)
    elif path.is_file():
        prev = load_semantic_wait_runtime(data_dir, defaults=defaults)
        ceiling = clamp_wait_max_ms(prev.max_ms)
    else:
        seeded = _state_from_defaults(defaults)
        ceiling = clamp_wait_max_ms(seeded.max_ms)
    body = {
        "enabled": bool(enabled),
        "max_ms": ceiling,
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(body, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
