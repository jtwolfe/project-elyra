"""Hierarchical token usage meter with atomic persistence.

Scope: TokenUsage / parse_token_usage, UsageMeter week/day/hour hard stops,
atomic ``data/runtime/usage.json`` (temp + os.replace), hard-stop override.
In scope: hierarchy math, threading lock, corrupt fail-soft (override false).
Out of scope: UsageGatedChatClient, supervisor wiring, credential checks.

**Import rule (normative):** this module must NEVER import ``elyra.llm.client``
(cycle-free: client.py → usage.py only).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from elyra.settings import UsageSettings

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
USAGE_REL = Path("runtime") / "usage.json"

# Log missing-usage estimate at most once per process.
_missing_usage_logged = False


@dataclass(frozen=True)
class TokenUsage:
    """Token counts from an OpenAI-compatible chat completion response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0  # prompt_tokens_details.cached_tokens (informational)

    @property
    def billable_tokens(self) -> int:
        """Prefer total_tokens; else prompt+completion; else 0.

        Does **not** subtract cached_tokens — billable is unchanged by cache hits.
        """
        if self.total_tokens > 0:
            return int(self.total_tokens)
        summed = int(self.prompt_tokens) + int(self.completion_tokens)
        return summed if summed > 0 else 0


def parse_token_usage(raw: Any) -> TokenUsage | None:
    """Parse OpenAI-style ``response['usage']`` dict; None if missing/unusable."""
    if not isinstance(raw, dict):
        return None
    has_any = any(
        k in raw for k in ("prompt_tokens", "completion_tokens", "total_tokens")
    )
    if not has_any:
        return None

    def _as_nonneg_int(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        try:
            n = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, n)

    prompt = _as_nonneg_int(raw.get("prompt_tokens", 0))
    completion = _as_nonneg_int(raw.get("completion_tokens", 0))
    total = _as_nonneg_int(raw.get("total_tokens", 0))

    reasoning = 0
    details = raw.get("completion_tokens_details")
    if isinstance(details, dict) and "reasoning_tokens" in details:
        reasoning = _as_nonneg_int(details.get("reasoning_tokens"))
    elif "reasoning_tokens" in raw:
        reasoning = _as_nonneg_int(raw.get("reasoning_tokens"))

    cached = 0
    prompt_details = raw.get("prompt_tokens_details")
    if isinstance(prompt_details, dict) and "cached_tokens" in prompt_details:
        cached = _as_nonneg_int(prompt_details.get("cached_tokens"))
    elif "cached_tokens" in raw:
        cached = _as_nonneg_int(raw.get("cached_tokens"))

    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        reasoning_tokens=reasoning,
        cached_tokens=cached,
    )


@dataclass(frozen=True)
class UsageSnapshot:
    """Immutable meter view for status / glass (live, not a durable cache)."""

    enabled: bool
    week_remaining_fraction: float
    day_remaining_fraction: float
    hour_remaining_fraction: float
    hard_stop: str | None  # None | "hour" | "day" | "week"
    # When override_active, hard_stop still reports the *would-be* level (glass honesty)
    # but can_call() returns True.
    hard_stop_reason: str | None
    override_active: bool
    last_record_at: str | None
    week_used_tokens: int
    day_used_tokens: int
    hour_used_tokens: int
    week_limit_tokens: int
    day_limit_tokens: int
    hour_limit_tokens: int


