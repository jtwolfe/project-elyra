"""Skill classification + playbook framing + post-load commit / no_speak policy.

Scope: work/social/no-commit name sets, pure classifiers, ``format_playbook_active``
for model-facing ``load_skill`` wire content, skill-commit HOST builder, pure
predicates for skill-commit inject + no_speak de-conflict (PR2), narrow
post-tool answer-speak reminder (choice-preserving; soft Decide owns monologue),
and optional post-load ``tool_choice=required`` lever (PR4, default OFF).
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
# Soft recovery: prefer tools; model still chooses which step / when to stop.
SKILL_COMMIT_HOST = (
    "HOST: skill {name} is loaded — prefer the next playbook step via tools "
    "(ledger / growth / speak as the playbook says). Free-text alone does not "
    "advance the skill."
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
            "Follow steps in order. Prefer a tool_call implementing step 1 "
            '(see "First tool call" in the body) over free-text re-planning.'
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


# Soft channel reminder (same family as NO_SPEAK_NUDGE): choice-preserving.
# Only for the post-tool glass gap — not free-text length heuristics (those
# false-positive after a full answer speak; soft Decide owns monologue cases).
ANSWER_SPEAK_HOST = (
    "HOST: free-text never reaches glass. If tools returned something the user "
    "still needs on glass, call speak with that; otherwise stop "
    "(no free-text monologue)."
)


@dataclass(frozen=True)
class AnswerSpeakNudgeDecision:
    """Result of should_answer_speak_nudge."""

    inject: bool
    reason: str  # injected | not_social | not_spoke | already_sent | no_content | no_post_tool_gap | flood | skill_commit_owns | not_free_text | …


def should_answer_speak_nudge(
    *,
    social_wake: bool,
    spoke: bool,
    answer_speak_nudge_sent: bool,
    free_text_no_tools: bool,
    free_text_content: str,
    tools_ran: bool,
    spoke_since_non_speak_tool: bool = True,
    hop_was_flood: bool = False,
    pending_skill_name: str | None = None,
    skill_commit_sent: bool = False,
) -> AnswerSpeakNudgeDecision:
    """Whether free-text path may inject ANSWER_SPEAK_HOST (post-tool glass gap).

    Narrow hard path (metacog hybrid): only when non-speak tools ran and no
    successful speak has happened *since* those tools — glass may be missing
    a tool result. Model still chooses: speak the result, or stop.

    Does **not** fire for free-text monologue after a pure social speak (status
    or full answer) — that is soft Decide (orient / talk). Length heuristics
    false-positive after complete answers. Never auto-copies free-text onto glass.
    """
    if not free_text_no_tools:
        return AnswerSpeakNudgeDecision(False, "not_free_text")
    if not social_wake:
        return AnswerSpeakNudgeDecision(False, "not_social")
    if not spoke:
        return AnswerSpeakNudgeDecision(False, "not_spoke")
    if answer_speak_nudge_sent:
        return AnswerSpeakNudgeDecision(False, "already_sent")
    if hop_was_flood:
        return AnswerSpeakNudgeDecision(False, "flood")
    if (
        pending_skill_name
        and is_work_skill_or_unknown(pending_skill_name)
        and not skill_commit_sent
    ):
        return AnswerSpeakNudgeDecision(False, "skill_commit_owns")
    text = (free_text_content or "").strip()
    if not text:
        return AnswerSpeakNudgeDecision(False, "no_content")
    # Only post-tool incompleteness: tools ran, no speak since those tools.
    if tools_ran and not spoke_since_non_speak_tool:
        return AnswerSpeakNudgeDecision(True, "injected")
    return AnswerSpeakNudgeDecision(False, "no_post_tool_gap")


def answer_speak_host_message() -> str:
    """HOST answer-speak line injected into the in-turn chain (obs / user)."""
    return ANSWER_SPEAK_HOST


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
    "ANSWER_SPEAK_HOST",
    "NO_COMMIT_SKILLS",
    "SKILL_COMMIT_HOST",
    "SOCIAL_SKILLS",
    "WORK_SKILLS",
    "AnswerSpeakNudgeDecision",
    "SkillCommitNudgeDecision",
    "answer_speak_host_message",
    "format_playbook_active",
    "is_commit_eligible_skill",
    "is_social_skill",
    "is_work_skill",
    "is_work_skill_or_unknown",
    "post_load_skill_tool_choice",
    "should_allow_no_speak",
    "should_answer_speak_nudge",
    "should_skill_commit_nudge",
    "skill_commit_host_message",
]
