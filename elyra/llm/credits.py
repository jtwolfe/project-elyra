"""SuperGrok credits snapshot types (PR3: no HTTP).

PR3 ships ``CreditsSnapshot`` and pure period-id helpers only.
PR4 adds ``fetch_billing`` / parse. This module must never import
``elyra.llm.client`` or ``elyra.llm.usage`` (usage imports credits).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ISO week label used as provisional period_id under authority=iso.
_ISO_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


@dataclass(frozen=True)
class CreditsSnapshot:
    """Injected (or later poller-built) SuperGrok billing snapshot.

    No network I/O here — construct from tests or PR4 ``fetch_billing``.
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
    """Best-effort product_usage map; None if unusable."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, float] = {}
    for key, val in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        out[key] = float(val)
    return out if out else None
