from pathlib import Path

import pytest
import tomllib

from elyra.llm.models import (
    CURATED_XAI_MODELS,
    DEFAULT_XAI_MODEL,
    DEFAULT_XAI_MODEL_LABEL,
    label_for_model,
)
from elyra.settings import (
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
    assert s.loop.orient_skill_catalog_max_tokens == 400
    assert s.loop.orient_goals_max_tokens == 600
    # K12 / item 5: optional post-load tool_choice pin — default OFF
    assert s.loop.post_load_skill_tool_choice_required is False
    assert s.wait.default_timeout_seconds == 300
    assert s.wait.free_text_timeout_seconds == 300
    assert s.tools.verify_timeout_seconds == 120
    assert s.goals.close_gate == "soft"
    # Continuous remains product-default OFF
    assert s.continuous.enabled is False
    # Provider / usage Phase 0 defaults (settings surface only)
    assert s.provider.name == "xai"
    assert s.provider.model == "grok-4.5"
    assert s.provider.model == DEFAULT_XAI_MODEL
    assert s.provider.model_label == "Grok 4.5 Fast"
    assert s.provider.model_label == DEFAULT_XAI_MODEL_LABEL
    assert s.provider.base_url == "https://api.x.ai/v1"
    assert s.provider.credential_source == "grok_build"
    assert s.provider.grok_auth_path is None
    assert s.provider.request_timeout_s == 120.0
    assert s.usage.enabled is True
    assert s.usage.weekly_allowed_tokens == 5_000_000
    assert s.usage.weekly_allowed_fraction == 0.50
    assert s.usage.hour_block_minutes == 60
    assert s.usage.day_allowed_tokens is None
    assert s.usage.hour_allowed_tokens is None
    assert s.api_host == "127.0.0.1"
    assert s.api_port == 8787
    assert not hasattr(s, "context_tokens")


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


def test_load_settings_expands_user_home(tmp_path, monkeypatch):
    home = tmp_path / "elyra-home"
    home.mkdir()
    (home / "elyra.toml").write_text(
        "[loop]\nmax_tool_hops = 11\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    s = load_settings("~/elyra-home")
    assert s.loop.max_tool_hops == 11


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
        },
    )
    assert merged.loop.max_tool_hops == 7
    assert merged.loop.generation_max_tokens == 1000  # from toml, not overridden
    assert merged.api_host == "0.0.0.0"
    assert merged.api_port == 9000


def test_merge_cli_ignores_none_values():
    base = Settings(loop=LoopSettings(max_tool_hops=99), api_host="keep.me")
    merged = merge_cli_overrides(
        base,
        {"api_host": None, "loop": {"max_tool_hops": None}},
    )
    assert merged.api_host == "keep.me"
    assert merged.loop.max_tool_hops == 99


def test_settings_as_dict_round_structure():
    d = settings_as_dict(default_settings())
    assert d["loop"]["max_tool_hops"] == 200
    assert d["goals"]["close_gate"] == "soft"
    assert d["provider"]["name"] == "xai"
    assert d["provider"]["model"] == "grok-4.5"
    assert d["usage"]["enabled"] is True
    assert d["usage"]["weekly_allowed_tokens"] == 5_000_000
    assert d["continuous"]["enabled"] is False


def test_load_settings_provider_and_usage_toml(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        """
[provider]
name = "local"
model = "custom-model"
model_label = "Custom"
base_url = "http://127.0.0.1:8080/v1"
credential_source = "api_key"
request_timeout_s = 60.0

[usage]
enabled = false
weekly_allowed_tokens = 1000
weekly_allowed_fraction = 0.25
hour_block_minutes = 30
day_allowed_tokens = 100
hour_allowed_tokens = 10

[continuous]
enabled = false
""".strip()
        + "\n",
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.provider.name == "local"
    assert s.provider.model == "custom-model"
    assert s.provider.model_label == "Custom"
    assert s.provider.base_url == "http://127.0.0.1:8080/v1"
    assert s.provider.credential_source == "api_key"
    assert s.provider.request_timeout_s == 60.0
    # unset optional path stays default
    assert s.provider.grok_auth_path is None
    assert s.usage.enabled is False
    assert s.usage.weekly_allowed_tokens == 1000
    assert s.usage.weekly_allowed_fraction == 0.25
    assert s.usage.hour_block_minutes == 30
    assert s.usage.day_allowed_tokens == 100
    assert s.usage.hour_allowed_tokens == 10
    assert s.continuous.enabled is False


def test_cli_overrides_provider_and_usage_win_over_toml(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        """
[provider]
name = "local"
model = "from-toml"
model_label = "From Toml"

[usage]
weekly_allowed_tokens = 999
enabled = false
""".strip()
        + "\n",
        encoding="utf-8",
    )
    base = load_settings(tmp_path)
    merged = merge_cli_overrides(
        base,
        {
            "provider": {"name": "xai", "model": "grok-4.5"},
            "usage": {"enabled": True, "weekly_allowed_tokens": 5_000_000},
        },
    )
    assert merged.provider.name == "xai"
    assert merged.provider.model == "grok-4.5"
    # toml-only field not overridden stays
    assert merged.provider.model_label == "From Toml"
    assert merged.usage.enabled is True
    assert merged.usage.weekly_allowed_tokens == 5_000_000


def test_merge_cli_provider_none_values_ignored():
    base = default_settings()
    merged = merge_cli_overrides(
        base,
        {
            "provider": {"name": None, "model": "only-this"},
            "usage": {"enabled": None, "weekly_allowed_tokens": 42},
        },
    )
    assert merged.provider.name == "xai"  # default preserved
    assert merged.provider.model == "only-this"
    assert merged.usage.enabled is True  # default preserved
    assert merged.usage.weekly_allowed_tokens == 42


def test_bad_int_type_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        '[loop]\nmax_tool_hops = "not-a-number"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="loop.max_tool_hops"):
        load_settings(tmp_path)


def test_bad_float_for_int_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[loop]\ncontinue_idle_minutes = 8.5\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="continue_idle_minutes"):
        load_settings(tmp_path)


