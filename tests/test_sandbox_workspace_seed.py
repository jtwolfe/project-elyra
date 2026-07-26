"""Host seed tree + ensure_host_tree helpers (H2a; no MSB)."""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.config import ElyraPaths, project_root
from elyra.sandbox.paths import (
    GUEST_WORKSPACE_ROOT,
    PRIMARY_NAME,
    ensure_host_tree,
    guest_env,
    host_root_for,
    mount_fingerprint,
    resolve_msb_network_policy_id,
)
from elyra.sandbox.workspace_seed import (
    NEW_ROOT_REL,
    ensure_primary_sandbox_tree,
    has_general_seed,
    host_primary_root,
    primary_sandbox_root,
    repo_seed_source,
    seed_run_workspace,
    workspace_snapshot_hash,
)


def _layout(tmp_path: Path) -> ElyraPaths:
    return ElyraPaths(
        home=tmp_path,
        model_dir=tmp_path / "model",
        data_dir=tmp_path / "data",
        skills_dir=tmp_path / "skills",
        tools_dir=tmp_path / "tools",
        prompts_dir=tmp_path / "prompts",
    )


def test_host_primary_root_is_sandbox0(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    root = host_primary_root(layout)
    assert root == tmp_path / "sandboxes" / "sandbox0"
    assert primary_sandbox_root(layout) == root.resolve()
    assert host_root_for(PRIMARY_NAME, layout) == root


def test_ensure_creates_scaffold_and_copies_repo_seed(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    seed = repo_seed_source()
    assert seed is not None
    assert (seed / "general" / "now.py").is_file()

    dest = ensure_primary_sandbox_tree(layout)
    assert dest == host_primary_root(layout).resolve()
    assert (dest / "tmp").is_dir()
    assert (dest / "tools").is_dir()
    assert (dest / "media").is_dir()  # KD22 always-dirs
    assert (dest / "lib").is_dir()
    assert (dest / "general" / "now.py").is_file()
    assert (dest / "fixtures" / "demo_note.txt").is_file()
    assert (dest / "lib" / "paths.py").is_file()
    assert has_general_seed(dest)


def test_ensure_host_tree_alias(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    dest = ensure_host_tree(PRIMARY_NAME, layout)
    assert (dest / "general" / "now.py").is_file()


def test_ensure_does_not_overwrite_existing_seed(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    dest = host_primary_root(layout)
    dest.mkdir(parents=True)
    (dest / "general").mkdir()
    custom = dest / "general" / "now.py"
    custom.write_text("# custom\n", encoding="utf-8")
    ensure_primary_sandbox_tree(layout)
    assert custom.read_text(encoding="utf-8") == "# custom\n"


def test_ensure_with_explicit_seed_source(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    source = tmp_path / "custom_seed"
    (source / "general").mkdir(parents=True)
    (source / "lib").mkdir()
    (source / "fixtures").mkdir()
    (source / "general" / "now.py").write_text("print('seed')\n", encoding="utf-8")
    (source / "README.md").write_text("seed\n", encoding="utf-8")

    dest = ensure_primary_sandbox_tree(layout, seed_source=source)
    assert (dest / "general" / "now.py").read_text(encoding="utf-8") == "print('seed')\n"
    assert (dest / "README.md").is_file()


def test_ensure_multi_sandbox_scaffold_only(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    root = ensure_host_tree("sandbox1", layout)
    assert root == (tmp_path / "sandboxes" / "sandbox1").resolve()
    for name in ("lib", "general", "fixtures", "media", "tmp", "tools"):
        assert (root / name).is_dir()
    # No seed copy for non-primary names.
    assert not (root / "general" / "now.py").exists()


def test_seed_run_workspace(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    ensure_primary_sandbox_tree(layout)
    run_dir = seed_run_workspace(paths=layout, dest=tmp_path / "run")
    assert (run_dir / "general" / "now.py").is_file()
    assert (run_dir / "tmp").is_dir()
    assert workspace_snapshot_hash(workspace_root=run_dir)


def test_workspace_snapshot_ignores_tmp_tools_and_media(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    root = ensure_primary_sandbox_tree(layout)
    before = workspace_snapshot_hash(paths=layout, workspace_root=root)
    (root / "tmp" / "noise.bin").write_bytes(b"x" * 100)
    (root / "tools" / "bundle.py").write_text("print(1)\n", encoding="utf-8")
    # KD22: media projection churn must not skew workspace_snapshot_hash.
    (root / "media" / "att_x").mkdir(parents=True)
    (root / "media" / "att_x" / "shot.png").write_bytes(b"\x89PNG")
    after = workspace_snapshot_hash(paths=layout, workspace_root=root)
    assert after == before


def test_repo_seed_layout_present() -> None:
    seed = project_root() / NEW_ROOT_REL
    assert seed.is_dir()
    assert (seed / "tmp" / ".gitkeep").is_file()
    assert (seed / "tools" / ".gitkeep").is_file()
    assert (seed / "general" / "now.py").is_file()
    assert (seed / "fixtures" / "demo_note.txt").is_file()
    assert (seed / "README.md").is_file()


def test_guest_env_and_network_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    env = guest_env()
    assert env["ELYRA_SANDBOX_ROOT"] == GUEST_WORKSPACE_ROOT
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"

    monkeypatch.delenv("ELYRA_SANDBOX_NETWORK", raising=False)
    assert resolve_msb_network_policy_id() == "public_only"
    monkeypatch.setenv("ELYRA_SANDBOX_NETWORK", "none")
    assert resolve_msb_network_policy_id() == "none"
    monkeypatch.setenv("ELYRA_SANDBOX_NETWORK", "allow_all")
    assert resolve_msb_network_policy_id() == "allow_all"
    monkeypatch.setenv("ELYRA_SANDBOX_NETWORK", "bogus")
    assert resolve_msb_network_policy_id() == "public_only"


def test_mount_fingerprint_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELYRA_SANDBOX_NETWORK", raising=False)
    root = tmp_path / "sandbox0"
    root.mkdir()
    for d in ("lib", "general", "fixtures", "media", "tmp", "tools"):
        (root / d).mkdir()
    a = mount_fingerprint(PRIMARY_NAME, root)
    b = mount_fingerprint(PRIMARY_NAME, root)
    assert a == b
    c = mount_fingerprint(PRIMARY_NAME, root, network_policy_id="none")
    assert c != a


def test_mount_fingerprint_includes_media_ro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MOUNT_SPEC media RO changes fingerprint (forces MSB recreate)."""
    from elyra.sandbox.paths import MOUNT_SPEC

    monkeypatch.delenv("ELYRA_SANDBOX_NETWORK", raising=False)
    root = tmp_path / "sandbox0"
    root.mkdir()
    for _guest, host_rel, _ro in MOUNT_SPEC:
        (root / host_rel).mkdir(exist_ok=True)
    fp = mount_fingerprint(PRIMARY_NAME, root)
    assert fp
    guests = [g for g, _, _ in MOUNT_SPEC]
    assert f"{GUEST_WORKSPACE_ROOT}/media" in guests


def test_product_sandbox_root_is_sandbox0(tmp_path: Path) -> None:
    """H2c cutover: product Sandbox roots at sandboxes/sandbox0."""
    from elyra.sandbox import Sandbox

    layout = _layout(tmp_path)
    layout.ensure_data_dirs()
    sb = Sandbox(layout)
    sb.ensure_root()
    assert sb.root == host_primary_root(layout).resolve()
    assert (sb.root / "tmp").is_dir()
    assert (sb.root / "tools").is_dir()
