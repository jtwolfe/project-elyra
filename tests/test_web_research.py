"""Tests for web-research lite skill package (PR4).

Hermetic only — package on disk, frontmatter, catalog discovery.
No live search / no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.prompts.loader import load_prompt
from elyra.skills import SOURCE_BUNDLED, SkillCatalog, resolve_bundled_skills_root
from elyra.skills.catalog import _parse_frontmatter, load_skill_meta
from elyra.tools import ToolContext
from elyra.tools.builtin.skills_tools import load_skill


SKILL_NAME = "web-research"

# Body must teach the lite research contract (design §3.1 / PR4 acceptance).
REQUIRED_BODY_NEEDLES = (
    "multi-query",
    "cite",
    "stop",
    "ledger",
    "web_search",
    "never invent",
    "search_unavailable",
    "First tool call",
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


def test_web_research_package_exists(skills_bundled_root: Path) -> None:
    skill_md = skills_bundled_root / SKILL_NAME / "SKILL.md"
    assert skill_md.is_file(), f"missing skill package: {skill_md}"
    text = skill_md.read_text(encoding="utf-8")
    assert text.strip()
    assert len(text.encode("utf-8")) < 64 * 1024  # identity-aligned body cap


def test_web_research_frontmatter_valid(skills_bundled_root: Path) -> None:
    skill_md = skills_bundled_root / SKILL_NAME / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    fields = _parse_frontmatter(text)
    assert fields.get("name", "").strip() == SKILL_NAME
    desc = fields.get("description", "").strip()
    assert desc, "frontmatter description must be non-empty"
    assert "body" not in fields  # short catalog surface only
    # Description is the catalog trigger line — keep it short.
    assert len(desc) < 200
    assert "multi-query" in desc.lower() or "research" in desc.lower()


def test_web_research_load_skill_meta(skills_bundled_root: Path) -> None:
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


def test_web_research_in_catalog(catalog: SkillCatalog) -> None:
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
    assert "Never invent" not in entry["description"]  # full body stays out of catalog


def test_web_research_body_contract(catalog: SkillCatalog) -> None:
    loaded = catalog.load(SKILL_NAME)
    assert loaded is not None
    body = loaded.body
    lower = body.lower()
    for needle in REQUIRED_BODY_NEEDLES:
        assert needle.lower() in lower, f"web-research SKILL.md missing {needle!r}"
    assert "mandatory" in lower
    # Lite: multi-query split guidance present.
    assert "2–4" in body or "2-4" in body
    # Failure / stop paths.
    assert "rate_limited" in lower or "rate-limited" in lower
    assert "create_goal" in body or "create_task" in body or "update_task" in body


def test_load_skill_tool_returns_web_research(
    home: Path, catalog: SkillCatalog
) -> None:
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths, extras={"skills": catalog})
    result = load_skill({"name": SKILL_NAME}, ctx)
    assert result.ok
    assert result.payload["name"] == SKILL_NAME
    body = result.payload["body"]
    assert "web_search" in body
    assert "First tool call" in body


def test_system_prompt_mentions_search_and_web_research() -> None:
    """PR4 catalog line: Search family + web-research skill in system.md."""
    text = load_prompt("system")
    assert "web-research" in text
    assert "web_search" in text
    # Family line present (agency-preserving short pointer).
    lower = text.lower()
    assert "search" in lower
    assert "never invent" in lower or "multi-query" in lower
