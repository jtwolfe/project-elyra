"""Skill package VCS: drafts, promote/revert, archive-on-overwrite, catalog.

Acceptance list from design PR2 (capability-growth implementation plan).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.identity.layout import MAX_BODY_BYTES
from elyra.settings import default_settings
from elyra.skills import SkillCatalog
from elyra.tools import ToolContext, ToolRegistry
from elyra.tools.builtin.growth import install_skill
from elyra.tools.builtin.package_vcs import (
    get_skill,
    install_skill_draft,
    promote_skill,
    revert_skill,
    skill_package_is_complete,
)
from elyra.tools.promote import VERSIONS_DIR_NAME, load_versions_meta
from elyra.util.versioning import VERSION_ID_RE


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


@pytest.fixture
def registry(paths) -> ToolRegistry:
    return ToolRegistry(paths)


@pytest.fixture
def catalog(paths) -> SkillCatalog:
    return SkillCatalog(paths)


@pytest.fixture
def ctx(paths, registry: ToolRegistry, catalog: SkillCatalog) -> ToolContext:
    return ToolContext(
        paths=paths,
        settings=default_settings(),
        registry=registry,
        extras={"skills": catalog},
    )


def _draft(
    ctx: ToolContext,
    name: str,
    *,
    description: str = "test skill",
    body: str = "Do the thing.",
    marker: str | None = None,
) -> None:
    body_text = body if marker is None else f"{body}\n\nMARKER={marker}\n"
    r = install_skill_draft(
        {"name": name, "description": description, "body": body_text},
        ctx,
    )
    assert r.ok is True, r


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------


def test_install_skill_draft_writes_only_under_drafts(ctx: ToolContext, paths) -> None:
    name = "draft-only-skill"
    r = install_skill_draft(
        {
            "name": name,
            "description": "A draft skill",
            "body": "# Draft\n\nSteps here.\n",
        },
        ctx,
    )
    assert r.ok is True, r
    draft_md = paths.skills_dir / "drafts" / name / "SKILL.md"
    assert draft_md.is_file()
    assert "A draft skill" in draft_md.read_text(encoding="utf-8")
    # Must not touch local.
    assert not (paths.skills_dir / "local" / name).exists()


def test_draft_only_absent_from_catalog(ctx: ToolContext, catalog: SkillCatalog) -> None:
    name = "invisible-draft"
    _draft(ctx, name, description="should not catalog")
    catalog.reload()
    assert not catalog.has(name)
    names = catalog.names()
    assert name not in names
    short = {row["name"] for row in catalog.catalog()}
    assert name not in short
    assert catalog.load(name) is None


def test_promote_skill_moves_to_local_and_catalog(
    ctx: ToolContext, catalog: SkillCatalog, paths
) -> None:
    name = "promoted-skill"
    _draft(ctx, name, description="Promoted skill", body="Live body content.")
    p = promote_skill({"name": name}, ctx)
    assert p.ok is True, p
    assert p.payload.get("archived_version_id") is None
    local_md = paths.skills_dir / "local" / name / "SKILL.md"
    assert local_md.is_file()
    assert "Live body content" in local_md.read_text(encoding="utf-8")
    assert not (paths.skills_dir / "drafts" / name).exists()
    assert catalog.has(name)
    loaded = catalog.load(name)
    assert loaded is not None
    assert "Live body content" in loaded.body


def test_second_promote_archives_previous(
    ctx: ToolContext, catalog: SkillCatalog, paths
) -> None:
    name = "skill-archive"
    _draft(ctx, name, marker="v1")
    assert promote_skill({"name": name}, ctx).ok

    local = paths.skills_dir / "local" / name
    assert "MARKER=v1" in (local / "SKILL.md").read_text(encoding="utf-8")

    _draft(ctx, name, marker="v2")
    p2 = promote_skill({"name": name}, ctx)
    assert p2.ok is True, p2
    archived_id = p2.payload.get("archived_version_id")
    assert isinstance(archived_id, str) and VERSION_ID_RE.fullmatch(archived_id)

    versions = local / VERSIONS_DIR_NAME
    assert (versions / archived_id).is_dir()
    assert "MARKER=v1" in (
        versions / archived_id / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "MARKER=v2" in (local / "SKILL.md").read_text(encoding="utf-8")
    assert skill_package_is_complete(local)

    meta = load_versions_meta(local)
    assert len(meta) == 1
    assert meta[0]["version_id"] == archived_id
    assert catalog.has(name)


def test_revert_skill_with_reason_restores(
    ctx: ToolContext, catalog: SkillCatalog, paths
) -> None:
    name = "skill-revert"
    _draft(ctx, name, marker="v1")
    assert promote_skill({"name": name}, ctx).ok
    _draft(ctx, name, marker="v2")
    p2 = promote_skill({"name": name}, ctx)
    assert p2.ok
    first_vid = p2.payload["archived_version_id"]

    local = paths.skills_dir / "local" / name
    assert "MARKER=v2" in (local / "SKILL.md").read_text(encoding="utf-8")

    rev = revert_skill(
        {
            "name": name,
            "version_id": first_vid,
            "reason": "restore prior good skill",
        },
        ctx,
    )
    assert rev.ok is True, rev
    assert rev.payload.get("restored_version_id") == first_vid
    assert "MARKER=v1" in (local / "SKILL.md").read_text(encoding="utf-8")
    assert catalog.has(name)
    loaded = catalog.load(name)
    assert loaded is not None
    assert "MARKER=v1" in loaded.body

    meta = load_versions_meta(local)
    assert any(
        isinstance(r.get("reason"), str) and r["reason"].startswith("pre_revert:")
        for r in meta
    )
    assert any(r.get("version_id") == first_vid for r in meta)
    assert skill_package_is_complete(local)


def test_promote_skill_refuses_bundled(ctx: ToolContext) -> None:
    name = "talk"
    r = install_skill_draft(
        {"name": name, "description": "hijack", "body": "nope"},
        ctx,
    )
    assert r.ok is True, r  # draft allowed
    p = promote_skill({"name": name}, ctx)
    assert p.ok is False
    assert p.error_reason == "refuses_overwrite_bundled"


def test_skill_body_too_large_rejected(ctx: ToolContext) -> None:
    name = "huge-skill"
    huge = "x" * (MAX_BODY_BYTES + 1)
    r = install_skill_draft(
        {"name": name, "description": "too big", "body": huge},
        ctx,
    )
    assert r.ok is False
    assert r.error_reason == "body_too_large"

    r2 = install_skill(
        {"name": name, "description": "too big", "body": huge},
        ctx,
    )
    assert r2.ok is False
    assert r2.error_reason == "body_too_large"


def test_install_skill_oneshot_archives_on_overwrite(
    ctx: ToolContext, catalog: SkillCatalog, paths
) -> None:
    name = "oneshot-skill"
    r1 = install_skill(
        {
            "name": name,
            "description": "first",
            "body": "BODY_V1\n",
        },
        ctx,
    )
    assert r1.ok is True, r1
    assert r1.payload.get("archived_version_id") is None
    local_md = paths.skills_dir / "local" / name / "SKILL.md"
    assert local_md.is_file()
    assert "BODY_V1" in local_md.read_text(encoding="utf-8")
    assert catalog.has(name)

    r2 = install_skill(
        {
            "name": name,
            "description": "second",
            "body": "BODY_V2\n",
        },
        ctx,
    )
    assert r2.ok is True, r2
    archived_id = r2.payload.get("archived_version_id")
    assert isinstance(archived_id, str) and VERSION_ID_RE.fullmatch(archived_id)
    assert "BODY_V2" in local_md.read_text(encoding="utf-8")
    archived = (
        paths.skills_dir / "local" / name / VERSIONS_DIR_NAME / archived_id / "SKILL.md"
    )
    assert archived.is_file()
    assert "BODY_V1" in archived.read_text(encoding="utf-8")


def test_promote_never_hollow_live_skill_name(
    ctx: ToolContext, paths, monkeypatch
) -> None:
    name = "skill-hollow"
    _draft(ctx, name, marker="v1")
    assert promote_skill({"name": name}, ctx).ok
    _draft(ctx, name, marker="v2")

    import elyra.tools.promote as promote_mod

    observations: list[bool] = []
    real_rename = promote_mod._rename_path

    def watching_rename(src: Path, dst: Path) -> None:
        real_rename(src, dst)
        live = paths.skills_dir / "local" / name
        if live.exists():
            observations.append(skill_package_is_complete(live))

    # whole_tree_rename_swap (used via promote) renames through promote._rename_path
    monkeypatch.setattr(promote_mod, "_rename_path", watching_rename)
    p = promote_skill({"name": name}, ctx)
    assert p.ok is True, p
    assert observations
    assert all(observations)
    assert skill_package_is_complete(paths.skills_dir / "local" / name)


def test_get_skill_list_versions(ctx: ToolContext, paths) -> None:
    name = "skill-get"
    _draft(ctx, name, marker="v1")
    assert promote_skill({"name": name}, ctx).ok
    _draft(ctx, name, marker="v2")
    p2 = promote_skill({"name": name}, ctx)
    assert p2.ok
    vid = p2.payload["archived_version_id"]

    g = get_skill({"name": name, "list_versions": True}, ctx)
    assert g.ok is True, g
    versions = g.payload.get("versions")
    assert isinstance(versions, list)
    assert len(versions) == 1
    row = versions[0]
    assert row["version_id"] == vid
    assert "content_hash" in row
    assert "skill_md_preview" not in row

    g_ver = get_skill(
        {"name": name, "which": "version", "version_id": vid},
        ctx,
    )
    assert g_ver.ok is True, g_ver
    preview = (g_ver.payload.get("package") or {}).get("skill_md_preview", "")
    assert "MARKER=v1" in preview


def test_revert_skill_requires_reason(ctx: ToolContext) -> None:
    name = "skill-reason"
    _draft(ctx, name, marker="v1")
    assert promote_skill({"name": name}, ctx).ok
    _draft(ctx, name, marker="v2")
    p2 = promote_skill({"name": name}, ctx)
    assert p2.ok
    vid = p2.payload["archived_version_id"]

    for bad in ("", "short", "1234567", None):
        args: dict = {"name": name, "version_id": vid}
        if bad is not None:
            args["reason"] = bad
        r = revert_skill(args, ctx)
        assert r.ok is False, bad
        assert r.error_reason == "reason_required"


def test_skill_vcs_tools_registered(registry: ToolRegistry) -> None:
    for name in (
        "install_skill_draft",
        "promote_skill",
        "revert_skill",
        "get_skill",
        "install_skill",
    ):
        assert registry.has(name), name
        pkg = registry.get(name)
        assert pkg is not None
        assert pkg.runner.kind == "builtin"
        assert pkg.handler is not None


def test_force_skill_promote_rejected(ctx: ToolContext) -> None:
    name = "skill-force"
    _draft(ctx, name)
    r = promote_skill({"name": name, "force": True}, ctx)
    assert r.ok is False
    assert r.error_reason == "force_not_allowed"


def test_promote_mid_failure_keeps_prior_skill(
    ctx: ToolContext, paths, monkeypatch
) -> None:
    name = "skill-midfail"
    _draft(ctx, name, marker="v1")
    assert promote_skill({"name": name}, ctx).ok
    local = paths.skills_dir / "local" / name
    prior = (local / "SKILL.md").read_text(encoding="utf-8")

    _draft(ctx, name, marker="v2")

    import elyra.tools.builtin.package_vcs as vcs_mod

    # package_vcs binds whole_tree_rename_swap at import time — patch there.
    monkeypatch.setattr(
        vcs_mod,
        "whole_tree_rename_swap",
        lambda **kwargs: (_ for _ in ()).throw(OSError("simulated rename failure")),
    )
    p = promote_skill({"name": name}, ctx)
    assert p.ok is False
    assert p.error_reason and p.error_reason.startswith("promote_failed")

    if local.exists():
        assert skill_package_is_complete(local)
        assert (local / "SKILL.md").read_text(encoding="utf-8") == prior
    else:
        assert (paths.skills_dir / "drafts" / name).is_dir()


def test_ensure_data_dirs_creates_skills_drafts(paths) -> None:
    drafts = paths.skills_dir / "drafts"
    assert drafts.is_dir()
