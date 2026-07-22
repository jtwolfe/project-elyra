"""Pure tests for skill_commit_policy name sets + playbook framing (PR1)."""

from __future__ import annotations

from elyra.loop.skill_commit_policy import (
    NO_COMMIT_SKILLS,
    SOCIAL_SKILLS,
    WORK_SKILLS,
    format_playbook_active,
    is_commit_eligible_skill,
    is_social_skill,
    is_work_skill,
    is_work_skill_or_unknown,
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
