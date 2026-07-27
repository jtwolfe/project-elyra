"""Pure tests for skill_commit_policy (name sets, framing, commit / no_speak)."""

from __future__ import annotations

from elyra.loop.skill_commit_policy import (
    ANSWER_SPEAK_HOST,
    NO_COMMIT_SKILLS,
    SKILL_COMMIT_HOST,
    SOCIAL_SKILLS,
    WORK_SKILLS,
    answer_speak_host_message,
    format_playbook_active,
    is_commit_eligible_skill,
    is_social_skill,
    is_work_skill,
    is_work_skill_or_unknown,
    post_load_skill_tool_choice,
    should_allow_no_speak,
    should_answer_speak_nudge,
    should_skill_commit_nudge,
    skill_commit_host_message,
)


def test_work_social_no_commit_sets_disjoint_and_complete() -> None:
    assert "rest" in SOCIAL_SKILLS
    assert "rest" in NO_COMMIT_SKILLS
    assert "talk" in SOCIAL_SKILLS
    assert "talk" not in NO_COMMIT_SKILLS
    assert WORK_SKILLS.isdisjoint(SOCIAL_SKILLS)
    assert NO_COMMIT_SKILLS <= SOCIAL_SKILLS
    assert WORK_SKILLS == {
        "plan-work",
        "do-work",
        "create-tool",
        "create-skill",
        "review-work",
    }


def test_is_work_skill_normalized() -> None:
    assert is_work_skill("plan-work")
    assert is_work_skill("Plan-Work")
    assert is_work_skill("  DO-WORK ")
    assert not is_work_skill("talk")
    assert not is_work_skill("rest")
    assert not is_work_skill("local-custom")
    assert not is_work_skill("")


def test_is_social_skill_normalized() -> None:
    assert is_social_skill("talk")
    assert is_social_skill("REST")
    assert not is_social_skill("plan-work")
    assert not is_social_skill("unknown-local")


def test_is_commit_eligible_skill_table() -> None:
    # Work + talk eligible
    for name in ("plan-work", "do-work", "create-tool", "create-skill", "review-work", "talk"):
        assert is_commit_eligible_skill(name), name
        assert is_commit_eligible_skill(name.upper()), name
    # rest never eligible (K16)
    assert not is_commit_eligible_skill("rest")
    assert not is_commit_eligible_skill("REST")
    # Local / unknown default eligible
    assert is_commit_eligible_skill("my-local-playbook")
    # Empty / non-string-normalized not eligible
    assert not is_commit_eligible_skill("")
    assert not is_commit_eligible_skill("   ")


def test_is_work_skill_or_unknown() -> None:
    assert is_work_skill_or_unknown("plan-work")
    assert is_work_skill_or_unknown("custom-skill")
    assert not is_work_skill_or_unknown("talk")
    assert not is_work_skill_or_unknown("rest")
    assert not is_work_skill_or_unknown("")


def test_format_playbook_active_work() -> None:
    body = "# plan-work\n\nDo step 1.\n"
    text = format_playbook_active(
        "plan-work",
        body,
        source="bundled",
        description="Break work into goals",
    )
    assert text.startswith("PLAYBOOK ACTIVE: plan-work\n")
    assert "source: bundled\n" in text
    assert "catalog: Break work into goals\n" in text
    assert "tool_call implementing step 1" in text
    assert "Prefer a tool_call" in text
    assert "must be a tool_call" not in text
    assert "## Playbook" in text
    assert body.rstrip() in text
    assert text.endswith("\n")


def test_format_playbook_active_rest_honest_stop() -> None:
    body = "# rest\n\nStop if idle.\n"
    text = format_playbook_active("rest", body, source="bundled")
    assert text.startswith("PLAYBOOK ACTIVE: rest\n")
    assert "honest stop with no tools" in text
    assert "must be a tool_call" not in text
    assert body.rstrip() in text


def test_format_playbook_active_minimal_name_body() -> None:
    """Positional name + body only (no source/description lines)."""
    text = format_playbook_active("talk", "Speak first.\n")
    assert text.startswith("PLAYBOOK ACTIVE: talk\n")
    assert "source:" not in text
    assert "catalog:" not in text
    assert "tool_call implementing step 1" in text
    assert "Speak first." in text


# ---------------------------------------------------------------------------
# PR2: skill-commit HOST + pure predicates
# ---------------------------------------------------------------------------


def test_skill_commit_host_message_exact_string() -> None:
    msg = skill_commit_host_message("plan-work")
    assert msg == SKILL_COMMIT_HOST.format(name="plan-work")
    assert msg.startswith("HOST:")
    assert "prefer the next playbook step" in msg
    assert "Free-text alone does not advance" in msg
    # Choice-preserving: not a hard "execute now" checklist
    assert "execute its next checklist step" not in msg
    # talk name
    talk = skill_commit_host_message("talk")
    assert "skill talk is loaded" in talk
    assert talk.startswith("HOST:")


