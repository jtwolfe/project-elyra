"""Grok Build instrument modes, timeouts, and async defaults.

Scope: Mode enum, default wall timeouts, ASYNC_TIMEOUT_THRESHOLD_S,
defaults_async(mode), deep_research experimental flag, slash skill names.
In scope: pure constants and pure predicates used by argv/validate/jobs.
Out of scope: subprocess, auth, jobs reaper, registry, filesystem I/O.
"""

from __future__ import annotations

from enum import Enum


# KD11: modes whose default timeout exceeds this default to async jobs.
ASYNC_TIMEOUT_THRESHOLD_S: int = 15 * 60

# KD16: deep_research stays experimental until PR0a spike signs a contract.
DEEP_RESEARCH_EXPERIMENTAL: bool = True

# Integration tip for execute_plan / branch-law (not main / not grok-improvement).
DEFAULT_BASE_BRANCH: str = "working"


class Mode(str, Enum):
    """Schema-frozen grok_build modes (v1; pr_babysit deferred)."""

    PROMPT = "prompt"
    DESIGN = "design"
    IMPLEMENT = "implement"
    EXECUTE_PLAN = "execute_plan"
    DEEP_RESEARCH = "deep_research"
    REVIEW = "review"

    @classmethod
    def parse(cls, value: str | Mode | None) -> Mode | None:
        """Parse a mode string; return None if unknown."""
        if value is None:
            return None
        if isinstance(value, Mode):
            return value
        if not isinstance(value, str):
            return None
        key = value.strip().casefold()
        for member in cls:
            if member.value == key:
                return member
        return None


# Default wall-clock timeouts (seconds). See design mode table.
DEFAULT_TIMEOUT_S: dict[Mode, int] = {
    Mode.PROMPT: 10 * 60,
    Mode.DESIGN: 90 * 60,
    Mode.IMPLEMENT: 120 * 60,
    Mode.EXECUTE_PLAN: 6 * 60 * 60,
    Mode.DEEP_RESEARCH: 60 * 60,
    Mode.REVIEW: 45 * 60,
}

# Hard caps for timeout_seconds overrides (same as defaults for v1; handler may tighten).
MAX_TIMEOUT_S: dict[Mode, int] = dict(DEFAULT_TIMEOUT_S)

# Slash skill / workflow name inside -p body (not CLI flags).
SLASH_PREFIX: dict[Mode, str] = {
    Mode.PROMPT: "",  # free-form; no slash
    Mode.DESIGN: "/design",
    Mode.IMPLEMENT: "/implement",
    Mode.EXECUTE_PLAN: "/execute-plan",
    Mode.DEEP_RESEARCH: "/deep-research",
    Mode.REVIEW: "/review",
}

# Modes that inject headless human-gate policy text (KD15).
HUMAN_GATE_MODES: frozenset[Mode] = frozenset(
    {
        Mode.DESIGN,
        Mode.IMPLEMENT,
        Mode.EXECUTE_PLAN,
        Mode.REVIEW,
    }
)

# Modes that expect a primary artifact under run_dir/artifacts/ (KD17).
ARTIFACT_REQUIRED_MODES: frozenset[Mode] = frozenset(
    {
        Mode.DESIGN,
        Mode.REVIEW,
    }
)

# Modes that default to async because timeout > ASYNC_TIMEOUT_THRESHOLD_S.
# Derived from DEFAULT_TIMEOUT_S — do not hardcode a second list.
def defaults_async(mode: Mode | str) -> bool:
    """Return True when this mode defaults to async job (timeout > 15 min).

    Sync-by-default only for ``prompt`` (10m). Callers may still pass
    ``async=false`` for operator/debug on long modes.
    """
    m = Mode.parse(mode) if not isinstance(mode, Mode) else mode
    if m is None:
        return False
    timeout = DEFAULT_TIMEOUT_S.get(m, 0)
    return timeout > ASYNC_TIMEOUT_THRESHOLD_S


def default_timeout_s(mode: Mode | str) -> int | None:
    """Return default wall timeout seconds for mode, or None if unknown."""
    m = Mode.parse(mode) if not isinstance(mode, Mode) else mode
    if m is None:
        return None
    return DEFAULT_TIMEOUT_S[m]


def is_long_mode(mode: Mode | str) -> bool:
    """Alias for defaults_async — long modes need jobs/reaper (PR3)."""
    return defaults_async(mode)


__all__ = [
    "ARTIFACT_REQUIRED_MODES",
    "ASYNC_TIMEOUT_THRESHOLD_S",
    "DEEP_RESEARCH_EXPERIMENTAL",
    "DEFAULT_BASE_BRANCH",
    "DEFAULT_TIMEOUT_S",
    "HUMAN_GATE_MODES",
    "MAX_TIMEOUT_S",
    "Mode",
    "SLASH_PREFIX",
    "default_timeout_s",
    "defaults_async",
    "is_long_mode",
]
