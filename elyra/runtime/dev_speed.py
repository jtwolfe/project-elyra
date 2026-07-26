"""Dev speed mode — optional inter-hop pause so glass is followable.

Default ON for dogfood: human can watch each step without a firehose.
Persisted in ``data/runtime/dev_speed.json`` (like continuous / usage prefs).
Does not invent wakes; only delays the do-loop between hops.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEV_SPEED_RUNTIME_REL = Path("runtime") / "dev_speed.json"

# Product defaults (dogfood-friendly).
DEFAULT_ENABLED = True
DEFAULT_DELAY_SECONDS = 8.0
MIN_DELAY_SECONDS = 5.0
MAX_DELAY_SECONDS = 15.0


@dataclass
class DevSpeedState:
    """Runtime toggle + delay for inter-hop pacing."""

    enabled: bool = DEFAULT_ENABLED
    delay_seconds: float = DEFAULT_DELAY_SECONDS


def clamp_delay_seconds(value: float | int) -> float:
    """Clamp to the allowed [5, 15] band (product UX)."""
    v = float(value)
    if v < MIN_DELAY_SECONDS:
        return MIN_DELAY_SECONDS
    if v > MAX_DELAY_SECONDS:
        return MAX_DELAY_SECONDS
    return v


def effective_hop_delay_seconds(state: DevSpeedState) -> float:
    """Seconds to sleep before hop N>0; 0 when mode is off."""
    if not state.enabled:
        return 0.0
    return clamp_delay_seconds(state.delay_seconds)


def dev_speed_status_block(state: DevSpeedState) -> dict[str, Any]:
    """Build the ``dev_speed`` object for /api/status."""
    delay = clamp_delay_seconds(state.delay_seconds)
    return {
        "enabled": bool(state.enabled),
        "delay_seconds": delay,
        "min_delay_seconds": MIN_DELAY_SECONDS,
        "max_delay_seconds": MAX_DELAY_SECONDS,
        "effective_hop_delay_seconds": effective_hop_delay_seconds(state),
    }


def dev_speed_runtime_path(data_dir: Path) -> Path:
    return Path(data_dir) / DEV_SPEED_RUNTIME_REL


def load_dev_speed_runtime(data_dir: Path) -> DevSpeedState:
    """Load state from data/runtime/dev_speed.json; missing → product defaults."""
    state = DevSpeedState()
    path = dev_speed_runtime_path(data_dir)
    if not path.is_file():
        return state
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("dev_speed runtime load failed (%s): %s", path, exc)
        return state
    if not isinstance(raw, dict):
        return state
    if "enabled" in raw:
        state.enabled = bool(raw["enabled"])
    if "delay_seconds" in raw and raw["delay_seconds"] is not None:
        try:
            state.delay_seconds = clamp_delay_seconds(raw["delay_seconds"])
        except (TypeError, ValueError):
            pass
    return state


def save_dev_speed_runtime(
    data_dir: Path,
    *,
    enabled: bool,
    delay_seconds: float | None = None,
) -> Path:
    """Persist enabled + delay; creates parent dirs."""
    path = dev_speed_runtime_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    delay = (
        clamp_delay_seconds(delay_seconds)
        if delay_seconds is not None
        else DEFAULT_DELAY_SECONDS
    )
    # Preserve existing delay when only toggling enabled.
    if delay_seconds is None and path.is_file():
        prev = load_dev_speed_runtime(data_dir)
        delay = clamp_delay_seconds(prev.delay_seconds)
    body = {
        "enabled": bool(enabled),
        "delay_seconds": delay,
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(body, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
