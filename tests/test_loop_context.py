"""Tests for sliding outer context meal assembly."""

from __future__ import annotations

from datetime import UTC, datetime

from elyra.loop.context import (
    assemble_outer_meal,
    estimate_messages_tokens,
    estimate_tokens,
    fill_orient,
    format_now,
)
from elyra.prompts.loader import load_prompt
from elyra.settings import LoopSettings


def test_estimate_tokens_chars_div_4():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 40) == 10


def test_fill_orient_placeholders():
    template = (
        "NOW={{NOW}}\nSELF={{SELF}}\nUSER={{USER}}\n"
        "WHY={{WHY_NOW}}\nG={{GOALS}}\nC={{SKILL_CATALOG}}\nB={{SKILL_BIAS}}\n"
    )
    text = fill_orient(
        template,
        now="when",
        self_digest="me",
        user_digest="them",
        why_now="wake",
        goals="g1",
        skill_catalog="talk",
        skill_bias="prefer talk",
    )
    assert "NOW=when" in text
    assert "SELF=me" in text
    assert "USER=them" in text
    assert "WHY=wake" in text
    assert "G=g1" in text
    assert "C=talk" in text
    assert "B=prefer talk" in text
    assert "{{" not in text


def test_fill_orient_values_not_rescanned():
    """Digest text containing later placeholder tokens must not be rewritten."""
    template = "SELF={{SELF}}\nGOALS={{GOALS}}\n"
    text = fill_orient(
        template,
        now="n",
        self_digest="has {{GOALS}} inside",
        goals="",
    )
    assert "has {{GOALS}} inside" in text
    assert text.endswith("GOALS=\n") or "GOALS=\n" in text


def test_format_now_includes_utc_and_weekday():
    fixed = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    label = format_now(fixed)
    assert "UTC" in label
    assert "2026-07-21" in label
    # Tuesday
    assert "Tuesday" in label


def test_meal_order_system_then_history_then_orient():
    system = "# system laws\n"
    orient = (
        "# Orient\n## NOW\n{{NOW}}\n## SELF\n{{SELF}}\n## USER\n{{USER}}\n"
        "## Why now\n{{WHY_NOW}}\n## Goals / tasks\n{{GOALS}}\n"
        "## Skills available\n{{SKILL_CATALOG}}\n## Soft skill bias\n{{SKILL_BIAS}}\n"
    )
    history = [
        {"role": "user", "content": "hello", "id": "m1"},
        {"role": "assistant", "content": "hi there", "id": "m2"},
    ]
    meal = assemble_outer_meal(
        glass_history=history,
        system_text=system,
        orient_template=orient,
        self_digest="I am Elyra",
        user_digest="Operator",
        why_now="user_message:hello",
        now=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
        sliding_input_tokens=24_000,
    )
    assert len(meal) == 4
    assert meal[0]["role"] == "system"
    assert meal[0]["content"] == system
    assert meal[1] == {"role": "user", "content": "hello"}
    assert meal[2] == {"role": "assistant", "content": "hi there"}
    assert meal[3]["role"] == "user"
    orient_body = meal[3]["content"]
    assert "I am Elyra" in orient_body
    assert "Operator" in orient_body
    assert "user_message:hello" in orient_body
    assert "2026-07-21" in orient_body
    # Orient is last message (near decision).
    assert meal[-1] is meal[3]


def test_no_reasoning_in_history():
    history = [
        {
            "role": "user",
            "content": "q",
            "reasoning": "should never appear",
        },
        {
            "role": "assistant",
            "content": "a",
            "reasoning": "private chain of thought",
        },
    ]
    meal = assemble_outer_meal(
        glass_history=history,
        system_text="SYS",
        orient_template="O {{NOW}} {{SELF}} {{USER}} {{WHY_NOW}} "
        "{{GOALS}} {{SKILL_CATALOG}} {{SKILL_BIAS}}",
        sliding_input_tokens=24_000,
    )
    blob = "\n".join(m["content"] for m in meal)
    assert "private chain of thought" not in blob
    assert "should never appear" not in blob
    assert meal[1]["content"] == "q"
    assert meal[2]["content"] == "a"
    # No reasoning keys on returned messages.
    for m in meal:
        assert "reasoning" not in m


