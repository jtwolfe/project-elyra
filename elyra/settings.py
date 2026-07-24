"""Settings from defaults, optional elyra.toml, and CLI overrides.

Scope: load/merge loop, wait, tools, goals, continuous, provider, usage
(and common CLI) knobs.
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
_PROVIDER_NAMES = frozenset({"xai", "local"})
_CREDENTIAL_SOURCES = frozenset({"grok_build", "api_key"})


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
    # Orient slice budgets (skill catalog + goals/tasks in outer meal).
    orient_skill_catalog_max_tokens: int = 400
    orient_goals_max_tokens: int = 600
    # Optional generation lever (K12 / item 5): pin tool_choice=required on the
    # hop while a commit-eligible skill is pending after load_skill. Default OFF;
    # evidence-gated — do not enable until Phase A live gate fails.
    post_load_skill_tool_choice_required: bool = False


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
class ContinuousSettings:
    """Hybrid continuous work knobs (in-moment nudge + outer moment_continue).

    Default ``enabled=False`` (safe dogfood). Open work is **always** required
    for outer ``moment_continue`` (K18) — hardcoded in policy; the
    ``require_open_work`` field is informational and **must stay True**
    (toml/CLI False is rejected). Runtime toggle lives in
    ``data/runtime/continuous.json`` and does not mutate frozen Settings.
    """

    enabled: bool = False
    # In-moment
    in_moment_work_nudge_max: int = 1  # per moment
    # Outer chain
    max_continue_streak: int = 8  # consecutive moment_continue without user wake
    cooldown_seconds: int = 30  # min wall time between moment_continue enqueues
    max_pending_continues: int = 1  # dedupe: at most one pending moment_continue
    require_progress: bool = True  # tools_ran (non-speak) OR ledger_mutated
    # K18: always True; product has no empty-ledger outer continue mode.
    require_open_work: bool = True
    skip_pure_social: bool = True  # social + no tools/ledger → no outer continue


@dataclass(frozen=True)
class ProviderSettings:
    """LLM provider config (product default: xAI / Grok).

    Settings surface only until supervisor/CLI wiring (later PR). Defaults
    match Phase 0 product posture; runtime still starts local/llama until then.
    """

    name: str = "xai"  # xai | local
    model: str = "grok-4.5"
    model_label: str = "Grok 4.5 Fast"
    base_url: str = "https://api.x.ai/v1"
    credential_source: str = "grok_build"  # grok_build | api_key
    grok_auth_path: str | None = None  # None → ~/.grok/auth.json
    request_timeout_s: float = 120.0


@dataclass(frozen=True)
class UsageSettings:
    """Hierarchical token-usage meter ceilings (Phase 0).

    ``weekly_allowed_tokens`` is the enforcement ceiling for the allowed week
    (ship default 5_000_000). ``weekly_allowed_fraction`` is **informational
    only** — product policy target (50% of real SuperGrok weekly quota). It is
    stored so elyra.toml can record the target next to the absolute ceiling;
    it is **not** multiplied into meter math until an external real-quota hook
    exists.

    ``hard_stop_override`` is a *runtime* preference (usage.json), not a
    Settings ship default — always starts/persists default False unless the
    operator turns it ON.
    """

    enabled: bool = True
    weekly_allowed_tokens: int = 5_000_000
    weekly_allowed_fraction: float = 0.50
    hour_block_minutes: int = 60
    day_allowed_tokens: int | None = None
    hour_allowed_tokens: int | None = None


@dataclass(frozen=True)
class Settings:
    loop: LoopSettings = field(default_factory=LoopSettings)
    wait: WaitSettings = field(default_factory=WaitSettings)
    tools: ToolsSettings = field(default_factory=ToolsSettings)
    goals: GoalsSettings = field(default_factory=GoalsSettings)
    continuous: ContinuousSettings = field(default_factory=ContinuousSettings)
    provider: ProviderSettings = field(default_factory=ProviderSettings)
    usage: UsageSettings = field(default_factory=UsageSettings)
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
    if "continuous" in data and isinstance(data["continuous"], Mapping):
        kwargs["continuous"] = _replace_section(
            settings.continuous, data["continuous"], "continuous"
        )
    if "provider" in data and isinstance(data["provider"], Mapping):
        kwargs["provider"] = _replace_section(
            settings.provider, data["provider"], "provider"
        )
    if "usage" in data and isinstance(data["usage"], Mapping):
        kwargs["usage"] = _replace_section(settings.usage, data["usage"], "usage")

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
        # K18: continuous outer re-entry always requires open work — no opt-out.
        if path == "continuous.require_open_work" and coerced is not True:
            raise ValueError(
                f"{path}: must be true (K18 — no empty-ledger outer continue); "
                f"got {coerced!r}"
            )
        if path == "provider.name" and coerced not in _PROVIDER_NAMES:
            raise ValueError(
                f"{path}: expected one of {sorted(_PROVIDER_NAMES)}, got {coerced!r}"
            )
        if (
            path == "provider.credential_source"
            and coerced not in _CREDENTIAL_SOURCES
        ):
            raise ValueError(
                f"{path}: expected one of {sorted(_CREDENTIAL_SOURCES)}, "
                f"got {coerced!r}"
            )
        if path == "usage.weekly_allowed_fraction":
            if not (0.0 < coerced <= 1.0):
                raise ValueError(
                    f"{path}: expected float in (0, 1], got {coerced!r}"
                )
        if path == "usage.weekly_allowed_tokens" and coerced <= 0:
            raise ValueError(f"{path}: expected positive int, got {coerced!r}")
        if path == "usage.hour_block_minutes" and coerced < 1:
            raise ValueError(f"{path}: expected int >= 1, got {coerced!r}")
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
