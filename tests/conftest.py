"""Root pytest fixtures — hermetic defaults for the suite.

Product default is isolation **on** when ``ELYRA_SANDBOX`` is unset. CI and
unit tests must stay hermetic (host stub / no KVM), so this autouse fixture
sets ``ELYRA_SANDBOX=0`` unless the test already set the env var.

Tests that exercise guest / Fake isolation paths should call
``monkeypatch.delenv("ELYRA_SANDBOX", raising=False)`` (or set ``1``).
"""

from __future__ import annotations

import os

import pytest

from elyra.sandbox.paths import ENV_ELYRA_SANDBOX


@pytest.fixture(autouse=True)
def _hermetic_sandbox_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force isolation off for hermetic host-stub tests unless already set."""
    if ENV_ELYRA_SANDBOX not in os.environ:
        monkeypatch.setenv(ENV_ELYRA_SANDBOX, "0")


@pytest.fixture(autouse=True)
def _reset_media_rate_limits() -> None:
    """PR10: process-local STT/TTS windows must not leak across tests."""
    from elyra.media.limits import reset_rate_limits_for_tests

    reset_rate_limits_for_tests()
    yield
    reset_rate_limits_for_tests()
