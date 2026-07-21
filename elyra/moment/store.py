"""Moment metadata index and append-only beat tapes.

Scope: open/close moments, append beats, list open, recover-as-interrupted.
In scope: ``data/moments/index.jsonl`` + ``data/moments/<id>.jsonl``.
Out of scope: do-loop, wakes, glass, mid-moment resume.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths
from elyra.moment.types import (
    BEAT_TYPES,
    SCHEMA_VERSION,
    STOP_REASONS,
    BeatDict,
    MomentMeta,
)

# Safe single path segment for moment ids (uuid hex + common uuid form).
_MOMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

INDEX_FILENAME = "index.jsonl"
# Stems whose ``<id>.jsonl`` would collide with the global moment index file.
_RESERVED_MOMENT_STEMS = frozenset({"index"})


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _validate_moment_id(moment_id: str) -> str:
    """Return ``moment_id`` if it is a single safe, non-reserved path segment.

    Rejects path escapes and stems that would make the beat tape path equal
    the moment index (``index.jsonl``). Comparison is case-insensitive so
    case-insensitive filesystems cannot alias ``Index`` onto the index file.
    """
    if not isinstance(moment_id, str) or not _MOMENT_ID_RE.fullmatch(moment_id):
        raise ValueError(f"invalid moment_id: {moment_id!r}")
    path = Path(moment_id)
    if path.is_absolute() or len(path.parts) != 1:
        raise ValueError(f"invalid moment_id: {moment_id!r}")
    if moment_id.lower() in _RESERVED_MOMENT_STEMS:
        raise ValueError(f"invalid moment_id: {moment_id!r}")
    # Defense in depth: tape basename must never be the index file name.
    if f"{moment_id}.jsonl".lower() == INDEX_FILENAME.lower():
        raise ValueError(f"invalid moment_id: {moment_id!r}")
    return moment_id


def _validate_stop_reason(stop_reason: str) -> str:
    if not isinstance(stop_reason, str) or stop_reason not in STOP_REASONS:
        raise ValueError(f"invalid stop_reason: {stop_reason!r}")
    return stop_reason


class MomentStore:
    """Persist moment envelopes and ordered beat tapes under ``data/moments/``."""

    def __init__(self, paths: ElyraPaths) -> None:
        self._paths = paths

    @property
    def moments_dir(self) -> Path:
        return self._paths.data_dir / "moments"

    @property
    def index_path(self) -> Path:
        return self.moments_dir / INDEX_FILENAME

    def tape_path(self, moment_id: str) -> Path:
        """Path to ``data/moments/<id>.jsonl`` (moment_id must be a single segment)."""
        safe_id = _validate_moment_id(moment_id)
        root = self.moments_dir.resolve()
        path = (root / f"{safe_id}.jsonl").resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"invalid moment_id: {moment_id!r}")
        return path

    def _ensure_moments_dir(self) -> None:
        self.moments_dir.mkdir(parents=True, exist_ok=True)

    def open_moment(
        self,
        *,
        why_now: str,
        user_id: str | None = None,
        wake_id: str | None = None,
        goal_ids: list[str] | None = None,
        task_ids: list[str] | None = None,
        moment_id: str | None = None,
    ) -> str:
        """Create open moment meta (``ended_at`` null) and return its id.

        Does not create a beat tape until the first ``append_beat``.
        """
        mid = _validate_moment_id(moment_id) if moment_id is not None else str(uuid.uuid4())
        self._ensure_moments_dir()

        existing = self._load_index_by_id()
        if mid in existing:
            raise ValueError(f"moment already exists: {mid!r}")

        meta: MomentMeta = {
            "schema_version": SCHEMA_VERSION,
            "id": mid,
            "started_at": _now(),
            "ended_at": None,
            "why_now": why_now,
            "user_id": user_id,
            "goal_ids": list(goal_ids or []),
            "task_ids": list(task_ids or []),
            "skills_used": [],
            "stop_reason": None,
            "wake_id": wake_id,
            "hop_count": 0,
        }
        self._append_index_line(meta)
        return mid

    def append_beat(self, moment_id: str, beat: BeatDict) -> None:
        """Append one beat dict to the moment tape.

        Requires ``type`` in the beat (one of the known beat types). Adds ``ts``
        when missing. Raises if the moment is unknown or already closed.
        """
        mid = _validate_moment_id(moment_id)
        meta = self.get_moment(mid)
        if meta is None:
            raise KeyError(f"unknown moment_id: {mid!r}")
        if meta.get("ended_at") is not None:
            raise ValueError(f"moment already closed: {mid!r}")

        if not isinstance(beat, dict):
            raise TypeError("beat must be a dict")
        beat_type = beat.get("type")
        if not isinstance(beat_type, str) or beat_type not in BEAT_TYPES:
            raise ValueError(f"invalid beat type: {beat_type!r}")

        row = dict(beat)
        if "ts" not in row or not row["ts"]:
            row["ts"] = _now()

        self._ensure_moments_dir()
        path = self.tape_path(mid)
        self._ensure_trailing_newline(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def close_moment(
        self,
        moment_id: str,
        stop_reason: str,
        *,
        hop_count: int | None = None,
        skills_used: list[str] | None = None,
    ) -> MomentMeta:
        """Close an open moment; idempotent if already closed.

        When already closed, returns existing meta without rewriting (safe no-op).
        When open, sets ``ended_at``, ``stop_reason``, and optional hop/skills.
        """
        mid = _validate_moment_id(moment_id)
        reason = _validate_stop_reason(stop_reason)

        by_id = self._load_index_by_id()
        meta = by_id.get(mid)
        if meta is None:
            raise KeyError(f"unknown moment_id: {mid!r}")

        if meta.get("ended_at") is not None:
            return dict(meta)

        updated: MomentMeta = dict(meta)
        updated["ended_at"] = _now()
        updated["stop_reason"] = reason
        if hop_count is not None:
            updated["hop_count"] = int(hop_count)
        if skills_used is not None:
            updated["skills_used"] = list(skills_used)
        # Preserve schema_version if somehow missing on older lines.
        updated.setdefault("schema_version", SCHEMA_VERSION)

        by_id[mid] = updated
        self._rewrite_index(by_id)
        return dict(updated)

    def list_open_moments(self) -> list[MomentMeta]:
        """Return moments whose latest index record has ``ended_at`` null."""
        open_list: list[MomentMeta] = []
        for meta in self._load_index_by_id().values():
            if meta.get("ended_at") is None:
                open_list.append(dict(meta))
        return open_list

    def list_moments(
        self,
        *,
        limit: int | None = None,
        open_only: bool = False,
    ) -> list[MomentMeta]:
        """Return moments newest-first (by ``started_at``).

        Parameters
        ----------
        limit:
            Max rows after sort (None = all).
        open_only:
            When True, only moments with ``ended_at`` null.
        """
        rows: list[MomentMeta] = []
        for meta in self._load_index_by_id().values():
            if open_only and meta.get("ended_at") is not None:
                continue
            rows.append(dict(meta))
        rows.sort(key=lambda m: str(m.get("started_at") or ""), reverse=True)
        if limit is not None and limit >= 0:
            rows = rows[: int(limit)]
        return rows

    def recover_open_moments(self) -> list[str]:
        """Close all open moments with ``stop_reason=interrupted``.

        Single index load + one rewrite for all orphans. Returns the ids that
        were closed. Safe to call when none are open.
        """
        by_id = self._load_index_by_id()
        closed: list[str] = []
        ended = _now()
        for mid, meta in list(by_id.items()):
            if meta.get("ended_at") is not None:
                continue
            updated: MomentMeta = dict(meta)
            updated["ended_at"] = ended
            updated["stop_reason"] = "interrupted"
            updated.setdefault("schema_version", SCHEMA_VERSION)
            by_id[mid] = updated
            closed.append(mid)
        if closed:
            self._rewrite_index(by_id)
        return closed

    def get_moment(self, moment_id: str) -> MomentMeta | None:
        """Latest index record for ``moment_id``, or None if unknown."""
        mid = _validate_moment_id(moment_id)
        return self._load_index_by_id().get(mid)

    def list_beats(self, moment_id: str) -> list[BeatDict]:
        """Return ordered beats for a moment (empty if tape missing)."""
        path = self.tape_path(moment_id)
        if not path.is_file():
            return []
        rows: list[BeatDict] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    # --- index I/O ---------------------------------------------------------

    def _ensure_trailing_newline(self, path: Path) -> None:
        """If ``path`` exists, is non-empty, and lacks a trailing ``\\n``, append one.

        Avoids gluing a new JSONL record onto a partial line left by a crash
        mid-write so the new record remains parseable.
        """
        if not path.is_file() or path.stat().st_size == 0:
            return
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            last = handle.read(1)
        if last != b"\n":
            with path.open("ab") as handle:
                handle.write(b"\n")

    def _append_index_line(self, meta: MomentMeta) -> None:
        self._ensure_moments_dir()
        self._ensure_trailing_newline(self.index_path)
        with self.index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(meta, ensure_ascii=False) + "\n")

    def _load_index_by_id(self) -> dict[str, MomentMeta]:
        """Fold index.jsonl last-write-wins by ``id``."""
        path = self.index_path
        if not path.is_file():
            return {}
        by_id: dict[str, MomentMeta] = {}
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row: dict[str, Any] = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mid = row.get("id")
                if not isinstance(mid, str) or not mid:
                    continue
                by_id[mid] = row  # type: ignore[assignment]
        return by_id

    def _rewrite_index(self, by_id: dict[str, MomentMeta]) -> None:
        """Rewrite index with one line per moment (stable insertion order)."""
        self._ensure_moments_dir()
        tmp = self.index_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for meta in by_id.values():
                handle.write(json.dumps(meta, ensure_ascii=False) + "\n")
        tmp.replace(self.index_path)
