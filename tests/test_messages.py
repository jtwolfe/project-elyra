from elyra.config import ElyraPaths
from elyra.messages import append_message, list_messages


def test_append_and_list(tmp_path):
    paths = ElyraPaths(
        home=tmp_path,
        model_dir=tmp_path / "model",
        data_dir=tmp_path / "data",
        skills_dir=tmp_path / "skills",
        tools_dir=tmp_path / "tools",
        prompts_dir=tmp_path / "prompts",
    )
    paths.ensure_data_dirs()
    append_message("user", "hello", paths=paths)
    append_message("assistant", "hi", reasoning="r", paths=paths)
    rows = list_messages(paths=paths)
    assert len(rows) == 2
    assert rows[0]["content"] == "hello"
    assert rows[1]["reasoning"] == "r"
