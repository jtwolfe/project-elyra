import pytest

from elyra.config import resolve_paths
from elyra.prompts.loader import load_prompt, resolve_prompt_path


def test_load_system_prompt_from_project():
    text = load_prompt("system")
    assert text.strip()
    assert "Elyra" in text
    assert "Self" in text or "self" in text


def test_load_orient_prompt_from_project():
    text = load_prompt("orient.md")
    assert text.strip()
    assert "SELF" in text or "Self" in text
    assert "NOW" in text or "Now" in text


def test_resolve_prompt_path_prefers_home(tmp_path):
    paths = resolve_paths(tmp_path)
    custom = paths.prompts_dir / "system.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("# custom system\n", encoding="utf-8")
    found = resolve_prompt_path("system", paths=paths)
    assert found == custom.resolve()
    assert load_prompt("system", paths=paths) == "# custom system\n"


def test_load_prompt_falls_back_to_project_when_home_empty(tmp_path):
    paths = resolve_paths(tmp_path)
    # home prompts dir absent / empty — still load repo prompts
    text = load_prompt("system", paths=paths)
    assert "Elyra" in text


def test_load_prompt_missing_raises(tmp_path):
    paths = resolve_paths(tmp_path)
    with pytest.raises(FileNotFoundError):
        load_prompt("does-not-exist-xyz", paths=paths)


def test_system_prompt_is_valid_nonempty_lean():
    """Disk smoke: system.md is usable lean prompt text (no model required)."""
    text = load_prompt("system")
    assert isinstance(text, str)
    assert len(text.strip()) > 40
    # lean system — not a multi-page bible
    assert len(text) < 4000


@pytest.mark.parametrize(
    "bad_name",
    ["../secret", "../../etc/passwd", "/etc/passwd", "a/b", "seeds/identity/self"],
)
def test_prompt_path_jail_rejects_escape(tmp_path, bad_name):
    paths = resolve_paths(tmp_path)
    # file outside prompts that a naive join might hit
    (tmp_path / "secret.md").write_text("NOPE\n", encoding="utf-8")
    assert resolve_prompt_path(bad_name, paths=paths) is None
    with pytest.raises(FileNotFoundError):
        load_prompt(bad_name, paths=paths)
