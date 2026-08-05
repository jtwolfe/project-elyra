"""Settings from defaults, optional elyra.toml, and CLI overrides.

Scope: load/merge loop, wait, tools, goals, continuous, provider, usage,
memory (and common CLI) knobs.
In scope: tomllib, frozen defaults, precedence defaults < toml < CLI, type checks.
Out of scope: runtime wiring, argv parsing, ELYRA_HOME (see config).
"""

from __future__ import annotations

import types
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Union, get_args, get_origin, get_type_hints
from urllib.parse import urlparse

import tomllib

from elyra.llm.constants import (
    DEFAULT_SLIDING_INPUT_TOKENS,
    MODEL_CONTEXT_WINDOW_TOKENS,
)
from elyra.llm.models import DEFAULT_XAI_MODEL, DEFAULT_XAI_MODEL_LABEL
from elyra.memory.config import (
    EDGE_CREATED_WITH_MAX_MAX,
    EDGE_CREATED_WITH_WRITE_CAP_MAX,
    EDGE_MAX_PER_ATOM_MAX,
    EDGE_RECALLS_ANN_K_MAX,
    EDGE_RECALLS_KEEP_MAX,
    EDGE_RECALLS_MAX_MAX,
    EDGE_RECALLS_MAX_MS_MAX,
    EDGE_RECALLS_SKIP_QUEUE_DEPTH_MAX,
    LADDER_SOURCE_EDGE_K_MAX,
    MEMORY_ANN_SEARCH_BACKENDS,
    MEMORY_BACKENDS,
    MEMORY_EMBED_BACKENDS,
    MEMORY_EMBED_DEVICES,
    MEMORY_SEARCH_CHANNELS,
    TRAVERSE_EXPAND_MAX_MS_MAX,
    TRAVERSE_FRONTIER_MAX_MAX,
    TRAVERSE_INSPECT_CHARS_PER_ID_MAX,
    TRAVERSE_INSPECT_MAX_IDS_MAX,
    TRAVERSE_INSPECT_MAX_TOTAL_CHARS_MAX,
    TRAVERSE_KEEP_MAX_MAX,
    TRAVERSE_LABEL_CHARS_MAX,
    TRAVERSE_MAX_DEPTH_MAX,
    TRAVERSE_MAX_EXPAND_PER_STEP_MAX,
    TRAVERSE_MAX_NODES_MAX,
    TRAVERSE_MAX_SEEDS_MAX,
    TRAVERSE_MAX_STEPS_MAX,
    TRAVERSE_PARCEL_CHILD_CAP_MAX,
    TRAVERSE_PREVIEW_CHARS_MAX,
    TRAVERSE_SAME_MOMENT_K_MAX,
    TRAVERSE_SCRATCHPAD_CHARS_MAX,
    TRAVERSE_SEMANTIC_K_MAX,
    TRAVERSE_SESSION_TTL_S_MAX,
    MemorySettings,
)
from elyra.memory.embed.types import CHANNEL_SET

from elyra.llm.auth import VALID_SOURCES as _CREDENTIAL_SOURCES

_CLOSE_GATES = frozenset({"soft", "hard"})
_PROVIDER_NAMES = frozenset({"xai", "local"})
# SSOT: VALID_SOURCES from elyra.llm.auth (KD14). Ship default is xai_oauth (PR5b).
_MEMORY_BACKENDS = MEMORY_BACKENDS
_MEMORY_EMBED_BACKENDS = MEMORY_EMBED_BACKENDS
_MEMORY_EMBED_DEVICES = MEMORY_EMBED_DEVICES
_MEMORY_SEARCH_CHANNELS = MEMORY_SEARCH_CHANNELS
_MEMORY_ANN_CHANNELS = CHANNEL_SET
_MEMORY_ANN_SEARCH_BACKENDS = MEMORY_ANN_SEARCH_BACKENDS


