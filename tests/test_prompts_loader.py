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
    # lean system — not a multi-page bible (room for sandbox/growth/search pointers)
    assert len(text) < 5200


def test_system_prompt_defines_growth_path():
    """Always-on growth pointer: load_skill + create-tool pipeline + post-load commit."""
    text = load_prompt("system")
    lower = text.lower()
    assert "load_skill" in lower
    assert "create-tool" in lower
    assert "install_tool_draft" in lower or "draft" in lower
    assert "verify_tool" in lower or "verify" in lower
    assert "promote" in lower
    # Pointer to playbook first-action (not a duplicated full checklist).
    assert "first tool call" in lower or "first action" in lower


def test_system_prompt_exact_skill_names_not_underscored_aliases():
    """Skill names are hyphenated catalog ids; do not teach create_tool underscore form."""
    text = load_prompt("system")
    assert "create-tool" in text
    assert "plan-work" in text
    assert "do-work" in text
    # Explicit anti-pattern (underscore skill names) called out as wrong.
    assert "create_tool" in text  # in the "Wrong:" line
    assert "Wrong:" in text or "wrong:" in text.lower()
    assert "snake_case" in text or "snake-case" in text.lower() or "snake_case" in text
    assert "speak" in text
    assert "install_tool_draft" in text


def test_orient_prompt_requires_load_skill():
    """Orient must push catalog → exact load_skill names + tools vs skills."""
    text = load_prompt("orient")
    lower = text.lower()
    assert "load_skill" in lower
    assert "create-tool" in lower
    assert "{{SKILL_CATALOG}}" in text
    assert "{{SKILL_BIAS}}" in text
    assert "first tool call" in lower or "first action" in lower
    assert "exact" in lower
    assert "snake_case" in text or "tool schema" in lower or "schemas" in lower


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
