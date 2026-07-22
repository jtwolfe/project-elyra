"""Skill classification + playbook framing + post-load commit / no_speak policy.

Scope: work/social/no-commit name sets, pure classifiers, ``format_playbook_active``
for model-facing ``load_skill`` wire content, skill-commit HOST builder, pure
predicates for skill-commit inject + no_speak de-conflict (PR2), and optional
post-load ``tool_choice=required`` lever (PR4, default OFF).
"""

from __future__ import annotations

from dataclasses import dataclass

from elyra.skills import normalize_skill_name

# Exact sets (normalize with normalize_skill_name / casefold).
WORK_SKILLS = frozenset(
    {
        "plan-work",
        "do-work",
        "create-tool",
        "create-skill",
        "review-work",
    }
)
SOCIAL_SKILLS = frozenset({"talk", "rest"})
# rest: social for talk-handoff semantics, but NEVER skill-commit arm (honest idle).
NO_COMMIT_SKILLS = frozenset({"rest"})

_WORK_KEYS = frozenset(normalize_skill_name(s) for s in WORK_SKILLS)
_SOCIAL_KEYS = frozenset(normalize_skill_name(s) for s in SOCIAL_SKILLS)
_NO_COMMIT_KEYS = frozenset(normalize_skill_name(s) for s in NO_COMMIT_SKILLS)

# Template for skill-commit HOST (chain-only; never SpeakTransport). {name} filled by builder.
SKILL_COMMIT_HOST = (
    "HOST: skill {name} is loaded — execute its next checklist step with tools now "
    "(update_task / create_task / install_tool_draft / verify_tool / promote_tool / "
    "install_skill / speak as the playbook says). Do not re-plan in free-text."
)


def is_work_skill(name: str) -> bool:
    """True if name is a known work playbook (normalized)."""
    key = normalize_skill_name(name)
    return key in _WORK_KEYS


def is_social_skill(name: str) -> bool:
    """True if name is a known social playbook (talk / rest)."""
    key = normalize_skill_name(name)
    return key in _SOCIAL_KEYS


def is_commit_eligible_skill(name: str) -> bool:
    """True if a successful load_skill should arm pending_skill_commit.

    rest is never eligible (honest no-tool stop). Known work skills and talk
    are eligible. Local / unknown names default to eligible (treated as work).
    Empty / invalid names are not eligible — ok load_skill with such a name
    (should not happen after handler validation) clears pending, does not arm.
    """
    key = normalize_skill_name(name)
    if not key:
        return False
    if key in _NO_COMMIT_KEYS:
        return False
    return True


def is_work_skill_or_unknown(name: str) -> bool:
    """Work set OR local/unknown (not social). Used for no_speak suppress."""
    key = normalize_skill_name(name)
    if not key:
        return False
    if is_social_skill(name):
        return False
    return True  # known work or local unknown


def format_playbook_active(
    name: str,
    body: str,
    *,
    source: str | None = None,
    description: str | None = None,
) -> str:
    """Model-facing plain-text frame for a successful load_skill result.

    Commit-eligible skills get a mandatory next-tool follow-line; ``rest``
    gets an honest-idle follow-line (K16) so the wire does not demand tools.
    """
    lines = [f"PLAYBOOK ACTIVE: {name}"]
    if source:
        lines.append(f"source: {source}")
    if description:
        lines.append(f"catalog: {description}")
    if normalize_skill_name(name) == "rest":
        follow = (
            "Follow the playbook. Prefer honest stop with no tools when idle "
            '(see "First action" in the body). Do not invent busywork.'
        )
    else:
        follow = (
            "Follow steps in order. Next action must be a tool_call implementing step 1 "
            '(see "First tool call (mandatory)" in the body). Do not narrate the plan in free-text.'
        )
    lines.extend(["", follow, "", "## Playbook", "", body.rstrip(), ""])
    return "\n".join(lines)


def skill_commit_host_message(name: str) -> str:
    """HOST skill-commit line injected into the in-turn chain (obs / user)."""
    return SKILL_COMMIT_HOST.format(name=name)


@dataclass(frozen=True)
class SkillCommitNudgeDecision:
    """Result of should_skill_commit_nudge."""

    inject: bool
    reason: str  # injected | none_pending | already_sent | not_eligible | not_free_text | …


def should_skill_commit_nudge(
    *,
    pending_skill_name: str | None,
    skill_commit_sent: bool,
    free_text_no_tools: bool = True,
) -> SkillCommitNudgeDecision:
    """Inject once when a commit-eligible skill is pending and free-text hop has no tools.

    Intentionally does NOT gate on flood, continuous_enabled, or social_wake.
    Flood is the live failure mode; continuous OFF must still recover mid-playbook.
    rest must never appear as pending if arm path is correct; belt-and-suspenders
    rejects non-eligible names here too.
    """
    if not free_text_no_tools:
        return SkillCommitNudgeDecision(False, "not_free_text")
    if skill_commit_sent:
        return SkillCommitNudgeDecision(False, "already_sent")
    if not pending_skill_name:
        return SkillCommitNudgeDecision(False, "none_pending")
    if not is_commit_eligible_skill(pending_skill_name):
        return SkillCommitNudgeDecision(False, "not_eligible")
    return SkillCommitNudgeDecision(True, "injected")


def should_allow_no_speak(
    *,
    social_wake: bool,
    spoke: bool,
    no_speak_nudge_sent: bool,
    pending_skill_name: str | None,
    skill_commit_sent: bool,
) -> bool:
    """Whether the free-text path may inject NO_SPEAK_NUDGE this hop.

    Embeds the legacy structural gates (social / !spoke / !sent) so the free-text
    branch can replace the bare if with this single call.
    """
    if not social_wake or spoke or no_speak_nudge_sent:
        return False
    # Work (or local-unknown) skill pending and commit HOST not yet sent →
    # skill_commit owns this hop. talk/rest pending: talk is social skill so
    # is_work_skill_or_unknown is False → no_speak allowed after skill_commit
    # order runs first when pending; if pending still set without commit
    # (should not happen for talk if order correct), no_speak not suppressed.
    if (
        pending_skill_name
        and is_work_skill_or_unknown(pending_skill_name)
        and not skill_commit_sent
    ):
        return False
    return True


def post_load_skill_tool_choice(
    *,
    pending_skill_name: str | None,
    enabled: bool,
) -> str | None:
    """When enabled, pin ``tool_choice=required`` while a commit-eligible skill is pending.

    Default ``enabled=False`` (evidence-gated; never product-default required for
    all hops). ``rest`` / empty / None pending → None even when enabled.

    Wire after social hop-0 speak pin: only apply when that pin returned None so
    hop-0 social speak is never overridden.
    """
    if not enabled:
        return None
    if not pending_skill_name or not is_commit_eligible_skill(pending_skill_name):
        return None
    return "required"


__all__ = [
    "NO_COMMIT_SKILLS",
    "SKILL_COMMIT_HOST",
    "SOCIAL_SKILLS",
    "WORK_SKILLS",
    "SkillCommitNudgeDecision",
    "format_playbook_active",
    "is_commit_eligible_skill",
    "is_social_skill",
    "is_work_skill",
    "is_work_skill_or_unknown",
    "post_load_skill_tool_choice",
    "should_allow_no_speak",
    "should_skill_commit_nudge",
    "skill_commit_host_message",
]
