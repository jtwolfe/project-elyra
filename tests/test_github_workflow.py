"""Tests for github-workflow skill package (PR8).

Hermetic only — package on disk, frontmatter, catalog discovery.
No live git/gh / network.
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


SKILL_NAME = "github-workflow"

# Body must teach the self-mod bridge contract (design §3.3 / PR8 acceptance).
REQUIRED_BODY_NEEDLES = (
    "worktree",
    "Projects",
    "grant",
    "package VCS",
    "force-push",
    "main",
    "grok_build",
    "First tool call",
    "revert_tool",
    "git_worktree",
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


def test_github_workflow_package_exists(skills_bundled_root: Path) -> None:
    skill_md = skills_bundled_root / SKILL_NAME / "SKILL.md"
    assert skill_md.is_file(), f"missing skill package: {skill_md}"
    text = skill_md.read_text(encoding="utf-8")
    assert text.strip()
    assert len(text.encode("utf-8")) < 64 * 1024  # identity-aligned body cap


def test_github_workflow_frontmatter_valid(skills_bundled_root: Path) -> None:
    skill_md = skills_bundled_root / SKILL_NAME / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    fields = _parse_frontmatter(text)
    assert fields.get("name", "").strip() == SKILL_NAME
    desc = fields.get("description", "").strip()
    assert desc, "frontmatter description must be non-empty"
    assert "body" not in fields
    assert len(desc) < 220
    lower = desc.lower()
    assert "worktree" in lower or "branch" in lower or "git" in lower


def test_github_workflow_load_skill_meta(skills_bundled_root: Path) -> None:
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


def test_github_workflow_in_catalog(catalog: SkillCatalog) -> None:
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
    assert "Never force-push" not in entry["description"]


def test_github_workflow_body_contract(catalog: SkillCatalog) -> None:
    loaded = catalog.load(SKILL_NAME)
    assert loaded is not None
    body = loaded.body
    lower = body.lower()
    for needle in REQUIRED_BODY_NEEDLES:
        assert needle.lower() in lower, f"github-workflow SKILL.md missing {needle!r}"
    assert "mandatory" in lower
    assert "never" in lower and "main" in lower
    # Grant stops and package recovery surface.
    assert "confirm" in lower
    assert "revert_skill" in body or "revert_tool" in body
    assert "create_goal" in body or "create_task" in body or "get_task" in body


def test_catalog_lists_growth_judgment_skills(catalog: SkillCatalog) -> None:
    """PR8 acceptance: catalog lists github-workflow + web-research (browse when PR6 present)."""
    for name in ("github-workflow", "web-research"):
        assert catalog.has(name), f"expected catalog skill {name}"
        assert name in catalog.names()


def test_load_skill_tool_returns_github_workflow(
    home: Path, catalog: SkillCatalog
) -> None:
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths, extras={"skills": catalog})
    result = load_skill({"name": SKILL_NAME}, ctx)
    assert result.ok
    assert result.payload["name"] == SKILL_NAME
    body = result.payload["body"]
    assert "worktree" in body.lower()
    assert "First tool call" in body


def test_system_prompt_mentions_github_workflow_and_families() -> None:
    """PR8: system.md skill + Search/Browser/Git/Secrets/package recovery lines."""
    text = load_prompt("system")
    assert "github-workflow" in text
    lower = text.lower()
    assert "search" in lower
    assert "browser" in lower
    assert "git" in lower
    assert "secret" in lower
    assert "revert" in lower or "package recovery" in lower or "list_versions" in lower
    # Agency preserved: skill is prefer/nudge, not hard-force every action.
    assert "prefer" in lower or "do not force" in lower or "not force" in lower


def test_growth_skills_mention_package_vcs_recovery(
    skills_bundled_root: Path,
) -> None:
    """Polish: create-tool / create-skill / review-work mention VCS recovery."""
    create_tool = (skills_bundled_root / "create-tool" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    create_skill = (skills_bundled_root / "create-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    review_work = (skills_bundled_root / "review-work" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "revert_tool" in create_tool
    assert "list_versions" in create_tool
    assert "versions" in create_tool.lower()
    assert "revert_skill" in create_skill
    assert "list_versions" in create_skill
    assert "revert_tool" in review_work or "revert_skill" in review_work
    assert "list_versions" in review_work
