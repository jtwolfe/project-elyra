"""S5 KD-SOFT: soft recall nudge lives only in skills/bundled/talk/SKILL.md."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TALK_SKILL = REPO_ROOT / "skills" / "bundled" / "talk" / "SKILL.md"

# Stable fragment required by design (D.3 / KD-SOFT).
_SOFT_RECALL_FRAGMENT = "glass-tail and directed_keep"


def test_soft_recall_nudge_present_when_memory_meal():
    """Talk skill carries layered recall preference for social memory asks."""
    assert TALK_SKILL.is_file(), f"missing talk skill: {TALK_SKILL}"
    body = TALK_SKILL.read_text(encoding="utf-8")
    assert _SOFT_RECALL_FRAGMENT in body
    # Prefer tip/keep before inventing from episodic alone.
    assert "episodic" in body.lower()
    # Single surface: must not also live in orient (v1 lock).
    orient = REPO_ROOT / "prompts" / "orient.md"
    if orient.is_file():
        orient_body = orient.read_text(encoding="utf-8")
        assert _SOFT_RECALL_FRAGMENT not in orient_body
