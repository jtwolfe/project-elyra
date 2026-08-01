"""Unit tests: elyra.instrument.modes — timeouts, async threshold, enum."""

from __future__ import annotations

import pytest

from elyra.instrument.modes import (
    ASYNC_TIMEOUT_THRESHOLD_S,
    DEEP_RESEARCH_EXPERIMENTAL,
    DEFAULT_BASE_BRANCH,
    DEFAULT_TIMEOUT_S,
    Mode,
    default_timeout_s,
    defaults_async,
    is_long_mode,
)


def test_mode_enum_values() -> None:
    assert Mode.PROMPT.value == "prompt"
    assert Mode.DESIGN.value == "design"
    assert Mode.IMPLEMENT.value == "implement"
    assert Mode.EXECUTE_PLAN.value == "execute_plan"
    assert Mode.DEEP_RESEARCH.value == "deep_research"
    assert Mode.REVIEW.value == "review"
    # pr_babysit deferred — not in v1 enum
    assert not hasattr(Mode, "PR_BABYSIT")


def test_mode_parse() -> None:
    assert Mode.parse("design") is Mode.DESIGN
    assert Mode.parse("EXECUTE_PLAN") is Mode.EXECUTE_PLAN  # casefold
    assert Mode.parse("execute_plan") is Mode.EXECUTE_PLAN
    assert Mode.parse(Mode.REVIEW) is Mode.REVIEW
    assert Mode.parse("nope") is None
    assert Mode.parse(None) is None
    assert Mode.parse(123) is None  # type: ignore[arg-type]


def test_async_threshold_is_15_minutes() -> None:
    assert ASYNC_TIMEOUT_THRESHOLD_S == 15 * 60


def test_default_timeouts_match_design_table() -> None:
    assert DEFAULT_TIMEOUT_S[Mode.PROMPT] == 10 * 60
    assert DEFAULT_TIMEOUT_S[Mode.DESIGN] == 90 * 60
    assert DEFAULT_TIMEOUT_S[Mode.IMPLEMENT] == 120 * 60
    assert DEFAULT_TIMEOUT_S[Mode.EXECUTE_PLAN] == 6 * 60 * 60
    assert DEFAULT_TIMEOUT_S[Mode.DEEP_RESEARCH] == 60 * 60
    assert DEFAULT_TIMEOUT_S[Mode.REVIEW] == 45 * 60


@pytest.mark.parametrize(
    "mode,expect_async",
    [
        (Mode.PROMPT, False),
        (Mode.DESIGN, True),
        (Mode.IMPLEMENT, True),
        (Mode.EXECUTE_PLAN, True),
        (Mode.DEEP_RESEARCH, True),
        (Mode.REVIEW, True),
        ("prompt", False),
        ("design", True),
    ],
)
def test_defaults_async_kd11(mode, expect_async) -> None:
    assert defaults_async(mode) is expect_async
    assert is_long_mode(mode) is expect_async
    # Rule: timeout > 15m ⇒ async
    m = Mode.parse(mode) if not isinstance(mode, Mode) else mode
    assert m is not None
    assert (DEFAULT_TIMEOUT_S[m] > ASYNC_TIMEOUT_THRESHOLD_S) is expect_async


def test_defaults_async_unknown_mode() -> None:
    assert defaults_async("not_a_mode") is False


def test_default_timeout_s_helper() -> None:
    assert default_timeout_s(Mode.PROMPT) == 600
    assert default_timeout_s("review") == 45 * 60
    assert default_timeout_s("nope") is None


def test_deep_research_experimental_flag() -> None:
    assert DEEP_RESEARCH_EXPERIMENTAL is True


def test_default_base_branch_working() -> None:
    assert DEFAULT_BASE_BRANCH == "working"