def test_sliding_drops_oldest_when_over_budget():
    # Fixed system + orient cost ~ small; force tiny budget so only newest fit.
    system = "S" * 40  # 10 tokens
    orient_t = "O" * 40 + "{{NOW}}{{SELF}}{{USER}}{{WHY_NOW}}{{GOALS}}"
    orient_t += "{{SKILL_CATALOG}}{{SKILL_BIAS}}"
    # Each history content ~ 100 tokens (400 chars)
    history = []
    for i in range(6):
        history.append({"role": "user", "content": f"U{i}" + ("x" * 396)})
        history.append({"role": "assistant", "content": f"A{i}" + ("y" * 396)})

    meal = assemble_outer_meal(
        glass_history=history,
        system_text=system,
        orient_template=orient_t,
        sliding_input_tokens=250,  # only a couple of history rows + fixed
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert meal[0]["role"] == "system"
    assert meal[-1]["role"] == "user"  # orient
    mid = meal[1:-1]
    # Oldest pairs dropped; newest remain.
    contents = [m["content"] for m in mid]
    assert any(c.startswith("U5") or c.startswith("A5") for c in contents)
    assert not any(c.startswith("U0") for c in contents)
    assert estimate_messages_tokens(meal) <= 250 or len(mid) < len(history)


def test_dedupe_wake_message_already_in_glass():
    history = [
        {"role": "user", "content": "please help", "id": "wake-1"},
    ]
    meal = assemble_outer_meal(
        glass_history=history,
        system_text="SYS",
        orient_template="orient {{NOW}}{{SELF}}{{USER}}{{WHY_NOW}}"
        "{{GOALS}}{{SKILL_CATALOG}}{{SKILL_BIAS}}",
        wake_content="please help",
        wake_message_id="wake-1",
        sliding_input_tokens=24_000,
    )
    users = [m for m in meal if m["role"] == "user" and m["content"] == "please help"]
    assert len(users) == 1


def test_injects_wake_when_missing_from_glass():
    meal = assemble_outer_meal(
        glass_history=[],
        system_text="SYS",
        orient_template="orient {{NOW}}{{SELF}}{{USER}}{{WHY_NOW}}"
        "{{GOALS}}{{SKILL_CATALOG}}{{SKILL_BIAS}}",
        wake_content="brand new",
        wake_message_id="w2",
        sliding_input_tokens=24_000,
    )
    assert any(m["role"] == "user" and m["content"] == "brand new" for m in meal)


def test_protected_wake_not_dropped_when_only_copy():
    system = "S" * 8
    orient_t = "O" * 8 + "{{NOW}}{{SELF}}{{USER}}{{WHY_NOW}}{{GOALS}}"
    orient_t += "{{SKILL_CATALOG}}{{SKILL_BIAS}}"
    # Old filler + protected wake that is the only copy
    history = [
        {"role": "user", "content": "old" + ("z" * 400)},
        {"role": "assistant", "content": "reply" + ("z" * 400)},
        {"role": "user", "content": "trigger only", "id": "t1"},
    ]
    meal = assemble_outer_meal(
        glass_history=history,
        system_text=system,
        orient_template=orient_t,
        wake_content="trigger only",
        wake_message_id="t1",
        sliding_input_tokens=40,  # force drop of old pair
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    mid = meal[1:-1]
    assert any(m["content"] == "trigger only" for m in mid)
    assert not any(m["content"].startswith("old") for m in mid)


def test_protects_at_least_one_trigger_when_duplicate_content_no_id():
    """Multi-copy same wake text without message_id must keep ≥1 trigger."""
    system = "S" * 8
    orient_t = "O" * 8 + "{{NOW}}{{SELF}}{{USER}}{{WHY_NOW}}{{GOALS}}"
    orient_t += "{{SKILL_CATALOG}}{{SKILL_BIAS}}"
    # Older duplicate + big assistant + latest same content (no ids).
    history = [
        {"role": "user", "content": "trigger"},
        {"role": "assistant", "content": "big" + ("z" * 400)},
        {"role": "user", "content": "trigger"},
    ]
    meal = assemble_outer_meal(
        glass_history=history,
        system_text=system,
        orient_template=orient_t,
        wake_content="trigger",
        # no wake_message_id
        sliding_input_tokens=30,  # force drop under pressure
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    mid = meal[1:-1]
    triggers = [m for m in mid if m["role"] == "user" and m["content"] == "trigger"]
    assert len(triggers) == 1
    # Older big assistant should be gone.
    assert not any(m["content"].startswith("big") for m in mid)


def test_default_meal_loads_disk_system_and_orient():
    """Production default path loads prompts/system.md and prompts/orient.md."""
    fixed = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    meal = assemble_outer_meal(
        glass_history=[{"role": "user", "content": "hi from glass"}],
        now=fixed,
        self_digest="Elyra self digest",
        why_now="user_message:hi",
    )
    assert meal[0]["role"] == "system"
    assert meal[0]["content"] == load_prompt("system")
    orient_body = meal[-1]["content"]
    assert meal[-1]["role"] == "user"
    assert "# Orient" in orient_body
    assert "## NOW" in orient_body
    assert "{{NOW}}" not in orient_body
    assert "{{SELF}}" not in orient_body
    assert "{{USER}}" not in orient_body
    assert "Elyra self digest" in orient_body
    assert "2026-07-21" in orient_body
    assert any(m["content"] == "hi from glass" for m in meal[1:-1])


def test_skips_system_and_empty_rows_in_glass():
    history = [
        {"role": "system", "content": "should skip"},
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "ok"},
        {"role": "tool", "content": "not glass speak"},
    ]
    meal = assemble_outer_meal(
        glass_history=history,
        system_text="SYS",
        orient_template="O {{NOW}}{{SELF}}{{USER}}{{WHY_NOW}}"
        "{{GOALS}}{{SKILL_CATALOG}}{{SKILL_BIAS}}",
        sliding_input_tokens=24_000,
    )
    assert meal[0]["content"] == "SYS"
    assert meal[1] == {"role": "assistant", "content": "ok"}
    assert meal[2]["role"] == "user"


def test_uses_loop_settings_budget():
    settings = LoopSettings(sliding_input_tokens=100)
    history = [
        {"role": "user", "content": "a" * 400},  # 100 tokens alone
        {"role": "assistant", "content": "b" * 400},
        {"role": "user", "content": "c" * 40},
    ]
    meal = assemble_outer_meal(
        glass_history=history,
        settings=settings,
        system_text="S",
        orient_template="O{{NOW}}{{SELF}}{{USER}}{{WHY_NOW}}"
        "{{GOALS}}{{SKILL_CATALOG}}{{SKILL_BIAS}}",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    # With tiny budget, oldest large rows should be gone or reduced.
    mid = meal[1:-1]
    assert not any(m["content"].startswith("a") for m in mid)


def test_meal_injects_goals_catalog_and_bias():
    """assemble_outer_meal fills GOALS / SKILL_CATALOG / SKILL_BIAS when provided."""
    orient = (
        "# Orient\n## Goals / tasks\n{{GOALS}}\n"
        "## Skills available\n{{SKILL_CATALOG}}\n"
        "## Soft skill bias\n{{SKILL_BIAS}}\n"
        "{{NOW}}{{SELF}}{{USER}}{{WHY_NOW}}"
    )
    meal = assemble_outer_meal(
        glass_history=[],
        system_text="SYS",
        orient_template=orient,
        goals="Goal g1 [open]: Ship orient",
        skill_catalog="- talk: Social presence.",
        skill_bias="Prefer skill: talk (social reply first; speak before wait).",
        now=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
        sliding_input_tokens=24_000,
    )
    body = meal[-1]["content"]
    assert "Goal g1 [open]: Ship orient" in body
    assert "- talk: Social presence." in body
    assert "Prefer skill: talk" in body
    assert "{{GOALS}}" not in body
    assert "{{SKILL_CATALOG}}" not in body
    assert "{{SKILL_BIAS}}" not in body