def test_should_skill_commit_nudge_table() -> None:
    # Happy path
    d = should_skill_commit_nudge(
        pending_skill_name="plan-work",
        skill_commit_sent=False,
        free_text_no_tools=True,
    )
    assert d.inject is True
    assert d.reason == "injected"

    # talk eligible
    d = should_skill_commit_nudge(
        pending_skill_name="talk",
        skill_commit_sent=False,
    )
    assert d.inject is True

    # local unknown eligible
    d = should_skill_commit_nudge(
        pending_skill_name="my-local",
        skill_commit_sent=False,
    )
    assert d.inject is True

    # already sent
    d = should_skill_commit_nudge(
        pending_skill_name="plan-work",
        skill_commit_sent=True,
    )
    assert d.inject is False
    assert d.reason == "already_sent"

    # none pending
    d = should_skill_commit_nudge(
        pending_skill_name=None,
        skill_commit_sent=False,
    )
    assert d.inject is False
    assert d.reason == "none_pending"

    d = should_skill_commit_nudge(
        pending_skill_name="",
        skill_commit_sent=False,
    )
    assert d.inject is False
    assert d.reason == "none_pending"

    # rest belt-and-suspenders not_eligible
    d = should_skill_commit_nudge(
        pending_skill_name="rest",
        skill_commit_sent=False,
    )
    assert d.inject is False
    assert d.reason == "not_eligible"

    # free_text_no_tools=False
    d = should_skill_commit_nudge(
        pending_skill_name="plan-work",
        skill_commit_sent=False,
        free_text_no_tools=False,
    )
    assert d.inject is False
    assert d.reason == "not_free_text"

    # Independent of flood / continuous — no parameters for those (always injects).


def test_should_allow_no_speak_table() -> None:
    # Legacy social silent path
    assert should_allow_no_speak(
        social_wake=True,
        spoke=False,
        no_speak_nudge_sent=False,
        pending_skill_name=None,
        skill_commit_sent=False,
    )

    # Non-social
    assert not should_allow_no_speak(
        social_wake=False,
        spoke=False,
        no_speak_nudge_sent=False,
        pending_skill_name=None,
        skill_commit_sent=False,
    )

    # Already spoke
    assert not should_allow_no_speak(
        social_wake=True,
        spoke=True,
        no_speak_nudge_sent=False,
        pending_skill_name=None,
        skill_commit_sent=False,
    )

    # Already sent
    assert not should_allow_no_speak(
        social_wake=True,
        spoke=False,
        no_speak_nudge_sent=True,
        pending_skill_name=None,
        skill_commit_sent=False,
    )

    # Work skill pending + commit not sent → suppress
    assert not should_allow_no_speak(
        social_wake=True,
        spoke=False,
        no_speak_nudge_sent=False,
        pending_skill_name="plan-work",
        skill_commit_sent=False,
    )

    # Local unknown pending → suppress
    assert not should_allow_no_speak(
        social_wake=True,
        spoke=False,
        no_speak_nudge_sent=False,
        pending_skill_name="custom-playbook",
        skill_commit_sent=False,
    )

    # After commit spent (pending cleared, skill_commit_sent True) → allow
    assert should_allow_no_speak(
        social_wake=True,
        spoke=False,
        no_speak_nudge_sent=False,
        pending_skill_name=None,
        skill_commit_sent=True,
    )

    # talk pending is social → not suppressed by work gate (skill_commit order owns hop first)
    assert should_allow_no_speak(
        social_wake=True,
        spoke=False,
        no_speak_nudge_sent=False,
        pending_skill_name="talk",
        skill_commit_sent=False,
    )

    # rest pending → not suppressed
    assert should_allow_no_speak(
        social_wake=True,
        spoke=False,
        no_speak_nudge_sent=False,
        pending_skill_name="rest",
        skill_commit_sent=False,
    )

    # Work pending but skill_commit_sent already True (odd state) → allow
    assert should_allow_no_speak(
        social_wake=True,
        spoke=False,
        no_speak_nudge_sent=False,
        pending_skill_name="plan-work",
        skill_commit_sent=True,
    )


# ---------------------------------------------------------------------------
# Answer-speak HOST (status speak ≠ answer speak; Stage B Step 6)
# ---------------------------------------------------------------------------


