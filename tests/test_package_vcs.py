"""Package VCS: archive-on-promote, get_tool, revert_tool, GC, lock, atomicity.

Acceptance list from design PR1 (capability-growth implementation plan).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.settings import default_settings
from elyra.tools import ToolContext, ToolRegistry
from elyra.tools.builtin.growth import install_tool_draft, promote_tool, verify_tool
from elyra.tools.builtin.package_vcs import get_tool, revert_tool
from elyra.tools.promote import (
    VERSIONS_DIR_NAME,
    VERSIONS_META_NAME,
    archive_local_payload,
    copy_package_payload,
    find_local_package_dir,
    gc_package_versions,
    load_versions_meta,
    local_package_dir,
    lock_path_for,
    package_is_complete,
    package_lock,
    promote_draft_tool,
    save_versions_meta,
)
from elyra.tools.registry import drafts_dir
from elyra.tools.verify import content_hash
from elyra.util.versioning import VERSION_GC_LIMIT, VERSION_ID_RE, mint_version_id


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
def ctx(paths, registry: ToolRegistry) -> ToolContext:
    return ToolContext(
        paths=paths,
        settings=default_settings(),
        registry=registry,
    )


def _minimal_draft_files(
    name: str = "sample_tool",
    *,
    body_marker: str = "v1",
) -> dict[str, str]:
    return {
        "TOOL.md": (
            f"---\nname: {name}\ndescription: sample {body_marker}\n"
            f"kind: read\n---\n\n# {name} {body_marker}\n"
        ),
        "schema.json": json.dumps(
            {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": False,
            }
        ),
        "runner.json": json.dumps({"kind": "sandbox_python", "module": "impl.main"}),
        "impl/main.py": (
            f"MARKER = {body_marker!r}\n"
            "def run(args):\n"
            "    return {'ok': True, 'marker': MARKER, 'echo': (args or {}).get('x')}\n"
        ),
        "impl/__init__.py": "",
        "tests/test_sample.py": "def test_ok():\n    assert True\n",
    }


def _install_and_verify(ctx: ToolContext, name: str, marker: str = "v1") -> None:
    files = _minimal_draft_files(name, body_marker=marker)
    r = install_tool_draft({"name": name, "files": files}, ctx)
    assert r.ok is True, r
    v = verify_tool({"name": name}, ctx)
    assert v.ok is True, v


def _plant_local_package(paths, name: str, marker: str = "v1") -> Path:
    """Write a complete local package without going through promote."""
    dest = local_package_dir(paths, name)
    dest.mkdir(parents=True, exist_ok=True)
    files = _minimal_draft_files(name, body_marker=marker)
    for rel, content in files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------


def test_promote_archives_existing_local(
    ctx: ToolContext, registry: ToolRegistry, paths
) -> None:
    name = "pkg_a"
    _install_and_verify(ctx, name, marker="v1")
    p1 = promote_tool({"name": name}, ctx)
    assert p1.ok is True, p1
    assert p1.payload.get("archived_version_id") is None
    assert registry.has(name)

    local = local_package_dir(paths, name)
    v1_tool = (local / "TOOL.md").read_text(encoding="utf-8")
    assert "v1" in v1_tool

    _install_and_verify(ctx, name, marker="v2")
    p2 = promote_tool({"name": name}, ctx)
    assert p2.ok is True, p2
    archived_id = p2.payload.get("archived_version_id")
    assert isinstance(archived_id, str) and VERSION_ID_RE.fullmatch(archived_id)

    versions = local / VERSIONS_DIR_NAME
    assert (versions / archived_id).is_dir()
    assert "v1" in (versions / archived_id / "TOOL.md").read_text(encoding="utf-8")
    assert "v2" in (local / "TOOL.md").read_text(encoding="utf-8")
    assert not (drafts_dir(paths) / name).exists()
    assert registry.has(name)
    assert package_is_complete(local)

    meta = load_versions_meta(local)
    assert len(meta) == 1
    assert meta[0]["version_id"] == archived_id


def test_promote_still_refuses_bundled(ctx: ToolContext) -> None:
    name = "read_file"
    _install_and_verify(ctx, name, marker="hijack")
    p = promote_tool({"name": name}, ctx)
    assert p.ok is False
    assert p.error_reason == "refuses_overwrite_bundled"


def test_revert_tool_restores_version(
    ctx: ToolContext, registry: ToolRegistry, paths
) -> None:
    name = "pkg_revert"
    _install_and_verify(ctx, name, marker="v1")
    assert promote_tool({"name": name}, ctx).ok

    _install_and_verify(ctx, name, marker="v2")
    p2 = promote_tool({"name": name}, ctx)
    assert p2.ok
    first_vid = p2.payload["archived_version_id"]

    local = local_package_dir(paths, name)
    assert "v2" in (local / "TOOL.md").read_text(encoding="utf-8")

    rev = revert_tool(
        {
            "name": name,
            "version_id": first_vid,
            "reason": "restore prior good version",
        },
        ctx,
    )
    assert rev.ok is True, rev
    assert rev.payload.get("restored_version_id") == first_vid
    assert "v1" in (local / "TOOL.md").read_text(encoding="utf-8")
    assert registry.has(name)

    # Previous current (v2) archived under pre_revert reason.
    meta = load_versions_meta(local)
    assert any(
        isinstance(r.get("reason"), str) and r["reason"].startswith("pre_revert:")
        for r in meta
    )
    # Restored version still in history.
    assert any(r.get("version_id") == first_vid for r in meta)
    assert package_is_complete(local)


def test_revert_requires_reason(ctx: ToolContext, paths) -> None:
    name = "pkg_reason"
    _install_and_verify(ctx, name, marker="v1")
    assert promote_tool({"name": name}, ctx).ok
    _install_and_verify(ctx, name, marker="v2")
    p2 = promote_tool({"name": name}, ctx)
    assert p2.ok
    vid = p2.payload["archived_version_id"]

    for bad in ("", "short", "1234567", None):
        args: dict = {"name": name, "version_id": vid}
        if bad is not None:
            args["reason"] = bad
        r = revert_tool(args, ctx)
        assert r.ok is False, bad
        assert r.error_reason == "reason_required"


def test_versions_gc_cap_50(paths) -> None:
    name = "pkg_gc"
    dest = _plant_local_package(paths, name, marker="base")
    # Explicit version_ids so chronological order is deterministic under sort.
    for i in range(53):
        vid = f"20200101T{i:06d}Z_aaaaaa"
        entry = archive_local_payload(
            dest, version_id=vid, reason=f"archive_{i:03d}"
        )
        meta = load_versions_meta(dest)
        meta.append(entry)
        save_versions_meta(dest, meta)
        gc_package_versions(dest, limit=VERSION_GC_LIMIT)

    meta = load_versions_meta(dest)
    assert len(meta) == VERSION_GC_LIMIT
    versions_root = dest / VERSIONS_DIR_NAME
    dirs = [c for c in versions_root.iterdir() if c.is_dir()]
    assert len(dirs) == VERSION_GC_LIMIT
    # Oldest-by-version_id dropped: remaining are archive_003..archive_052.
    reasons = [r.get("reason") for r in meta]
    assert reasons[0] == "archive_003"
    assert reasons[-1] == "archive_052"
    assert {r["version_id"] for r in meta} == {
        f"20200101T{i:06d}Z_aaaaaa" for i in range(3, 53)
    }


def test_archive_excludes_nested_versions(paths) -> None:
    name = "pkg_nested"
    dest = _plant_local_package(paths, name)
    # Plant a versions tree + meta + pycache that must not nest.
    nested = dest / VERSIONS_DIR_NAME / "20200101T000000Z_aaaaaa"
    nested.mkdir(parents=True)
    (nested / "TOOL.md").write_text("old\n", encoding="utf-8")
    save_versions_meta(
        dest,
        [
            {
                "version_id": "20200101T000000Z_aaaaaa",
                "content_hash": "x",
                "archived_at": None,
                "bytes": 1,
            }
        ],
    )
    pyc = dest / "impl" / "__pycache__"
    pyc.mkdir(parents=True, exist_ok=True)
    (pyc / "main.cpython-312.pyc").write_bytes(b"\0\0")

    entry = archive_local_payload(dest)
    archive_dir = dest / VERSIONS_DIR_NAME / entry["version_id"]
    assert archive_dir.is_dir()
    # Nested versions must not appear inside the new archive snapshot.
    assert not (archive_dir / VERSIONS_DIR_NAME).exists()
    assert not (archive_dir / VERSIONS_META_NAME).exists()
    assert not (archive_dir / "impl" / "__pycache__").exists()
    assert (archive_dir / "TOOL.md").is_file()


def test_archive_content_hash_is_payload_only(paths) -> None:
    name = "pkg_hash"
    dest = _plant_local_package(paths, name)
    # First archive → creates versions/ under dest.
    e1 = archive_local_payload(dest)
    meta = [e1]
    save_versions_meta(dest, meta)

    # Second archive of same payload; hash must not be polluted by sibling versions/.
    e2 = archive_local_payload(dest)
    a1 = dest / VERSIONS_DIR_NAME / e1["version_id"]
    a2 = dest / VERSIONS_DIR_NAME / e2["version_id"]
    assert e1["content_hash"] == e2["content_hash"]
    assert e1["content_hash"] == content_hash(a1)
    assert e2["content_hash"] == content_hash(a2)
    # Live tree with versions/ hashes differently than payload-only archive.
    live_hash = content_hash(dest)
    assert live_hash != e2["content_hash"]


def test_get_tool_list_versions(ctx: ToolContext, paths) -> None:
    name = "pkg_get"
    _install_and_verify(ctx, name, marker="v1")
    assert promote_tool({"name": name}, ctx).ok
    _install_and_verify(ctx, name, marker="v2")
    p2 = promote_tool({"name": name}, ctx)
    assert p2.ok
    vid = p2.payload["archived_version_id"]

    g = get_tool({"name": name, "list_versions": True}, ctx)
    assert g.ok is True, g
    versions = g.payload.get("versions")
    assert isinstance(versions, list)
    assert len(versions) == 1
    row = versions[0]
    assert row["version_id"] == vid
    assert "content_hash" in row
    # Meta only — no nested package body dump.
    assert "tool_md_preview" not in row
    assert "files" not in row

    g_ver = get_tool(
        {"name": name, "which": "version", "version_id": vid},
        ctx,
    )
    assert g_ver.ok is True, g_ver
    assert "v1" in (g_ver.payload.get("package") or {}).get("tool_md_preview", "")


def test_force_still_rejected(ctx: ToolContext) -> None:
    name = "pkg_force"
    _install_and_verify(ctx, name)
    r = promote_tool({"name": name, "force": True}, ctx)
    assert r.ok is False
    assert r.error_reason == "force_not_allowed"

    # promote_draft_tool force path
    r2 = promote_draft_tool(ctx.paths, name, force=True)
    assert r2["ok"] is False
    assert r2["error_reason"] == "force_not_allowed"


def test_promote_mid_failure_keeps_prior_payload(ctx: ToolContext, paths, monkeypatch):
    name = "pkg_midfail"
    _install_and_verify(ctx, name, marker="v1")
    assert promote_tool({"name": name}, ctx).ok
    local = local_package_dir(paths, name)
    prior_tool = (local / "TOOL.md").read_text(encoding="utf-8")

    _install_and_verify(ctx, name, marker="v2")

    import elyra.tools.promote as promote_mod

    real_swap = promote_mod.whole_tree_rename_swap

    def boom(**kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(promote_mod, "whole_tree_rename_swap", boom)
    p = promote_tool({"name": name}, ctx)
    assert p.ok is False
    assert p.error_reason and p.error_reason.startswith("promote_failed")

    # Prior complete package still at live name (or name absent — never hollow).
    if local.exists():
        assert package_is_complete(local)
        assert (local / "TOOL.md").read_text(encoding="utf-8") == prior_tool
    else:
        # Name absent is allowed; draft should still exist (not deleted on fail).
        assert (drafts_dir(paths) / name).is_dir()

    monkeypatch.setattr(promote_mod, "whole_tree_rename_swap", real_swap)


def test_promote_never_hollow_live_name(ctx: ToolContext, paths, monkeypatch):
    name = "pkg_hollow"
    _install_and_verify(ctx, name, marker="v1")
    assert promote_tool({"name": name}, ctx).ok
    _install_and_verify(ctx, name, marker="v2")

    import elyra.tools.promote as promote_mod

    observations: list[bool] = []
    real_rename = promote_mod._rename_path

    def watching_rename(src: Path, dst: Path) -> None:
        real_rename(src, dst)
        live = local_package_dir(paths, name)
        if live.exists():
            observations.append(package_is_complete(live))

    monkeypatch.setattr(promote_mod, "_rename_path", watching_rename)
    p = promote_tool({"name": name}, ctx)
    assert p.ok is True, p
    # Whenever live name existed after a rename, it was complete.
    assert observations
    assert all(observations)
    assert package_is_complete(local_package_dir(paths, name))


def test_promote_locked_second_caller(ctx: ToolContext, paths) -> None:
    name = "pkg_lock"
    _install_and_verify(ctx, name, marker="v1")
    assert promote_tool({"name": name}, ctx).ok
    _install_and_verify(ctx, name, marker="v2")

    held = threading.Event()
    release = threading.Event()
    results: list[tuple[str, dict]] = []

    def holder() -> None:
        # Hold lock while second caller attempts promote, then promote after unlock.
        with package_lock(paths, name):
            held.set()
            release.wait(timeout=10)
        r = promote_draft_tool(paths, name)
        results.append(("holder", r))

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(timeout=5)

    second = promote_draft_tool(paths, name)
    assert second["ok"] is False
    assert second["error_reason"] in ("promote_locked", "package_locked")

    release.set()
    t.join(timeout=30)
    assert any(tag == "holder" and r.get("ok") for tag, r in results)

    local = local_package_dir(paths, name)
    meta = load_versions_meta(local)
    assert len(meta) == 1
    assert package_is_complete(local)


def test_mint_version_id_from_identity_layout_reexport() -> None:
    from elyra.identity.layout import (
        VERSION_GC_LIMIT as LAYOUT_LIMIT,
        VERSION_ID_RE as LAYOUT_RE,
        mint_version_id as layout_mint,
    )

    vid = layout_mint()
    assert LAYOUT_RE.fullmatch(vid)
    assert LAYOUT_LIMIT == 50
    assert VERSION_ID_RE.fullmatch(mint_version_id())
    assert VERSION_GC_LIMIT == LAYOUT_LIMIT


def test_get_tool_and_revert_registered(registry: ToolRegistry) -> None:
    for name in ("get_tool", "revert_tool", "promote_tool"):
        assert registry.has(name), name
        pkg = registry.get(name)
        assert pkg is not None
        assert pkg.runner.kind == "builtin"
        assert pkg.handler is not None


def test_copy_payload_helper_excludes_meta(paths) -> None:
    src = _plant_local_package(paths, "copy_src")
    (src / VERSIONS_DIR_NAME / "x").mkdir(parents=True)
    save_versions_meta(src, [{"version_id": "x"}])
    dst = paths.tools_dir / "local" / "copy_dst"
    copy_package_payload(src, dst)
    assert (dst / "TOOL.md").is_file()
    assert not (dst / VERSIONS_DIR_NAME).exists()
    assert not (dst / VERSIONS_META_NAME).exists()


def test_lock_path_sibling_not_inside_package(paths) -> None:
    name = "pkg_lockpath"
    lp = lock_path_for(paths, name)
    assert lp.name == f".{name.casefold()}.lock"
    assert lp.parent == paths.tools_dir / "local"


def test_promote_failed_swap_leaves_dest_history_unchanged(
    ctx: ToolContext, paths, monkeypatch
) -> None:
    """Issue 1: archive lands on stage only; failed swap must not grow dest GC."""
    name = "pkg_fail_gc"
    dest = _plant_local_package(paths, name, marker="live")
    # Plant full GC-cap history on dest.
    for i in range(VERSION_GC_LIMIT):
        entry = archive_local_payload(
            dest,
            version_id=f"20200101T0000{i:02d}Z_aaaaaa",
            reason=f"seed_{i:03d}",
        )
        meta = load_versions_meta(dest)
        meta.append(entry)
        save_versions_meta(dest, meta)
    gc_package_versions(dest, limit=VERSION_GC_LIMIT)
    prior_meta = load_versions_meta(dest)
    assert len(prior_meta) == VERSION_GC_LIMIT
    prior_ids = {r["version_id"] for r in prior_meta}
    prior_tool = (dest / "TOOL.md").read_text(encoding="utf-8")

    _install_and_verify(ctx, name, marker="new")
    import elyra.tools.promote as promote_mod

    monkeypatch.setattr(
        promote_mod,
        "whole_tree_rename_swap",
        lambda **kwargs: (_ for _ in ()).throw(OSError("simulated swap fail")),
    )
    # Drive five failed re-promotes (each rebuilds draft verify first once).
    for _ in range(5):
        # Re-verify may be needed if draft still present after fail.
        if (drafts_dir(paths) / name).is_dir():
            # Ensure verify record still good after failed promote.
            from elyra.tools.verify import load_verify_record

            if load_verify_record(drafts_dir(paths) / name) is None:
                assert verify_tool({"name": name}, ctx).ok
        p = promote_tool({"name": name}, ctx)
        assert p.ok is False
        assert p.error_reason and p.error_reason.startswith("promote_failed")

    # Dest history must be unchanged (no archive pollution / GC breach).
    after_meta = load_versions_meta(dest)
    assert len(after_meta) == VERSION_GC_LIMIT
    assert {r["version_id"] for r in after_meta} == prior_ids
    assert package_is_complete(dest)
    assert (dest / "TOOL.md").read_text(encoding="utf-8") == prior_tool
    versions_dirs = [
        c for c in (dest / VERSIONS_DIR_NAME).iterdir() if c.is_dir()
    ]
    assert len(versions_dirs) == VERSION_GC_LIMIT


def test_promote_locked_casefold_alias(ctx: ToolContext, paths) -> None:
    """Issue 2: Foo and foo share one package lock."""
    # Tool names are validated lowercase-ish; use mixed case still matching RE.
    # is_valid_tool_name allows A-Z; normalize casefolds.
    name_a = "PkgCase"
    name_b = "pkgcase"
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with package_lock(paths, name_a):
            held.set()
            release.wait(timeout=10)

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(timeout=5)

    # Same logical package under different casing → promote_locked.
    second = promote_draft_tool(paths, name_b)
    # draft missing is ok if we never installed; force lock path via package_lock probe.
    from elyra.tools.promote import PackageLockedError

    blocked = False
    try:
        with package_lock(paths, name_b):
            pass
    except PackageLockedError:
        blocked = True
    assert blocked is True

    # Lock files must be the same path.
    assert lock_path_for(paths, name_a) == lock_path_for(paths, name_b)

    release.set()
    t.join(timeout=10)


def test_gc_drops_older_orphan_before_newer_index(paths) -> None:
    """Issue 3: orphan heal sorts by version_id before oldest-first trim."""
    name = "pkg_orphan_gc"
    dest = _plant_local_package(paths, name)
    # Three newer indexed archives.
    indexed_ids = [
        "20260101T000001Z_bbbbbb",
        "20260101T000002Z_bbbbbb",
        "20260101T000003Z_bbbbbb",
    ]
    meta: list[dict] = []
    for vid in indexed_ids:
        entry = archive_local_payload(dest, version_id=vid)
        meta.append(entry)
    save_versions_meta(dest, meta)

    # Ancient orphan dir not in index (simulates crash after mkdir/copy).
    orphan_id = "20000101T000000Z_ffffff"
    orphan = dest / VERSIONS_DIR_NAME / orphan_id
    orphan.mkdir(parents=True)
    (orphan / "TOOL.md").write_text("orphan\n", encoding="utf-8")

    kept = gc_package_versions(dest, limit=3)
    kept_ids = [r["version_id"] for r in kept]
    assert orphan_id not in kept_ids
    assert orphan_id not in {
        c.name for c in (dest / VERSIONS_DIR_NAME).iterdir() if c.is_dir()
    }
    assert set(kept_ids) == set(indexed_ids)
    assert len(kept) == 3
