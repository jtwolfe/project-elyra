"""Settings from defaults, optional elyra.toml, and CLI overrides.

Scope: load/merge loop, wait, tools, goals (and common CLI) knobs.
In scope: tomllib, frozen defaults, precedence defaults < toml < CLI.
Out of scope: runtime wiring, argv parsing, ELYRA_HOME (see config).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover — py<3.11
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class LoopSettings:
    continue_idle_minutes: int = 8
    moment_wall_clock_minutes: int = 45
    continue_max_injects: int = 3
    max_tool_hops: int = 200
    sliding_input_tokens: int = 24000
    in_turn_max_tokens: int = 24000
    tool_result_max_chars: int = 8000
    generation_max_tokens: int = 8192


@dataclass(frozen=True)
class WaitSettings:
    default_timeout_seconds: int = 120


@dataclass(frozen=True)
class ToolsSettings:
    verify_timeout_seconds: int = 120


@dataclass(frozen=True)
class GoalsSettings:
    close_gate: str = "soft"  # soft | hard


@dataclass(frozen=True)
class Settings:
    loop: LoopSettings = field(default_factory=LoopSettings)
    wait: WaitSettings = field(default_factory=WaitSettings)
    tools: ToolsSettings = field(default_factory=ToolsSettings)
    goals: GoalsSettings = field(default_factory=GoalsSettings)
    # Common CLI knobs (not required in elyra.toml)
    api_host: str = "127.0.0.1"
    api_port: int = 8787
    context_tokens: int | None = None


def default_settings() -> Settings:
    return Settings()


def load_settings(home: Path | str | None = None) -> Settings:
    """Load defaults then optional ``$home/elyra.toml`` (if present)."""
    base = default_settings()
    if home is None:
        return base
    path = Path(home) / "elyra.toml"
    if not path.is_file():
        return base
    with path.open("rb") as f:
        data = tomllib.load(f)
    return _apply_mapping(base, data)


def merge_cli_overrides(
    settings: Settings,
    overrides: Mapping[str, Any] | None,
) -> Settings:
    """Apply CLI/runtime overrides on top of settings (wins over toml).

    Accepts nested section dicts (``{"loop": {"max_tool_hops": 10}}``) and
    flat top-level keys (``api_host``, ``api_port``, ``context_tokens``).
    ``None`` values are ignored so unset CLI flags do not clobber config.
    """
    if not overrides:
        return settings
    # Drop Nones so argparse defaults of None leave toml/defaults alone.
    cleaned = {k: v for k, v in overrides.items() if v is not None}
    if not cleaned:
        return settings
    return _apply_mapping(settings, cleaned)


def _apply_mapping(settings: Settings, data: Mapping[str, Any]) -> Settings:
    kwargs: dict[str, Any] = {}

    if "loop" in data and isinstance(data["loop"], Mapping):
        kwargs["loop"] = _replace_section(settings.loop, data["loop"])
    if "wait" in data and isinstance(data["wait"], Mapping):
        kwargs["wait"] = _replace_section(settings.wait, data["wait"])
    if "tools" in data and isinstance(data["tools"], Mapping):
        kwargs["tools"] = _replace_section(settings.tools, data["tools"])
    if "goals" in data and isinstance(data["goals"], Mapping):
        kwargs["goals"] = _replace_section(settings.goals, data["goals"])

    for key in ("api_host", "api_port", "context_tokens"):
        if key in data and data[key] is not None:
            kwargs[key] = data[key]

    return replace(settings, **kwargs) if kwargs else settings


def _replace_section(section: Any, values: Mapping[str, Any]) -> Any:
    known = {f.name for f in fields(section)}
    filtered = {k: v for k, v in values.items() if k in known and v is not None}
    return replace(section, **filtered) if filtered else section


def settings_as_dict(settings: Settings) -> dict[str, Any]:
    """Serialize settings for debugging / status snapshots."""
    return asdict(settings)