def test_invalid_close_gate_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        '[goals]\nclose_gate = "maybe"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="close_gate"):
        load_settings(tmp_path)


def test_invalid_provider_name_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        '[provider]\nname = "openai"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="provider.name"):
        load_settings(tmp_path)


def test_invalid_credential_source_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        '[provider]\ncredential_source = "env"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="provider.credential_source"):
        load_settings(tmp_path)


def test_invalid_weekly_allowed_fraction_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[usage]\nweekly_allowed_fraction = 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="weekly_allowed_fraction"):
        load_settings(tmp_path)
    (tmp_path / "elyra.toml").write_text(
        "[usage]\nweekly_allowed_fraction = 1.5\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="weekly_allowed_fraction"):
        load_settings(tmp_path)


def test_invalid_weekly_allowed_tokens_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[usage]\nweekly_allowed_tokens = 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="weekly_allowed_tokens"):
        load_settings(tmp_path)


def test_invalid_hour_block_minutes_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[usage]\nhour_block_minutes = 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="hour_block_minutes"):
        load_settings(tmp_path)


def test_invalid_day_and_hour_allowed_tokens_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[usage]\nday_allowed_tokens = 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="day_allowed_tokens"):
        load_settings(tmp_path)
    (tmp_path / "elyra.toml").write_text(
        "[usage]\nhour_allowed_tokens = -1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="hour_allowed_tokens"):
        load_settings(tmp_path)


def test_invalid_request_timeout_s_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[provider]\nrequest_timeout_s = 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="request_timeout_s"):
        load_settings(tmp_path)
    (tmp_path / "elyra.toml").write_text(
        "[provider]\nrequest_timeout_s = -1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="request_timeout_s"):
        load_settings(tmp_path)


def test_cli_invalid_provider_and_usage_raise():
    base = default_settings()
    with pytest.raises(ValueError, match="provider.name"):
        merge_cli_overrides(base, {"provider": {"name": "openai"}})
    with pytest.raises(ValueError, match="provider.credential_source"):
        merge_cli_overrides(base, {"provider": {"credential_source": "env"}})
    with pytest.raises(ValueError, match="weekly_allowed_fraction"):
        merge_cli_overrides(base, {"usage": {"weekly_allowed_fraction": 0}})
    with pytest.raises(ValueError, match="weekly_allowed_tokens"):
        merge_cli_overrides(base, {"usage": {"weekly_allowed_tokens": -3}})
    with pytest.raises(ValueError, match="day_allowed_tokens"):
        merge_cli_overrides(base, {"usage": {"day_allowed_tokens": 0}})
    with pytest.raises(ValueError, match="hour_allowed_tokens"):
        merge_cli_overrides(base, {"usage": {"hour_allowed_tokens": -5}})
    with pytest.raises(ValueError, match="request_timeout_s"):
        merge_cli_overrides(base, {"provider": {"request_timeout_s": 0.0}})


def test_malformed_toml_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[provider\nname = broken\n",
        encoding="utf-8",
    )
    with pytest.raises(tomllib.TOMLDecodeError):
        load_settings(tmp_path)


def test_label_for_model_and_curated_defaults():
    assert DEFAULT_XAI_MODEL in CURATED_XAI_MODELS
    assert label_for_model(DEFAULT_XAI_MODEL) == DEFAULT_XAI_MODEL_LABEL
    assert label_for_model("grok-4.3") == "Grok 4.3"
    assert label_for_model("unknown-model-xyz") == "unknown-model-xyz"


def test_string_int_coerces_in_cli_override():
    merged = merge_cli_overrides(
        default_settings(),
        {"api_port": "9001", "loop": {"max_tool_hops": "50"}},
    )
    assert merged.api_port == 9001
    assert merged.loop.max_tool_hops == 50


def test_bool_rejected_as_int():
    with pytest.raises(ValueError, match="api_port"):
        merge_cli_overrides(default_settings(), {"api_port": True})
