"""isolation_enabled() via ELYRA_SANDBOX env (product default on)."""

from __future__ import annotations

import pytest

from elyra.sandbox import ENV_ELYRA_SANDBOX, isolation_enabled
from elyra.sandbox.paths import isolation_enabled as isolation_enabled_paths


def test_isolation_enabled_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_ELYRA_SANDBOX, raising=False)
    assert isolation_enabled() is True
    assert isolation_enabled_paths() is True


@pytest.mark.parametrize(
    "value",
    ["1", "true", "TRUE", "yes", "on", "On", "enabled"],
)
def test_isolation_enabled_truthy(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ENV_ELYRA_SANDBOX, value)
    assert isolation_enabled() is True


@pytest.mark.parametrize(
    "value",
    ["0", "false", "FALSE", "no", "off", "Off", ""],
)
def test_isolation_enabled_falsy(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ENV_ELYRA_SANDBOX, value)
    assert isolation_enabled() is False


def test_isolation_enabled_whitespace_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_ELYRA_SANDBOX, "  0  ")
    assert isolation_enabled() is False


def test_isolation_enabled_unknown_nonempty_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unknown non-empty → treat as enabled (fail-closed isolation path).
    monkeypatch.setenv(ENV_ELYRA_SANDBOX, "maybe")
    assert isolation_enabled() is True