@dataclass(frozen=True)
class LoopSettings:
    continue_idle_minutes: int = 8
    moment_wall_clock_minutes: int = 45
    continue_max_injects: int = 3
    max_tool_hops: int = 200
    # Fallback meal size when runtime meal_budget is not applied (unit tests).
    # Product paths use effective_meal_budget_tokens (fraction × model window).
    sliding_input_tokens: int = DEFAULT_SLIDING_INPUT_TOKENS
    in_turn_max_tokens: int = DEFAULT_SLIDING_INPUT_TOKENS
    tool_result_max_chars: int = 8000
    generation_max_tokens: int = 8192
    # Full model context window (Grok 4.5 class). Product meal budget = fraction
    # of this window (runtime meal_budget.json; default 0.5 → 250k).
    model_context_window_tokens: int = MODEL_CONTEXT_WINDOW_TOKENS
    # Orient slice budgets (skill catalog + goals/tasks in outer meal).
    # Soft operating budget (2× historical 400). Full bundled catalog is ~573
    # tokens today; meal-content review will re-tune before v0.1.
    orient_skill_catalog_max_tokens: int = 800
    orient_goals_max_tokens: int = 600
    # Optional generation lever (K12 / item 5): pin tool_choice=required on the
    # hop while a commit-eligible skill is pending after load_skill. Default OFF;
    # evidence-gated — do not enable until Phase A live gate fails.
    post_load_skill_tool_choice_required: bool = False


@dataclass(frozen=True)
class WaitSettings:
    """User-wait timeouts for ``wait_user``.

    Defaults are deliberately long so thoughtful human replies are not
    truncated by a 2-minute timer. Free-text waits (no multi-choice) use
    ``free_text_timeout_seconds`` when the model omits an explicit timeout —
    collaborative multi-choice can stay on ``default_timeout_seconds``.
    """

    # Multi-choice / general default when timeout_seconds omitted.
    default_timeout_seconds: int = 300
    # Free-text or "I'll type" style waits (no choices) — same floor by default.
    free_text_timeout_seconds: int = 300


@dataclass(frozen=True)
class ToolsSettings:
    verify_timeout_seconds: int = 120
    # Empty sentinel: resolve at use site in vcs_jail (project_root + paths.home).
    allowed_repo_roots: tuple[str, ...] = ()


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

    ``local`` is reserved / unimplemented (fails closed at runtime).
    """

    name: str = "xai"  # xai | local
    model: str = DEFAULT_XAI_MODEL
    model_label: str = DEFAULT_XAI_MODEL_LABEL
    base_url: str = "https://api.x.ai/v1"
    # PR5b: new installs / empty prefs → xai_oauth. Existing provider.json preserved
    # via merge (prefs > settings). api_key and grok_build remain fully selectable.
    credential_source: str = "xai_oauth"  # xai_oauth | api_key | grok_build
    grok_auth_path: str | None = None  # None → ~/.grok/auth.json
    request_timeout_s: float = 120.0


@dataclass(frozen=True)
class UsageSettings:
    """Hierarchical token-usage meter ceilings + SuperGrok pacing knobs.

    ``weekly_allowed_tokens`` is the enforcement ceiling for the allowed week
    (ship default 5_000_000). ``weekly_allowed_fraction`` is **informational
    only** — product policy target (50% of real SuperGrok weekly quota). It is
    stored so elyra.toml can record the target next to the absolute ceiling;
    it is **not** multiplied into meter math until an external real-quota hook
    exists.

    Day/hour hard-stop flags default **off** (soft diagnostics only). The meter
    enforces day/hour hard stop only when the corresponding flag is true.
    Pace bands (green/yellow/red), burst cushion, and account hard stop
    (``account_hard_stop_percent`` from an applied SuperGrok credits snapshot)
    are enforced by ``UsageMeter``. Credits HTTP polling is separate (poller
    injects ``CreditsSnapshot``); these settings only configure thresholds and
    poller knobs.

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
    day_hard_stop_enabled: bool = False
    hour_hard_stop_enabled: bool = False
    account_hard_stop_percent: float = 95.0
    pace_yellow_ratio: float = 1.0
    pace_red_ratio: float = 1.5
    burst_hours: float = 4.0
    credits_poll_enabled: bool = True
    credits_base_url: str = "https://cli-chat-proxy.grok.com"
    credits_poll_interval_s: float = 300.0
    credits_stale_after_s: float = 3600.0
    auto_throttle_model: bool = False
    throttle_model: str | None = None


