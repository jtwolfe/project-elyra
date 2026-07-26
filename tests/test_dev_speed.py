"""Dev speed mode: runtime JSON, clamp, hop delay effective value."""

from __future__ import annotations

from pathlib import Path

from elyra.runtime.dev_speed import (
    DEFAULT_DELAY_SECONDS,
    DEFAULT_ENABLED,
    DevSpeedState,
    clamp_delay_seconds,
    dev_speed_status_block,
    effective_hop_delay_seconds,
    load_dev_speed_runtime,
    save_dev_speed_runtime,
)


def test_defaults_on_with_8s() -> None:
    s = DevSpeedState()
    assert s.enabled is DEFAULT_ENABLED is True
    assert s.delay_seconds == DEFAULT_DELAY_SECONDS == 8.0
    assert effective_hop_delay_seconds(s) == 8.0


def test_clamp_band() -> None:
    assert clamp_delay_seconds(1) == 5.0
    assert clamp_delay_seconds(20) == 15.0
    assert clamp_delay_seconds(9) == 9.0


def test_effective_zero_when_off() -> None:
    s = DevSpeedState(enabled=False, delay_seconds=10)
    assert effective_hop_delay_seconds(s) == 0.0


def test_load_save_roundtrip(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    save_dev_speed_runtime(data, enabled=False, delay_seconds=12)
    loaded = load_dev_speed_runtime(data)
    assert loaded.enabled is False
    assert loaded.delay_seconds == 12.0
    block = dev_speed_status_block(loaded)
    assert block["enabled"] is False
    assert block["delay_seconds"] == 12.0
    assert block["effective_hop_delay_seconds"] == 0.0


def test_missing_file_uses_defaults(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    loaded = load_dev_speed_runtime(data)
    assert loaded.enabled is True
    assert loaded.delay_seconds == 8.0
