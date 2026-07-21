"""Time-based continue inject and wall-clock backstop (pure policy).

Scope: idle continue inject decisions from settings knobs.
In scope: continue_idle_minutes, continue_max_injects, moment_wall_clock_minutes.
Out of scope: do-loop scheduling, HOST message delivery, hop thrash (max_tool_hops).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from elyra.settings import LoopSettings, Settings, default_settings

# Defaults match LoopSettings / design Stretch 1.
DEFAULT_CONTINUE_IDLE_MINUTES = 8
DEFAULT_CONTINUE_MAX_INJECTS = 3
DEFAULT_MOMENT_WALL_CLOCK_MINUTES = 45

CONTINUE_HOST_TEMPLATE = (
    "HOST: {minutes} minutes idle on this work — "
    "continue / speak / wait / stop / schedule?"
)


def _loop_settings(settings: Settings | LoopSettings | None) -> LoopSettings:
    if settings is None:
        return default_settings().loop
    if isinstance(settings, LoopSettings):
        return settings
    return settings.loop


def continue_host_message(idle_minutes: int) -> str:
    """HOST continue line injected into the in-turn chain (obs / user)."""
    return CONTINUE_HOST_TEMPLATE.format(minutes=idle_minutes)


def _idle_minutes(last_activity: datetime, now: datetime) -> float:
    delta = now - last_activity
    return delta.total_seconds() / 60.0


def should_inject_continue(
    last_activity: datetime,
    injects_so_far: int,
    now: datetime,
    *,
    continue_idle_minutes: int | None = None,
    continue_max_injects: int | None = None,
    settings: Settings | LoopSettings | None = None,
) -> bool:
    """True when idle long enough and inject budget remains.

    Idle is measured since last speak or task change (caller tracks that).
    Injecting resets idle for the next check (caller updates last_activity).
    """
    loop = _loop_settings(settings)
    idle_min = (
        continue_idle_minutes
        if continue_idle_minutes is not None
        else loop.continue_idle_minutes
    )
    max_injects = (
        continue_max_injects
        if continue_max_injects is not None
        else loop.continue_max_injects
    )
    if injects_so_far < 0:
        injects_so_far = 0
    if injects_so_far >= max_injects:
        return False
    if idle_min < 0:
        idle_min = 0
    return (now - last_activity) >= timedelta(minutes=idle_min)


def should_stop_time_continue_declined(
    last_activity: datetime,
    injects_so_far: int,
    now: datetime,
    *,
    continue_idle_minutes: int | None = None,
    continue_max_injects: int | None = None,
    settings: Settings | LoopSettings | None = None,
) -> bool:
    """True when max continue injects exhausted and work is still idle.

    Matches multi-hop pre-check:
    ``continue_injects >= continue_max and still idle → time_continue_declined``.
    """
    loop = _loop_settings(settings)
    idle_min = (
        continue_idle_minutes
        if continue_idle_minutes is not None
        else loop.continue_idle_minutes
    )
    max_injects = (
        continue_max_injects
        if continue_max_injects is not None
        else loop.continue_max_injects
    )
    if injects_so_far < max_injects:
        return False
    if idle_min < 0:
        idle_min = 0
    return (now - last_activity) >= timedelta(minutes=idle_min)


def should_stop_wall_clock(
    started_at: datetime,
    now: datetime,
    *,
    moment_wall_clock_minutes: int | None = None,
    settings: Settings | LoopSettings | None = None,
) -> bool:
    """True when moment absolute wall-clock budget is exceeded."""
    loop = _loop_settings(settings)
    wall = (
        moment_wall_clock_minutes
        if moment_wall_clock_minutes is not None
        else loop.moment_wall_clock_minutes
    )
    if wall < 0:
        wall = 0
    return (now - started_at) >= timedelta(minutes=wall)


def idle_minutes_since(last_activity: datetime, now: datetime) -> float:
    """Elapsed minutes since last activity (may be fractional)."""
    return _idle_minutes(last_activity, now)
