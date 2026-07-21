from elyra.config import resolve_paths
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
    from elyra.config import ElyraPaths

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
