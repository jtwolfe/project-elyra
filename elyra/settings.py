"""Settings from defaults, optional elyra.toml, and CLI overrides.

Scope: load/merge loop, wait, tools, goals (and common CLI) knobs.
In scope: tomllib, frozen defaults, precedence defaults < toml < CLI, type checks.
Out of scope: runtime wiring, argv parsing, ELYRA_HOME (see config).
"""

from __future__ import annotations

import types
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Union, get_args, get_origin, get_type_hints

import tomllib

_CLOSE_GATES = frozenset({"soft", "hard"})


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
    root = Path(home).expanduser().resolve()
    path = root / "elyra.toml"
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
        kwargs["loop"] = _replace_section(settings.loop, data["loop"], "loop")
    if "wait" in data and isinstance(data["wait"], Mapping):
        kwargs["wait"] = _replace_section(settings.wait, data["wait"], "wait")
    if "tools" in data and isinstance(data["tools"], Mapping):
        kwargs["tools"] = _replace_section(settings.tools, data["tools"], "tools")
    if "goals" in data and isinstance(data["goals"], Mapping):
        kwargs["goals"] = _replace_section(settings.goals, data["goals"], "goals")

    # get_type_hints resolves postponed annotations (str -> real types).
    top_types = get_type_hints(Settings)
    for key in ("api_host", "api_port", "context_tokens"):
        if key in data and data[key] is not None:
            kwargs[key] = _coerce_value(key, data[key], top_types[key])

    return replace(settings, **kwargs) if kwargs else settings


def _replace_section(section: Any, values: Mapping[str, Any], prefix: str) -> Any:
    known = get_type_hints(type(section))
    filtered: dict[str, Any] = {}
    for k, v in values.items():
        if k not in known or v is None:
            continue
        path = f"{prefix}.{k}"
        coerced = _coerce_value(path, v, known[k])
        if path == "goals.close_gate" and coerced not in _CLOSE_GATES:
            raise ValueError(
                f"{path}: expected one of {sorted(_CLOSE_GATES)}, got {coerced!r}"
            )
        filtered[k] = coerced
    return replace(section, **filtered) if filtered else section


def _coerce_value(key: str, value: Any, annotation: Any) -> Any:
    """Coerce ``value`` to ``annotation`` or raise ValueError with key path."""
    expected = _unwrap_optional(annotation)
    if expected is None:
        # annotation was None-only (should not happen); accept None only
        if value is None:
            return None
        raise ValueError(f"{key}: expected None, got {type(value).__name__}")

    if expected is int:
        return _as_int(key, value)
    if expected is str:
        if not isinstance(value, str):
            raise ValueError(
                f"{key}: expected str, got {type(value).__name__}: {value!r}"
            )
        return value
    if expected is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"{key}: expected float, got {type(value).__name__}: {value!r}"
            )
        return float(value)
    if expected is bool:
        if not isinstance(value, bool):
            raise ValueError(
                f"{key}: expected bool, got {type(value).__name__}: {value!r}"
            )
        return value
    # Fallback: exact type match
    if not isinstance(value, expected):
        raise ValueError(
            f"{key}: expected {getattr(expected, '__name__', expected)}, "
            f"got {type(value).__name__}: {value!r}"
        )
    return value


def _unwrap_optional(annotation: Any) -> Any | None:
    """Return the non-None arm of T | None, or annotation as-is."""
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
        return annotation
    return annotation


def _as_int(key: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{key}: expected int, got bool: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"{key}: expected int, got non-integer float: {value!r}")
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 10)
        except ValueError as exc:
            raise ValueError(
                f"{key}: expected int, got non-integer str: {value!r}"
            ) from exc
    raise ValueError(f"{key}: expected int, got {type(value).__name__}: {value!r}")


def settings_as_dict(settings: Settings) -> dict[str, Any]:
    """Serialize settings for debugging / status snapshots."""
    return asdict(settings)
