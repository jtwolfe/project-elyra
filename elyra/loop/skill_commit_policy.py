"""Skill classification + playbook framing for post-load_skill recovery.

Scope (PR1): work/social/no-commit name sets, pure classifiers, and
``format_playbook_active`` for model-facing ``load_skill`` wire content.
Out of scope (later PRs): skill-commit HOST inject, no_speak de-conflict
predicates, post-load tool_choice lever.
"""

from __future__ import annotations

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


__all__ = [
    "NO_COMMIT_SKILLS",
    "SOCIAL_SKILLS",
    "WORK_SKILLS",
    "format_playbook_active",
    "is_commit_eligible_skill",
    "is_social_skill",
    "is_work_skill",
    "is_work_skill_or_unknown",
]
