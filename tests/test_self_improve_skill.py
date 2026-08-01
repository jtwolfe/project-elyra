"""Tests for self-improve skill package (PR5).

Hermetic only — package on disk, frontmatter, catalog discovery.
No live grok_build / network / subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.skills import SOURCE_BUNDLED, SkillCatalog, resolve_bundled_skills_root
from elyra.skills.catalog import _parse_frontmatter, load_skill_meta
from elyra.tools import ToolContext
from elyra.tools.builtin.skills_tools import load_skill


SKILL_NAME = "self-improve"

# Body must teach L/M/H + H-spine + async job rails (design PR5 / skill outline).
REQUIRED_BODY_NEEDLES = (
    "L / M / H",
    "grok_build",
    "job_id",
    "background",
    "source=grok_build",
    "First tool call",
    "working",
    "execute_plan",
    "design",
    "implement",
    "deep_research",
    "review",
    "grant",
    "error_reason",
    "github-workflow",
    "needs_human",
    "H-spine",
)


@pytest.fixture
def skills_bundled_root() -> Path:
    return resolve_bundled_skills_root()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def catalog(home: Path, skills_bundled_root: Path) -> SkillCatalog:
    paths = resolve_paths(home)
    return SkillCatalog(paths, bundled_root=skills_bundled_root)


def test_self_improve_package_exists(skills_bundled_root: Path) -> None:
    skill_md = skills_bundled_root / SKILL_NAME / "SKILL.md"
    assert skill_md.is_file(), f"missing skill package: {skill_md}"
    text = skill_md.read_text(encoding="utf-8")
    assert text.strip()
    assert len(text.encode("utf-8")) < 64 * 1024  # identity-aligned body cap


def test_self_improve_frontmatter_valid(skills_bundled_root: Path) -> None:
    skill_md = skills_bundled_root / SKILL_NAME / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    fields = _parse_frontmatter(text)
    assert fields.get("name", "").strip() == SKILL_NAME
    desc = fields.get("description", "").strip()
    assert desc, "frontmatter description must be non-empty"
    assert "body" not in fields
    assert len(desc) < 320
    lower = desc.lower()
    assert "l/m/h" in lower or "complexity" in lower or "self-mod" in lower
    assert "grok_build" in lower or "grok build" in lower


def test_self_improve_load_skill_meta(skills_bundled_root: Path) -> None:
    meta = load_skill_meta(
        skills_bundled_root / SKILL_NAME,
        source=SOURCE_BUNDLED,
        include_body=True,
    )
    assert meta.name == SKILL_NAME
    assert meta.source == SOURCE_BUNDLED
    assert meta.description
    assert meta.body
    assert "---" in meta.body


def test_self_improve_in_catalog(catalog: SkillCatalog) -> None:
    assert catalog.has(SKILL_NAME)
    assert SKILL_NAME in catalog.names()
    entry_meta = catalog.get(SKILL_NAME)
    assert entry_meta is not None
    assert entry_meta.source == SOURCE_BUNDLED
    assert entry_meta.description
    assert entry_meta.body == ""  # short meta only

    entries = catalog.catalog()
    by_name = {e["name"]: e for e in entries}
    assert SKILL_NAME in by_name
    entry = by_name[SKILL_NAME]
    assert set(entry.keys()) == {"name", "description"}
    assert entry["description"]
    # Full body stays out of short catalog.
    assert "H-spine" not in entry["description"]
    assert "job_id" not in entry["description"]


def test_self_improve_body_contract(catalog: SkillCatalog) -> None:
    loaded = catalog.load(SKILL_NAME)
    assert loaded is not None
    body = loaded.body
    lower = body.lower()
    for needle in REQUIRED_BODY_NEEDLES:
        assert needle.lower() in lower, f"self-improve SKILL.md missing {needle!r}"
    assert "mandatory" in lower
    # Tip law: working is base; do not teach grok-improvement as current tip alone.
    assert "working" in body
    assert "on top of `grok-improvement`" not in body
    assert "on top of **`grok-improvement`**" not in body
    # Async poll manners.
    assert "instrument_job" in body  # never invent
    assert "async" in lower
    # Grant stops / no auto-merge / no pin move.
    assert "auto-merge" in lower or "auto merge" in lower
    assert "pin" in lower
    # Prefer single tool name grok_build with modes.
    assert "never invent" in lower or "do not invent" in lower
    # Out of scope: implementing grok_build itself.
    assert "out of scope" in lower
    assert "implementing the `grok_build`" in lower or "implementing the grok_build" in lower


def test_self_improve_lmh_and_h_spine(catalog: SkillCatalog) -> None:
    """L/M/H tiers + H-spine order surface in body."""
    loaded = catalog.load(SKILL_NAME)
    assert loaded is not None
    body = loaded.body
    lower = body.lower()
    # Tier sections present.
    assert "### l" in lower or "l — low" in lower or "low (" in lower
    assert "### m" in lower or "m — medium" in lower or "medium (" in lower
    assert "### h" in lower or "h — high" in lower or "high (" in lower
    # H-spine steps (design order).
    assert "deep_research" in body
    assert "mode=`design`" in body or "mode=design" in body
    assert "execute_plan" in body
    assert "wait_user" in body
    assert "speak" in body
    # M path: implement without requiring execute_plan for medium.
    assert "effort" in lower


def test_self_improve_async_job_id_background(catalog: SkillCatalog) -> None:
    """Async: job_id return, poll, background wake source=grok_build."""
    loaded = catalog.load(SKILL_NAME)
    assert loaded is not None
    body = loaded.body
    assert "job_id" in body
    assert "source=grok_build" in body
    assert "background" in body.lower()
    assert "schedule_wake" in body or "timer" in body.lower()
    # Soft-fail catalog presence.
    for reason in (
        "auth_unavailable",
        "base_branch_missing",
        "mode_experimental",
        "mode_not_ready",
        "usage_hard_stop",
    ):
        assert reason in body, f"missing soft-fail needle {reason!r}"


def test_load_skill_tool_returns_self_improve(
    home: Path, catalog: SkillCatalog
) -> None:
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths, extras={"skills": catalog})
    result = load_skill({"name": SKILL_NAME}, ctx)
    assert result.ok
    assert result.payload["name"] == SKILL_NAME
    body = result.payload["body"]
    assert "grok_build" in body
    assert "First tool call" in body
    assert "job_id" in body
    assert "working" in body
