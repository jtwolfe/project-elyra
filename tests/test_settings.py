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
    # IK11: empty sentinel; auto roots resolve at use site (vcs_jail), not load.
    assert s.tools.allowed_repo_roots == ()
    assert s.goals.close_gate == "soft"
    # Continuous remains product-default OFF
    assert s.continuous.enabled is False
    # Provider / usage Phase 0 defaults (settings surface only)
    assert s.provider.name == "xai"
    assert s.provider.model == "grok-4.5"
    assert s.provider.model == DEFAULT_XAI_MODEL
    assert s.provider.model_label == "Grok 4.5"
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
    # Soft day/hour hard-stops by default; pace/burst/account/credits knobs
    assert s.usage.day_hard_stop_enabled is False
    assert s.usage.hour_hard_stop_enabled is False
    assert s.usage.account_hard_stop_percent == 95.0
    assert s.usage.pace_yellow_ratio == 1.0
    assert s.usage.pace_red_ratio == 1.5
    assert s.usage.burst_hours == 4.0
    assert s.usage.credits_poll_enabled is True
    assert s.usage.credits_base_url == "https://cli-chat-proxy.grok.com"
    assert s.usage.credits_poll_interval_s == 300.0
    assert s.usage.credits_stale_after_s == 3600.0
    assert s.usage.auto_throttle_model is False
    assert s.usage.throttle_model is None
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
    assert d["usage"]["day_hard_stop_enabled"] is False
    assert d["usage"]["hour_hard_stop_enabled"] is False
    assert d["usage"]["pace_yellow_ratio"] == 1.0
    assert d["usage"]["pace_red_ratio"] == 1.5
    assert d["usage"]["burst_hours"] == 4.0
    assert d["usage"]["account_hard_stop_percent"] == 95.0
    assert d["usage"]["credits_base_url"] == "https://cli-chat-proxy.grok.com"
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
day_hard_stop_enabled = true
hour_hard_stop_enabled = true
account_hard_stop_percent = 90.0
pace_yellow_ratio = 0.8
pace_red_ratio = 1.2
burst_hours = 2.0
credits_poll_enabled = false
credits_base_url = "https://billing.example.com"
credits_poll_interval_s = 60.0
credits_stale_after_s = 120.0
auto_throttle_model = true
throttle_model = "grok-3-mini"

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
    assert s.usage.day_hard_stop_enabled is True
    assert s.usage.hour_hard_stop_enabled is True
    assert s.usage.account_hard_stop_percent == 90.0
    assert s.usage.pace_yellow_ratio == 0.8
    assert s.usage.pace_red_ratio == 1.2
    assert s.usage.burst_hours == 2.0
    assert s.usage.credits_poll_enabled is False
    assert s.usage.credits_base_url == "https://billing.example.com"
    assert s.usage.credits_poll_interval_s == 60.0
    assert s.usage.credits_stale_after_s == 120.0
    assert s.usage.auto_throttle_model is True
    assert s.usage.throttle_model == "grok-3-mini"
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
    with pytest.raises(ValueError, match="account_hard_stop_percent"):
        merge_cli_overrides(base, {"usage": {"account_hard_stop_percent": 0}})
    with pytest.raises(ValueError, match="pace_yellow_ratio"):
        merge_cli_overrides(base, {"usage": {"pace_yellow_ratio": 0}})
    with pytest.raises(ValueError, match="pace_red_ratio"):
        merge_cli_overrides(base, {"usage": {"pace_red_ratio": 0.5}})
    with pytest.raises(ValueError, match="burst_hours"):
        merge_cli_overrides(base, {"usage": {"burst_hours": -1}})
    with pytest.raises(ValueError, match="credits_poll_interval_s"):
        merge_cli_overrides(base, {"usage": {"credits_poll_interval_s": 10}})
    with pytest.raises(ValueError, match="credits_stale_after_s"):
        merge_cli_overrides(base, {"usage": {"credits_stale_after_s": 100}})
    with pytest.raises(ValueError, match="credits_base_url"):
        merge_cli_overrides(
            base, {"usage": {"credits_base_url": "https://x.com/v1"}}
        )
    with pytest.raises(ValueError, match="throttle_model"):
        merge_cli_overrides(base, {"usage": {"throttle_model": ""}})


