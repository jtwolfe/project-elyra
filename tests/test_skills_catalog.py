"""Tests for skills catalog, BUNDLED_SKILLS_ROOT, and load_skill tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.skills import (
    SOURCE_BUNDLED,
    SOURCE_LOCAL,
    BundledSkillsRootError,
    SkillCatalog,
    is_valid_skill_name,
    normalize_skill_name,
    resolve_bundled_skills_root,
)
from elyra.tools import ToolContext, ToolRegistry
from elyra.tools.builtin.skills_tools import load_skill


# Base playbooks required by Stretch 1 / PR8d.
BASE_SKILLS = (
    "talk",
    "plan-work",
    "do-work",
    "review-work",
    "rest",
    "create-skill",
    "create-tool",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "test skill",
    body: str = "Do the thing.",
    frontmatter_name: str | None = None,
) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    fm_name = frontmatter_name if frontmatter_name is not None else name
    (pkg / "SKILL.md").write_text(
        f"---\nname: {fm_name}\ndescription: {description}\n---\n\n"
        f"# {name}\n\n{body}\n",
        encoding="utf-8",
    )
    return pkg


# ---------------------------------------------------------------------------
# Name policy + root resolution
# ---------------------------------------------------------------------------


def test_normalize_and_valid_skill_name() -> None:
    assert normalize_skill_name("Talk") == "talk"
    assert normalize_skill_name("  Do-Work ") == "do-work"
    assert normalize_skill_name(None) == ""
    assert normalize_skill_name(123) == ""
    assert is_valid_skill_name("talk")
    assert is_valid_skill_name("plan-work")
    assert is_valid_skill_name("create_tool")
    assert not is_valid_skill_name("")
    assert not is_valid_skill_name("../evil")
    assert not is_valid_skill_name("has space")
    assert not is_valid_skill_name(None)


def test_resolve_bundled_skills_root_exists() -> None:
    root = resolve_bundled_skills_root()
    assert root.is_dir()
    for name in BASE_SKILLS:
        assert (root / name / "SKILL.md").is_file(), f"missing skill {name}"


def test_missing_bundled_skills_root_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(BundledSkillsRootError, match="not a directory"):
        resolve_bundled_skills_root(missing)


def test_catalog_init_raises_when_bundled_missing(
    home: Path, tmp_path: Path
) -> None:
    paths = resolve_paths(home)
    missing = tmp_path / "absent_bundled"
    with pytest.raises(BundledSkillsRootError):
        SkillCatalog(paths, bundled_root=missing)


# ---------------------------------------------------------------------------
# Discovery + short catalog
# ---------------------------------------------------------------------------


def test_discover_bundled_base_skills(catalog: SkillCatalog) -> None:
    for name in BASE_SKILLS:
        assert catalog.has(name), f"expected bundled skill {name}"
    names = catalog.names()
    for name in BASE_SKILLS:
        assert name in names
    # Sources are bundled when using repo skills/bundled.
    talk = catalog.get("talk")
    assert talk is not None
    assert talk.source == SOURCE_BUNDLED
    assert talk.body == ""  # short meta only


def test_catalog_short_entries_only(catalog: SkillCatalog) -> None:
    entries = catalog.catalog()
    assert isinstance(entries, list)
    assert len(entries) >= len(BASE_SKILLS)
    by_name = {e["name"]: e for e in entries}
    for name in BASE_SKILLS:
        assert name in by_name
        entry = by_name[name]
        assert set(entry.keys()) == {"name", "description"}
        assert entry["description"]
        # Short catalog must not include full body.
        assert "body" not in entry
        assert "Never silent" not in entry["description"]


def test_load_full_body(catalog: SkillCatalog) -> None:
    meta = catalog.load("talk")
    assert meta is not None
    assert meta.name == "talk"
    assert meta.source == SOURCE_BUNDLED
    assert meta.body
    assert "---" in meta.body
    assert "Never silent on social wakes" in meta.body
    assert "Speak before wait" in meta.body

    review = catalog.load("review-work")
    assert review is not None
    assert "Do not close a goal without review" in review.body

    create_tool = catalog.load("create-tool")
    assert create_tool is not None
    assert "Never skip verify" in create_tool.body
    assert "install_tool_draft" in create_tool.body
    assert "verify_tool" in create_tool.body
    assert "promote_tool" in create_tool.body


def test_load_unknown_returns_none(catalog: SkillCatalog) -> None:
    assert catalog.load("not-a-real-skill") is None
    assert catalog.load("") is None
    assert catalog.load("../escape") is None
    assert catalog.load(None) is None  # type: ignore[arg-type]


def test_local_overrides_bundled(
    home: Path,
    skills_bundled_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    paths = resolve_paths(home)
    local = paths.skills_dir / "local"
    local.mkdir(parents=True, exist_ok=True)
    _write_skill(
        local,
        "talk",
        description="local talk override",
        body="LOCAL BODY ONLY",
    )

    with caplog.at_level("INFO"):
        cat = SkillCatalog(paths, bundled_root=skills_bundled_root)

    assert cat.has("talk")
    meta = cat.get("talk")
    assert meta is not None
    assert meta.source == SOURCE_LOCAL
    assert meta.description == "local talk override"
    loaded = cat.load("talk")
    assert loaded is not None
    assert "LOCAL BODY ONLY" in loaded.body
    assert any("overrides bundled" in r.message for r in caplog.records)


def test_reload_picks_up_new_local(
    home: Path, skills_bundled_root: Path
) -> None:
    paths = resolve_paths(home)
    cat = SkillCatalog(paths, bundled_root=skills_bundled_root)
    assert not cat.has("my-custom")

    local = paths.skills_dir / "local"
    local.mkdir(parents=True, exist_ok=True)
    _write_skill(local, "my-custom", description="brand new")

    cat.reload()
    assert cat.has("my-custom")
    assert cat.get("my-custom") is not None
    assert cat.get("my-custom").source == SOURCE_LOCAL  # type: ignore[union-attr]


def test_directory_name_is_canonical(
    home: Path, skills_bundled_root: Path
) -> None:
    """Frontmatter name that differs in identity still uses directory name."""
    paths = resolve_paths(home)
    local = paths.skills_dir / "local"
    local.mkdir(parents=True, exist_ok=True)
    _write_skill(
        local,
        "real-name",
        description="canonical dir",
        frontmatter_name="other-name",
    )
    cat = SkillCatalog(paths, bundled_root=skills_bundled_root)
    assert cat.has("real-name")
    assert not cat.has("other-name")
    meta = cat.get("real-name")
    assert meta is not None
    assert meta.name == "real-name"


def test_incomplete_package_skipped(
    home: Path, skills_bundled_root: Path
) -> None:
    paths = resolve_paths(home)
    local = paths.skills_dir / "local"
    empty = local / "empty-skill"
    empty.mkdir(parents=True, exist_ok=True)
    # No SKILL.md
    cat = SkillCatalog(paths, bundled_root=skills_bundled_root)
    assert not cat.has("empty-skill")


def test_name_isolation_casefold(
    home: Path, skills_bundled_root: Path
) -> None:
    paths = resolve_paths(home)
    local = paths.skills_dir / "local"
    local.mkdir(parents=True, exist_ok=True)
    # Local package named with different case of a bundled skill.
    # On case-sensitive FS this is a separate dir; catalog key is casefold.
    _write_skill(local, "Talk", description="case override", body="CASE")
    cat = SkillCatalog(paths, bundled_root=skills_bundled_root)
    # Key collision: "Talk".casefold() == "talk"
    assert cat.has("talk")
    assert cat.has("Talk")
    loaded = cat.load("TALK")
    assert loaded is not None
    # Local wins when dir name normalizes to same key.
    assert loaded.source == SOURCE_LOCAL
    assert "CASE" in loaded.body


# ---------------------------------------------------------------------------
# load_skill tool (builtin + registry package)
# ---------------------------------------------------------------------------


@pytest.fixture
def tools_bundled_root() -> Path:
    from elyra.tools import resolve_bundled_tools_root

    return resolve_bundled_tools_root()


@pytest.fixture
def registry(home: Path, tools_bundled_root: Path) -> ToolRegistry:
    paths = resolve_paths(home)
    return ToolRegistry(paths, bundled_root=tools_bundled_root)


def test_load_skill_package_discovered(registry: ToolRegistry) -> None:
    assert registry.has("load_skill")
    pkg = registry.get("load_skill")
    assert pkg is not None
    assert pkg.meta.kind == "read"
    assert pkg.handler is not None
    assert "name" in pkg.meta.parameters.get("properties", {})


def test_load_skill_via_registry(
    home: Path,
    registry: ToolRegistry,
    catalog: SkillCatalog,
) -> None:
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths, extras={"skills": catalog})
    result = registry.execute("load_skill", {"name": "talk"}, ctx)
    assert result.ok is True
    assert result.payload["name"] == "talk"
    assert "Never silent on social wakes" in result.payload["body"]
    assert result.payload["source"] == SOURCE_BUNDLED
    assert "talk" in ctx.skills_used


def test_load_skill_appends_skills_used_once(
    home: Path, catalog: SkillCatalog
) -> None:
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths, extras={"skills": catalog})
    r1 = load_skill({"name": "do-work"}, ctx)
    r2 = load_skill({"name": "do-work"}, ctx)
    assert r1.ok and r2.ok
    assert ctx.skills_used.count("do-work") == 1


def test_load_skill_unknown_includes_catalog_hint(
    home: Path, catalog: SkillCatalog
) -> None:
    """unknown_skill payload lists available names (exact catalog, not fuzzy)."""
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths, extras={"skills": catalog})
    result = load_skill({"name": "nope-skill"}, ctx)
    assert result.ok is False
    assert result.error_reason == "unknown_skill"
    assert result.payload.get("name") == "nope-skill"
    assert "available" in result.payload
    assert "create-tool" in result.payload["available"]
    assert "hint" in result.payload
    assert "did_you_mean" not in result.payload


def test_load_skill_unknown_underscore_suggests_hyphenated(
    home: Path, catalog: SkillCatalog
) -> None:
    """create_tool miss → did_you_mean create-tool (explicit suggestion only)."""
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths, extras={"skills": catalog})
    result = load_skill({"name": "create_tool"}, ctx)
    assert result.ok is False
    assert result.error_reason == "unknown_skill"
    assert result.payload.get("did_you_mean") == "create-tool"
    assert "create-tool" in result.payload.get("available", [])


def test_load_skill_unknown(home: Path, catalog: SkillCatalog) -> None:
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths, extras={"skills": catalog})
    result = load_skill({"name": "nope-skill"}, ctx)
    assert result.ok is False
    assert result.error_reason == "unknown_skill"
    assert ctx.skills_used == []


def test_load_skill_missing_name(home: Path, catalog: SkillCatalog) -> None:
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths, extras={"skills": catalog})
    assert load_skill({}, ctx).error_reason == "missing_name"
    assert load_skill({"name": None}, ctx).error_reason == "missing_name"
    assert load_skill({"name": ""}, ctx).error_reason == "missing_name"
    assert load_skill({"name": "  "}, ctx).error_reason == "missing_name"


def test_load_skill_invalid_name(home: Path, catalog: SkillCatalog) -> None:
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths, extras={"skills": catalog})
    result = load_skill({"name": "../etc"}, ctx)
    assert result.ok is False
    assert result.error_reason == "invalid_name"
    # Type-wrong args are invalid, not missing (align with PR6 tool execute).
    assert load_skill({"name": 123}, ctx).error_reason == "invalid_name"
    assert load_skill({"name": ["talk"]}, ctx).error_reason == "invalid_name"
    assert load_skill({"name": True}, ctx).error_reason == "invalid_name"


def test_load_skill_builds_catalog_from_paths_when_not_injected(
    home: Path,
) -> None:
    """Without extras['skills'], handler constructs SkillCatalog from paths."""
    paths = resolve_paths(home)
    # Handler uses SkillCatalog(ctx.paths) which resolves BUNDLED from project_root.
    ctx = ToolContext(paths=paths)
    result = load_skill({"name": "rest"}, ctx)
    # Repo checkout has skills/bundled → should succeed.
    assert result.ok is True
    assert result.payload["name"] == "rest"
    assert "Idle honestly" in result.payload["body"] or "Rest" in result.payload["body"]
    assert "rest" in ctx.skills_used


def test_load_skill_skills_unavailable(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When catalog cannot be built and no inject, return skills_unavailable."""
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths)

    def _fail(_override: object = None) -> Path:
        raise BundledSkillsRootError("forced for test")

    # Fail construction without replacing SkillCatalog type (isinstance must work).
    monkeypatch.setattr(
        "elyra.skills.catalog.resolve_bundled_skills_root",
        _fail,
    )
    result = load_skill({"name": "talk"}, ctx)
    assert result.ok is False
    assert result.error_reason == "skills_unavailable"
    assert ctx.skills_used == []


def test_load_skill_create_tool_checklist(
    home: Path, catalog: SkillCatalog
) -> None:
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths, extras={"skills": catalog})
    result = load_skill({"name": "create-tool"}, ctx)
    assert result.ok
    body = result.payload["body"]
    assert "Never skip verify" in body
    assert "install_tool_draft" in body
    assert "verify_tool" in body
    assert "promote_tool" in body
    assert "First tool call" in body


def test_bundled_skills_have_first_action_section(catalog: SkillCatalog) -> None:
    """Work+talk: First tool call (mandatory); rest: First action (honest stop)."""
    work_and_talk = (
        "talk",
        "plan-work",
        "do-work",
        "review-work",
        "create-tool",
        "create-skill",
    )
    for name in work_and_talk:
        loaded = catalog.load(name)
        assert loaded is not None, name
        body = loaded.body
        assert "First tool call" in body, f"{name} missing First tool call"
        assert "mandatory" in body.lower(), f"{name} missing mandatory framing"
    rest = catalog.load("rest")
    assert rest is not None
    assert "First action" in rest.body
    assert "First tool call" not in rest.body
    assert "stop with no tools" in rest.body.lower() or "no tools" in rest.body.lower()
