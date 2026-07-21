"""Tests for wait_user and schedule_wake social tools (PR8b).

Behaviour: ends_moment + arm_wait; durable wait via TimerService; schedule timer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.presence import TimerService, WakeQueue
from elyra.presence.timers import STATUS_PENDING, STATUS_SCHEDULED, parse_utc
from elyra.settings import Settings, WaitSettings, default_settings
from elyra.tools import ToolContext, ToolRegistry, ToolResult, WaitArm
from elyra.tools.builtin.social import schedule_wake, wait_user
from elyra.tools.policy import resolve_bundled_tools_root


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


@pytest.fixture
def queue(paths) -> WakeQueue:
    return WakeQueue(paths)


@pytest.fixture
def timers(paths, queue: WakeQueue) -> TimerService:
    return TimerService(paths, queue)


@pytest.fixture
def settings() -> Settings:
    return default_settings()


@pytest.fixture
def ctx(paths, timers: TimerService, settings: Settings) -> ToolContext:
    return ToolContext(
        paths=paths,
        timers=timers,
        settings=settings,
        moment_id="moment-wait-1",
        user_id="operator",
    )


@pytest.fixture
def registry(home: Path) -> ToolRegistry:
    return ToolRegistry(
        resolve_paths(home),
        bundled_root=resolve_bundled_tools_root(),
    )


# ---------------------------------------------------------------------------
# wait_user
# ---------------------------------------------------------------------------


def test_wait_user_returns_ends_moment_and_arm_wait(ctx: ToolContext) -> None:
    result = wait_user(
        {"prompt": "Ready to continue?", "choices": ["yes", "no"]},
        ctx,
    )
    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert result.ends_moment is True
    assert result.stop_reason == "wait"
    assert result.counts_as_speak is False
    assert result.error_reason is None

    arm = result.arm_wait
    assert isinstance(arm, WaitArm)
    assert arm.prompt == "Ready to continue?"
    assert arm.choices == ["yes", "no"]
    assert arm.user_id == "operator"
    assert arm.timeout_seconds == 120  # settings default
    assert arm.wait_id
    assert result.payload["wait_id"] == arm.wait_id
    assert result.payload["timeout_seconds"] == 120
    assert result.payload["choices"] == ["yes", "no"]
    assert result.payload["armed"] is True


def test_wait_user_default_timeout_from_settings(paths, timers: TimerService) -> None:
    settings = Settings(wait=WaitSettings(default_timeout_seconds=45))
    ctx = ToolContext(
        paths=paths,
        timers=timers,
        settings=settings,
        moment_id="m",
        user_id="operator",
    )
    result = wait_user({"prompt": "Pick"}, ctx)
    assert result.ok is True
    assert result.arm_wait is not None
    assert result.arm_wait.timeout_seconds == 45
    assert result.payload["timeout_seconds"] == 45


def test_wait_user_default_timeout_without_settings(paths, timers: TimerService) -> None:
    ctx = ToolContext(paths=paths, timers=timers, moment_id="m")
    result = wait_user({"prompt": "?"}, ctx)
    assert result.ok is True
    assert result.arm_wait is not None
    assert result.arm_wait.timeout_seconds == 120


def test_wait_user_explicit_timeout(ctx: ToolContext) -> None:
    result = wait_user({"prompt": "?", "timeout_seconds": 30}, ctx)
    assert result.ok is True
    assert result.arm_wait is not None
    assert result.arm_wait.timeout_seconds == 30


def test_wait_user_arms_durable_wait_on_timers(
    ctx: ToolContext, timers: TimerService
) -> None:
    result = wait_user(
        {"prompt": "Choose", "choices": ["A", "B"], "timeout_seconds": 60},
        ctx,
    )
    assert result.ok is True
    arm = result.arm_wait
    assert arm is not None
    pending = timers.get_wait(arm.wait_id)
    assert pending is not None
    assert pending.status == STATUS_PENDING
    assert pending.prompt == "Choose"
    assert pending.choices == ["A", "B"]
    assert pending.user_id == "operator"
    assert pending.moment_id == "moment-wait-1"
    assert pending.timeout == 60.0
    assert pending.expires_at


def test_wait_user_without_timers_still_returns_arm_wait(paths) -> None:
    """Loop may arm later; tool still sets ends_moment + arm_wait without store."""
    ctx = ToolContext(
        paths=paths,
        settings=default_settings(),
        moment_id="m2",
        user_id="alice",
    )
    result = wait_user({"prompt": "Go?", "choices": ["y"]}, ctx)
    assert result.ok is True
    assert result.ends_moment is True
    assert result.stop_reason == "wait"
    assert result.arm_wait is not None
    assert result.arm_wait.user_id == "alice"
    assert result.payload["armed"] is False


def test_wait_user_missing_prompt(ctx: ToolContext) -> None:
    result = wait_user({}, ctx)
    assert result.ok is False
    assert result.ends_moment is False
    assert result.arm_wait is None
    assert result.error_reason == "missing_prompt"
    assert result.counts_as_speak is False


def test_wait_user_empty_prompt(ctx: ToolContext) -> None:
    result = wait_user({"prompt": "   "}, ctx)
    assert result.ok is False
    assert result.ends_moment is False
    assert result.error_reason == "empty_prompt"


def test_wait_user_invalid_prompt_type(ctx: ToolContext) -> None:
    for bad in (42, True, ["x"], None):
        # None with key present
        result = wait_user({"prompt": bad}, ctx)
        assert result.ok is False
        assert result.ends_moment is False
        if bad is None:
            assert result.error_reason == "invalid_prompt"
        else:
            assert result.error_reason == "invalid_prompt"


def test_wait_user_invalid_choices(ctx: ToolContext) -> None:
    result = wait_user({"prompt": "?", "choices": "yes"}, ctx)
    assert result.ok is False
    assert result.error_reason == "invalid_choices"
    assert result.ends_moment is False

    result2 = wait_user({"prompt": "?", "choices": [1, 2]}, ctx)
    assert result2.ok is False
    assert result2.error_reason == "invalid_choices"


def test_wait_user_invalid_timeout(ctx: ToolContext) -> None:
    for bad in (-1, 1.5, True, "30"):
        result = wait_user({"prompt": "?", "timeout_seconds": bad}, ctx)
        assert result.ok is False, bad
        assert result.error_reason == "invalid_timeout"
        assert result.ends_moment is False


def test_wait_user_args_user_id_overrides_context(paths, timers: TimerService) -> None:
    ctx = ToolContext(
        paths=paths,
        timers=timers,
        settings=default_settings(),
        user_id="operator",
    )
    result = wait_user({"prompt": "Hi", "user_id": "jim"}, ctx)
    assert result.ok is True
    assert result.arm_wait is not None
    assert result.arm_wait.user_id == "jim"
    assert timers.get_wait(result.arm_wait.wait_id).user_id == "jim"


def test_wait_user_free_text_empty_choices(ctx: ToolContext) -> None:
    result = wait_user({"prompt": "Anything to add?"}, ctx)
    assert result.ok is True
    assert result.arm_wait is not None
    assert result.arm_wait.choices == []


def test_wait_user_arm_wait_failed_no_ends_moment(
    paths, timers: TimerService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TimerService.arm_wait raise → ok=False, no ends_moment, no arm_wait."""
    def boom(**_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(timers, "arm_wait", boom)
    ctx = ToolContext(
        paths=paths,
        timers=timers,
        settings=default_settings(),
        moment_id="m-fail",
        user_id="operator",
    )
    result = wait_user({"prompt": "Still there?"}, ctx)
    assert result.ok is False
    assert result.ends_moment is False
    assert result.arm_wait is None
    assert result.stop_reason is None
    assert result.counts_as_speak is False
    assert result.error_reason == "arm_wait_failed:OSError"
    assert result.payload["reason"] == "arm_wait_failed:OSError"


def test_wait_user_arm_wait_value_error_no_ends_moment(
    paths, timers: TimerService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**_kwargs):
        raise ValueError("expires_at or timeout is required")

    monkeypatch.setattr(timers, "arm_wait", boom)
    ctx = ToolContext(paths=paths, timers=timers, moment_id="m")
    result = wait_user({"prompt": "?"}, ctx)
    assert result.ok is False
    assert result.ends_moment is False
    assert result.arm_wait is None
    assert result.error_reason == "arm_wait_failed:ValueError"


# ---------------------------------------------------------------------------
# schedule_wake
# ---------------------------------------------------------------------------


def test_schedule_wake_creates_timer(ctx: ToolContext, timers: TimerService) -> None:
    wake_at = "2030-01-01T00:00:00Z"
    result = schedule_wake(
        {"wake_at": wake_at, "reason": "check inbox", "goal_id": "G1"},
        ctx,
    )
    assert result.ok is True
    assert result.ends_moment is False
    assert result.stop_reason is None
    assert result.arm_wait is None
    assert result.counts_as_speak is False
    assert result.payload["timer_id"]
    assert result.payload["wake_at"] == wake_at
    assert result.payload["reason"] == "check inbox"
    assert result.payload["goal_id"] == "G1"
    assert result.payload["status"] == STATUS_SCHEDULED

    listed = timers.list_timers(status=STATUS_SCHEDULED)
    assert len(listed) == 1
    assert listed[0].id == result.payload["timer_id"]
    assert listed[0].reason == "check inbox"
    assert listed[0].goal_id == "G1"


def test_schedule_wake_delay_seconds(ctx: ToolContext, timers: TimerService) -> None:
    before = datetime.now(UTC)
    result = schedule_wake({"delay_seconds": 90, "reason": "soon"}, ctx)
    assert result.ok is True
    wake_at = parse_utc(result.payload["wake_at"])
    # ~90s from now (allow small clock skew)
    delta = (wake_at - before).total_seconds()
    assert 85 <= delta <= 100

    listed = timers.list_timers(status=STATUS_SCHEDULED)
    assert len(listed) == 1
    assert listed[0].id == result.payload["timer_id"]


def test_schedule_wake_fires_via_schedule_due(
    ctx: ToolContext, timers: TimerService, queue: WakeQueue
) -> None:
    result = schedule_wake(
        {"wake_at": "2020-01-01T00:00:00Z", "reason": "past due"},
        ctx,
    )
    assert result.ok is True
    timer_id = result.payload["timer_id"]
    fired = timers.schedule_due(now="2021-01-01T00:00:00Z")
    assert len(fired) == 1
    assert fired[0].kind == "timer"
    assert fired[0].payload["timer_id"] == timer_id
    assert fired[0].payload["reason"] == "past due"
    assert queue.peek() is not None
    assert queue.peek().kind == "timer"


def test_schedule_wake_without_timers(paths) -> None:
    ctx = ToolContext(paths=paths)
    result = schedule_wake({"delay_seconds": 10}, ctx)
    assert result.ok is False
    assert result.error_reason == "timers_unavailable"
    assert result.payload["reason"] == "timers_unavailable"


def test_schedule_wake_timers_from_extras(paths, timers: TimerService) -> None:
    ctx = ToolContext(paths=paths, extras={"timers": timers})
    result = schedule_wake({"wake_at": "2030-06-01T12:00:00Z", "reason": "x"}, ctx)
    assert result.ok is True
    assert timers.list_timers()


def test_schedule_wake_missing_when(ctx: ToolContext) -> None:
    result = schedule_wake({"reason": "no when"}, ctx)
    assert result.ok is False
    assert result.error_reason == "missing_when"


def test_schedule_wake_ambiguous_when(ctx: ToolContext) -> None:
    result = schedule_wake(
        {"wake_at": "2030-01-01T00:00:00Z", "delay_seconds": 10},
        ctx,
    )
    assert result.ok is False
    assert result.error_reason == "ambiguous_when"


def test_schedule_wake_invalid_wake_at(ctx: ToolContext) -> None:
    result = schedule_wake({"wake_at": "not-a-date"}, ctx)
    assert result.ok is False
    assert result.error_reason == "invalid_wake_at"


def test_schedule_wake_invalid_delay(ctx: ToolContext) -> None:
    for bad in (-5, True, "10"):
        result = schedule_wake({"delay_seconds": bad}, ctx)
        assert result.ok is False, bad
        assert result.error_reason == "invalid_delay_seconds"


def test_schedule_wake_invalid_reason(ctx: ToolContext) -> None:
    result = schedule_wake({"delay_seconds": 5, "reason": 123}, ctx)
    assert result.ok is False
    assert result.error_reason == "invalid_reason"
    assert result.ends_moment is False


def test_schedule_wake_invalid_goal_id(ctx: ToolContext) -> None:
    result = schedule_wake({"delay_seconds": 5, "goal_id": 99}, ctx)
    assert result.ok is False
    assert result.error_reason == "invalid_goal_id"


def test_schedule_wake_invalid_task_id(ctx: ToolContext) -> None:
    result = schedule_wake({"delay_seconds": 5, "task_id": ["t"]}, ctx)
    assert result.ok is False
    assert result.error_reason == "invalid_task_id"


def test_schedule_wake_task_and_goal_ids(ctx: ToolContext, timers: TimerService) -> None:
    result = schedule_wake(
        {
            "delay_seconds": 1,
            "reason": "task nudge",
            "goal_id": "g-1",
            "task_id": "t-1",
        },
        ctx,
    )
    assert result.ok is True
    assert result.payload["goal_id"] == "g-1"
    assert result.payload["task_id"] == "t-1"
    t = timers.list_timers()[0]
    assert t.goal_id == "g-1"
    assert t.task_id == "t-1"


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


def test_registry_discovers_wait_user_and_schedule_wake(
    registry: ToolRegistry,
) -> None:
    assert registry.has("wait_user")
    assert registry.has("schedule_wake")
    wu = registry.get("wait_user")
    sw = registry.get("schedule_wake")
    assert wu is not None and sw is not None
    assert wu.meta.kind == "control"
    assert sw.meta.kind == "control"
    assert "prompt" in wu.meta.parameters.get("required", [])
    assert wu.runner.kind == "builtin"
    assert wu.handler is not None
    assert sw.handler is not None


def test_registry_execute_wait_user_preserves_control_flags(
    registry: ToolRegistry, paths, timers: TimerService
) -> None:
    """kind=control allowlists ends_moment / stop_reason / arm_wait."""
    ctx = ToolContext(
        paths=paths,
        timers=timers,
        settings=default_settings(),
        moment_id="reg-wait",
        user_id="operator",
    )
    result = registry.execute(
        "wait_user",
        {"prompt": "Continue?", "choices": ["y", "n"]},
        ctx,
    )
    assert result.ok is True
    assert result.ends_moment is True
    assert result.stop_reason == "wait"
    assert result.arm_wait is not None
    assert result.arm_wait.prompt == "Continue?"
    assert result.counts_as_speak is False
    assert timers.get_wait(result.arm_wait.wait_id) is not None


def test_registry_execute_schedule_wake(
    registry: ToolRegistry, paths, timers: TimerService
) -> None:
    ctx = ToolContext(paths=paths, timers=timers)
    result = registry.execute(
        "schedule_wake",
        {"wake_at": "2031-01-01T00:00:00Z", "reason": "via registry"},
        ctx,
    )
    assert result.ok is True
    assert result.ends_moment is False
    assert result.payload["reason"] == "via registry"
    assert len(timers.list_timers()) == 1


def test_openai_tools_includes_social_wait_tools(registry: ToolRegistry) -> None:
    tools = registry.openai_tools()
    names = [t["function"]["name"] for t in tools]
    assert "wait_user" in names
    assert "schedule_wake" in names
    assert "speak" in names
    wait_tool = next(t for t in tools if t["function"]["name"] == "wait_user")
    assert "prompt" in wait_tool["function"]["parameters"]["properties"]
    assert "prompt" in wait_tool["function"]["parameters"]["required"]
