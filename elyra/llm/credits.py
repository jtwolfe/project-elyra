"""SuperGrok credits snapshot types + fail-soft billing fetch.

PR3: ``CreditsSnapshot`` and pure period-id helpers.
PR4: ``parse_billing_payload`` / ``fetch_billing`` (HTTP, no ChatClient).

This module must never import ``elyra.llm.client`` or ``elyra.llm.usage``
(usage imports credits).
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

# ISO week label used as provisional period_id under authority=iso.
_ISO_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")

_LOG = logging.getLogger(__name__)

# Default connect/read timeout for billing GET (design: 5s).
DEFAULT_BILLING_TIMEOUT_S = 5.0

# Status values (normative)
STATUS_OK = "ok"
STATUS_AUTH_FAILED = "auth_failed"
STATUS_ERROR = "error"
STATUS_UNSUPPORTED = "unsupported"
STATUS_STALE = "stale"


@dataclass(frozen=True)
class CreditsSnapshot:
    """Injected or poller-built SuperGrok billing snapshot.

    No network I/O in the type itself — construct from tests or ``fetch_billing``.
    """

    credit_usage_percent: float | None = None
    period_start: str | None = None
    period_end: str | None = None
    period_id: str | None = None
    period_type: str | None = None
    is_unified: bool | None = None
    product_usage: dict[str, float] | None = None
    fetched_at: str | None = None
    status: str | None = None  # ok | auth_failed | error | unsupported | stale
    detail: str | None = None
    ok: bool | None = None  # convenience; when None, inferred from status == "ok"


def canonical_period_id(period_start: str, period_end: str) -> str:
    """Stable id for a SuperGrok billing period: ``{start}/{end}``."""
    return f"{period_start}/{period_end}"


def is_iso_week_period_id(period_id: str | None) -> bool:
    """True if period_id looks like provisional ISO week ``YYYY-Www``."""
    if not period_id or not isinstance(period_id, str):
        return False
    return bool(_ISO_WEEK_RE.match(period_id))


def is_provisional_iso_period(
    period_id: str | None,
    *,
    week_id: str | None = None,
    period_authority: str | None = None,
) -> bool:
    """True when period identity is still ISO-provisional (first-adopt path)."""
    if period_authority == "iso":
        return True
    if week_id and period_id == week_id:
        return True
    return is_iso_week_period_id(period_id)


def snapshot_is_ok(snap: CreditsSnapshot) -> bool:
    """Whether the snapshot reports a successful billing read."""
    if snap.ok is True:
        return True
    if snap.ok is False:
        return False
    return (snap.status or "") == "ok"


def coerce_product_usage(raw: Any) -> dict[str, float] | None:
    """Best-effort product_usage map; None if unusable.

    Accepts:
    - dict[str, number] (already normalized)
    - list of ``{product, usagePercent}`` (live billing shape)
    """
    if isinstance(raw, dict):
        out: dict[str, float] = {}
        for key, val in raw.items():
            if not isinstance(key, str):
                continue
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                continue
            out[key] = float(val)
        return out if out else None
    if isinstance(raw, list):
        out = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            product = item.get("product")
            pct = item.get("usagePercent", item.get("usage_percent"))
            if not isinstance(product, str) or not product:
                continue
            if isinstance(pct, bool) or not isinstance(pct, (int, float)):
                continue
            out[product] = float(pct)
        return out if out else None
    return None


def _utc_now_iso_z() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _billing_url(base_url: str) -> str:
    """``{credits_base_url}/v1/billing?format=credits`` (origin + path)."""
    origin = (base_url or "").strip().rstrip("/")
    return f"{origin}/v1/billing?format=credits"


def _period_bounds_from_config(config: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Extract (start, end, period_type) from config dict."""
    period = config.get("currentPeriod")
    start: str | None = None
    end: str | None = None
    period_type: str | None = None
    if isinstance(period, dict):
        s = period.get("start")
        e = period.get("end")
        t = period.get("type")
        if isinstance(s, str) and s.strip():
            start = s.strip()
        if isinstance(e, str) and e.strip():
            end = e.strip()
        if isinstance(t, str) and t.strip():
            period_type = t.strip()
    # Mirror fields (fallback when currentPeriod missing/partial)
    if start is None:
        s = config.get("billingPeriodStart")
        if isinstance(s, str) and s.strip():
            start = s.strip()
    if end is None:
        e = config.get("billingPeriodEnd")
        if isinstance(e, str) and e.strip():
            end = e.strip()
    return start, end, period_type


