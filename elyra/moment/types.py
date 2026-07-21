"""Moment / beat constants and lightweight types.

Scope: stop_reason and beat type vocabularies; schema version.
In scope: frozen allowed values for validation at the store edge.
Out of scope: do-loop policy, wake routing, glass.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

SCHEMA_VERSION = 1

StopReason = Literal[
    "no_tools",
    "wait",
    "blocked",
    "policy",
    "time_continue_declined",
    "wall_clock",
    "interrupted",
    "error",
    "max_hops",
]

STOP_REASONS: frozenset[str] = frozenset(
    {
        "no_tools",
        "wait",
        "blocked",
        "policy",
        "time_continue_declined",
        "wall_clock",
        "interrupted",
        "error",
        "max_hops",
    }
)

BeatType = Literal[
    "model",
    "tool",
    "speak",
    "obs",
    "ledger",
    "skill_load",
    "stop",
]

BEAT_TYPES: frozenset[str] = frozenset(
    {
        "model",
        "tool",
        "speak",
        "obs",
        "ledger",
        "skill_load",
        "stop",
    }
)


class MomentMeta(TypedDict, total=False):
    """Moment index line shape (schema_version 1)."""

    schema_version: int
    id: str
    started_at: str
    ended_at: str | None
    why_now: str
    user_id: str | None
    goal_ids: list[str]
    task_ids: list[str]
    skills_used: list[str]
    stop_reason: str | None
    wake_id: str | None
    hop_count: int


# Beat dicts are free-form beyond required ``type`` (+ usually ``ts``).
BeatDict = dict[str, Any]
