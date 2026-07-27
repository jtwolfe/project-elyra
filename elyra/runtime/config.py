"""Runtime process configuration + start-time settings merge.

Merge order (normative)::

    defaults  <  elyra.toml  <  data/runtime/provider.json  <  explicit CLI

``provider.json`` supplies ``model``, ``credential_source``, and
``reasoning_effort`` (non-secret). Effort is prefs-only (no toml/CLI this pass).
No inference process is launched; ``local`` config is retained for future
OpenAI-compat wiring only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from elyra.llm.config import LocalClientConfig
from elyra.llm.models import DEFAULT_XAI_MODEL, DEFAULT_XAI_MODEL_LABEL, label_for_model
from elyra.llm.provider_prefs import (
    DEFAULT_REASONING_EFFORT,
    load_provider_prefs,
    resolve_reasoning_effort,
)
from elyra.settings import Settings, UsageSettings, load_settings, merge_cli_overrides

_LOG = logging.getLogger(__name__)


@dataclass
class RuntimeConfig:
    api_host: str = "127.0.0.1"
    api_port: int = 8787
    # Future OpenAI-compat local endpoint shape (unused for HTTP this pass).
    local: LocalClientConfig = field(default_factory=LocalClientConfig)
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
    # Resolved wire effort (low|medium|high); from provider.json prefs only.
    reasoning_effort: str = DEFAULT_REASONING_EFFORT


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
) -> Settings:
    """Load defaults < toml < provider.json < explicit CLI overrides.

    CLI kwargs that are ``None`` are ignored (do not clobber prefs/toml).
    ``no_usage_meter=True`` forces ``usage.enabled=False``.

    Note: ``reasoning_effort`` is not on Settings (no toml/CLI); resolved
    into ``RuntimeConfig`` via ``runtime_config_from_settings(data_dir=...)``.
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
    if cli_map:
        settings = merge_cli_overrides(settings, cli_map)

    return settings


def runtime_config_from_settings(
    settings: Settings,
    *,
    stub_llm: bool = False,
    data_dir: Path | str | None = None,
) -> RuntimeConfig:
    """Build RuntimeConfig from merged settings.

    ``stub_llm`` is accepted for CLI symmetry; client selection lives on the
    supervisor (``use_stub_llm``). No inference process is started.

    When ``data_dir`` is provided, ``reasoning_effort`` is resolved from
    ``provider.json`` prefs (default high when missing/invalid).
    """
    del stub_llm  # selection is supervisor-side; flag reserved for callers
    name = settings.provider.name
    effort = DEFAULT_REASONING_EFFORT
    if data_dir is not None:
        prefs = load_provider_prefs(Path(data_dir))
        effort = resolve_reasoning_effort(prefs.reasoning_effort)
    else:
        # Production CLI always passes data_dir; without it prefs are not
        # consulted and effort defaults to high (test harness convenience).
        _LOG.debug(
            "runtime_config_from_settings: data_dir omitted; "
            "reasoning_effort defaults to %s",
            DEFAULT_REASONING_EFFORT,
        )
    return RuntimeConfig(
        api_host=settings.api_host,
        api_port=settings.api_port,
        provider_name=name,
        model=settings.provider.model,
        model_label=settings.provider.model_label,
        base_url=settings.provider.base_url,
        credential_source=settings.provider.credential_source,
        grok_auth_path=settings.provider.grok_auth_path,
        request_timeout_s=settings.provider.request_timeout_s,
        usage=settings.usage,
        continuous_enabled=settings.continuous.enabled,
        reasoning_effort=effort,
    )