def parse_billing_payload(
    data: Any,
    *,
    fetched_at: str | None = None,
) -> CreditsSnapshot:
    """Parse SuperGrok billing JSON body → ``CreditsSnapshot`` (fail-soft).

    Live shape (validated 2026-07-27)::

        {"config": {
            "currentPeriod": {"type": "...", "start": "...", "end": "..."},
            "creditUsagePercent": 22.0,
            "productUsage": [{"product": "GrokBuild", "usagePercent": 18.0}, ...],
            "isUnifiedBillingUser": true,
            "billingPeriodStart": "...",
            "billingPeriodEnd": "..."
        }}

    Schema drift → ``status=error`` (never raises).
    """
    at = fetched_at if fetched_at is not None else _utc_now_iso_z()
    if not isinstance(data, dict):
        return CreditsSnapshot(
            status=STATUS_ERROR,
            ok=False,
            detail="invalid_billing_root",
            fetched_at=at,
        )
    config = data.get("config")
    if not isinstance(config, dict):
        return CreditsSnapshot(
            status=STATUS_ERROR,
            ok=False,
            detail="missing_config",
            fetched_at=at,
        )

    pct_raw = config.get("creditUsagePercent")
    credit_pct: float | None = None
    if isinstance(pct_raw, bool):
        credit_pct = None
    elif isinstance(pct_raw, (int, float)):
        credit_pct = float(pct_raw)

    start, end, period_type = _period_bounds_from_config(config)
    period_id = (
        canonical_period_id(start, end) if start is not None and end is not None else None
    )

    is_unified_raw = config.get("isUnifiedBillingUser")
    is_unified: bool | None
    if isinstance(is_unified_raw, bool):
        is_unified = is_unified_raw
    else:
        is_unified = None

    product_usage = coerce_product_usage(config.get("productUsage"))

    # Require at least a usable percent to count as ok; missing percent is error
    # (tokens-only continues; meter keeps last good A).
    if credit_pct is None:
        return CreditsSnapshot(
            credit_usage_percent=None,
            period_start=start,
            period_end=end,
            period_id=period_id,
            period_type=period_type,
            is_unified=is_unified,
            product_usage=product_usage,
            fetched_at=at,
            status=STATUS_ERROR,
            ok=False,
            detail="missing_credit_usage_percent",
        )

    return CreditsSnapshot(
        credit_usage_percent=credit_pct,
        period_start=start,
        period_end=end,
        period_id=period_id,
        period_type=period_type,
        is_unified=is_unified,
        product_usage=product_usage,
        fetched_at=at,
        status=STATUS_OK,
        ok=True,
        detail=None,
    )


def fetch_billing(
    base_url: str,
    bearer: str,
    timeout: float = DEFAULT_BILLING_TIMEOUT_S,
    *,
    credential_source: str | None = None,
    fetched_at: str | None = None,
    urlopen: Callable[..., Any] | None = None,
) -> CreditsSnapshot:
    """GET SuperGrok billing credits — fail-soft, never raises.

    Status mapping:
    - HTTP 200 + parseable body → ``ok``
    - 401/403 → ``auth_failed`` (grok_build) or ``unsupported`` (api_key)
    - 404 → ``error`` (grok_build) or ``unsupported`` (api_key)
    - 5xx / network / timeout / bad JSON → ``error``

    ``api_key`` path: 401/403/404 → ``unsupported`` (try-then-terminal for poller).
    """
    at = fetched_at if fetched_at is not None else _utc_now_iso_z()
    token = (bearer or "").strip()
    if not token:
        return CreditsSnapshot(
            status=STATUS_ERROR,
            ok=False,
            detail="missing_bearer",
            fetched_at=at,
        )

    url = _billing_url(base_url)
    is_api_key = (credential_source or "") == "api_key"
    open_fn = urlopen if urlopen is not None else urllib.request.urlopen

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with open_fn(request, timeout=float(timeout)) as response:
            raw = response.read().decode("utf-8")
            code = getattr(response, "status", None) or response.getcode()
    except urllib.error.HTTPError as exc:
        code = int(exc.code)
        try:
            exc.read()  # drain
        except Exception:  # noqa: BLE001
            pass
        return _status_for_http_code(code, fetched_at=at, api_key=is_api_key)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        detail = f"network:{reason!s}"[:200] if reason is not None else "network"
        return CreditsSnapshot(
            status=STATUS_ERROR,
            ok=False,
            detail=detail,
            fetched_at=at,
        )
    except TimeoutError:
        return CreditsSnapshot(
            status=STATUS_ERROR,
            ok=False,
            detail="timeout",
            fetched_at=at,
        )
    except Exception as exc:  # noqa: BLE001
        return CreditsSnapshot(
            status=STATUS_ERROR,
            ok=False,
            detail=f"request_failed:{type(exc).__name__}"[:200],
            fetched_at=at,
        )

    if int(code) != 200:
        return _status_for_http_code(int(code), fetched_at=at, api_key=is_api_key)

    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return CreditsSnapshot(
            status=STATUS_ERROR,
            ok=False,
            detail="invalid_json",
            fetched_at=at,
        )
    return parse_billing_payload(data, fetched_at=at)


def _status_for_http_code(
    code: int,
    *,
    fetched_at: str,
    api_key: bool,
) -> CreditsSnapshot:
    if code in (401, 403):
        if api_key:
            return CreditsSnapshot(
                status=STATUS_UNSUPPORTED,
                ok=False,
                detail=f"http_{code}",
                fetched_at=fetched_at,
            )
        return CreditsSnapshot(
            status=STATUS_AUTH_FAILED,
            ok=False,
            detail=f"http_{code}",
            fetched_at=fetched_at,
        )
    if code == 404 and api_key:
        return CreditsSnapshot(
            status=STATUS_UNSUPPORTED,
            ok=False,
            detail="http_404",
            fetched_at=fetched_at,
        )
    return CreditsSnapshot(
        status=STATUS_ERROR,
        ok=False,
        detail=f"http_{code}",
        fetched_at=fetched_at,
    )