@dataclass(frozen=True)
class Settings:
    loop: LoopSettings = field(default_factory=LoopSettings)
    wait: WaitSettings = field(default_factory=WaitSettings)
    tools: ToolsSettings = field(default_factory=ToolsSettings)
    goals: GoalsSettings = field(default_factory=GoalsSettings)
    continuous: ContinuousSettings = field(default_factory=ContinuousSettings)
    provider: ProviderSettings = field(default_factory=ProviderSettings)
    usage: UsageSettings = field(default_factory=UsageSettings)
    # Stretch 2 Phase 1 memory (write_atoms + meal enabled on by default).
    memory: MemorySettings = field(default_factory=MemorySettings)
    # Common CLI knobs (not required in elyra.toml)
    api_host: str = "127.0.0.1"
    api_port: int = 8787


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
    flat top-level keys (``api_host``, ``api_port``).
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
    if "memory" in data and isinstance(data["memory"], Mapping):
        kwargs["memory"] = _replace_section(
            settings.memory, data["memory"], "memory"
        )

    # get_type_hints resolves postponed annotations (str -> real types).
    top_types = get_type_hints(Settings)
    for key in ("api_host", "api_port"):
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
        if path == "provider.request_timeout_s" and coerced <= 0:
            raise ValueError(f"{path}: expected positive float, got {coerced!r}")
        if path == "usage.weekly_allowed_fraction":
            if not (0.0 < coerced <= 1.0):
                raise ValueError(
                    f"{path}: expected float in (0, 1], got {coerced!r}"
                )
        if path == "usage.weekly_allowed_tokens" and coerced <= 0:
            raise ValueError(f"{path}: expected positive int, got {coerced!r}")
        if path == "usage.hour_block_minutes" and coerced < 1:
            raise ValueError(f"{path}: expected int >= 1, got {coerced!r}")
        # Optional tighter ceilings (when set) must be positive like weekly.
        if path in ("usage.day_allowed_tokens", "usage.hour_allowed_tokens"):
            if coerced <= 0:
                raise ValueError(f"{path}: expected positive int, got {coerced!r}")
        if path == "usage.account_hard_stop_percent":
            if not (0.0 < coerced <= 100.0):
                raise ValueError(
                    f"{path}: expected float in (0, 100], got {coerced!r}"
                )
        if path == "usage.pace_yellow_ratio" and coerced <= 0:
            raise ValueError(f"{path}: expected float > 0, got {coerced!r}")
        if path == "usage.burst_hours" and coerced < 0:
            raise ValueError(f"{path}: expected float >= 0, got {coerced!r}")
        if path == "usage.credits_poll_interval_s" and coerced < 30:
            raise ValueError(f"{path}: expected float >= 30, got {coerced!r}")
        if path == "usage.credits_base_url" and not _is_origin_url(coerced):
            raise ValueError(
                f"{path}: expected absolute origin URL "
                f"(http/https host, path empty or '/', no query/fragment), "
                f"got {coerced!r}"
            )
        if path == "usage.throttle_model" and (
            not isinstance(coerced, str) or not coerced.strip()
        ):
            raise ValueError(
                f"{path}: expected None or non-empty str, got {coerced!r}"
            )
        # Memory (Phase 1 + Phase 2): allowlists + fraction/horizon/budget floors.
        if path == "memory.backend" and coerced not in _MEMORY_BACKENDS:
            raise ValueError(
                f"{path}: expected one of {sorted(_MEMORY_BACKENDS)}, "
                f"got {coerced!r}"
            )
        if path == "memory.episodic_fraction":
            if not (0.0 <= coerced <= 1.0):
                raise ValueError(
                    f"{path}: expected float in [0.0, 1.0], got {coerced!r}"
                )
        if path == "memory.episodic_horizon_hours" and coerced <= 0:
            raise ValueError(f"{path}: expected float > 0, got {coerced!r}")
        if path == "memory.ladder_max_ms_per_tick" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.summary_mode":
            if isinstance(coerced, str):
                coerced = coerced.strip().lower()
            if coerced not in ("template", "llm"):
                raise ValueError(
                    f"{path}: expected one of ['template', 'llm'], got {coerced!r}"
                )
        if path == "memory.ladder_hourly_max_ms" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.ladder_catchup_max_hours" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.ladder_llm_max_calls_per_tick" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.ladder_llm_max_calls_per_hour" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.ladder_recent_1h_meal" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.ladder_rebuild_max_hours" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.ladder_rebuild_max_ms" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.ladder_rebuild_max_llm_calls" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        # ladder_age_gates_enabled is bool — no extra validation
        if path == "memory.ladder_source_edge_k":
            if coerced < 0 or coerced > LADDER_SOURCE_EDGE_K_MAX:
                raise ValueError(
                    f"{path}: expected int in [0, {LADDER_SOURCE_EDGE_K_MAX}], "
                    f"got {coerced!r}"
                )
        if path == "memory.traverse_summary_expand":
            if isinstance(coerced, str):
                coerced = coerced.strip().lower()
            if coerced not in ("lite", "deep"):
                raise ValueError(
                    f"{path}: expected one of ['lite', 'deep'], got {coerced!r}"
                )
        if path == "memory.max_tool_atoms_per_moment" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.atom_max_chars" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.model_promote_min_chars" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.protect_tail_atoms" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.tool_ok_preview_chars" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.regather_every_n_hops" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        if path == "memory.compact_max_tokens" and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        # Phase 2 embed / semantic (KD9 defaults off; validation always active).
        # Lowercase/strip string allowlist fields to match open_encoder/select_device.
        if path == "memory.embed_backend":
            if isinstance(coerced, str):
                coerced = coerced.strip().lower()
            if coerced not in _MEMORY_EMBED_BACKENDS:
                raise ValueError(
                    f"{path}: expected one of {sorted(_MEMORY_EMBED_BACKENDS)}, "
                    f"got {coerced!r}"
                )
        if path == "memory.embed_device":
            if isinstance(coerced, str):
                coerced = coerced.strip().lower()
            if coerced not in _MEMORY_EMBED_DEVICES:
                raise ValueError(
                    f"{path}: expected one of {sorted(_MEMORY_EMBED_DEVICES)}, "
                    f"got {coerced!r}"
                )
        if path == "memory.semantic_search_channel":
            if isinstance(coerced, str):
                coerced = coerced.strip().lower()
            if coerced not in _MEMORY_SEARCH_CHANNELS:
                raise ValueError(
                    f"{path}: expected one of {sorted(_MEMORY_SEARCH_CHANNELS)}, "
                    f"got {coerced!r}"
                )
        if path == "memory.ann_search_backend":
            if isinstance(coerced, str):
                coerced = coerced.strip().lower()
            if coerced not in _MEMORY_ANN_SEARCH_BACKENDS:
                raise ValueError(
                    f"{path}: expected one of "
                    f"{sorted(_MEMORY_ANN_SEARCH_BACKENDS)}, got {coerced!r}"
                )
        if path in (
            "memory.semantic_fraction",
            "memory.episodic_fraction_with_semantic",
            "memory.temporal_min_fraction",
            "memory.semantic_min_score",
        ):
            if not (0.0 <= coerced <= 1.0):
                raise ValueError(
                    f"{path}: expected float in [0.0, 1.0], got {coerced!r}"
                )
        if path in (
            "memory.semantic_horizon_hours",
            "memory.embed_media_max_seconds",
            "memory.embed_catchup_horizon_hours",
        ) and coerced <= 0:
            raise ValueError(f"{path}: expected float/int > 0, got {coerced!r}")
        if path == "memory.encode_queue_max" and coerced < 1:
            raise ValueError(f"{path}: expected int >= 1, got {coerced!r}")
        if path == "memory.encode_worker_poll_s" and coerced <= 0:
            raise ValueError(f"{path}: expected float > 0, got {coerced!r}")
        if path == "memory.encode_worker_restart_window_s" and coerced <= 0:
            raise ValueError(f"{path}: expected float > 0, got {coerced!r}")
        if path == "memory.encode_worker_restart_backoff_max_s" and coerced <= 0:
            raise ValueError(f"{path}: expected float > 0, got {coerced!r}")
        if path in (
            "memory.encode_max_ms_per_tick",
            "memory.encode_max_items_per_tick",
            "memory.encode_max_attempts",
            "memory.encode_query_max_ms",
            "memory.encode_worker_max_restarts",
            "memory.semantic_select_max_ms",
            "memory.semantic_top_k",
            "memory.ann_recent_buffer_max",
            "memory.ann_full_search_below",
            "memory.ann_optimize_every_n_encodes",
            "memory.ann_optimize_interval_s",
            "memory.ann_optimize_max_ms",
            "memory.ann_ivf_min_vectors",
            "memory.parcel_threshold_chars",
            "memory.embed_media_max_bytes",
            "memory.embed_catchup_max",
            "memory.embed_catchup_per_tick",
            "memory.joint_repair_max_per_open",
            "memory.joint_repair_max_per_tick",
        ) and coerced < 0:
            raise ValueError(f"{path}: expected int >= 0, got {coerced!r}")
        # Wait ceiling: same product band as runtime clamp (no silent rewrite).
        if path == "memory.semantic_wait_max_ms":
            from elyra.memory.config import (  # noqa: PLC0415
                SEMANTIC_WAIT_MAX_MS_MAX,
                SEMANTIC_WAIT_MAX_MS_MIN,
            )

            if not (
                SEMANTIC_WAIT_MAX_MS_MIN <= coerced <= SEMANTIC_WAIT_MAX_MS_MAX
            ):
                raise ValueError(
                    f"{path}: expected int in "
                    f"[{SEMANTIC_WAIT_MAX_MS_MIN}, {SEMANTIC_WAIT_MAX_MS_MAX}], "
                    f"got {coerced!r}"
                )
        if path == "memory.ann_index_channels":
            # Normalize emb_* prefixes; require non-empty ⊂ CHANNEL_SET.
            if not isinstance(coerced, tuple) or not coerced:
                raise ValueError(
                    f"{path}: expected non-empty list/tuple of channel names "
                    f"from {sorted(_MEMORY_ANN_CHANNELS)}, got {coerced!r}"
                )
            normalized: list[str] = []
            for i, item in enumerate(coerced):
                ch = str(item).strip().lower()
                if ch.startswith("emb_"):
                    ch = ch[len("emb_") :]
                if ch not in _MEMORY_ANN_CHANNELS:
                    raise ValueError(
                        f"{path}[{i}]: expected one of "
                        f"{sorted(_MEMORY_ANN_CHANNELS)}, got {item!r}"
                    )
                if ch not in normalized:
                    normalized.append(ch)
            coerced = tuple(normalized)
        # Phase 2a directed traversal budgets (hard maxes — design table).
        if path == "memory.directed_keep_fraction":
            if not (0.0 <= coerced <= 1.0):
                raise ValueError(
                    f"{path}: expected float in [0.0, 1.0], got {coerced!r}"
                )
        if path == "memory.traverse_min_expand_weight":
            if not (0.0 <= coerced <= 1.0):
                raise ValueError(
                    f"{path}: expected float in [0.0, 1.0], got {coerced!r}"
                )
        if path == "memory.traverse_temporal_half_life_hours" and coerced <= 0:
            raise ValueError(f"{path}: expected float > 0, got {coerced!r}")
        _traverse_int_caps = {
            "memory.traverse_expand_max_ms": TRAVERSE_EXPAND_MAX_MS_MAX,
            "memory.traverse_start_expand_max_ms": TRAVERSE_EXPAND_MAX_MS_MAX,
            "memory.traverse_max_depth": TRAVERSE_MAX_DEPTH_MAX,
            "memory.traverse_max_nodes": TRAVERSE_MAX_NODES_MAX,
            "memory.traverse_max_steps": TRAVERSE_MAX_STEPS_MAX,
            "memory.traverse_max_seeds": TRAVERSE_MAX_SEEDS_MAX,
            "memory.traverse_frontier_max": TRAVERSE_FRONTIER_MAX_MAX,
            "memory.traverse_max_expand_per_step": TRAVERSE_MAX_EXPAND_PER_STEP_MAX,
            "memory.traverse_keep_max": TRAVERSE_KEEP_MAX_MAX,
            "memory.traverse_session_ttl_s": TRAVERSE_SESSION_TTL_S_MAX,
            "memory.traverse_label_chars": TRAVERSE_LABEL_CHARS_MAX,
            "memory.traverse_preview_chars": TRAVERSE_PREVIEW_CHARS_MAX,
            "memory.traverse_inspect_chars_per_id": TRAVERSE_INSPECT_CHARS_PER_ID_MAX,
            "memory.traverse_inspect_max_ids": TRAVERSE_INSPECT_MAX_IDS_MAX,
            "memory.traverse_inspect_max_total_chars": (
                TRAVERSE_INSPECT_MAX_TOTAL_CHARS_MAX
            ),
            "memory.traverse_scratchpad_chars": TRAVERSE_SCRATCHPAD_CHARS_MAX,
            "memory.traverse_semantic_k": TRAVERSE_SEMANTIC_K_MAX,
            "memory.traverse_parcel_child_cap": TRAVERSE_PARCEL_CHILD_CAP_MAX,
            "memory.traverse_same_moment_k": TRAVERSE_SAME_MOMENT_K_MAX,
        }
        if path in _traverse_int_caps:
            hi = _traverse_int_caps[path]
            if coerced < 0 or coerced > hi:
                raise ValueError(
                    f"{path}: expected int in [0, {hi}], got {coerced!r}"
                )
        # Durable EdgeStore budgets (design-memory-edges-and-traversal §7).
        _edge_int_caps = {
            "memory.edge_max_per_atom": EDGE_MAX_PER_ATOM_MAX,
            "memory.edge_created_with_max": EDGE_CREATED_WITH_MAX_MAX,
            "memory.edge_created_with_write_cap": EDGE_CREATED_WITH_WRITE_CAP_MAX,
            "memory.edge_recalls_max": EDGE_RECALLS_MAX_MAX,
            "memory.edge_recalls_ann_k": EDGE_RECALLS_ANN_K_MAX,
            "memory.edge_recalls_keep": EDGE_RECALLS_KEEP_MAX,
            "memory.edge_recalls_max_ms": EDGE_RECALLS_MAX_MS_MAX,
            "memory.edge_recalls_skip_queue_depth": (
                EDGE_RECALLS_SKIP_QUEUE_DEPTH_MAX
            ),
        }
        if path in _edge_int_caps:
            hi = _edge_int_caps[path]
            if coerced < 0 or coerced > hi:
                raise ValueError(
                    f"{path}: expected int in [0, {hi}], got {coerced!r}"
                )
        filtered[k] = coerced

    # Cross-field usage constraints use the post-merge effective values.
    if prefix == "usage" and filtered:
        yellow = filtered.get(
            "pace_yellow_ratio", getattr(section, "pace_yellow_ratio")
        )
        red = filtered.get("pace_red_ratio", getattr(section, "pace_red_ratio"))
        if "pace_red_ratio" in filtered or "pace_yellow_ratio" in filtered:
            if not (red > yellow):
                raise ValueError(
                    f"usage.pace_red_ratio: expected > pace_yellow_ratio "
                    f"({yellow!r}), got {red!r}"
                )
        poll_s = filtered.get(
            "credits_poll_interval_s",
            getattr(section, "credits_poll_interval_s"),
        )
        stale_s = filtered.get(
            "credits_stale_after_s",
            getattr(section, "credits_stale_after_s"),
        )
        if (
            "credits_stale_after_s" in filtered
            or "credits_poll_interval_s" in filtered
        ):
            if stale_s < poll_s:
                raise ValueError(
                    f"usage.credits_stale_after_s: expected >= "
                    f"credits_poll_interval_s ({poll_s!r}), got {stale_s!r}"
                )

    return replace(section, **filtered) if filtered else section


