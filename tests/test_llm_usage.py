"""Unit tests for hierarchical UsageMeter (elyra.llm.usage)."""

from __future__ import annotations

import ast
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyra.llm.usage import (
    TokenUsage,
    UsageHardStopError,
    UsageMeter,
    UsageSnapshot,
    compute_limits,
    parse_token_usage,
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
    assert data["schema_version"] == 1


def test_hard_stop_hour_then_day_then_week(tmp_path: Path):
    # Tight hour, looser day/week
    s = _settings(
        weekly_allowed_tokens=1000,
        day_allowed_tokens=100,
        hour_allowed_tokens=10,
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
    m2 = _meter(
        tmp_path,
        _settings(
            weekly_allowed_tokens=1000,
            day_allowed_tokens=50,
            hour_allowed_tokens=100,
        ),
        clock=clock,
    )
    # Fresh meter file from m — clear by using new dir
    d2 = tmp_path / "d2"
    m2 = _meter(
        d2,
        _settings(
            weekly_allowed_tokens=1000,
            day_allowed_tokens=50,
            hour_allowed_tokens=100,
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
        ),
        clock=clock,
    )
    m3.record(TokenUsage(total_tokens=80))
    assert m3.snapshot().hard_stop == "week"
    assert "week" in (m3.hard_stop_reason() or "")


def test_hard_stop_precedence_week_over_day_over_hour(tmp_path: Path):
    """When all ceilings exceeded, hard_stop reports week."""
    s = _settings(
        weekly_allowed_tokens=10,
        day_allowed_tokens=10,
        hour_allowed_tokens=10,
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
