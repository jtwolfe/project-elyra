"""Sticky directed-keep tray — types, disk I/O, pure merge/TTL/LRU helpers.

Module owns types + load/save + pure helpers only (KD-TRAY-SOT).
In-process source of truth is ``TraversalRegistry`` (not this module).
Persist path: ``data/runtime/directed_keep_tray.json``.

Instance-global today (one tray for the process). Future multi-user residual
(#131 A): optional key by ``conversation_id`` or ``"_solo"`` — **hook only**;
no per-conversation entry field, no meal directed_keep filter by conversation
in the C12 stack (KD10).

Out of scope: meal packing, soft-recall (S5), graph UX (S6).
Replace/remove for model keep updates live on DirectedKeepTray (merge_confirm + remove_ids).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from elyra.config import ElyraPaths
from elyra.memory.types import parse_iso_z, to_iso_z, utc_now_iso

_LOG = logging.getLogger(__name__)

DIRECTED_KEEP_TRAY_REL = Path("runtime") / "directed_keep_tray.json"

# Host-owned defaults (design C.5 / OQ3).
DEFAULT_HARD_TTL_HOURS = 24.0
DEFAULT_SOFT_TTL_HOURS = 3.0
DEFAULT_ENTRY_CAP = 32


@dataclass
class KeepTrayEntry:
    """One pinned atom in the instance directed-keep tray."""

    atom_id: str
    confirmed_at: str
    last_reinforced_at: str
    source_session_id: str | None = None
    source_moment_id: str | None = None  # audit only — NOT a meal compose filter
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "atom_id": self.atom_id,
            "confirmed_at": self.confirmed_at,
            "last_reinforced_at": self.last_reinforced_at,
        }
        if self.source_session_id is not None:
            d["source_session_id"] = self.source_session_id
        if self.source_moment_id is not None:
            d["source_moment_id"] = self.source_moment_id
        if self.note is not None:
            d["note"] = self.note
        return d

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> KeepTrayEntry | None:
        aid = str(raw.get("atom_id") or "").strip()
        if not aid:
            return None
        confirmed = str(raw.get("confirmed_at") or "").strip() or utc_now_iso()
        reinforced = (
            str(raw.get("last_reinforced_at") or "").strip() or confirmed
        )
        note = raw.get("note")
        return cls(
            atom_id=aid,
            confirmed_at=confirmed,
            last_reinforced_at=reinforced,
            source_session_id=(
                str(raw["source_session_id"])
                if raw.get("source_session_id") is not None
                else None
            ),
            source_moment_id=(
                str(raw["source_moment_id"])
                if raw.get("source_moment_id") is not None
                else None
            ),
            note=str(note) if note is not None else None,
        )


@dataclass
class DirectedKeepTray:
    """Instance-local sticky keep tray (registry-owned live instance)."""

    entries: list[KeepTrayEntry] = field(default_factory=list)
    walk_summary_nl: str | None = None
    # Policy mirrors (persisted for inspect; host uses settings on mutate).
    max_age_hard_hours: float = DEFAULT_HARD_TTL_HOURS
    soft_evict_after_hours: float = DEFAULT_SOFT_TTL_HOURS
    entry_cap: int = DEFAULT_ENTRY_CAP

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "walk_summary_nl": self.walk_summary_nl,
            "policy": {
                "max_age_hard_hours": self.max_age_hard_hours,
                "soft_evict_after_hours": self.soft_evict_after_hours,
                "entry_cap": self.entry_cap,
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> DirectedKeepTray:
        if not raw or not isinstance(raw, Mapping):
            return cls()
        entries: list[KeepTrayEntry] = []
        for item in raw.get("entries") or ():
            if not isinstance(item, Mapping):
                continue
            ent = KeepTrayEntry.from_dict(item)
            if ent is not None:
                entries.append(ent)
        policy = raw.get("policy") if isinstance(raw.get("policy"), Mapping) else {}
        hard = _float_or(
            policy.get("max_age_hard_hours"), DEFAULT_HARD_TTL_HOURS
        )
        soft = _float_or(
            policy.get("soft_evict_after_hours"), DEFAULT_SOFT_TTL_HOURS
        )
        cap = _int_or(policy.get("entry_cap"), DEFAULT_ENTRY_CAP)
        summary = raw.get("walk_summary_nl")
        return cls(
            entries=entries,
            walk_summary_nl=str(summary).strip() if summary else None,
            max_age_hard_hours=hard,
            soft_evict_after_hours=soft,
            entry_cap=max(1, cap),
        )

    def atom_ids(self) -> list[str]:
        return [e.atom_id for e in self.entries]

    def entry_map(self) -> dict[str, KeepTrayEntry]:
        return {e.atom_id: e for e in self.entries}

    def ages_seconds(self, *, now: str | datetime | None = None) -> dict[str, float]:
        """Age of each entry by ``last_reinforced_at`` (seconds)."""
        now_dt = parse_iso_z(now or utc_now_iso())
        out: dict[str, float] = {}
        for e in self.entries:
            try:
                t = parse_iso_z(e.last_reinforced_at)
            except (TypeError, ValueError):
                out[e.atom_id] = 0.0
                continue
            out[e.atom_id] = max(0.0, (now_dt - t).total_seconds())
        return out

    def soft_aged_ids(
        self,
        *,
        now: str | datetime | None = None,
        soft_hours: float | None = None,
    ) -> set[str]:
        soft_h = (
            float(soft_hours)
            if soft_hours is not None
            else float(self.soft_evict_after_hours)
        )
        soft_s = max(0.0, soft_h) * 3600.0
        ages = self.ages_seconds(now=now)
        return {aid for aid, age in ages.items() if age >= soft_s}

    def drop_hard_ttl(
        self,
        *,
        now: str | datetime | None = None,
        hard_hours: float | None = None,
    ) -> int:
        """Drop entries older than hard max age. Returns count dropped."""
        hard_h = (
            float(hard_hours)
            if hard_hours is not None
            else float(self.max_age_hard_hours)
        )
        if hard_h <= 0:
            return 0
        hard_s = hard_h * 3600.0
        now_dt = parse_iso_z(now or utc_now_iso())
        kept: list[KeepTrayEntry] = []
        dropped = 0
        for e in self.entries:
            try:
                t = parse_iso_z(e.last_reinforced_at)
            except (TypeError, ValueError):
                kept.append(e)
                continue
            age = (now_dt - t).total_seconds()
            if age > hard_s:
                dropped += 1
                continue
            kept.append(e)
        self.entries = kept
        return dropped

    def lru_trim(self, *, entry_cap: int | None = None) -> int:
        """Drop oldest-reinforced entries over cap. Returns count dropped."""
        cap = int(entry_cap) if entry_cap is not None else int(self.entry_cap)
        cap = max(0, cap)
        if len(self.entries) <= cap:
            return 0
        ranked = sorted(
            self.entries,
            key=lambda e: (
                _safe_ts(e.last_reinforced_at),
                e.atom_id,
            ),
        )
        # Oldest first → drop prefix; keep newest `cap`.
        drop_n = len(ranked) - cap
        keep_set = {e.atom_id for e in ranked[drop_n:]}
        # Preserve relative order of survivors as currently listed.
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.atom_id in keep_set]
        return before - len(self.entries)

    def remove_ids(self, ids: Sequence[str]) -> int:
        """Drop listed atom ids from tray. Returns count removed.

        Unknown / blank ids are ignored (no error). Order of survivors is
        preserved. Does not touch ``walk_summary_nl`` or policy fields.
        """
        drop = {
            str(raw).strip()
            for raw in (ids or ())
            if raw is not None and str(raw).strip()
        }
        if not drop:
            return 0
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.atom_id not in drop]
        return before - len(self.entries)

    def merge_confirm(
        self,
        keep_ids: Sequence[str],
        *,
        now: str | None = None,
        session_id: str | None = None,
        moment_id: str | None = None,
        walk_summary_nl: str | None = None,
        hard_hours: float | None = None,
        soft_hours: float | None = None,
        entry_cap: int | None = None,
    ) -> DirectedKeepTray:
        """Union keep_ids into tray (KD-MRG). Mutates self; returns self.

        New ids get confirmed_at=now; re-confirmed bump last_reinforced_at.
        Then hard-TTL drop + LRU trim under entry_cap.
        """
        now_s = now or utc_now_iso()
        if hard_hours is not None:
            self.max_age_hard_hours = float(hard_hours)
        if soft_hours is not None:
            self.soft_evict_after_hours = float(soft_hours)
        if entry_cap is not None:
            self.entry_cap = max(1, int(entry_cap))

        by_id = self.entry_map()
        for raw_id in keep_ids:
            aid = str(raw_id or "").strip()
            if not aid:
                continue
            existing = by_id.get(aid)
            if existing is not None:
                existing.last_reinforced_at = now_s
                if session_id is not None:
                    existing.source_session_id = session_id
                if moment_id is not None:
                    existing.source_moment_id = moment_id
            else:
                ent = KeepTrayEntry(
                    atom_id=aid,
                    confirmed_at=now_s,
                    last_reinforced_at=now_s,
                    source_session_id=session_id,
                    source_moment_id=moment_id,
                )
                self.entries.append(ent)
                by_id[aid] = ent

        if walk_summary_nl is not None:
            text = str(walk_summary_nl).strip()
            self.walk_summary_nl = text or None

        self.drop_hard_ttl(now=now_s, hard_hours=self.max_age_hard_hours)
        self.lru_trim(entry_cap=self.entry_cap)
        return self

    def meal_keep_ids(
        self,
        *,
        now: str | datetime | None = None,
        hard_hours: float | None = None,
        soft_hours: float | None = None,
    ) -> tuple[list[str], str | None, set[str]]:
        """Ids for meal pack: hard-TTL applied; soft-aged last (cut first).

        Returns ``(ids, walk_summary_nl, soft_aged_ids)``. No moment filter.
        Order: non-soft (newest reinforced first) then soft-aged (newest first).
        """
        self.drop_hard_ttl(now=now, hard_hours=hard_hours)
        soft = self.soft_aged_ids(now=now, soft_hours=soft_hours)
        young = [e for e in self.entries if e.atom_id not in soft]
        aged = [e for e in self.entries if e.atom_id in soft]
        young.sort(
            key=lambda e: (_safe_ts(e.last_reinforced_at), e.atom_id),
            reverse=True,
        )
        aged.sort(
            key=lambda e: (_safe_ts(e.last_reinforced_at), e.atom_id),
            reverse=True,
        )
        ids = [e.atom_id for e in young] + [e.atom_id for e in aged]
        return ids, self.walk_summary_nl, soft

    def inspect_block(
        self, *, now: str | datetime | None = None
    ) -> dict[str, Any]:
        """Tray fields for /api/memory/context inspect."""
        now_s = to_iso_z(now) if now is not None else utc_now_iso()
        ages = self.ages_seconds(now=now_s)
        soft = self.soft_aged_ids(now=now_s)
        return {
            "entry_count": len(self.entries),
            "walk_summary_nl": self.walk_summary_nl,
            "policy": {
                "max_age_hard_hours": self.max_age_hard_hours,
                "soft_evict_after_hours": self.soft_evict_after_hours,
                "entry_cap": self.entry_cap,
            },
            "entries": [
                {
                    "atom_id": e.atom_id,
                    "confirmed_at": e.confirmed_at,
                    "last_reinforced_at": e.last_reinforced_at,
                    "age_seconds": ages.get(e.atom_id, 0.0),
                    "soft_aged": e.atom_id in soft,
                    "source_moment_id": e.source_moment_id,
                    "source_session_id": e.source_session_id,
                }
                for e in self.entries
            ],
        }


def tray_runtime_path(data_dir: Path) -> Path:
    return Path(data_dir) / DIRECTED_KEEP_TRAY_REL


def load_directed_keep_tray(
    paths: ElyraPaths | Path | None = None,
) -> DirectedKeepTray:
    """Load tray from ``data/runtime/directed_keep_tray.json``; missing → empty."""
    path = _resolve_tray_path(paths)
    if path is None or not path.is_file():
        return DirectedKeepTray()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("directed_keep_tray load failed (%s): %s", path, exc)
        return DirectedKeepTray()
    if not isinstance(raw, dict):
        return DirectedKeepTray()
    return DirectedKeepTray.from_dict(raw)


def save_directed_keep_tray(
    tray: DirectedKeepTray,
    paths: ElyraPaths | Path | None = None,
) -> Path | None:
    """Atomic write tray JSON. Returns path written, or None if no paths."""
    path = _resolve_tray_path(paths)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(tray.to_dict(), ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(
        f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def merge_confirm(
    tray: DirectedKeepTray,
    keep_ids: Sequence[str],
    *,
    now: str | None = None,
    session_id: str | None = None,
    moment_id: str | None = None,
    walk_summary_nl: str | None = None,
    hard_hours: float | None = None,
    soft_hours: float | None = None,
    entry_cap: int | None = None,
) -> DirectedKeepTray:
    """Pure-ish helper: merge keep_ids into tray (mutates and returns tray)."""
    return tray.merge_confirm(
        keep_ids,
        now=now,
        session_id=session_id,
        moment_id=moment_id,
        walk_summary_nl=walk_summary_nl,
        hard_hours=hard_hours,
        soft_hours=soft_hours,
        entry_cap=entry_cap,
    )


def seed_tray_from_keep_ids(
    keep_ids: Sequence[str],
    *,
    now: str | None = None,
    session_id: str | None = None,
    moment_id: str | None = None,
    walk_summary_nl: str | None = None,
    hard_hours: float = DEFAULT_HARD_TTL_HOURS,
    soft_hours: float = DEFAULT_SOFT_TTL_HOURS,
    entry_cap: int = DEFAULT_ENTRY_CAP,
) -> DirectedKeepTray:
    """Build a tray from a thin ConfirmedKeepSnapshot-style id list."""
    tray = DirectedKeepTray(
        max_age_hard_hours=hard_hours,
        soft_evict_after_hours=soft_hours,
        entry_cap=entry_cap,
    )
    return tray.merge_confirm(
        keep_ids,
        now=now,
        session_id=session_id,
        moment_id=moment_id,
        walk_summary_nl=walk_summary_nl,
        hard_hours=hard_hours,
        soft_hours=soft_hours,
        entry_cap=entry_cap,
    )


# ── internals ───────────────────────────────────────────────────────────────


def _resolve_tray_path(paths: ElyraPaths | Path | None) -> Path | None:
    if paths is None:
        return None
    if isinstance(paths, Path):
        # Treat bare Path as data_dir.
        return tray_runtime_path(paths)
    data_dir = getattr(paths, "data_dir", None)
    if data_dir is None:
        return None
    return tray_runtime_path(Path(data_dir))


def _float_or(raw: Any, default: float) -> float:
    if raw is None:
        return default
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return v


def _int_or(raw: Any, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _safe_ts(value: str) -> float:
    try:
        return parse_iso_z(value).timestamp()
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "DEFAULT_ENTRY_CAP",
    "DEFAULT_HARD_TTL_HOURS",
    "DEFAULT_SOFT_TTL_HOURS",
    "DIRECTED_KEEP_TRAY_REL",
    "DirectedKeepTray",
    "KeepTrayEntry",
    "load_directed_keep_tray",
    "merge_confirm",
    "save_directed_keep_tray",
    "seed_tray_from_keep_ids",
    "tray_runtime_path",
]