def _is_origin_url(url: str) -> bool:
    """True if ``url`` is an absolute http(s) origin (no path/query/fragment).

    Accepts ``http(s)://host[:port]`` with path empty or a single ``/``.
    Rejects userinfo, whitespace padding, non-numeric/out-of-range ports,
    query, fragment, and non-empty paths other than ``/``.
    """
    if not isinstance(url, str) or not url:
        return False
    # No silent normalize: leading/trailing whitespace is not an origin form.
    if url.strip() != url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port is not None and not (1 <= port <= 65535):
        return False
    if parsed.path not in ("", "/"):
        return False
    if parsed.query or parsed.fragment or parsed.params:
        return False
    return True


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

    # tuple[str, ...] (and bare tuple): accept TOML/Python list or tuple of str.
    origin = get_origin(expected)
    if origin is tuple or expected is tuple:
        args = get_args(expected) if origin is tuple else ()
        # tuple[str, ...] or unparameterized tuple → homogeneous str sequence.
        elem_ok = (
            not args
            or (len(args) == 2 and args[1] is Ellipsis and args[0] is str)
            or (len(args) == 1 and args[0] is str)
        )
        if elem_ok:
            if not isinstance(value, (list, tuple)):
                raise ValueError(
                    f"{key}: expected list/tuple of str, "
                    f"got {type(value).__name__}: {value!r}"
                )
            out: list[str] = []
            for i, item in enumerate(value):
                if not isinstance(item, str):
                    raise ValueError(
                        f"{key}[{i}]: expected str, "
                        f"got {type(item).__name__}: {item!r}"
                    )
                out.append(item)
            return tuple(out)

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
