"""Semantic wait-for-select — keep slow query encodes for meal packing.

Default ON for CPU dogfood: Nemotron encode often exceeds the snappy
``semantic_select_max_ms`` / ``encode_query_max_ms`` budgets; wait mode raises
the ceiling and keeps a finished encode when the vector is usable.
Persisted in ``data/runtime/semantic_wait.json`` (like continuous / dev_speed).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SEMANTIC_WAIT_RUNTIME_REL = Path("runtime") / "semantic_wait.json"

# Product defaults (CPU dogfood-friendly).
DEFAULT_ENABLED = True
DEFAULT_MAX_MS = 15_000
MIN_MAX_MS = 1_000
MAX_MAX_MS = 120_000


@dataclass
class SemanticWaitState:
    """Runtime toggle + absolute ceiling for select_semantic wait mode."""

    enabled: bool = DEFAULT_ENABLED
    max_ms: int = DEFAULT_MAX_MS


def clamp_wait_max_ms(value: float | int) -> int:
    """Clamp to the allowed [1000, 120000] band (ms)."""
    v = int(value)
    if v < MIN_MAX_MS:
        return MIN_MAX_MS
    if v > MAX_MAX_MS:
        return MAX_MAX_MS
    return v


def effective_select_max_ms(
    state: SemanticWaitState,
    *,
    snappy_max_ms: int = 50,
) -> int:
    """Wall-clock ceiling for select_semantic given runtime wait state.

    When wait is on, use clamped ``state.max_ms``; when off, use the snappy
    ``semantic_select_max_ms`` budget (caller-supplied).
    """
    if not state.enabled:
        return max(0, int(snappy_max_ms))
    return clamp_wait_max_ms(state.max_ms)


def semantic_wait_status_block(state: SemanticWaitState) -> dict[str, Any]:
    """Build the ``semantic_wait`` object for /api/status."""
    max_ms = clamp_wait_max_ms(state.max_ms)
    return {
        "enabled": bool(state.enabled),
        "max_ms": max_ms,
        "min_max_ms": MIN_MAX_MS,
        "max_max_ms": MAX_MAX_MS,
        "effective_select_max_ms": effective_select_max_ms(state),
    }


def semantic_wait_runtime_path(data_dir: Path) -> Path:
    return Path(data_dir) / SEMANTIC_WAIT_RUNTIME_REL


def load_semantic_wait_runtime(data_dir: Path) -> SemanticWaitState:
    """Load state from data/runtime/semantic_wait.json; missing → product defaults."""
    state = SemanticWaitState()
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
) -> Path:
    """Persist enabled + max_ms; creates parent dirs."""
    path = semantic_wait_runtime_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    ceiling = (
        clamp_wait_max_ms(max_ms)
        if max_ms is not None
        else DEFAULT_MAX_MS
    )
    # Preserve existing max_ms when only toggling enabled.
    if max_ms is None and path.is_file():
        prev = load_semantic_wait_runtime(data_dir)
        ceiling = clamp_wait_max_ms(prev.max_ms)
    body = {
        "enabled": bool(enabled),
        "max_ms": ceiling,
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(body, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
