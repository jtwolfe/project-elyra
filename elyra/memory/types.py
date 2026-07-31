"""Memory atom pure data types and helpers (Phase 1).

Scope: Atom record, kind/scale vocabularies, id helpers, period window bounds.
In scope: schema_version 1 fields, stable summary ids, UTC window grids.
Out of scope: store I/O, promote, meal, ladder refresh.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Mapping

SCHEMA_VERSION = 1

AtomKind = Literal[
    "observation",
    "speak",
    "tool",
    "model",
    "ledger",
    "summary",
    "parcel",
    "moment_meta",
]

ATOM_KINDS: frozenset[str] = frozenset(
    {
        "observation",
        "speak",
        "tool",
        "model",
        "ledger",
        "summary",
        "parcel",
        "moment_meta",
    }
)

PeriodScale = Literal["15m", "1h", "6h", "1d", "1w", "1m", "1y"]

# Write-era ladder (new summaries): 1h → 1d → 1w → 1m → 1y.
PERIOD_SCALES_WRITE: frozenset[str] = frozenset(
    {"1h", "1d", "1w", "1m", "1y"}
)
PERIOD_SCALE_ORDER_WRITE: tuple[PeriodScale, ...] = (
    "1h",
    "1d",
    "1w",
    "1m",
    "1y",
)

# Legacy scales: read / optional repair only (no new writes by default).
PERIOD_SCALES_LEGACY: frozenset[str] = frozenset({"15m", "6h"})

PERIOD_SCALES_ALL: frozenset[str] = PERIOD_SCALES_WRITE | PERIOD_SCALES_LEGACY
# PERIOD_SCALES remains ALL for window_bounds / read validation.
PERIOD_SCALES: frozenset[str] = PERIOD_SCALES_ALL

# Scale order fine → coarse (read/legacy-aware; includes 1y).
PERIOD_SCALE_ORDER: tuple[PeriodScale, ...] = (
    "15m",
    "1h",
    "6h",
    "1d",
    "1w",
    "1m",
    "1y",
)

# KD8: skipped = empty content / encoder permanently unavailable for atom.
EmbeddingStatus = Literal["none", "pending", "ready", "failed", "skipped"]
EMBEDDING_STATUSES: frozenset[str] = frozenset(
    {"none", "pending", "ready", "failed", "skipped"}
)


def new_atom_id() -> str:
    """Return a new atom id: ``a_`` + uuid hex."""
    return "a_" + uuid.uuid4().hex


def utc_now_iso() -> str:
    """UTC timestamp as ISO-8601 with ``Z`` suffix."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def ensure_utc(dt: datetime) -> datetime:
    """Return ``dt`` as timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_iso_z(dt: datetime | str) -> str:
    """Normalize datetime or ISO string to UTC ``…Z`` form."""
    if isinstance(dt, str):
        text = dt.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return to_iso_z(parsed)
    dt = ensure_utc(dt)
    return dt.isoformat().replace("+00:00", "Z")


def parse_iso_z(value: datetime | str) -> datetime:
    """Parse datetime or ISO string to aware UTC datetime."""
    if isinstance(value, datetime):
        return ensure_utc(value)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return ensure_utc(datetime.fromisoformat(text))


def stable_summary_id(scale: PeriodScale | str, window_start: datetime | str) -> str:
    """Deterministic summary atom id for ``(scale, window_start)``.

    **Sole normative source** for tip identity of 1h (and legacy) summaries.
    Key material is ``f\"{scale}|{to_iso_z(window_start)}\"`` — always UTC
    with ``Z`` suffix so ``…Z`` and ``…+00:00`` callers produce the same id.
    """
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    start = parse_iso_z(window_start)
    key = f"{scale}|{to_iso_z(start)}"
    return "as_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def versioned_summary_id(
    scale: PeriodScale | str,
    window_start: datetime | str,
    version: int,
) -> str:
    """Deterministic versioned summary atom id (coarser cascade archaeology).

    Used for ``1d`` / ``1w`` / ``1m`` / ``1y`` — one new atom id per cascade
    version. Ladder index tip is independent of this id (KD-TIP).
    Key material: ``f\"{scale}|{to_iso_z(window_start)}|v{version}\"``.
    """
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    if int(version) < 1:
        raise ValueError(f"version must be >= 1, got {version!r}")
    start = parse_iso_z(window_start)
    key = f"{scale}|{to_iso_z(start)}|v{int(version)}"
    return "as_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def window_bounds(
    scale: PeriodScale | str, t: datetime | str
) -> tuple[datetime, datetime]:
    """Return half-open ``[start, end)`` UTC window containing ``t`` for ``scale``.

    Grids (KD13 + 1y):
    - 15m: floor to 15-min UTC
    - 1h: floor hour
    - 6h: floor to 00, 06, 12, 18 UTC
    - 1d: UTC midnight → next
    - 1w: Monday 00:00 UTC → +7d
    - 1m: first of month → first of next
    - 1y: Jan 1 UTC → next year
    """
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    dt = parse_iso_z(t)
    dt = dt.replace(second=0, microsecond=0)

    if scale == "15m":
        minute = (dt.minute // 15) * 15
        start = dt.replace(minute=minute)
        end = start + timedelta(minutes=15)
    elif scale == "1h":
        start = dt.replace(minute=0)
        end = start + timedelta(hours=1)
    elif scale == "6h":
        hour = (dt.hour // 6) * 6
        start = dt.replace(hour=hour, minute=0)
        end = start + timedelta(hours=6)
    elif scale == "1d":
        start = dt.replace(hour=0, minute=0)
        end = start + timedelta(days=1)
    elif scale == "1w":
        # Monday = 0 in weekday().
        start = dt.replace(hour=0, minute=0) - timedelta(days=dt.weekday())
        end = start + timedelta(days=7)
    elif scale == "1m":
        start = dt.replace(day=1, hour=0, minute=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    else:  # 1y
        start = dt.replace(month=1, day=1, hour=0, minute=0)
        end = start.replace(year=start.year + 1)

    return start, end


@dataclass(frozen=True)
class Atom:
    """Logical memory atom (schema_version 1).

    ``content_text`` is always the meal/render body (KD18).
    ``content_ref`` is a storage locator only: ``\"inline\"`` or ``\"blob:{relpath}\"``.
    """

    atom_id: str
    t_start: str
    kind: AtomKind | str
    content_ref: str = "inline"
    content_text: str = ""
    t_end: str | None = None
    moment_id: str | None = None
    media_ids: tuple[str, ...] = ()
    prev_atom_id: str | None = None
    next_atom_id: str | None = None
    parent_atom_id: str | None = None
    scale: PeriodScale | str | None = None
    window_start: str | None = None
    window_end: str | None = None
    source_beat_ts: str | None = None
    source_beat_type: str | None = None
    embedding_status: EmbeddingStatus | str = "none"
    qualia: None = None
    meta: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Defensive copy so callers cannot mutate shared meta through the atom.
        object.__setattr__(self, "meta", dict(self.meta))
        if isinstance(self.media_ids, list):
            object.__setattr__(self, "media_ids", tuple(self.media_ids))


def validate_atom(atom: Atom) -> Atom:
    """Validate atom invariants; return ``atom`` unchanged on success.

    Raises ``ValueError`` on invalid kind/scale/summary windows/embedding_status.
    Phase 1 writes require ``schema_version == SCHEMA_VERSION`` (currently 1).
    """
    if not isinstance(atom.atom_id, str) or not atom.atom_id:
        raise ValueError(f"invalid atom_id: {atom.atom_id!r}")
    if not isinstance(atom.t_start, str) or not atom.t_start:
        raise ValueError("t_start is required")
    if atom.kind not in ATOM_KINDS:
        raise ValueError(f"invalid kind: {atom.kind!r}")
    if atom.embedding_status not in EMBEDDING_STATUSES:
        raise ValueError(f"invalid embedding_status: {atom.embedding_status!r}")
    if atom.scale is not None and atom.scale not in PERIOD_SCALES:
        raise ValueError(f"invalid scale: {atom.scale!r}")
    if atom.kind == "summary" and atom.scale is not None:
        if not atom.window_start or not atom.window_end:
            raise ValueError(
                "summary atoms with scale require window_start and window_end"
            )
    if atom.content_ref is None or not isinstance(atom.content_ref, str):
        raise ValueError("content_ref must be a string locator")
    if atom.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version: {atom.schema_version!r} "
            f"(Phase 1 requires {SCHEMA_VERSION})"
        )
    return atom


def atom_to_dict(atom: Atom) -> dict[str, Any]:
    """Serialize atom to a JSON-ready dict (media_ids as list)."""
    return {
        "atom_id": atom.atom_id,
        "t_start": atom.t_start,
        "t_end": atom.t_end,
        "moment_id": atom.moment_id,
        "kind": atom.kind,
        "content_ref": atom.content_ref,
        "content_text": atom.content_text,
        "media_ids": list(atom.media_ids),
        "prev_atom_id": atom.prev_atom_id,
        "next_atom_id": atom.next_atom_id,
        "parent_atom_id": atom.parent_atom_id,
        "scale": atom.scale,
        "window_start": atom.window_start,
        "window_end": atom.window_end,
        "source_beat_ts": atom.source_beat_ts,
        "source_beat_type": atom.source_beat_type,
        "embedding_status": atom.embedding_status,
        "qualia": atom.qualia,
        "meta": dict(atom.meta),
        "schema_version": atom.schema_version,
    }


def _normalize_ts(value: Any) -> str | None:
    """Normalize an optional timestamp field to UTC ``…Z``; pass through None."""
    if value is None or value == "":
        return None
    if not isinstance(value, (str, datetime)):
        return str(value)
    try:
        return to_iso_z(value)
    except (TypeError, ValueError):
        return str(value) if not isinstance(value, str) else value


def atom_from_dict(data: Mapping[str, Any]) -> Atom:
    """Build an Atom from a mapping (tolerant of missing optional keys).

    Timestamp fields are normalized to UTC ``Z`` so mixed ``+00:00`` / ``Z``
    on-disk rows compare consistently after load.
    """
    if not isinstance(data, Mapping):
        raise TypeError("atom data must be a mapping")
    atom_id = data.get("atom_id")
    if not isinstance(atom_id, str) or not atom_id:
        raise ValueError("atom_id required")
    t_start_raw = data.get("t_start")
    if not isinstance(t_start_raw, str) or not t_start_raw:
        raise ValueError("t_start required")
    t_start = _normalize_ts(t_start_raw) or t_start_raw
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in ATOM_KINDS:
        raise ValueError(f"invalid kind: {kind!r}")

    media_raw = data.get("media_ids") or ()
    if isinstance(media_raw, str):
        media_ids: tuple[str, ...] = (media_raw,)
    else:
        media_ids = tuple(str(x) for x in media_raw)

    meta_raw = data.get("meta")
    meta: dict[str, Any] = dict(meta_raw) if isinstance(meta_raw, Mapping) else {}

    emb = data.get("embedding_status", "none")
    if emb is None or emb == "":
        emb = "none"
    if emb not in EMBEDDING_STATUSES:
        emb = "none"

    return Atom(
        atom_id=atom_id,
        t_start=t_start,
        t_end=_normalize_ts(data.get("t_end")),
        moment_id=data.get("moment_id"),
        kind=kind,
        content_ref=str(data.get("content_ref") or "inline"),
        content_text=str(data.get("content_text") or ""),
        media_ids=media_ids,
        prev_atom_id=data.get("prev_atom_id"),
        next_atom_id=data.get("next_atom_id"),
        parent_atom_id=data.get("parent_atom_id"),
        scale=data.get("scale"),
        window_start=_normalize_ts(data.get("window_start")),
        window_end=_normalize_ts(data.get("window_end")),
        source_beat_ts=_normalize_ts(data.get("source_beat_ts")),
        source_beat_type=data.get("source_beat_type"),
        embedding_status=str(emb),
        qualia=None,
        meta=meta,
        schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
    )


def atom_replace(atom: Atom, **changes: Any) -> Atom:
    """Return a copy of ``atom`` with the given fields replaced."""
    known = {f.name for f in fields(Atom)}
    bad = set(changes) - known
    if bad:
        raise TypeError(f"unknown atom fields: {sorted(bad)}")
    return replace(atom, **changes)


__all__ = [
    "ATOM_KINDS",
    "Atom",
    "AtomKind",
    "EMBEDDING_STATUSES",
    "EmbeddingStatus",
    "PERIOD_SCALES",
    "PERIOD_SCALES_ALL",
    "PERIOD_SCALES_LEGACY",
    "PERIOD_SCALES_WRITE",
    "PERIOD_SCALE_ORDER",
    "PERIOD_SCALE_ORDER_WRITE",
    "PeriodScale",
    "SCHEMA_VERSION",
    "atom_from_dict",
    "atom_replace",
    "atom_to_dict",
    "ensure_utc",
    "new_atom_id",
    "parse_iso_z",
    "stable_summary_id",
    "to_iso_z",
    "utc_now_iso",
    "validate_atom",
    "versioned_summary_id",
    "window_bounds",
]
