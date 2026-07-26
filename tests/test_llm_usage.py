"""Unit tests for hierarchical UsageMeter (elyra.llm.usage)."""

from __future__ import annotations

import ast
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyra.llm.credits import CreditsSnapshot
from elyra.llm.usage import (
    TokenUsage,
    UsageHardStopError,
    UsageMeter,
    UsageSnapshot,
    burst_max,
    burst_remaining,
    compute_band,
    compute_limits,
    effective_overshoot,
    elapsed_hours,
    hard_level,
    linear_schedule,
    pace_ratio,
    parse_token_usage,
    period_hours,
    window_ids,
)
from elyra.settings import UsageSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Clock:
    """Mutable injectable clock for window rollover tests."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)


def _settings(**kwargs: object) -> UsageSettings:
    base = dict(
        enabled=True,
        weekly_allowed_tokens=7000,
        weekly_allowed_fraction=0.50,
        hour_block_minutes=60,
        day_allowed_tokens=None,
        hour_allowed_tokens=None,
    )
    base.update(kwargs)
    return UsageSettings(**base)  # type: ignore[arg-type]


def _meter(tmp_path: Path, settings: UsageSettings | None = None, **kwargs) -> UsageMeter:
    s = settings or _settings()
    return UsageMeter.load(tmp_path, s, **kwargs)


# ---------------------------------------------------------------------------
# TokenUsage / parse_token_usage
# ---------------------------------------------------------------------------


def test_token_usage_billable_prefers_total():
    u = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=40)
    assert u.billable_tokens == 40


def test_token_usage_billable_falls_back_to_sum():
    u = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=0)
    assert u.billable_tokens == 15


def test_token_usage_billable_zero_when_empty():
    assert TokenUsage().billable_tokens == 0


def test_parse_token_usage_openai_shape():
    raw = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "completion_tokens_details": {"reasoning_tokens": 12},
    }
    u = parse_token_usage(raw)
    assert u is not None
    assert u.prompt_tokens == 100
    assert u.completion_tokens == 50
    assert u.total_tokens == 150
    assert u.reasoning_tokens == 12
    assert u.cached_tokens == 0
    assert u.billable_tokens == 150


def test_parse_token_usage_cached_from_prompt_tokens_details():
    raw = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "prompt_tokens_details": {"cached_tokens": 40},
    }
    u = parse_token_usage(raw)
    assert u is not None
    assert u.cached_tokens == 40
    # Billable is total_tokens; do not subtract cached.
    assert u.billable_tokens == 150


def test_parse_token_usage_cached_top_level():
    raw = {
        "prompt_tokens": 80,
        "completion_tokens": 20,
        "total_tokens": 100,
        "cached_tokens": 25,
    }
    u = parse_token_usage(raw)
    assert u is not None
    assert u.cached_tokens == 25
    assert u.billable_tokens == 100


def test_parse_token_usage_prompt_details_preferred_over_top_level_cached():
    raw = {
        "prompt_tokens": 100,
        "completion_tokens": 10,
        "total_tokens": 110,
        "cached_tokens": 99,
        "prompt_tokens_details": {"cached_tokens": 30},
    }
    u = parse_token_usage(raw)
    assert u is not None
    assert u.cached_tokens == 30


def test_parse_token_usage_cached_invalid_types_zero():
    raw = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "prompt_tokens_details": {"cached_tokens": "nope"},
    }
    u = parse_token_usage(raw)
    assert u is not None
    assert u.cached_tokens == 0
    assert u.billable_tokens == 15

    raw2 = {
        "prompt_tokens": 10,
        "total_tokens": 10,
        "prompt_tokens_details": "not-a-dict",
        "cached_tokens": True,  # bool excluded like other fields
    }
    u2 = parse_token_usage(raw2)
    assert u2 is not None
    assert u2.cached_tokens == 0


def test_token_usage_billable_ignores_cached():
    """cached_tokens is informational; never reduces billable_tokens."""
    u = TokenUsage(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cached_tokens=100,
    )
    assert u.billable_tokens == 150
    u2 = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=0, cached_tokens=8)
    assert u2.billable_tokens == 15


def test_parse_token_usage_missing_returns_none():
    assert parse_token_usage(None) is None
    assert parse_token_usage("nope") is None
    assert parse_token_usage({}) is None
    assert parse_token_usage({"foo": 1}) is None


def test_parse_token_usage_partial_fields():
    u = parse_token_usage({"prompt_tokens": 3})
    assert u is not None
    assert u.prompt_tokens == 3
    assert u.completion_tokens == 0
    assert u.cached_tokens == 0
    assert u.billable_tokens == 3


# ---------------------------------------------------------------------------
# Limits / window ids
# ---------------------------------------------------------------------------


def test_compute_limits_defaults_from_weekly():
    s = UsageSettings(weekly_allowed_tokens=5_000_000, hour_block_minutes=60)
    week, day, hour = compute_limits(s)
    assert week == 5_000_000
    assert day == 5_000_000 // 7
    assert hour == day // 24


def test_compute_limits_explicit_day_hour():
    s = UsageSettings(
        weekly_allowed_tokens=1000,
        day_allowed_tokens=100,
        hour_allowed_tokens=10,
    )
    assert compute_limits(s) == (1000, 100, 10)


def test_compute_limits_ignores_fraction():
    """weekly_allowed_fraction must not affect enforcement math."""
    a = compute_limits(UsageSettings(weekly_allowed_tokens=7000, weekly_allowed_fraction=0.5))
    b = compute_limits(UsageSettings(weekly_allowed_tokens=7000, weekly_allowed_fraction=1.0))
    assert a == b


def test_window_ids_utc_iso_week():
    # 2026-07-24 is Friday of ISO week 30
    now = datetime(2026, 7, 24, 14, 22, 1, tzinfo=UTC)
    week_id, day_id, hour_id = window_ids(now, hour_block_minutes=60)
    assert week_id == "2026-W30"
    assert day_id == "2026-07-24"
    assert hour_id == "2026-07-24T14"


# ---------------------------------------------------------------------------
# UsageHardStopError
# ---------------------------------------------------------------------------


def test_usage_hard_stop_error_fields():
    err = UsageHardStopError("week budget exhausted", level="week")
    assert err.reason == "week budget exhausted"
    assert err.level == "week"
    assert isinstance(err, RuntimeError)
    assert "week budget exhausted" in str(err)


# ---------------------------------------------------------------------------
# Meter: load / missing / corrupt
# ---------------------------------------------------------------------------


def test_load_missing_file_zeroed_override_false(tmp_path: Path):
    m = _meter(tmp_path)
    snap = m.snapshot()
    assert snap.week_used_tokens == 0
    assert snap.day_used_tokens == 0
    assert snap.hour_used_tokens == 0
    assert snap.override_active is False
    assert snap.hard_stop is None
    assert m.can_call() is True
    assert not (tmp_path / "runtime" / "usage.json").exists()


def test_load_corrupt_json_fail_soft_override_false(tmp_path: Path, caplog):
    path = tmp_path / "runtime" / "usage.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not valid json!!", encoding="utf-8")
    with caplog.at_level("WARNING"):
        m = _meter(tmp_path)
    snap = m.snapshot()
    assert snap.week_used_tokens == 0
    assert snap.override_active is False
    assert m.can_call() is True
    assert any("corrupt" in r.message.lower() or "usage.json" in r.message for r in caplog.records)


def test_load_corrupt_non_object_fail_soft(tmp_path: Path):
    path = tmp_path / "runtime" / "usage.json"
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2, 3]\n", encoding="utf-8")
    m = _meter(tmp_path)
    assert m.snapshot().override_active is False
    assert m.snapshot().week_used_tokens == 0


def test_load_missing_override_defaults_false(tmp_path: Path):
    path = tmp_path / "runtime" / "usage.json"
    path.parent.mkdir(parents=True)
    now = datetime(2026, 7, 24, 14, 0, tzinfo=UTC)
    body = {
        "schema_version": 1,
        "week_id": "2026-W30",
        "day_id": "2026-07-24",
        "hour_id": "2026-07-24T14",
        "week_used_tokens": 10,
        "day_used_tokens": 10,
        "hour_used_tokens": 10,
        "last_record_at": "2026-07-24T14:00:00Z",
        # hard_stop_override intentionally omitted
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    clock = _Clock(now)
    m = UsageMeter.load(tmp_path, _settings(), clock=clock)
    assert m.snapshot().override_active is False
    assert m.snapshot().week_used_tokens == 10


def test_load_never_invents_override_on_from_truthy_string(tmp_path: Path):
    path = tmp_path / "runtime" / "usage.json"
    path.parent.mkdir(parents=True)
    now = datetime(2026, 7, 24, 14, 0, tzinfo=UTC)
    body = {
        "schema_version": 1,
        "week_id": "2026-W30",
        "day_id": "2026-07-24",
        "hour_id": "2026-07-24T14",
        "week_used_tokens": 0,
        "day_used_tokens": 0,
        "hour_used_tokens": 0,
        "hard_stop_override": "true",  # not bool True
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    m = UsageMeter.load(tmp_path, _settings(), clock=_Clock(now))
    assert m.snapshot().override_active is False


# ---------------------------------------------------------------------------
# Record / hard stop / override
# ---------------------------------------------------------------------------


def test_record_counts_and_persists(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, clock=clock)
    snap = m.record(TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150))
    assert snap.week_used_tokens == 150
    assert snap.day_used_tokens == 150
    assert snap.hour_used_tokens == 150
    assert snap.last_record_at is not None

    path = tmp_path / "runtime" / "usage.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["week_used_tokens"] == 150
    assert data["hard_stop_override"] is False
    assert data["schema_version"] == 2
    assert data["period_authority"] == "iso"
    assert "period_id" in data


def test_hard_stop_hour_then_day_then_week(tmp_path: Path):
    # Tight hour, looser day/week — flags must be ON for day/hour hard.
    s = _settings(
        weekly_allowed_tokens=1000,
        day_allowed_tokens=100,
        hour_allowed_tokens=10,
        day_hard_stop_enabled=True,
        hour_hard_stop_enabled=True,
    )
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, s, clock=clock)

    m.record(TokenUsage(total_tokens=10))
    assert m.is_over_budget() is True
    assert m.can_call() is False
    assert m.snapshot().hard_stop == "hour"
    assert m.hard_stop_reason() is not None
    assert "hour" in m.hard_stop_reason()

    # Day stop takes precedence over hour when day is also exhausted
    d2 = tmp_path / "d2"
    m2 = _meter(
        d2,
        _settings(
            weekly_allowed_tokens=1000,
            day_allowed_tokens=50,
            hour_allowed_tokens=100,
            day_hard_stop_enabled=True,
            hour_hard_stop_enabled=True,
        ),
        clock=clock,
    )
    m2.record(TokenUsage(total_tokens=50))
    assert m2.snapshot().hard_stop == "day"

    d3 = tmp_path / "d3"
    m3 = _meter(
        d3,
        _settings(
            weekly_allowed_tokens=80,
            day_allowed_tokens=1000,
            hour_allowed_tokens=1000,
            day_hard_stop_enabled=True,
            hour_hard_stop_enabled=True,
        ),
        clock=clock,
    )
    m3.record(TokenUsage(total_tokens=80))
    assert m3.snapshot().hard_stop == "week"
    assert "week" in (m3.hard_stop_reason() or "")


def test_hard_stop_precedence_week_over_day_over_hour(tmp_path: Path):
    """When all ceilings exceeded, hard_stop reports week (account none)."""
    s = _settings(
        weekly_allowed_tokens=10,
        day_allowed_tokens=10,
        hour_allowed_tokens=10,
        day_hard_stop_enabled=True,
        hour_hard_stop_enabled=True,
    )
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=10))
    assert m.snapshot().hard_stop == "week"


def test_override_allows_call_but_record_still_counts(tmp_path: Path):
    s = _settings(
        weekly_allowed_tokens=100,
        day_allowed_tokens=20,
        hour_allowed_tokens=10,
        day_hard_stop_enabled=True,
        hour_hard_stop_enabled=True,
    )
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=10))
    assert m.can_call() is False
    assert m.is_over_budget() is True

    snap = m.set_hard_stop_override(True)
    assert snap.override_active is True
    assert m.can_call() is True
    # Would-be hard_stop still visible for glass honesty
    assert m.snapshot().hard_stop == "hour"
    assert m.hard_stop_reason() is not None
    assert m.is_over_budget() is True

    # record still counts under override
    m.record(TokenUsage(total_tokens=5))
    assert m.snapshot().hour_used_tokens == 15
    assert m.snapshot().override_active is True

    # Persist override
    data = json.loads((tmp_path / "runtime" / "usage.json").read_text(encoding="utf-8"))
    assert data["hard_stop_override"] is True
    assert data["hour_used_tokens"] == 15

    # Override OFF again
    m.set_hard_stop_override(False)
    assert m.can_call() is False
    assert m.snapshot().override_active is False


def test_override_default_off_on_fresh_meter(tmp_path: Path):
    m = _meter(tmp_path)
    assert m.snapshot().override_active is False


def test_record_missing_usage_uses_estimate(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, clock=clock)
    m.record(None, estimated_if_missing=7)
    assert m.snapshot().week_used_tokens == 7
    m.record(None)  # default estimated 0
    assert m.snapshot().week_used_tokens == 7


def test_disabled_meter_always_can_call(tmp_path: Path):
    s = _settings(
        enabled=False,
        weekly_allowed_tokens=10,
        day_allowed_tokens=10,
        hour_allowed_tokens=5,
        day_hard_stop_enabled=True,
        hour_hard_stop_enabled=True,
    )
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=100))
    assert m.can_call() is True
    assert m.is_over_budget() is True  # budgets still tracked
    assert m.snapshot().enabled is False


def test_remaining_tokens(tmp_path: Path):
    s = _settings(
        weekly_allowed_tokens=100,
        day_allowed_tokens=50,
        hour_allowed_tokens=20,
    )
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=8))
    rem = m.remaining()
    assert rem == {"week": 92, "day": 42, "hour": 12}
    m.record(TokenUsage(total_tokens=20))
    rem2 = m.remaining()
    assert rem2["hour"] == 0
    assert rem2["week"] == 72


def test_snapshot_fractions(tmp_path: Path):
    s = _settings(
        weekly_allowed_tokens=100,
        day_allowed_tokens=100,
        hour_allowed_tokens=100,
    )
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=25))
    snap = m.snapshot()
    assert isinstance(snap, UsageSnapshot)
    assert snap.week_remaining_fraction == pytest.approx(0.75)
    assert snap.week_limit_tokens == 100
    assert snap.hard_stop is None


# ---------------------------------------------------------------------------
# Window rollover
# ---------------------------------------------------------------------------


def test_hour_rollover_zeros_hour_keeps_day(tmp_path: Path):
    s = _settings(
        weekly_allowed_tokens=10_000,
        day_allowed_tokens=10_000,
        hour_allowed_tokens=10_000,
    )
    clock = _Clock(datetime(2026, 7, 24, 14, 30, tzinfo=UTC))
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=40))
    assert m.snapshot().hour_used_tokens == 40
    assert m.snapshot().day_used_tokens == 40

    clock.advance(hours=1)  # 15:30 → new hour block
    m.refresh_windows()
    snap = m.snapshot()
    assert snap.hour_used_tokens == 0
    assert snap.day_used_tokens == 40
    assert snap.week_used_tokens == 40


def test_day_rollover_zeros_day_and_hour(tmp_path: Path):
    s = _settings(weekly_allowed_tokens=10_000)
    clock = _Clock(datetime(2026, 7, 24, 23, 0, tzinfo=UTC))
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=99))
    clock.advance(hours=2)  # → 2026-07-25 01:00
    m.refresh_windows()
    snap = m.snapshot()
    assert snap.day_used_tokens == 0
    assert snap.hour_used_tokens == 0
    assert snap.week_used_tokens == 99  # still same ISO week 30


def test_week_rollover_zeros_all(tmp_path: Path):
    s = _settings(weekly_allowed_tokens=10_000)
    # Sunday 2026-07-26 is still week 30; Monday 2026-07-27 is week 31
    clock = _Clock(datetime(2026, 7, 26, 12, 0, tzinfo=UTC))
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=50))
    assert window_ids(clock.now)[0] == "2026-W30"
    clock.now = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)
    assert window_ids(clock.now)[0] == "2026-W31"
    m.refresh_windows()
    snap = m.snapshot()
    assert snap.week_used_tokens == 0
    assert snap.day_used_tokens == 0
    assert snap.hour_used_tokens == 0


# ---------------------------------------------------------------------------
# Restart survival / persistence
# ---------------------------------------------------------------------------


def test_restart_survival(tmp_path: Path):
    s = _settings(
        weekly_allowed_tokens=5000,
        day_allowed_tokens=500,
        hour_allowed_tokens=100,
    )
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m1 = _meter(tmp_path, s, clock=clock)
    m1.record(TokenUsage(total_tokens=33))
    m1.set_hard_stop_override(True)

    m2 = UsageMeter.load(tmp_path, s, clock=clock)
    snap = m2.snapshot()
    assert snap.week_used_tokens == 33
    assert snap.day_used_tokens == 33
    assert snap.hour_used_tokens == 33
    assert snap.override_active is True
    assert m2.can_call() is True  # override ON


def test_restart_after_hard_stop_without_override(tmp_path: Path):
    s = _settings(
        weekly_allowed_tokens=100,
        day_allowed_tokens=20,
        hour_allowed_tokens=10,
        day_hard_stop_enabled=True,
        hour_hard_stop_enabled=True,
    )
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m1 = _meter(tmp_path, s, clock=clock)
    m1.record(TokenUsage(total_tokens=10))
    assert m1.can_call() is False

    m2 = UsageMeter.load(tmp_path, s, clock=clock)
    assert m2.can_call() is False
    assert m2.snapshot().hard_stop == "hour"
    assert m2.snapshot().override_active is False


def test_atomic_persist_no_tmp_left_behind(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, clock=clock)
    m.record(TokenUsage(total_tokens=1))
    runtime = tmp_path / "runtime"
    leftovers = list(runtime.glob("*.tmp"))
    assert leftovers == []
    assert (runtime / "usage.json").is_file()


# ---------------------------------------------------------------------------
# Concurrent-ish lock
# ---------------------------------------------------------------------------


def test_concurrent_record_totals(tmp_path: Path):
    s = _settings(
        weekly_allowed_tokens=1_000_000,
        day_allowed_tokens=1_000_000,
        hour_allowed_tokens=1_000_000,
    )
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, s, clock=clock)
    n_threads = 8
    per_thread = 25
    tokens_each = 3
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(per_thread):
                m.record(TokenUsage(total_tokens=tokens_each))
        except BaseException as exc:  # noqa: BLE001 — collect for assert
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []
    expected = n_threads * per_thread * tokens_each
    assert m.snapshot().week_used_tokens == expected
    data = json.loads((tmp_path / "runtime" / "usage.json").read_text(encoding="utf-8"))
    assert data["week_used_tokens"] == expected


# ---------------------------------------------------------------------------
# Import hygiene
# ---------------------------------------------------------------------------


def test_usage_module_does_not_import_client():
    """elyra.llm.usage must never import elyra.llm.client (cycle-free)."""
    usage_path = Path(__file__).resolve().parents[1] / "elyra" / "llm" / "usage.py"
    source = usage_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "client" not in alias.name.split("."), alias.name
                assert alias.name != "elyra.llm.client"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod != "elyra.llm.client"
            assert not mod.endswith(".client")
            assert "client" not in (mod.split(".") if mod else [])
            for alias in node.names:
                assert alias.name != "client"
        elif isinstance(node, ast.Call):
            # Guard importlib.import_module("elyra.llm.client") style loads.
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name == "import_module" and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    assert arg0.value != "elyra.llm.client"
                    assert not arg0.value.endswith(".client")
    # No executable import_module usage in this leaf module.
    assert "import_module" not in source


def test_usage_module_importable_without_client():
    import importlib
    import sys

    # Ensure a clean import works and doesn't pull client as a dependency
    # of usage itself (client may already be loaded by other tests).
    mod = importlib.import_module("elyra.llm.usage")
    assert hasattr(mod, "UsageMeter")
    assert hasattr(mod, "TokenUsage")
    assert hasattr(mod, "parse_token_usage")
    assert hasattr(mod, "UsageHardStopError")
    # usage's own globals must not reference the client module object
    for name, val in vars(mod).items():
        if name.startswith("_"):
            continue
        mod_of = getattr(val, "__module__", "") or ""
        assert not mod_of.startswith("elyra.llm.client"), name


def test_ship_default_weekly_allowed_tokens():
    from elyra.settings import default_settings

    assert default_settings().usage.weekly_allowed_tokens == 5_000_000


# ---------------------------------------------------------------------------
# Pure pace / burst (model A)
# ---------------------------------------------------------------------------


def test_pure_pace_under_schedule_green_full_burst():
    """Under-pace: over=0 → green, remaining=BurstMax."""
    B, H, k = 7000.0, 168.0, 4.0
    t = 24.0  # schedule = (7000/168)*24 ≈ 1000
    S = linear_schedule(B, H, t)  # exactly on schedule
    bmax = burst_max(B, H, k)
    assert bmax == pytest.approx(k * (B / H))
    assert effective_overshoot(S, B, H, t) == pytest.approx(0.0)
    rem = burst_remaining(S, B, H, t, k)
    assert rem == pytest.approx(bmax)
    assert compute_band(S, B, H, t, k, yellow=1.0, red=1.5) == "green"
    assert pace_ratio(S, B, H, t) == pytest.approx(1.0)


def test_pure_pace_spike_half_burst_still_green():
    """Spike over=0.5·BurstMax → remaining≈half, band green."""
    B, H, k = 7000.0, 168.0, 4.0
    t = 24.0
    schedule = linear_schedule(B, H, t)
    bmax = burst_max(B, H, k)
    S = schedule + 0.5 * bmax
    over = effective_overshoot(S, B, H, t)
    assert over == pytest.approx(0.5 * bmax)
    rem = burst_remaining(S, B, H, t, k)
    assert rem == pytest.approx(0.5 * bmax)
    assert compute_band(S, B, H, t, k, yellow=1.0, red=1.5) == "green"


def test_pure_pace_over_burst_yellow_and_red_by_pace():
    """over > BurstMax → yellow/red by pace thresholds (strict).

    For p=1.2: over = 0.2·(B/H)·t; need t > k/0.2 so over > BurstMax=k·(B/H).
    With k=4, t=24 works: over=200 > BurstMax≈166.67, band yellow.
    """
    B, H, k = 7000.0, 168.0, 4.0
    t = 24.0
    yellow, red = 1.0, 1.5
    bmax = burst_max(B, H, k)

    # Yellow: target p=1.2 with over > BurstMax
    S_y = 1.2 * (B * t) / H
    p_y = pace_ratio(S_y, B, H, t)
    over_y = effective_overshoot(S_y, B, H, t)
    assert p_y == pytest.approx(1.2)
    assert yellow <= p_y < red
    assert over_y > bmax
    assert compute_band(S_y, B, H, t, k, yellow, red) == "yellow"

    # Red: p >= 1.5 and over > BurstMax
    S_red = 2.0 * (B * t) / H
    assert pace_ratio(S_red, B, H, t) == pytest.approx(2.0)
    assert effective_overshoot(S_red, B, H, t) > bmax
    assert compute_band(S_red, B, H, t, k, yellow, red) == "red"


def test_pure_hard_level_precedence_account_week_day_hour():
    assert (
        hard_level(
            S=10,
            B=100,
            day_used=0,
            day_limit=10,
            day_hard_enabled=True,
            hour_used=0,
            hour_limit=5,
            hour_hard_enabled=True,
            account_usage_fraction=0.96,
            account_hard_fraction=0.95,
        )
        == "account"
    )
    assert (
        hard_level(
            S=100,
            B=100,
            day_used=100,
            day_limit=10,
            day_hard_enabled=True,
            hour_used=100,
            hour_limit=5,
            hour_hard_enabled=True,
            account_usage_fraction=None,
            account_hard_fraction=0.95,
        )
        == "week"
    )
    assert (
        hard_level(
            S=5,
            B=100,
            day_used=10,
            day_limit=10,
            day_hard_enabled=True,
            hour_used=100,
            hour_limit=5,
            hour_hard_enabled=True,
            account_usage_fraction=None,
            account_hard_fraction=0.95,
        )
        == "day"
    )
    assert (
        hard_level(
            S=5,
            B=100,
            day_used=0,
            day_limit=10,
            day_hard_enabled=False,
            hour_used=100,
            hour_limit=5,
            hour_hard_enabled=True,
            account_usage_fraction=None,
            account_hard_fraction=0.95,
        )
        == "hour"
    )
    # Flags off: day/hour soft only
    assert (
        hard_level(
            S=5,
            B=100,
            day_used=999,
            day_limit=10,
            day_hard_enabled=False,
            hour_used=999,
            hour_limit=5,
            hour_hard_enabled=False,
            account_usage_fraction=None,
            account_hard_fraction=0.95,
        )
        is None
    )


def test_period_hours_and_elapsed():
    assert period_hours(None, None) == 168.0
    assert period_hours("bad", "also-bad") == 168.0
    H = period_hours("2026-07-21T00:00:00Z", "2026-07-28T00:00:00Z")
    assert H == pytest.approx(168.0)
    now = datetime(2026, 7, 24, 0, 0, tzinfo=UTC)
    t = elapsed_hours(now, "2026-07-21T00:00:00Z", 168.0)
    assert t == pytest.approx(72.0)


# ---------------------------------------------------------------------------
# Meter: pace / burst / hard (schema v2)
# ---------------------------------------------------------------------------


def test_meter_under_pace_green_full_burst(tmp_path: Path):
    """B=7000, k=4, under schedule → green + remaining≈BurstMax."""
    B = 7000
    k = 4.0
    # Monday start of ISO week 30 is 2026-07-20; use mid-week with low S
    clock = _Clock(datetime(2026, 7, 24, 12, 0, tzinfo=UTC))  # Fri, ~4.5d into week
    s = _settings(weekly_allowed_tokens=B, burst_hours=k)
    m = _meter(tmp_path, s, clock=clock)
    # tiny spend so over ≈ 0
    m.record(TokenUsage(total_tokens=1))
    snap = m.snapshot()
    H = 168.0
    bmax = burst_max(B, H, k)
    assert snap.pace_band == "green"
    assert snap.burst_max_tokens == int(round(bmax))
    assert snap.burst_remaining_tokens == int(round(bmax)) or snap.burst_remaining_tokens >= int(
        round(bmax * 0.99)
    )
    assert m.can_call() is True


def test_meter_spike_half_burst_green(tmp_path: Path):
    """Worked example: schedule=1000, S=1000+0.5·BurstMax → remaining≈half, green."""
    B, k = 7000, 4.0
    H = 168.0
    bmax = burst_max(B, H, k)
    # Choose t such that schedule = 1000 → t = 1000 * H / B
    t_hours = 1000.0 * H / B
    # ISO week start Mon 2026-07-20 00:00; set clock = start + t
    week_start = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    clock = _Clock(week_start + timedelta(hours=t_hours))
    s = _settings(weekly_allowed_tokens=B, burst_hours=k)
    m = _meter(tmp_path, s, clock=clock)
    S = int(round(1000 + 0.5 * bmax))
    m.record(TokenUsage(total_tokens=S))
    snap = m.snapshot()
    assert snap.pace_band == "green"
    assert snap.burst_remaining_tokens == pytest.approx(0.5 * bmax, rel=0.05, abs=2)
    assert m.can_call() is True


def test_meter_over_burst_red_still_can_call(tmp_path: Path):
    """over>BurstMax and high pace → red band but soft (can_call true)."""
    B, k = 7000, 4.0
    H = 168.0
    week_start = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    t_hours = 24.0
    clock = _Clock(week_start + timedelta(hours=t_hours))
    s = _settings(weekly_allowed_tokens=B, burst_hours=k)
    m = _meter(tmp_path, s, clock=clock)
    # p=2 → S = 2 * B * t / H
    S = int(2.0 * B * t_hours / H)
    m.record(TokenUsage(total_tokens=S))
    snap = m.snapshot()
    assert snap.hard_stop is None
    assert snap.pace_band == "red"
    assert m.can_call() is True  # soft bands never refuse


def test_hard_week_even_with_burst_remaining(tmp_path: Path):
    """S>=B is hard week even if burst_remaining would still be >0 at lower S."""
    B = 1000
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    s = _settings(weekly_allowed_tokens=B, burst_hours=4.0)
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=B))
    snap = m.snapshot()
    assert snap.hard_stop == "week"
    assert snap.pace_band == "hard"
    assert m.can_call() is False
    # Hard ignores burst cushion: remaining may still compute >0 relative to schedule
    # but can_call is false.
    assert snap.week_used_tokens >= B


def test_day_over_week_under_hard_flags_off_can_call(tmp_path: Path):
    """Day soft-exhausted, week under B, day_hard off → can_call true."""
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    s = _settings(
        weekly_allowed_tokens=10_000,
        day_allowed_tokens=100,
        hour_allowed_tokens=50,
        day_hard_stop_enabled=False,
        hour_hard_stop_enabled=False,
    )
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=100))
    snap = m.snapshot()
    assert snap.day_soft_exhausted is True
    assert snap.hard_stop is None
    assert m.can_call() is True
    assert snap.day_hard_stop_enabled is False


def test_day_hard_flag_on_refuses(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    s = _settings(
        weekly_allowed_tokens=10_000,
        day_allowed_tokens=100,
        day_hard_stop_enabled=True,
        hour_hard_stop_enabled=False,
    )
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=100))
    assert m.snapshot().hard_stop == "day"
    assert m.can_call() is False


def test_account_hard_from_injected_snapshot(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    s = _settings(
        weekly_allowed_tokens=1_000_000,
        account_hard_stop_percent=95.0,
    )
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=10))
    assert m.can_call() is True
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=96.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
            status="ok",
            ok=True,
        )
    )
    snap = m.snapshot()
    assert snap.hard_stop == "account"
    assert "account" in (snap.hard_stop_reason or "")
    assert m.can_call() is False
    assert snap.credit_usage_percent == 96.0


def test_account_hard_beats_week(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    s = _settings(weekly_allowed_tokens=100, account_hard_stop_percent=95.0)
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=100))
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=99.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
            status="ok",
        )
    )
    assert m.snapshot().hard_stop == "account"


def test_stale_account_snapshot_no_account_hard(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    s = _settings(
        weekly_allowed_tokens=1_000_000,
        account_hard_stop_percent=95.0,
        credits_stale_after_s=3600.0,
    )
    m = _meter(tmp_path, s, clock=clock)
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=99.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T10:00:00Z",  # 4h old
            status="ok",
        )
    )
    assert m.snapshot().hard_stop is None
    assert m.can_call() is True


def test_first_adoption_retains_S(tmp_path: Path):
    """KD18: ISO → SuperGrok first adopt rewrites identity only; keeps S."""
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, _settings(weekly_allowed_tokens=5_000_000), clock=clock)
    m.record(TokenUsage(total_tokens=1_200_000))
    assert m.snapshot().period_authority == "iso"
    assert m.snapshot().week_used_tokens == 1_200_000
    old_period = m.snapshot().period_id

    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=20.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
            status="ok",
        )
    )
    snap = m.snapshot()
    assert snap.period_authority == "supergrok"
    assert snap.week_used_tokens == 1_200_000  # RETAINED
    assert snap.period_id == "2026-07-21T00:00:00Z/2026-07-28T00:00:00Z"
    assert snap.period_id != old_period
    data = json.loads((tmp_path / "runtime" / "usage.json").read_text(encoding="utf-8"))
    assert data["week_used_tokens"] == 1_200_000
    assert data["period_authority"] == "supergrok"


def test_true_roll_zeros_S_preserves_override(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, _settings(weekly_allowed_tokens=5_000_000), clock=clock)
    m.record(TokenUsage(total_tokens=5000))
    m.set_hard_stop_override(True)
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=10.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
            status="ok",
        )
    )
    assert m.snapshot().week_used_tokens == 5000
    # True roll to next period
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=1.0,
            period_start="2026-07-28T00:00:00Z",
            period_end="2026-08-04T00:00:00Z",
            fetched_at="2026-07-28T00:01:00Z",
            status="ok",
        )
    )
    snap = m.snapshot()
    assert snap.week_used_tokens == 0
    assert snap.override_active is True
    assert snap.period_id == "2026-07-28T00:00:00Z/2026-08-04T00:00:00Z"
    assert snap.period_authority == "supergrok"


def test_iso_week_roll_zeros_S_when_authority_iso(tmp_path: Path):
    s = _settings(weekly_allowed_tokens=10_000)
    clock = _Clock(datetime(2026, 7, 26, 12, 0, tzinfo=UTC))  # W30 Sunday
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=50))
    assert m.snapshot().period_authority == "iso"
    clock.now = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)  # W31
    m.refresh_windows()
    assert m.snapshot().week_used_tokens == 0
    assert m.snapshot().period_id == "2026-W31"


def test_iso_week_change_does_not_zero_S_when_supergrok(tmp_path: Path):
    s = _settings(weekly_allowed_tokens=10_000)
    clock = _Clock(datetime(2026, 7, 26, 12, 0, tzinfo=UTC))
    m = _meter(tmp_path, s, clock=clock)
    m.record(TokenUsage(total_tokens=777))
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=5.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-26T12:00:00Z",
            status="ok",
        )
    )
    assert m.snapshot().period_authority == "supergrok"
    assert m.snapshot().week_used_tokens == 777
    clock.now = datetime(2026, 7, 27, 0, 0, tzinfo=UTC)  # ISO week rolls
    m.refresh_windows()
    snap = m.snapshot()
    assert snap.week_used_tokens == 777  # NOT zeroed
    assert snap.period_authority == "supergrok"
    # week_id label updates but S retained
    assert window_ids(clock.now)[0] == "2026-W31"


def test_override_bypasses_week_hard(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, _settings(weekly_allowed_tokens=50), clock=clock)
    m.record(TokenUsage(total_tokens=50))
    assert m.can_call() is False
    m.set_hard_stop_override(True)
    assert m.can_call() is True
    assert m.snapshot().hard_stop == "week"
    m.record(TokenUsage(total_tokens=10))
    assert m.snapshot().week_used_tokens == 60


def test_corrupt_usage_override_false(tmp_path: Path):
    path = tmp_path / "runtime" / "usage.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    m = _meter(tmp_path)
    assert m.snapshot().override_active is False
    assert m.snapshot().period_authority == "iso"


def test_missing_period_authority_defaults_iso(tmp_path: Path):
    path = tmp_path / "runtime" / "usage.json"
    path.parent.mkdir(parents=True)
    now = datetime(2026, 7, 24, 14, 0, tzinfo=UTC)
    body = {
        "schema_version": 2,
        "week_id": "2026-W30",
        "day_id": "2026-07-24",
        "hour_id": "2026-07-24T14",
        "period_id": "2026-W30",
        # period_authority omitted
        "week_used_tokens": 42,
        "day_used_tokens": 42,
        "hour_used_tokens": 42,
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    m = UsageMeter.load(tmp_path, _settings(), clock=_Clock(now))
    snap = m.snapshot()
    assert snap.period_authority == "iso"
    assert snap.week_used_tokens == 42


def test_v1_migrate_to_v2_iso(tmp_path: Path):
    path = tmp_path / "runtime" / "usage.json"
    path.parent.mkdir(parents=True)
    now = datetime(2026, 7, 24, 14, 0, tzinfo=UTC)
    body = {
        "schema_version": 1,
        "week_id": "2026-W30",
        "day_id": "2026-07-24",
        "hour_id": "2026-07-24T14",
        "week_used_tokens": 99,
        "day_used_tokens": 99,
        "hour_used_tokens": 99,
        "hard_stop_override": True,
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    m = UsageMeter.load(tmp_path, _settings(), clock=_Clock(now))
    snap = m.snapshot()
    assert snap.week_used_tokens == 99
    assert snap.override_active is True
    assert snap.period_authority == "iso"
    assert snap.period_id == "2026-W30"
    # Next persist writes v2
    m.record(TokenUsage(total_tokens=1))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert data["period_authority"] == "iso"
    assert data["week_used_tokens"] == 100


def test_unparseable_period_does_not_roll(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, _settings(), clock=clock)
    m.record(TokenUsage(total_tokens=100))
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=20.0,
            period_start="not-a-date",
            period_end="also-bad",
            fetched_at="2026-07-24T14:00:00Z",
            status="ok",
        )
    )
    snap = m.snapshot()
    assert snap.week_used_tokens == 100
    assert snap.period_authority == "iso"  # still iso


def test_same_period_refresh_does_not_zero_S(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, _settings(), clock=clock)
    m.record(TokenUsage(total_tokens=55))
    snap1 = CreditsSnapshot(
        credit_usage_percent=10.0,
        period_start="2026-07-21T00:00:00Z",
        period_end="2026-07-28T00:00:00Z",
        fetched_at="2026-07-24T14:00:00Z",
        status="ok",
    )
    m.apply_credits_snapshot(snap1)
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=15.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:05:00Z",
            status="ok",
        )
    )
    assert m.snapshot().week_used_tokens == 55
    assert m.snapshot().credit_usage_percent == 15.0


def test_record_accumulates_week_cached(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, clock=clock)
    m.record(
        TokenUsage(
            prompt_tokens=100,
            completion_tokens=10,
            total_tokens=110,
            cached_tokens=40,
        )
    )
    snap = m.snapshot()
    assert snap.week_used_tokens == 110
    assert snap.week_cached_tokens == 40


def test_credits_module_import_boundary():
    """PR4 may use stdlib urllib; must not import client/usage (or third-party HTTP)."""
    import ast
    from pathlib import Path as P

    src = (P(__file__).resolve().parents[1] / "elyra" / "llm" / "credits.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", None) or ""
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            blob = " ".join([mod] + names)
            # Stdlib urllib is allowed for fetch_billing; ban third-party HTTP.
            assert "httpx" not in blob
            assert "requests" not in blob
            parts = blob.replace("/", ".").split(".")
            assert "client" not in parts
            assert "usage" not in parts
            # Never import elyra.llm.client / elyra.llm.usage.
            if mod.startswith("elyra"):
                assert "client" not in mod.split(".")
                assert not mod.endswith(".usage")
                assert ".usage" not in mod


# ---------------------------------------------------------------------------
# Fail-soft credits apply / persist rollback (review fixes)
# ---------------------------------------------------------------------------


def test_unparseable_poll_retains_account_hard(tmp_path: Path):
    """Prior A≥A_hard + unparseable follow-up keeps account hard until stale."""
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    s = _settings(
        weekly_allowed_tokens=1_000_000,
        account_hard_stop_percent=95.0,
    )
    m = _meter(tmp_path, s, clock=clock)
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=96.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
            status="ok",
            ok=True,
        )
    )
    assert m.snapshot().hard_stop == "account"
    assert m.can_call() is False
    prior_pct = m.snapshot().credit_usage_percent

    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=1.0,  # would clear hard if applied
            period_start="not-a-date",
            period_end="also-bad",
            fetched_at="2026-07-24T14:01:00Z",
            status="ok",
            ok=True,
        )
    )
    snap = m.snapshot()
    assert snap.hard_stop == "account"
    assert m.can_call() is False
    assert snap.credit_usage_percent == prior_pct
    assert snap.credits_status == "ok"
    assert snap.period_authority == "supergrok"


def test_error_status_does_not_wipe_prior_good_A(tmp_path: Path):
    """Non-ok poll must not clear last-good account percent / hard stop."""
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    s = _settings(weekly_allowed_tokens=1_000_000, account_hard_stop_percent=95.0)
    m = _meter(tmp_path, s, clock=clock)
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=97.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
            status="ok",
        )
    )
    assert m.snapshot().hard_stop == "account"

    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=0.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:02:00Z",
            status="error",
            ok=False,
        )
    )
    snap = m.snapshot()
    assert snap.hard_stop == "account"
    assert snap.credit_usage_percent == 97.0
    assert snap.credits_status == "ok"
    assert m.can_call() is False


def test_ok_percent_without_dates_does_not_invent_account_hard(tmp_path: Path):
    """ok+percent with no period dates must not engage account hard."""
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    s = _settings(weekly_allowed_tokens=1_000_000, account_hard_stop_percent=95.0)
    m = _meter(tmp_path, s, clock=clock)
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=99.0,
            period_start=None,
            period_end=None,
            fetched_at="2026-07-24T14:00:00Z",
            status="ok",
            ok=True,
        )
    )
    snap = m.snapshot()
    assert snap.period_authority == "iso"
    assert snap.hard_stop is None
    assert m.can_call() is True


def test_error_status_with_parseable_dates_does_not_adopt(tmp_path: Path):
    """status=error must not flip period_authority / period_id / zero S."""
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, _settings(weekly_allowed_tokens=5_000_000), clock=clock)
    m.record(TokenUsage(total_tokens=500))
    assert m.snapshot().period_authority == "iso"
    old_id = m.snapshot().period_id

    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=10.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
            status="error",
            ok=False,
        )
    )
    snap = m.snapshot()
    assert snap.period_authority == "iso"
    assert snap.period_id == old_id
    assert snap.week_used_tokens == 500
    assert snap.hard_stop is None


def test_true_roll_persist_failure_rolls_back_memory(tmp_path: Path, monkeypatch):
    """True roll that fails atomic persist must restore in-memory S and period."""
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, _settings(weekly_allowed_tokens=5_000_000), clock=clock)
    m.record(TokenUsage(total_tokens=5000))
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=10.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
            status="ok",
        )
    )
    assert m.snapshot().week_used_tokens == 5000
    assert m.snapshot().period_authority == "supergrok"
    period_before = m.snapshot().period_id

    def _boom(self):  # noqa: ANN001
        raise OSError("disk full")

    monkeypatch.setattr(UsageMeter, "_persist_unlocked", _boom)
    with pytest.raises(OSError, match="disk full"):
        m.apply_credits_snapshot(
            CreditsSnapshot(
                credit_usage_percent=1.0,
                period_start="2026-07-28T00:00:00Z",
                period_end="2026-08-04T00:00:00Z",
                fetched_at="2026-07-28T00:01:00Z",
                status="ok",
            )
        )
    # Memory restored — not rolled
    snap = m.snapshot()
    assert snap.week_used_tokens == 5000
    assert snap.period_id == period_before
    assert snap.period_authority == "supergrok"


def test_meter_yellow_band_still_can_call(tmp_path: Path):
    """over>BurstMax with p in [yellow, red) → yellow soft, can_call true."""
    B, k = 7000, 4.0
    H = 168.0
    t_hours = 24.0
    week_start = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    clock = _Clock(week_start + timedelta(hours=t_hours))
    s = _settings(weekly_allowed_tokens=B, burst_hours=k)
    m = _meter(tmp_path, s, clock=clock)
    # p=1.2 → yellow (see pure test algebra)
    S = int(round(1.2 * B * t_hours / H))
    m.record(TokenUsage(total_tokens=S))
    snap = m.snapshot()
    assert snap.hard_stop is None
    assert snap.pace_band == "yellow"
    assert m.can_call() is True

