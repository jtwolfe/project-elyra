"""Runtime process configuration + start-time settings merge.

Merge order (normative)::

    defaults  <  elyra.toml  <  data/runtime/provider.json  <  explicit CLI

``provider.json`` only supplies ``model`` and ``credential_source`` (non-secret).
``start_llama_server`` is derived: provider==local and not stub and not --no-llama.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from elyra.llm.config import LocalClientConfig
from elyra.llm.models import DEFAULT_XAI_MODEL, DEFAULT_XAI_MODEL_LABEL, label_for_model
from elyra.llm.provider_prefs import load_provider_prefs
from elyra.settings import Settings, UsageSettings, load_settings, merge_cli_overrides


@dataclass
class RuntimeConfig:
    api_host: str = "127.0.0.1"
    api_port: int = 8787
    # True only when provider=local and not --no-llama and not pure stub path.
    start_llama_server: bool = False
    llama: LocalClientConfig = field(default_factory=LocalClientConfig)
    llama_health_timeout: float = 180.0
    # KV ceiling; lower if VRAM crashes (see docs/inference.md).
    context_tokens: int | None = None
    # Provider / usage (Phase 0)
    provider_name: str = "xai"
    model: str = DEFAULT_XAI_MODEL
    model_label: str = DEFAULT_XAI_MODEL_LABEL
    base_url: str = "https://api.x.ai/v1"
    credential_source: str = "grok_build"
    grok_auth_path: str | None = None
    request_timeout_s: float = 120.0
    usage: UsageSettings = field(default_factory=UsageSettings)
    continuous_enabled: bool = False


def load_merged_settings(
    home: Path | str,
    data_dir: Path | str,
    *,
    provider: str | None = None,
    model: str | None = None,
    credential_source: str | None = None,
    no_usage_meter: bool = False,
    api_host: str | None = None,
    api_port: int | None = None,
    context_tokens: int | None = None,
) -> Settings:
    """Load defaults < toml < provider.json < explicit CLI overrides.

    CLI kwargs that are ``None`` are ignored (do not clobber prefs/toml).
    ``no_usage_meter=True`` forces ``usage.enabled=False``.
    """
    settings = load_settings(home)

    prefs = load_provider_prefs(Path(data_dir))
    pref_map: dict[str, Any] = {}
    if prefs.model is not None or prefs.credential_source is not None:
        psec: dict[str, Any] = {}
        if prefs.model is not None:
            psec["model"] = prefs.model
            psec["model_label"] = label_for_model(prefs.model)
        if prefs.credential_source is not None:
            psec["credential_source"] = prefs.credential_source
        pref_map["provider"] = psec
    if pref_map:
        settings = merge_cli_overrides(settings, pref_map)

    cli_map: dict[str, Any] = {}
    pcli: dict[str, Any] = {}
    if provider is not None:
        pcli["name"] = provider
    if model is not None:
        pcli["model"] = model
        pcli["model_label"] = label_for_model(model)
    if credential_source is not None:
        pcli["credential_source"] = credential_source
    if pcli:
        cli_map["provider"] = pcli
    if no_usage_meter:
        cli_map["usage"] = {"enabled": False}
    if api_host is not None:
        cli_map["api_host"] = api_host
    if api_port is not None:
        cli_map["api_port"] = api_port
    if context_tokens is not None:
        cli_map["context_tokens"] = context_tokens
    if cli_map:
        settings = merge_cli_overrides(settings, cli_map)

    return settings


def runtime_config_from_settings(
    settings: Settings,
    *,
    no_llama: bool = False,
    stub_llm: bool = False,
) -> RuntimeConfig:
    """Build RuntimeConfig; derive ``start_llama_server`` from provider + flags."""
    name = settings.provider.name
    start_llama = (name == "local") and (not stub_llm) and (not no_llama)
    return RuntimeConfig(
        api_host=settings.api_host,
        api_port=settings.api_port,
        start_llama_server=start_llama,
        context_tokens=settings.context_tokens,
        provider_name=name,
        model=settings.provider.model,
        model_label=settings.provider.model_label,
        base_url=settings.provider.base_url,
        credential_source=settings.provider.credential_source,
        grok_auth_path=settings.provider.grok_auth_path,
        request_timeout_s=settings.provider.request_timeout_s,
        usage=settings.usage,
        continuous_enabled=settings.continuous.enabled,
    )