def test_should_answer_speak_nudge_table() -> None:
    long_mono = "x" * 120  # long free-text after full answer speak — soft Decide only

    # Post-tool gap: tools ran, no speak since tools — soft HOST may fire
    d = should_answer_speak_nudge(
        social_wake=True,
        spoke=True,
        answer_speak_nudge_sent=False,
        free_text_no_tools=True,
        free_text_content="42",
        tools_ran=True,
        spoke_since_non_speak_tool=False,
    )
    assert d.inject is True
    assert d.reason == "injected"

    # Long monologue after social speak, no tools — no hard inject (false-positive case)
    d = should_answer_speak_nudge(
        social_wake=True,
        spoke=True,
        answer_speak_nudge_sent=False,
        free_text_no_tools=True,
        free_text_content=long_mono,
        tools_ran=False,
    )
    assert d.inject is False
    assert d.reason == "no_post_tool_gap"

    # Tools ran but speak already carried the answer — no inject
    d = should_answer_speak_nudge(
        social_wake=True,
        spoke=True,
        answer_speak_nudge_sent=False,
        free_text_no_tools=True,
        free_text_content="done",
        tools_ran=True,
        spoke_since_non_speak_tool=True,
    )
    assert d.inject is False
    assert d.reason == "no_post_tool_gap"

    # Short idle after pure speak — not the hole
    d = should_answer_speak_nudge(
        social_wake=True,
        spoke=True,
        answer_speak_nudge_sent=False,
        free_text_no_tools=True,
        free_text_content="done",
        tools_ran=False,
    )
    assert d.inject is False
    assert d.reason == "no_post_tool_gap"

    # !spoke → no_speak owns the path
    d = should_answer_speak_nudge(
        social_wake=True,
        spoke=False,
        answer_speak_nudge_sent=False,
        free_text_no_tools=True,
        free_text_content="42",
        tools_ran=True,
        spoke_since_non_speak_tool=False,
    )
    assert d.inject is False
    assert d.reason == "not_spoke"

    # Non-social
    d = should_answer_speak_nudge(
        social_wake=False,
        spoke=True,
        answer_speak_nudge_sent=False,
        free_text_no_tools=True,
        free_text_content="42",
        tools_ran=True,
        spoke_since_non_speak_tool=False,
    )
    assert d.inject is False
    assert d.reason == "not_social"

    # Once only
    d = should_answer_speak_nudge(
        social_wake=True,
        spoke=True,
        answer_speak_nudge_sent=True,
        free_text_no_tools=True,
        free_text_content="42",
        tools_ran=True,
        spoke_since_non_speak_tool=False,
    )
    assert d.inject is False
    assert d.reason == "already_sent"

    # Empty free-text
    d = should_answer_speak_nudge(
        social_wake=True,
        spoke=True,
        answer_speak_nudge_sent=False,
        free_text_no_tools=True,
        free_text_content="   ",
        tools_ran=True,
        spoke_since_non_speak_tool=False,
    )
    assert d.inject is False
    assert d.reason == "no_content"

    # Flood hard-stop
    d = should_answer_speak_nudge(
        social_wake=True,
        spoke=True,
        answer_speak_nudge_sent=False,
        free_text_no_tools=True,
        free_text_content="42",
        tools_ran=True,
        spoke_since_non_speak_tool=False,
        hop_was_flood=True,
    )
    assert d.inject is False
    assert d.reason == "flood"

    # Work skill pending commit owns hop
    d = should_answer_speak_nudge(
        social_wake=True,
        spoke=True,
        answer_speak_nudge_sent=False,
        free_text_no_tools=True,
        free_text_content="42",
        tools_ran=True,
        spoke_since_non_speak_tool=False,
        pending_skill_name="plan-work",
        skill_commit_sent=False,
    )
    assert d.inject is False
    assert d.reason == "skill_commit_owns"

    host = answer_speak_host_message()
    assert host == ANSWER_SPEAK_HOST
    # Choice-preserving like NO_SPEAK (if / otherwise), not "must answer-speak"
    assert "still needs" in host.lower() or "if tools returned" in host.lower()
    assert "otherwise stop" in host.lower()
    assert "early status" not in host.lower()


# ---------------------------------------------------------------------------
# PR4 — optional post-load tool_choice=required (default OFF)
# ---------------------------------------------------------------------------


def test_post_load_skill_tool_choice_default_off() -> None:
    """Flag OFF → always None even with eligible pending."""
    assert (
        post_load_skill_tool_choice(
            pending_skill_name="plan-work",
            enabled=False,
        )
        is None
    )
    assert (
        post_load_skill_tool_choice(
            pending_skill_name="talk",
            enabled=False,
        )
        is None
    )
    assert (
        post_load_skill_tool_choice(
            pending_skill_name=None,
            enabled=False,
        )
        is None
    )


def test_post_load_skill_tool_choice_on_eligible() -> None:
    """Flag ON + commit-eligible pending → \"required\"."""
    for name in ("plan-work", "do-work", "talk", "create-tool", "my-local"):
        assert (
            post_load_skill_tool_choice(
                pending_skill_name=name,
                enabled=True,
            )
            == "required"
        ), name


def test_post_load_skill_tool_choice_on_not_eligible() -> None:
    """Flag ON but no pending / rest / empty → None."""
    assert (
        post_load_skill_tool_choice(
            pending_skill_name=None,
            enabled=True,
        )
        is None
    )
    assert (
        post_load_skill_tool_choice(
            pending_skill_name="",
            enabled=True,
        )
        is None
    )
    assert (
        post_load_skill_tool_choice(
            pending_skill_name="rest",
            enabled=True,
        )
        is None
    )
    assert (
        post_load_skill_tool_choice(
            pending_skill_name="REST",
            enabled=True,
        )
        is None
    )
