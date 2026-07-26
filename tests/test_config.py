from pathlib import Path

import pytest

from elyra.config import ElyraPaths, project_root, resolve_paths
from elyra.llm.constants import CONTEXT_WINDOW_TOKENS
from elyra.llm.server import build_server_command, validate_model_paths


def test_resolve_paths_default():
    paths = resolve_paths()
    assert paths.model_dir == paths.home / "model"
    assert paths.data_dir == paths.home / "data"


def test_build_server_command_includes_context():
    paths = resolve_paths()
    cmd = build_server_command(paths)
    assert "-c" in cmd
    assert str(CONTEXT_WINDOW_TOKENS) in cmd
    assert "--jinja" in cmd
    assert "--reasoning" in cmd


def test_validate_model_paths_reports_missing(tmp_path):
    paths = ElyraPaths(
        home=tmp_path,
        model_dir=tmp_path / "model",
        data_dir=tmp_path / "data",
        skills_dir=tmp_path / "skills",
        tools_dir=tmp_path / "tools",
        prompts_dir=tmp_path / "prompts",
    )
    problems = validate_model_paths(paths)
    assert problems


def test_ensure_data_dirs_creates_runtime_layout(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()

    for name in (
        "moments",
        "wakes",
        "identity",
        "users",
        "goals",
        "sandbox",
        "runtime",
        "secrets",
    ):
        assert (paths.data_dir / name).is_dir()
    assert (paths.skills_dir / "local").is_dir()
    assert (paths.tools_dir / "local").is_dir()
    assert (paths.tools_dir / "drafts").is_dir()


def test_ensure_data_dirs_seeds_once_from_project_prompts(tmp_path):
    """tmp ELYRA_HOME has no prompts/; seeds resolve from project root."""
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()

    # Prefer current.md layout (legacy self.md / profile.md not required on fresh).
    self_md = paths.data_dir / "identity" / "current.md"
    op_md = paths.data_dir / "users" / "operator" / "current.md"
    assert self_md.is_file()
    assert op_md.is_file()
    assert (paths.data_dir / "identity" / "meta.json").is_file()
    assert (paths.data_dir / "users" / "operator" / "meta.json").is_file()
    self_text = self_md.read_text(encoding="utf-8")
    assert "Elyra" in self_text
    assert "Self" in self_text or "self" in self_text.lower()
    op_text = op_md.read_text(encoding="utf-8")
    assert "Operator" in op_text
    assert "Stretch 1" in op_text


def test_ensure_data_dirs_never_overwrites_existing_digests(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()

    self_md = paths.data_dir / "identity" / "current.md"
    op_md = paths.data_dir / "users" / "operator" / "current.md"
    self_md.write_text("CUSTOM SELF\n", encoding="utf-8")
    op_md.write_text("CUSTOM OPERATOR\n", encoding="utf-8")

    paths.ensure_data_dirs()
    assert self_md.read_text(encoding="utf-8") == "CUSTOM SELF\n"
    assert op_md.read_text(encoding="utf-8") == "CUSTOM OPERATOR\n"


def test_ensure_data_dirs_raises_if_seed_dest_is_directory(tmp_path):
    paths = resolve_paths(tmp_path)
    blocked = paths.data_dir / "identity" / "current.md"
    blocked.mkdir(parents=True)
    with pytest.raises(FileExistsError, match="not a file"):
        paths.ensure_data_dirs()


def test_resolve_seed_prefers_home_prompts(tmp_path):
    paths = resolve_paths(tmp_path)
    seed_rel = Path("seeds") / "identity" / "self.md"
    local = paths.prompts_dir / seed_rel
    local.parent.mkdir(parents=True)
    local.write_text("HOME SEED\n", encoding="utf-8")

    found = paths.resolve_seed(seed_rel)
    assert found == local.resolve()
    assert found.read_text(encoding="utf-8") == "HOME SEED\n"


def test_resolve_seed_missing_returns_none(tmp_path):
    paths = resolve_paths(tmp_path)
    assert paths.resolve_seed("seeds/no-such-template.md") is None


def test_resolve_seed_rejects_absolute_and_dotdot(tmp_path):
    paths = resolve_paths(tmp_path)
    assert paths.resolve_seed(Path("/etc/passwd")) is None
    assert paths.resolve_seed("../secret.md") is None
    assert paths.resolve_seed(Path("seeds") / ".." / "secret.md") is None


def test_ensure_still_creates_dirs_when_seed_name_missing(tmp_path):
    """Quiet no-op on missing seed does not block directory creation."""
    paths = resolve_paths(tmp_path)
    # ensure uses real seed names which exist in project; dirs always created
    paths.ensure_data_dirs()
    assert (paths.data_dir / "goals").is_dir()
    # synthetic missing seed via resolve only
    assert paths.resolve_seed("seeds/missing/x.md") is None


def test_project_root_points_at_repo():
    root = project_root()
    assert (root / "elyra").is_dir()
    assert (root / "prompts" / "seeds" / "identity" / "self.md").is_file()
