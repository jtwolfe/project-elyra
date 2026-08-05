"""Moment-scoped viewing set (host working set; not durable identity).

KD-V4/V5/V12: process-local membership for Completions expand (wake ∪ viewing).
FIFO cap; clear on moment finalize. Dirty flag lives on the worker; helpers
here only mutate the ordered entry map.

Out of scope for this module: tool handler, URL fetch, promote, AV wire parts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

# Design cap: FIFO by first-add (re-view does not reorder).
MAX_VIEWING_SET = 8

# Synthetic meal row id for expand carrier (host-only; stripped before wire).
VIEWING_CARRIER_ID = "_viewing_carrier"


@dataclass
class ViewingEntry:
    """One membership slot in the moment viewing set."""

    att_id: str
    kind: str = "file"
    mime: str = "application/octet-stream"
    filename: str = "file"
    byte_size: int = 0
    duration_s: float | None = None
    # Free-form host meta (not required for expand; tools may stamp later).
    extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "att_id": self.att_id,
            "kind": self.kind,
            "mime": self.mime,
            "filename": self.filename,
            "byte_size": self.byte_size,
        }
        if self.duration_s is not None:
            d["duration_s"] = self.duration_s
        return d


def _normalize_att_id(att_id: str | None) -> str | None:
    if att_id is None:
        return None
    s = str(att_id).strip()
    return s or None


def list_viewing(
    entries: Mapping[str, ViewingEntry] | None,
) -> list[ViewingEntry]:
    """Return viewing entries in FIFO (insertion) order."""
    if not entries:
        return []
    return list(entries.values())


def list_viewing_att_ids(
    entries: Mapping[str, ViewingEntry] | None,
) -> list[str]:
    """Return att_ids in FIFO order (expand / tool JSON)."""
    if not entries:
        return []
    return list(entries.keys())


def add_viewing(
    entries: MutableMapping[str, ViewingEntry],
    att_id: str,
    *,
    kind: str = "file",
    mime: str = "application/octet-stream",
    filename: str = "file",
    byte_size: int = 0,
    duration_s: float | None = None,
    max_size: int = MAX_VIEWING_SET,
    **extra: Any,
) -> tuple[ViewingEntry, bool]:
    """Add or re-touch an att_id in the viewing set.

    * New id: append at end; if over ``max_size``, drop oldest (FIFO).
    * Existing id: **no reorder**; metadata refreshed; still a successful view.

    Returns ``(entry, created)`` where ``created`` is True only on first add.
    Callers always mark viewing dirty on successful view (including re-view).
    """
    aid = _normalize_att_id(att_id)
    if not aid:
        raise ValueError("att_id required")
    if max_size < 1:
        raise ValueError("max_size must be >= 1")

    if aid in entries:
        prev = entries[aid]
        # Refresh metadata in place; preserve insertion order (no move-to-end).
        prev.kind = kind or prev.kind
        prev.mime = mime or prev.mime
        prev.filename = filename or prev.filename
        if byte_size:
            prev.byte_size = int(byte_size)
        if duration_s is not None:
            prev.duration_s = duration_s
        if extra:
            prev.extra.update(extra)
        return prev, False

    entry = ViewingEntry(
        att_id=aid,
        kind=str(kind or "file"),
        mime=str(mime or "application/octet-stream"),
        filename=str(filename or "file"),
        byte_size=int(byte_size or 0),
        duration_s=duration_s,
        extra=dict(extra) if extra else {},
    )
    entries[aid] = entry
    # FIFO drop oldest while over cap.
    while len(entries) > max_size:
        oldest = next(iter(entries))
        del entries[oldest]
    return entry, True


def drop_viewing(
    entries: MutableMapping[str, ViewingEntry],
    att_id: str,
) -> bool:
    """Remove one att_id. Returns True if it was present."""
    aid = _normalize_att_id(att_id)
    if not aid or aid not in entries:
        return False
    del entries[aid]
    return True


def clear_viewing(entries: MutableMapping[str, ViewingEntry]) -> int:
    """Empty the set. Returns number of entries removed."""
    n = len(entries)
    entries.clear()
    return n


def _entry_field(entry: Any, key: str, default: Any = None) -> Any:
    """Read a field from ViewingEntry or plain mapping (fail-closed callers)."""
    if entry is None:
        return default
    if isinstance(entry, Mapping):
        val = entry.get(key, default)
        return default if val is None else val
    return getattr(entry, key, default)


def viewing_att_dicts(
    entries: Mapping[str, Any] | Sequence[str] | None,
    media_store: Any | None = None,
) -> list[dict[str, Any]]:
    """Resolve viewing membership to attachment dicts for expand / glass seed.

    Accepts either a ViewingEntry (or plain-dict) map or a plain list of att_ids.
    Missing media meta yields a minimal ``{"id": att_id}`` row.

    When a viewing entry has ``duration_s``, it **wins** over store/glass meta
    (tool-stamped duration is authoritative for expand hard caps).
    """
    ids: list[str]
    meta_by_id: dict[str, Any] = {}
    if entries is None:
        return []
    if isinstance(entries, Mapping):
        ids = list(entries.keys())
        meta_by_id = dict(entries)
    else:
        ids = [str(x) for x in entries if x]

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in ids:
        aid = _normalize_att_id(raw)
        if not aid or aid in seen:
            continue
        seen.add(aid)
        d: dict[str, Any] | None = None
        if media_store is not None:
            try:
                meta = media_store.get(aid)
            except Exception:  # noqa: BLE001 — inventory best-effort
                meta = None
            if meta is not None:
                if hasattr(meta, "to_dict"):
                    td = meta.to_dict()
                    if isinstance(td, dict):
                        d = dict(td)
                if d is None:
                    d = {
                        "id": aid,
                        "filename": getattr(meta, "filename", None) or "file",
                        "kind": getattr(meta, "kind", None) or "file",
                        "mime": getattr(meta, "mime", None)
                        or "application/octet-stream",
                        "byte_size": getattr(meta, "byte_size", None) or 0,
                        "sandbox_relpath": getattr(meta, "sandbox_relpath", None),
                    }
        if d is None:
            ve = meta_by_id.get(aid)
            if ve is not None:
                d = {
                    "id": aid,
                    "filename": str(_entry_field(ve, "filename", "file") or "file"),
                    "kind": str(_entry_field(ve, "kind", "file") or "file"),
                    "mime": str(
                        _entry_field(ve, "mime", "application/octet-stream")
                        or "application/octet-stream"
                    ),
                    "byte_size": int(_entry_field(ve, "byte_size", 0) or 0),
                    "sandbox_relpath": None,
                }
            else:
                d = {"id": aid}
        # Viewing-entry duration is authoritative when known (expand hard caps).
        ve = meta_by_id.get(aid)
        ve_dur = _entry_field(ve, "duration_s", None) if ve is not None else None
        if ve_dur is not None:
            try:
                d["duration_s"] = float(ve_dur)
            except (TypeError, ValueError):
                pass
        out.append(d)
    return out


def inject_viewing_carrier(
    messages: Sequence[Mapping[str, Any]],
    *,
    glass_by_id: Mapping[str, Mapping[str, Any]] | None,
    viewing_att_ids: Sequence[str] | None,
    media_store: Any | None = None,
    carrier_id: str = VIEWING_CARRIER_ID,
    entries: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Mapping[str, Any]], str | None]:
    """Inject a synthetic full-expand row for the viewing set (before orient).

    When ``viewing_att_ids`` is empty/None, returns shallow-copied messages and
    glass unchanged with ``carrier_id=None`` (status quo).

    Returns ``(messages, glass, carrier_id_or_none)``.
    """
    glass: dict[str, Mapping[str, Any]] = dict(glass_by_id or {})
    meal: list[dict[str, Any]] = [dict(m) for m in messages]

    ids: list[str] = []
    if viewing_att_ids:
        for raw in viewing_att_ids:
            aid = _normalize_att_id(raw)
            if aid and aid not in ids:
                ids.append(aid)
    if not ids:
        return meal, glass, None

    atts = viewing_att_dicts(entries if entries is not None else ids, media_store)
    # Ensure id order matches viewing_att_ids even if meta missing.
    if not atts:
        atts = [{"id": aid} for aid in ids]

    carrier: dict[str, Any] = {
        "role": "user",
        "content": "[host: moment viewing set]",
        "id": carrier_id,
    }
    glass[carrier_id] = {
        "id": carrier_id,
        "role": "user",
        "content": carrier["content"],
        "attachments": atts,
    }

    # Insert immediately before final orient-like row (user, no glass id).
    insert_at = len(meal)
    if meal:
        last = meal[-1]
        if (
            last.get("role") == "user"
            and last.get("id") is None
            and not last.get("_memory_media_ids")
        ):
            insert_at = len(meal) - 1
    meal.insert(insert_at, carrier)
    return meal, glass, carrier_id


__all__ = [
    "MAX_VIEWING_SET",
    "VIEWING_CARRIER_ID",
    "ViewingEntry",
    "add_viewing",
    "clear_viewing",
    "drop_viewing",
    "inject_viewing_carrier",
    "list_viewing",
    "list_viewing_att_ids",
    "viewing_att_dicts",
]
