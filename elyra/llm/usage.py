"""Week-ledger usage meter with pace bands and burst cushion (schema v2).

Scope: TokenUsage / parse_token_usage, UsageMeter week ledger + pace/burst,
period authority (iso | supergrok), atomic ``data/runtime/usage.json``,
hard-stop override, apply_credits_snapshot (injected CreditsSnapshot only).
In scope: model A burst capacity, hard levels account>week>day>hour,
threading lock, corrupt fail-soft (override false), v1→v2 migrate.
Out of scope: UsageGatedChatClient, credits HTTP poller, record_media_call,
session subtotals, Glass/auto throttle.

**Import rule (normative):** this module must NEVER import ``elyra.llm.client``
(cycle-free: client.py → usage.py only). credits.py is types-only (no HTTP).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from elyra.llm.credits import (
    CreditsSnapshot,
    canonical_period_id,
    coerce_product_usage,
    is_iso_week_period_id,
    is_provisional_iso_period,
    snapshot_is_ok,
)
from elyra.settings import UsageSettings

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
USAGE_REL = Path("runtime") / "usage.json"

# Floor for elapsed hours t (1 minute) so pace_ratio is defined at period start.
_EPS_HOURS = 1.0 / 60.0
_DEFAULT_PERIOD_HOURS = 168.0

# Log missing-usage estimate at most once per process.
_missing_usage_logged = False
# Log nested supergrok corrupt at most once per process.
_supergrok_corrupt_logged = False


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
    hard_stop: str | None  # None | "account" | "week" | "day" | "hour"
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
    # v2 pace / burst (derived; burst is capacity cushion, not a token-bucket drain)
    pace_band: str = "green"  # green | yellow | red | hard
    pace_ratio: float = 0.0
    burst_remaining_tokens: int = 0
    burst_max_tokens: int = 0
    period_id: str = ""
    period_authority: str = "iso"
    day_hard_stop_enabled: bool = False
    hour_hard_stop_enabled: bool = False
    day_soft_exhausted: bool = False
    hour_soft_exhausted: bool = False
    week_cached_tokens: int = 0
    credit_usage_percent: float | None = None
    credits_status: str | None = None


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


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse ISO-8601 datetime string (Z or offset); None if unusable."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _ensure_aware_utc(dt)


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


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def hours_since_iso_week_start(now: datetime) -> float:
    """Hours since Monday 00:00 UTC of the current ISO week (offline fallback)."""
    now = _ensure_aware_utc(now)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = midnight - timedelta(days=now.weekday())
    return max(0.0, (now - week_start).total_seconds() / 3600.0)


def period_hours(
    period_start: str | datetime | None,
    period_end: str | datetime | None,
) -> float:
    """Period length H in hours; default 168 when dates unparseable."""
    start = (
        period_start
        if isinstance(period_start, datetime)
        else parse_iso_datetime(period_start if isinstance(period_start, str) else None)
    )
    end = (
        period_end
        if isinstance(period_end, datetime)
        else parse_iso_datetime(period_end if isinstance(period_end, str) else None)
    )
    if start is not None and end is not None and end > start:
        return max(1.0, (end - start).total_seconds() / 3600.0)
    return _DEFAULT_PERIOD_HOURS


def elapsed_hours(
    now: datetime,
    period_start: str | datetime | None,
    H: float,
) -> float:
    """Hours elapsed t in period, clamped to [ε, H]."""
    now = _ensure_aware_utc(now)
    start = (
        period_start
        if isinstance(period_start, datetime)
        else parse_iso_datetime(period_start if isinstance(period_start, str) else None)
    )
    if start is not None:
        raw = (now - start).total_seconds() / 3600.0
    else:
        raw = hours_since_iso_week_start(now)
    h = max(1.0, float(H))
    return _clamp(raw, _EPS_HOURS, h)


def pace_ratio(S: float, B: float, H: float, t: float) -> float:
    """p = (S/t) / (B/H) = S·H / (B·t)."""
    b = max(1.0, float(B))
    h = max(1.0, float(H))
    tt = max(_EPS_HOURS, float(t))
    return (float(S) * h) / (b * tt)


def burst_max(B: float, H: float, k: float) -> float:
    """BurstMax = k · (B / H) — fixed overshoot cushion capacity (model A)."""
    h = max(1.0, float(H))
    return max(0.0, float(k) * (float(B) / h))


def linear_schedule(B: float, H: float, t: float) -> float:
    """Linear token schedule at elapsed t: (B/H)·t."""
    h = max(1.0, float(H))
    return (float(B) / h) * float(t)


def effective_overshoot(S: float, B: float, H: float, t: float) -> float:
    """over = max(0, S − (B/H)·t)."""
    return max(0.0, float(S) - linear_schedule(B, H, t))


def burst_remaining(S: float, B: float, H: float, t: float, k: float) -> float:
    """Glass/status numerator: max(0, BurstMax − over). Derived, not a drain counter."""
    return max(0.0, burst_max(B, H, k) - effective_overshoot(S, B, H, t))


def compute_band(
    S: float,
    B: float,
    H: float,
    t: float,
    k: float,
    yellow: float,
    red: float,
) -> str:
    """Pace band (model A): green while over ≤ BurstMax; else by pace thresholds.

    Soft bands never refuse calls — status/throttle advice only.
    """
    p = pace_ratio(S, B, H, t)
    over = effective_overshoot(S, B, H, t)
    bmax = burst_max(B, H, k)
    if over <= bmax:
        return "green"
    if p < float(yellow):
        return "green"
    if p < float(red):
        return "yellow"
    return "red"


def hard_level(
    *,
    S: int,
    B: int,
    day_used: int,
    day_limit: int,
    day_hard_enabled: bool,
    hour_used: int,
    hour_limit: int,
    hour_hard_enabled: bool,
    account_usage_fraction: float | None,
    account_hard_fraction: float,
) -> str | None:
    """Hard-stop level precedence: account > week > day > hour.

    Day/hour only when their hard-stop flags are enabled.
    """
    if (
        account_usage_fraction is not None
        and account_usage_fraction >= float(account_hard_fraction)
    ):
        return "account"
    if int(S) >= int(B):
        return "week"
    if day_hard_enabled and int(day_used) >= int(day_limit):
        return "day"
    if hour_hard_enabled and int(hour_used) >= int(hour_limit):
        return "hour"
    return None


def default_period_authority(
    period_id: str | None,
    *,
    week_id: str | None = None,
) -> str:
    """Infer period_authority when missing on partial v2 load."""
    if not period_id:
        return "iso"
    if is_iso_week_period_id(period_id) or (
        week_id is not None and period_id == week_id
    ):
        return "iso"
    # start/end style ids contain '/'
    if "/" in period_id:
        parts = period_id.split("/", 1)
        if len(parts) == 2 and parse_iso_datetime(parts[0]) and parse_iso_datetime(
            parts[1]
        ):
            return "supergrok"
        # looks like start/end even if not fully parseable — prefer supergrok
        # only when both sides parse; else safe iso (retain-S adoption path).
        return "iso"
    return "iso"


class UsageMeter:
    """Thread-safe week-ledger usage meter with pace/burst (schema v2).

    All public methods take ``self._lock``.
    Persist via write-temp + ``os.replace`` only.
    ``hard_stop_override`` (default False) is persisted in usage.json and
    survives restarts. Override never skips ``record``.
    Soft yellow/red bands never make ``can_call`` False.
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
        self._period_id = ""
        self._period_authority = "iso"
        self._period_start: str | None = None
        self._period_end: str | None = None
        self._week_used = 0
        self._day_used = 0
        self._hour_used = 0
        self._week_cached = 0
        self._week_stt_calls = 0
        self._week_tts_calls = 0
        self._last_record_at: str | None = None
        self._last_hard_stop: str | None = None
        self._last_hard_stop_reason: str | None = None
        self._hard_stop_override = False

        # SuperGrok snapshot cache (applied via apply_credits_snapshot; no HTTP).
        self._sg_credit_usage_percent: float | None = None
        self._sg_product_usage: dict[str, float] | None = None
        self._sg_fetched_at: str | None = None
        self._sg_status: str | None = None
        self._sg_period_type: str | None = None
        self._sg_is_unified: bool | None = None
        self._sg_detail: str | None = None

        # Initialize window ids for current clock (zeroed counters).
        w, d, h = window_ids(
            self._clock(), hour_block_minutes=settings.hour_block_minutes
        )
        self._week_id, self._day_id, self._hour_id = w, d, h
        self._period_id = w
        self._period_authority = "iso"

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
        v1 files migrate to v2 (period_id=week_id, authority=iso).
        """
        path = Path(data_dir) / USAGE_REL
        meter = cls(path, settings, clock=clock)
        meter._load_from_disk()
        return meter

    # --- public API (all under lock) -----------------------------------------

    def can_call(self) -> bool:
        """True if meter disabled, hard_level is None, or hard_stop_override ON.

        Soft yellow/red bands do NOT make this False.
        Override does NOT skip credential checks (those are can_open_model_moment).
        """
        with self._lock:
            self._refresh_windows_unlocked()
            if not self._settings.enabled:
                return True
            if self._hard_stop_override:
                return True
            return self._hard_stop_level_unlocked() is None

    def hard_stop_level(self) -> str | None:
        """account | week | day | hour | None — precedence account>week>day>hour."""
        with self._lock:
            self._refresh_windows_unlocked()
            return self._hard_stop_level_unlocked()

    def hard_stop_reason(self) -> str | None:
        """Would-be stop reason from budgets, even if override allows calls.

        None when under all ceilings.
        """
        with self._lock:
            self._refresh_windows_unlocked()
            return self._hard_stop_reason_unlocked()

    def is_over_budget(self) -> bool:
        """True when any hard ceiling is hit (ignores override; ignores soft bands)."""
        with self._lock:
            self._refresh_windows_unlocked()
            return self._hard_stop_level_unlocked() is not None

    def pace_band(self) -> str:
        """green | yellow | red | hard (hard when hard_level is not None)."""
        with self._lock:
            self._refresh_windows_unlocked()
            return self._pace_band_unlocked()

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

        Never silently defaults to True. Memory rolls back if persist fails.
        """
        with self._lock:
            prev = self._capture_durable_unlocked()
            try:
                self._hard_stop_override = bool(active)
                self._refresh_windows_unlocked()
                self._sync_hard_stop_fields_unlocked()
                self._persist_unlocked()
                return self._snapshot_unlocked()
            except Exception:
                self._restore_durable_unlocked(prev)
                raise

    def apply_credits_snapshot(self, snap: CreditsSnapshot) -> UsageSnapshot:
        """Merge SuperGrok snapshot; first-adopt retains S; true roll zeros S.

        No HTTP — caller injects a constructed ``CreditsSnapshot``.
        Identity adopt/roll only on successful (ok) + parseable period.
        Unparseable / non-ok polls do not wipe last-good account snapshot;
        memory rolls back if persist fails.
        """
        with self._lock:
            prev = self._capture_durable_unlocked()
            try:
                self._refresh_windows_unlocked()
                self._apply_credits_snapshot_unlocked(snap)
                self._sync_hard_stop_fields_unlocked()
                self._persist_unlocked()
                return self._snapshot_unlocked()
            except Exception:
                self._restore_durable_unlocked(prev)
                raise

    def record(
        self,
        usage: TokenUsage | None,
        *,
        estimated_if_missing: int = 0,
    ) -> UsageSnapshot:
        """Always records tokens when usage present — even if override_active.

        Recording is never disabled by override. Missing usage records
        ``estimated_if_missing`` (default 0). Cached tokens accumulate
        informationally; never subtracted from billable S.
        Memory rolls back if persist fails.
        """
        global _missing_usage_logged
        with self._lock:
            prev = self._capture_durable_unlocked()
            try:
                self._refresh_windows_unlocked()
                if usage is not None:
                    tokens = max(0, int(usage.billable_tokens))
                    cached = max(0, int(usage.cached_tokens))
                else:
                    tokens = max(0, int(estimated_if_missing))
                    cached = 0
                    if not _missing_usage_logged:
                        logger.debug(
                            "usage.record: missing usage; "
                            "recording estimated_if_missing=%s",
                            tokens,
                        )
                        _missing_usage_logged = True

                if tokens or cached:
                    if tokens:
                        self._week_used += tokens
                        self._day_used += tokens
                        self._hour_used += tokens
                    if cached:
                        self._week_cached += cached
                    self._last_record_at = _iso_z(self._clock())

                self._sync_hard_stop_fields_unlocked()
                self._persist_unlocked()
                return self._snapshot_unlocked()
            except Exception:
                self._restore_durable_unlocked(prev)
                raise

    def refresh_windows(self) -> None:
        """Roll window counters when week/day/hour ids change (UTC).

        Week S zeros only when period_authority is iso (or true SuperGrok roll
        via apply_credits_snapshot). Day/hour counters always zero on id change.
        Memory rolls back if persist fails after a roll.
        """
        with self._lock:
            prev = self._capture_durable_unlocked()
            try:
                rolled = self._refresh_windows_unlocked()
                if rolled:
                    self._sync_hard_stop_fields_unlocked()
                    self._persist_unlocked()
            except Exception:
                self._restore_durable_unlocked(prev)
                raise

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

    def _period_H_t_unlocked(self) -> tuple[float, float]:
        """Return (H, t) from period authority / server bounds."""
        now = self._clock()
        if self._period_authority == "supergrok" and self._period_start:
            H = period_hours(self._period_start, self._period_end)
            t = elapsed_hours(now, self._period_start, H)
        else:
            H = _DEFAULT_PERIOD_HOURS
            t = elapsed_hours(now, None, H)
        return H, t

    def _account_usage_fraction_unlocked(self) -> float | None:
        """Account used fraction A when last-good snapshot ok and not stale.

        Requires status ok, a usable percent, and an established period basis
        (supergrok with period_start, or parseable bounds already stored).
        """
        if (self._sg_status or "") != "ok":
            return None
        if self._sg_credit_usage_percent is None:
            return None
        # Account hard only once we have a period basis (adopted or stored).
        if not self._period_start or parse_iso_datetime(self._period_start) is None:
            return None
        # Staleness: if fetched_at older than credits_stale_after_s → unavailable
        if self._sg_fetched_at:
            fetched = parse_iso_datetime(self._sg_fetched_at)
            if fetched is not None:
                age = (self._clock() - fetched).total_seconds()
                stale_after = float(self._settings.credits_stale_after_s)
                if age > stale_after:
                    return None
        try:
            return float(self._sg_credit_usage_percent) / 100.0
        except (TypeError, ValueError):
            return None

    def _refresh_windows_unlocked(self) -> bool:
        now = self._clock()
        week_id, day_id, hour_id = window_ids(
            now, hour_block_minutes=self._settings.hour_block_minutes
        )
        rolled = False
        if week_id != self._week_id:
            self._week_id = week_id
            # Zero week ledger S only under ISO authority (KD10 / KD18).
            if self._period_authority == "iso":
                self._week_used = 0
                self._week_cached = 0
                self._week_stt_calls = 0
                self._week_tts_calls = 0
                self._period_id = week_id
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

    def _zero_week_ledger_unlocked(self) -> None:
        """True SuperGrok period roll: zero week counters; preserve override."""
        self._week_used = 0
        self._week_cached = 0
        self._week_stt_calls = 0
        self._week_tts_calls = 0

    def _capture_durable_unlocked(self) -> dict[str, Any]:
        """Snapshot in-memory durable fields for rollback if persist fails."""
        return {
            "week_id": self._week_id,
            "day_id": self._day_id,
            "hour_id": self._hour_id,
            "period_id": self._period_id,
            "period_authority": self._period_authority,
            "period_start": self._period_start,
            "period_end": self._period_end,
            "week_used": self._week_used,
            "day_used": self._day_used,
            "hour_used": self._hour_used,
            "week_cached": self._week_cached,
            "week_stt_calls": self._week_stt_calls,
            "week_tts_calls": self._week_tts_calls,
            "last_record_at": self._last_record_at,
            "last_hard_stop": self._last_hard_stop,
            "last_hard_stop_reason": self._last_hard_stop_reason,
            "hard_stop_override": self._hard_stop_override,
            "sg_credit_usage_percent": self._sg_credit_usage_percent,
            "sg_product_usage": (
                dict(self._sg_product_usage)
                if self._sg_product_usage is not None
                else None
            ),
            "sg_fetched_at": self._sg_fetched_at,
            "sg_status": self._sg_status,
            "sg_period_type": self._sg_period_type,
            "sg_is_unified": self._sg_is_unified,
            "sg_detail": self._sg_detail,
        }

    def _restore_durable_unlocked(self, prev: dict[str, Any]) -> None:
        """Restore durable fields after a failed persist (no partial commit)."""
        self._week_id = prev["week_id"]
        self._day_id = prev["day_id"]
        self._hour_id = prev["hour_id"]
        self._period_id = prev["period_id"]
        self._period_authority = prev["period_authority"]
        self._period_start = prev["period_start"]
        self._period_end = prev["period_end"]
        self._week_used = prev["week_used"]
        self._day_used = prev["day_used"]
        self._hour_used = prev["hour_used"]
        self._week_cached = prev["week_cached"]
        self._week_stt_calls = prev["week_stt_calls"]
        self._week_tts_calls = prev["week_tts_calls"]
        self._last_record_at = prev["last_record_at"]
        self._last_hard_stop = prev["last_hard_stop"]
        self._last_hard_stop_reason = prev["last_hard_stop_reason"]
        self._hard_stop_override = prev["hard_stop_override"]
        self._sg_credit_usage_percent = prev["sg_credit_usage_percent"]
        pu = prev["sg_product_usage"]
        self._sg_product_usage = dict(pu) if pu is not None else None
        self._sg_fetched_at = prev["sg_fetched_at"]
        self._sg_status = prev["sg_status"]
        self._sg_period_type = prev["sg_period_type"]
        self._sg_is_unified = prev["sg_is_unified"]
        self._sg_detail = prev["sg_detail"]

    def _apply_credits_snapshot_unlocked(self, snap: CreditsSnapshot) -> None:
        """Merge injected credits snap under lock (caller handles persist/rollback).

        Normative fail-soft:
        - First adopt / true roll only when ``snapshot_is_ok`` **and** period
          dates parse.
        - Account A (percent / fetched_at / status used for hard stop) only
          refreshes on ok + (parseable period **or** already-adopted SuperGrok
          period basis). Unparseable / non-ok polls retain last-good A until
          stale.
        - Diagnostic ``detail`` may update on failed attempts without wiping A.
        """
        ok = snapshot_is_ok(snap)

        start = snap.period_start
        end = snap.period_end
        start_dt = parse_iso_datetime(start if isinstance(start, str) else None)
        end_dt = parse_iso_datetime(end if isinstance(end, str) else None)
        parseable = (
            start_dt is not None
            and end_dt is not None
            and end_dt > start_dt
            and isinstance(start, str)
            and isinstance(end, str)
        )

        def _nonempty_str(value: Any) -> bool:
            return isinstance(value, str) and bool(value.strip())

        # Snap tried to supply period bounds but they do not parse — never
        # adopt/roll and never overwrite last-good A with this attempt.
        attempted_bad_period = (not parseable) and (
            _nonempty_str(start) or _nonempty_str(end)
        )

        # Identity changes: successful apply of a parseable billing period only.
        if ok and parseable:
            new_id = snap.period_id or canonical_period_id(start, end)  # type: ignore[arg-type]
            provisional = is_provisional_iso_period(
                self._period_id,
                week_id=self._week_id,
                period_authority=self._period_authority,
            )
            if self._period_authority == "iso" or provisional:
                old_id = self._period_id
                self._period_id = new_id
                self._period_authority = "supergrok"
                self._period_start = start
                self._period_end = end
                # RETAIN S, day/hour, override, media counters (KD18)
                logger.info(
                    "usage.period_adopted old=%s new=%s S=%s",
                    old_id,
                    new_id,
                    self._week_used,
                )
            elif (
                self._period_authority == "supergrok"
                and new_id != self._period_id
            ):
                self._zero_week_ledger_unlocked()
                self._period_id = new_id
                self._period_start = start
                self._period_end = end
                logger.info(
                    "usage.period_rolled new=%s override=%s",
                    new_id,
                    self._hard_stop_override,
                )
            else:
                # same period — refresh bounds if present
                self._period_start = start
                self._period_end = end

        # Account / cache refresh when ok and:
        # - period just validated (parseable), or
        # - percent-only refresh after SuperGrok already adopted (no bad bounds).
        already_adopted = (
            self._period_authority == "supergrok"
            and self._period_start is not None
            and parse_iso_datetime(self._period_start) is not None
        )
        may_refresh_account = ok and not attempted_bad_period and (
            parseable or already_adopted
        )
        if may_refresh_account:
            if snap.credit_usage_percent is not None:
                try:
                    self._sg_credit_usage_percent = float(snap.credit_usage_percent)
                except (TypeError, ValueError):
                    pass
            if snap.product_usage is not None:
                coerced = coerce_product_usage(snap.product_usage)
                if coerced is not None:
                    self._sg_product_usage = coerced
            if snap.fetched_at is not None:
                self._sg_fetched_at = str(snap.fetched_at)
            self._sg_status = "ok"
            if snap.period_type is not None:
                self._sg_period_type = str(snap.period_type)
            if snap.is_unified is not None:
                self._sg_is_unified = bool(snap.is_unified)
            if snap.detail is not None:
                self._sg_detail = str(snap.detail)
            return

        # Fail-soft path: non-ok, unparseable bounds, or no period basis.
        # Do NOT mutate last-good percent, fetched_at, or status used for
        # account hard. Optional attempt detail only.
        if snap.detail is not None:
            self._sg_detail = str(snap.detail)
        elif attempted_bad_period:
            self._sg_detail = "credits_apply_rejected:unparseable_period"
        elif not ok:
            status_label = snap.status or ("error" if snap.ok is False else "error")
            self._sg_detail = f"credits_apply_rejected:{status_label}"
        else:
            self._sg_detail = "credits_apply_rejected:no_period_basis"

    def _hard_stop_level_unlocked(self) -> str | None:
        week_lim, day_lim, hour_lim = self._limits_unlocked()
        a_hard = float(self._settings.account_hard_stop_percent) / 100.0
        return hard_level(
            S=self._week_used,
            B=week_lim,
            day_used=self._day_used,
            day_limit=day_lim,
            day_hard_enabled=bool(self._settings.day_hard_stop_enabled),
            hour_used=self._hour_used,
            hour_limit=hour_lim,
            hour_hard_enabled=bool(self._settings.hour_hard_stop_enabled),
            account_usage_fraction=self._account_usage_fraction_unlocked(),
            account_hard_fraction=a_hard,
        )

    def _hard_stop_reason_unlocked(self) -> str | None:
        level = self._hard_stop_level_unlocked()
        if level is None:
            return None
        week_lim, day_lim, hour_lim = self._limits_unlocked()
        if level == "account":
            pct = self._sg_credit_usage_percent
            cap = float(self._settings.account_hard_stop_percent)
            pct_s = f"{pct:g}" if pct is not None else "?"
            return (
                f"account weekly budget nearly exhausted "
                f"({pct_s}/{cap:g}%)"
            )
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

    def _pace_metrics_unlocked(self) -> tuple[float, float, float, str]:
        """Return (p, BurstMax, remaining, band) with band green|yellow|red."""
        week_lim, _, _ = self._limits_unlocked()
        H, t = self._period_H_t_unlocked()
        B = float(week_lim)
        S = float(self._week_used)
        k = float(self._settings.burst_hours)
        yellow = float(self._settings.pace_yellow_ratio)
        red = float(self._settings.pace_red_ratio)
        p = pace_ratio(S, B, H, t)
        bmax = burst_max(B, H, k)
        remaining = burst_remaining(S, B, H, t, k)
        band = compute_band(S, B, H, t, k, yellow, red)
        return p, bmax, remaining, band

    def _pace_band_unlocked(self) -> str:
        if self._hard_stop_level_unlocked() is not None:
            return "hard"
        _, _, _, band = self._pace_metrics_unlocked()
        return band

    def _sync_hard_stop_fields_unlocked(self) -> None:
        level = self._hard_stop_level_unlocked()
        reason = self._hard_stop_reason_unlocked()
        self._last_hard_stop = level
        self._last_hard_stop_reason = reason

    def _snapshot_unlocked(self) -> UsageSnapshot:
        week_lim, day_lim, hour_lim = self._limits_unlocked()
        level = self._hard_stop_level_unlocked()
        reason = self._hard_stop_reason_unlocked()
        p, bmax, remaining, soft_band = self._pace_metrics_unlocked()
        band = "hard" if level is not None else soft_band
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
            pace_band=band,
            pace_ratio=float(p),
            burst_remaining_tokens=int(round(remaining)),
            burst_max_tokens=int(round(bmax)),
            period_id=str(self._period_id),
            period_authority=str(self._period_authority),
            day_hard_stop_enabled=bool(self._settings.day_hard_stop_enabled),
            hour_hard_stop_enabled=bool(self._settings.hour_hard_stop_enabled),
            day_soft_exhausted=self._day_used >= day_lim,
            hour_soft_exhausted=self._hour_used >= hour_lim,
            week_cached_tokens=int(self._week_cached),
            credit_usage_percent=self._sg_credit_usage_percent,
            credits_status=self._sg_status,
        )

    def _supergrok_state_unlocked(self) -> dict[str, Any] | None:
        if (
            self._sg_status is None
            and self._sg_credit_usage_percent is None
            and self._sg_fetched_at is None
            and self._period_authority != "supergrok"
        ):
            return None
        return {
            "credit_usage_percent": self._sg_credit_usage_percent,
            "period_start": self._period_start,
            "period_end": self._period_end,
            "period_type": self._sg_period_type,
            "is_unified": self._sg_is_unified,
            "product_usage": self._sg_product_usage,
            "fetched_at": self._sg_fetched_at,
            "status": self._sg_status,
        }

    def _state_dict_unlocked(self) -> dict[str, Any]:
        _, _bmax, remaining, _ = self._pace_metrics_unlocked()
        state: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "period_id": self._period_id,
            "period_authority": self._period_authority,
            "week_id": self._week_id,
            "day_id": self._day_id,
            "hour_id": self._hour_id,
            "week_used_tokens": int(self._week_used),
            "day_used_tokens": int(self._day_used),
            "hour_used_tokens": int(self._hour_used),
            "week_cached_tokens": int(self._week_cached),
            "week_stt_calls": int(self._week_stt_calls),
            "week_tts_calls": int(self._week_tts_calls),
            # Convenience mirror only; load paths recompute from S,B,H,t,k.
            "burst_remaining_tokens": int(round(remaining)),
            "last_record_at": self._last_record_at,
            "last_hard_stop": self._last_hard_stop,
            "last_hard_stop_reason": self._last_hard_stop_reason,
            "hard_stop_override": bool(self._hard_stop_override),
        }
        sg = self._supergrok_state_unlocked()
        if sg is not None:
            state["supergrok"] = sg
        return state

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
        self._period_id = w
        self._period_authority = "iso"
        self._period_start = None
        self._period_end = None
        self._week_used = 0
        self._day_used = 0
        self._hour_used = 0
        self._week_cached = 0
        self._week_stt_calls = 0
        self._week_tts_calls = 0
        self._last_record_at = None
        self._last_hard_stop = None
        self._last_hard_stop_reason = None
        self._hard_stop_override = False
        self._sg_credit_usage_percent = None
        self._sg_product_usage = None
        self._sg_fetched_at = None
        self._sg_status = None
        self._sg_period_type = None
        self._sg_is_unified = None
        self._sg_detail = None

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
        self._week_cached = _int_field("week_cached_tokens")
        self._week_stt_calls = _int_field("week_stt_calls")
        self._week_tts_calls = _int_field("week_tts_calls")
        self._last_record_at = _str_or_none("last_record_at")
        self._last_hard_stop = _str_or_none("last_hard_stop")
        self._last_hard_stop_reason = _str_or_none("last_hard_stop_reason")
        # Missing / non-bool → False (never invent override ON).
        override = raw.get("hard_stop_override", False)
        self._hard_stop_override = override is True

        # period_id / period_authority — v1 migrate + partial v2 defaults
        schema_ver = raw.get("schema_version", 1)
        try:
            schema_ver_i = int(schema_ver) if not isinstance(schema_ver, bool) else 1
        except (TypeError, ValueError):
            schema_ver_i = 1

        period_id_raw = raw.get("period_id")
        if isinstance(period_id_raw, str) and period_id_raw:
            self._period_id = period_id_raw
        else:
            self._period_id = self._week_id

        auth_raw = raw.get("period_authority")
        if auth_raw in ("iso", "supergrok"):
            self._period_authority = auth_raw
        else:
            # Missing / invalid → infer (prefer iso so first adopt retains S)
            self._period_authority = default_period_authority(
                self._period_id, week_id=self._week_id
            )

        # v1 → v2: force iso identity from week_id
        if schema_ver_i < 2:
            self._period_id = self._week_id
            self._period_authority = "iso"

        # Nested supergrok (fail-soft)
        global _supergrok_corrupt_logged
        sg = raw.get("supergrok")
        if sg is None:
            pass
        elif not isinstance(sg, dict):
            if not _supergrok_corrupt_logged:
                logger.warning(
                    "usage.json nested supergrok corrupt (not an object); ignoring"
                )
                _supergrok_corrupt_logged = True
        else:
            pct = sg.get("credit_usage_percent")
            if isinstance(pct, (int, float)) and not isinstance(pct, bool):
                self._sg_credit_usage_percent = float(pct)
            ps = sg.get("period_start")
            pe = sg.get("period_end")
            if isinstance(ps, str):
                self._period_start = ps
            if isinstance(pe, str):
                self._period_end = pe
            st = sg.get("status")
            if isinstance(st, str):
                self._sg_status = st
            fa = sg.get("fetched_at")
            if isinstance(fa, str):
                self._sg_fetched_at = fa
            pt = sg.get("period_type")
            if isinstance(pt, str):
                self._sg_period_type = pt
            iu = sg.get("is_unified")
            if isinstance(iu, bool):
                self._sg_is_unified = iu
            pu = coerce_product_usage(sg.get("product_usage"))
            if pu is not None:
                self._sg_product_usage = pu
            # If authority is supergrok but period_start/end only in nested blob,
            # already applied above. If period_id missing start/end in root, keep.

        # burst_remaining_tokens in file is ignored for math (recomputed).

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
