from pathlib import Path

from elyra.settings import (
    GoalsSettings,
    LoopSettings,
    Settings,
    default_settings,
    load_settings,
    merge_cli_overrides,
    settings_as_dict,
)


def test_default_settings_match_design():
    s = default_settings()
    assert s.loop.continue_idle_minutes == 8
    assert s.loop.moment_wall_clock_minutes == 45
    assert s.loop.continue_max_injects == 3
    assert s.loop.max_tool_hops == 200
    assert s.loop.sliding_input_tokens == 24000
    assert s.loop.in_turn_max_tokens == 24000
    assert s.loop.tool_result_max_chars == 8000
    assert s.loop.generation_max_tokens == 8192
    assert s.wait.default_timeout_seconds == 120
    assert s.tools.verify_timeout_seconds == 120
    assert s.goals.close_gate == "soft"
    assert s.api_host == "127.0.0.1"
    assert s.api_port == 8787
    assert s.context_tokens is None


def test_load_settings_without_toml_returns_defaults(tmp_path):
    s = load_settings(tmp_path)
    assert s == default_settings()


def test_load_settings_reads_elyra_toml(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        """
[loop]
max_tool_hops = 42
continue_idle_minutes = 2

[wait]
default_timeout_seconds = 30

[tools]
verify_timeout_seconds = 90

[goals]
close_gate = "hard"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.loop.max_tool_hops == 42
    assert s.loop.continue_idle_minutes == 2
    # untouched loop defaults preserved
    assert s.loop.generation_max_tokens == 8192
    assert s.wait.default_timeout_seconds == 30
    assert s.tools.verify_timeout_seconds == 90
    assert s.goals.close_gate == "hard"


def test_cli_overrides_win_over_toml(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        """
[loop]
max_tool_hops = 42
generation_max_tokens = 1000
""".strip()
        + "\n",
        encoding="utf-8",
    )
    base = load_settings(tmp_path)
    merged = merge_cli_overrides(
        base,
        {
            "loop": {"max_tool_hops": 7},
            "api_host": "0.0.0.0",
            "api_port": 9000,
            "context_tokens": 4096,
        },
    )
    assert merged.loop.max_tool_hops == 7
    assert merged.loop.generation_max_tokens == 1000  # from toml, not overridden
    assert merged.api_host == "0.0.0.0"
    assert merged.api_port == 9000
    assert merged.context_tokens == 4096


def test_merge_cli_ignores_none_values():
    base = Settings(loop=LoopSettings(max_tool_hops=99), api_host="keep.me")
    merged = merge_cli_overrides(
        base,
        {"api_host": None, "context_tokens": None, "loop": {"max_tool_hops": None}},
    )
    assert merged.api_host == "keep.me"
    assert merged.loop.max_tool_hops == 99
    assert merged.context_tokens is None


def test_settings_as_dict_round_structure():
    d = settings_as_dict(default_settings())
    assert d["loop"]["max_tool_hops"] == 200
    assert d["goals"]["close_gate"] == "soft"