class UsageHardStopError(RuntimeError):
    """Raised by the usage gate when a hierarchical hard stop refuses a call."""

    def __init__(self, reason: str, *, level: str) -> None:
        self.reason = reason
        self.level = level
        super().__init__(reason)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def window_ids(
    now: datetime, *, hour_block_minutes: int = 60
) -> tuple[str, str, str]:
    """Return (week_id, day_id, hour_id) in UTC.

    Window ids UTC: ISO week (``YYYY-Www``), calendar day (``YYYY-MM-DD``),
    hour block (``YYYY-MM-DDTHH`` for 60m; includes minutes for other blocks).
    """
    now = _ensure_aware_utc(now)
    iso = now.isocalendar()
    week_id = f"{iso.year}-W{iso.week:02d}"
    day_id = now.strftime("%Y-%m-%d")
    block = max(1, int(hour_block_minutes))
    minutes_from_midnight = now.hour * 60 + now.minute
    block_start = (minutes_from_midnight // block) * block
    block_h = block_start // 60
    block_m = block_start % 60
    if block == 60:
        hour_id = f"{day_id}T{block_h:02d}"
    else:
        hour_id = f"{day_id}T{block_h:02d}:{block_m:02d}"
    return week_id, day_id, hour_id


def compute_limits(settings: UsageSettings) -> tuple[int, int, int]:
    """Return (allowed_week, allowed_day, allowed_hour) from settings.

    ``weekly_allowed_fraction`` is **not** used (policy documentation only).
    """
    allowed_week = max(1, int(settings.weekly_allowed_tokens))
    if settings.day_allowed_tokens is not None:
        allowed_day = max(1, int(settings.day_allowed_tokens))
    else:
        allowed_day = max(1, allowed_week // 7)
    if settings.hour_allowed_tokens is not None:
        allowed_hour = max(1, int(settings.hour_allowed_tokens))
    else:
        block = max(1, int(settings.hour_block_minutes))
        blocks_per_day = max(1, 1440 // block)
        allowed_hour = max(1, allowed_day // blocks_per_day)
    return allowed_week, allowed_day, allowed_hour


def _remaining_fraction(used: int, limit: int) -> float:
    if limit <= 0:
        return 0.0
    frac = 1.0 - (float(used) / float(limit))
    if frac < 0.0:
        return 0.0
    if frac > 1.0:
        return 1.0
    return frac


def _iso_z(dt: datetime) -> str:
    dt = _ensure_aware_utc(dt)
    return dt.isoformat().replace("+00:00", "Z")


class UsageMeter:
    """Thread-safe hierarchical usage meter.

    All public methods take ``self._lock``.
    Persist via write-temp + ``os.replace`` only.
    ``hard_stop_override`` (default False) is persisted in usage.json and
    survives restarts. Override never skips ``record``.
    """

    def __init__(
        self,
        path: Path,
        settings: UsageSettings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = Path(path)
        self._settings = settings
        self._clock: Callable[[], datetime] = clock or _utc_now
        self._lock = threading.Lock()

        self._week_id = ""
        self._day_id = ""
        self._hour_id = ""
        self._week_used = 0
        self._day_used = 0
        self._hour_used = 0
        self._last_record_at: str | None = None
        self._last_hard_stop: str | None = None
        self._last_hard_stop_reason: str | None = None
        self._hard_stop_override = False

        # Initialize window ids for current clock (zeroed counters).
        w, d, h = window_ids(
            self._clock(), hour_block_minutes=settings.hour_block_minutes
        )
        self._week_id, self._day_id, self._hour_id = w, d, h

    @classmethod
    def load(
        cls,
        data_dir: Path,
        settings: UsageSettings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> UsageMeter:
        """Load ``usage.json``.

        On missing file: zeroed windows, override_active=False.
        On corrupt/unreadable JSON: log WARNING, start zeroed windows,
        override_active=False (fail-soft; never invent override ON).
        """
        path = Path(data_dir) / USAGE_REL
        meter = cls(path, settings, clock=clock)
        meter._load_from_disk()
        return meter

    # --- public API (all under lock) -----------------------------------------

    def can_call(self) -> bool:
        """True if meter disabled, or under budget, or hard_stop_override is ON.

        Override does NOT skip credential checks (those are can_open_model_moment).
        """
        with self._lock:
            self._refresh_windows_unlocked()
            if not self._settings.enabled:
                return True
            if self._hard_stop_override:
                return True
            return self._hard_stop_level_unlocked() is None

    def hard_stop_reason(self) -> str | None:
        """Would-be stop reason from budgets, even if override allows calls.

        None when under all ceilings.
        """
        with self._lock:
            self._refresh_windows_unlocked()
            return self._hard_stop_reason_unlocked()

    def is_over_budget(self) -> bool:
        """True when any window is at/over ceiling (ignores override)."""
        with self._lock:
            self._refresh_windows_unlocked()
            return self._hard_stop_level_unlocked() is not None

    def remaining(self) -> dict[str, int]:
        """Tokens remaining per window (clamped ≥ 0). Keys: week, day, hour."""
        with self._lock:
            self._refresh_windows_unlocked()
            week_lim, day_lim, hour_lim = compute_limits(self._settings)
            return {
                "week": max(0, week_lim - self._week_used),
                "day": max(0, day_lim - self._day_used),
                "hour": max(0, hour_lim - self._hour_used),
            }

    def set_hard_stop_override(self, active: bool) -> UsageSnapshot:
        """Persist hard_stop_override to usage.json (atomic). Default path: False.

        Never silently defaults to True.
        """
        with self._lock:
            self._hard_stop_override = bool(active)
            self._refresh_windows_unlocked()
            self._sync_hard_stop_fields_unlocked()
            self._persist_unlocked()
            return self._snapshot_unlocked()

    def record(
        self,
        usage: TokenUsage | None,
        *,
        estimated_if_missing: int = 0,
    ) -> UsageSnapshot:
        """Always records tokens when usage present — even if override_active.

        Recording is never disabled by override. Missing usage records
        ``estimated_if_missing`` (default 0).
        """
        global _missing_usage_logged
        with self._lock:
            self._refresh_windows_unlocked()
            if usage is not None:
                tokens = max(0, int(usage.billable_tokens))
            else:
                tokens = max(0, int(estimated_if_missing))
                if not _missing_usage_logged:
                    logger.debug(
                        "usage.record: missing usage; recording estimated_if_missing=%s",
                        tokens,
                    )
                    _missing_usage_logged = True

            if tokens:
                self._week_used += tokens
                self._day_used += tokens
                self._hour_used += tokens
                self._last_record_at = _iso_z(self._clock())

            self._sync_hard_stop_fields_unlocked()
            self._persist_unlocked()
            return self._snapshot_unlocked()

    def refresh_windows(self) -> None:
        """Roll window counters when week/day/hour ids change (UTC)."""
        with self._lock:
            rolled = self._refresh_windows_unlocked()
            if rolled:
                self._sync_hard_stop_fields_unlocked()
                self._persist_unlocked()

    def snapshot(self) -> UsageSnapshot:
        """refresh_windows + immutable snapshot including override_active.

        Safe for /api/status.
        """
        with self._lock:
            self._refresh_windows_unlocked()
            return self._snapshot_unlocked()

    # --- internals (caller holds lock) ---------------------------------------

    def _limits_unlocked(self) -> tuple[int, int, int]:
        return compute_limits(self._settings)

    def _refresh_windows_unlocked(self) -> bool:
        now = self._clock()
        week_id, day_id, hour_id = window_ids(
            now, hour_block_minutes=self._settings.hour_block_minutes
        )
        rolled = False
        if week_id != self._week_id:
            self._week_id = week_id
            self._week_used = 0
            rolled = True
        if day_id != self._day_id:
            self._day_id = day_id
            self._day_used = 0
            rolled = True
        if hour_id != self._hour_id:
            self._hour_id = hour_id
            self._hour_used = 0
            rolled = True
        return rolled

    def _hard_stop_level_unlocked(self) -> str | None:
        """Display precedence: week > day > hour."""
        week_lim, day_lim, hour_lim = self._limits_unlocked()
        if self._week_used >= week_lim:
            return "week"
        if self._day_used >= day_lim:
            return "day"
        if self._hour_used >= hour_lim:
            return "hour"
        return None

    def _hard_stop_reason_unlocked(self) -> str | None:
        level = self._hard_stop_level_unlocked()
        if level is None:
            return None
        week_lim, day_lim, hour_lim = self._limits_unlocked()
        if level == "week":
            return (
                f"week budget exhausted "
                f"({self._week_used}/{week_lim} tokens)"
            )
        if level == "day":
            return (
                f"day budget exhausted "
                f"({self._day_used}/{day_lim} tokens)"
            )
        return (
            f"hour budget exhausted "
            f"({self._hour_used}/{hour_lim} tokens)"
        )

    def _sync_hard_stop_fields_unlocked(self) -> None:
        level = self._hard_stop_level_unlocked()
        reason = self._hard_stop_reason_unlocked()
        self._last_hard_stop = level
        self._last_hard_stop_reason = reason

    def _snapshot_unlocked(self) -> UsageSnapshot:
        week_lim, day_lim, hour_lim = self._limits_unlocked()
        level = self._hard_stop_level_unlocked()
        reason = self._hard_stop_reason_unlocked()
        return UsageSnapshot(
            enabled=bool(self._settings.enabled),
            week_remaining_fraction=_remaining_fraction(
                self._week_used, week_lim
            ),
            day_remaining_fraction=_remaining_fraction(
                self._day_used, day_lim
            ),
            hour_remaining_fraction=_remaining_fraction(
                self._hour_used, hour_lim
            ),
            hard_stop=level,
            hard_stop_reason=reason,
            override_active=bool(self._hard_stop_override),
            last_record_at=self._last_record_at,
            week_used_tokens=int(self._week_used),
            day_used_tokens=int(self._day_used),
            hour_used_tokens=int(self._hour_used),
            week_limit_tokens=int(week_lim),
            day_limit_tokens=int(day_lim),
            hour_limit_tokens=int(hour_lim),
        )

    def _state_dict_unlocked(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "week_id": self._week_id,
            "day_id": self._day_id,
            "hour_id": self._hour_id,
            "week_used_tokens": int(self._week_used),
            "day_used_tokens": int(self._day_used),
            "hour_used_tokens": int(self._hour_used),
            "last_record_at": self._last_record_at,
            "last_hard_stop": self._last_hard_stop,
            "last_hard_stop_reason": self._last_hard_stop_reason,
            "hard_stop_override": bool(self._hard_stop_override),
        }

    def _persist_unlocked(self) -> None:
        """Atomic write: unique temp in same dir, then os.replace.

        Cleans up temp on failure so exception paths leave no durable junk.
        """
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._state_dict_unlocked(), ensure_ascii=False, indent=2) + "\n"
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _zero_state_unlocked(self) -> None:
        w, d, h = window_ids(
            self._clock(),
            hour_block_minutes=self._settings.hour_block_minutes,
        )
        self._week_id, self._day_id, self._hour_id = w, d, h
        self._week_used = 0
        self._day_used = 0
        self._hour_used = 0
        self._last_record_at = None
        self._last_hard_stop = None
        self._last_hard_stop_reason = None
        self._hard_stop_override = False

    def _apply_loaded_unlocked(self, raw: dict[str, Any]) -> None:
        def _int_field(key: str, default: int = 0) -> int:
            v = raw.get(key, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return default
            try:
                return max(0, int(v))
            except (TypeError, ValueError):
                return default

        def _str_or_none(key: str) -> str | None:
            v = raw.get(key)
            if v is None:
                return None
            if isinstance(v, str):
                return v
            return None

        self._week_id = str(raw.get("week_id") or self._week_id)
        self._day_id = str(raw.get("day_id") or self._day_id)
        self._hour_id = str(raw.get("hour_id") or self._hour_id)
        self._week_used = _int_field("week_used_tokens")
        self._day_used = _int_field("day_used_tokens")
        self._hour_used = _int_field("hour_used_tokens")
        self._last_record_at = _str_or_none("last_record_at")
        self._last_hard_stop = _str_or_none("last_hard_stop")
        self._last_hard_stop_reason = _str_or_none("last_hard_stop_reason")
        # Missing / non-bool → False (never invent override ON).
        override = raw.get("hard_stop_override", False)
        self._hard_stop_override = override is True

    def _load_from_disk(self) -> None:
        with self._lock:
            path = self._path
            if not path.is_file():
                self._zero_state_unlocked()
                return
            try:
                text = path.read_text(encoding="utf-8")
                raw = json.loads(text)
            except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                logger.warning(
                    "usage.json corrupt/unreadable (%s): %s; "
                    "starting zeroed meter (override=false)",
                    path,
                    exc,
                )
                self._zero_state_unlocked()
                return
            if not isinstance(raw, dict):
                logger.warning(
                    "usage.json corrupt (not an object: %s); "
                    "starting zeroed meter (override=false)",
                    path,
                )
                self._zero_state_unlocked()
                return
            self._apply_loaded_unlocked(raw)
            # Roll windows relative to current clock (may zero stale counters).
            self._refresh_windows_unlocked()
            self._sync_hard_stop_fields_unlocked()