def test_invalid_account_hard_stop_percent_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[usage]\naccount_hard_stop_percent = 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="account_hard_stop_percent"):
        load_settings(tmp_path)
    (tmp_path / "elyra.toml").write_text(
        "[usage]\naccount_hard_stop_percent = 100.1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="account_hard_stop_percent"):
        load_settings(tmp_path)


def test_invalid_pace_ratios_raise(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[usage]\npace_yellow_ratio = 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="pace_yellow_ratio"):
        load_settings(tmp_path)
    # red must be strictly greater than yellow (default yellow=1.0)
    (tmp_path / "elyra.toml").write_text(
        "[usage]\npace_red_ratio = 1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="pace_red_ratio"):
        load_settings(tmp_path)
    # raising yellow above existing red fails
    (tmp_path / "elyra.toml").write_text(
        "[usage]\npace_yellow_ratio = 2.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="pace_red_ratio"):
        load_settings(tmp_path)
    # both set together with red <= yellow fails
    (tmp_path / "elyra.toml").write_text(
        "[usage]\npace_yellow_ratio = 1.5\npace_red_ratio = 1.5\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="pace_red_ratio"):
        load_settings(tmp_path)


def test_valid_pace_ratios_accept(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[usage]\npace_yellow_ratio = 0.5\npace_red_ratio = 0.75\n",
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.usage.pace_yellow_ratio == 0.5
    assert s.usage.pace_red_ratio == 0.75


def test_invalid_burst_hours_raises(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[usage]\nburst_hours = -0.1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="burst_hours"):
        load_settings(tmp_path)
    # zero is allowed
    (tmp_path / "elyra.toml").write_text(
        "[usage]\nburst_hours = 0\n",
        encoding="utf-8",
    )
    assert load_settings(tmp_path).usage.burst_hours == 0.0


def test_invalid_credits_poll_and_stale_raise(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[usage]\ncredits_poll_interval_s = 29\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="credits_poll_interval_s"):
        load_settings(tmp_path)
    # stale must be >= poll interval (default poll=300)
    (tmp_path / "elyra.toml").write_text(
        "[usage]\ncredits_stale_after_s = 299\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="credits_stale_after_s"):
        load_settings(tmp_path)
    # raising poll above existing stale fails
    (tmp_path / "elyra.toml").write_text(
        "[usage]\ncredits_poll_interval_s = 4000\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="credits_stale_after_s"):
        load_settings(tmp_path)
    # boundary: stale == poll is ok
    (tmp_path / "elyra.toml").write_text(
        "[usage]\ncredits_poll_interval_s = 60\ncredits_stale_after_s = 60\n",
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.usage.credits_poll_interval_s == 60.0
    assert s.usage.credits_stale_after_s == 60.0
    # floor boundary: poll interval == 30 is ok
    (tmp_path / "elyra.toml").write_text(
        "[usage]\ncredits_poll_interval_s = 30\ncredits_stale_after_s = 30\n",
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.usage.credits_poll_interval_s == 30.0
    assert s.usage.credits_stale_after_s == 30.0


def test_account_hard_stop_percent_boundary_accepts_100(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        "[usage]\naccount_hard_stop_percent = 100\n",
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.usage.account_hard_stop_percent == 100.0


def test_invalid_credits_base_url_raises(tmp_path):
    bad_urls = [
        "not-a-url",
        "ftp://example.com",
        "https://",  # no host
        "https://example.com/v1",  # path not empty or /
        "https://example.com?q=1",  # query
        "https://example.com#frag",  # fragment
        "http://example.com/path/",
        "https://[::1",  # malformed IPv6 — must surface credits_base_url path
        "https://u:p@example.com",  # userinfo
        "https://example.com:abc",  # non-numeric port
        " https://example.com",  # leading whitespace
        "https://example.com ",  # trailing whitespace
    ]
    for url in bad_urls:
        (tmp_path / "elyra.toml").write_text(
            f'[usage]\ncredits_base_url = "{url}"\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="credits_base_url"):
            load_settings(tmp_path)
    # trailing slash origin is allowed
    (tmp_path / "elyra.toml").write_text(
        '[usage]\ncredits_base_url = "http://127.0.0.1:8080/"\n',
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.usage.credits_base_url == "http://127.0.0.1:8080/"
    # valid IPv6 origin is allowed
    (tmp_path / "elyra.toml").write_text(
        '[usage]\ncredits_base_url = "https://[::1]"\n',
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.usage.credits_base_url == "https://[::1]"


def test_invalid_throttle_model_and_bool_types_raise(tmp_path):
    (tmp_path / "elyra.toml").write_text(
        '[usage]\nthrottle_model = ""\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="throttle_model"):
        load_settings(tmp_path)
    (tmp_path / "elyra.toml").write_text(
        '[usage]\nthrottle_model = "   "\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="throttle_model"):
        load_settings(tmp_path)
    (tmp_path / "elyra.toml").write_text(
        "[usage]\nday_hard_stop_enabled = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="day_hard_stop_enabled"):
        load_settings(tmp_path)
    (tmp_path / "elyra.toml").write_text(
        '[usage]\ncredits_poll_enabled = "yes"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="credits_poll_enabled"):
        load_settings(tmp_path)
    (tmp_path / "elyra.toml").write_text(
        "[usage]\nauto_throttle_model = 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="auto_throttle_model"):
        load_settings(tmp_path)
    (tmp_path / "elyra.toml").write_text(
        "[usage]\nhour_hard_stop_enabled = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="hour_hard_stop_enabled"):
        load_settings(tmp_path)


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


def test_allowed_repo_roots_from_toml(tmp_path):
    """TOML array coerces to tuple[str, ...] (IK11 / PR7)."""
    (tmp_path / "elyra.toml").write_text(
        """
[tools]
verify_timeout_seconds = 99
allowed_repo_roots = ["/home/jim/Workspace/project-elyra", "~/code"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.tools.verify_timeout_seconds == 99
    assert s.tools.allowed_repo_roots == (
        "/home/jim/Workspace/project-elyra",
        "~/code",
    )
    assert isinstance(s.tools.allowed_repo_roots, tuple)
    assert all(isinstance(x, str) for x in s.tools.allowed_repo_roots)


def test_allowed_repo_roots_cli_list_coerces():
    merged = merge_cli_overrides(
        default_settings(),
        {"tools": {"allowed_repo_roots": ["/a", "/b"]}},
    )
    assert merged.tools.allowed_repo_roots == ("/a", "/b")


def test_allowed_repo_roots_rejects_non_str_elements():
    with pytest.raises(ValueError, match="allowed_repo_roots"):
        merge_cli_overrides(
            default_settings(),
            {"tools": {"allowed_repo_roots": ["/ok", 1]}},
        )
