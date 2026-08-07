"""Conversation store (DM + group social addresses).

Scope: durable CRUD under ``data/conversations/`` (index + by_id/*.json).
In scope: ensure_layout, get, list, ensure_dm, create_group, update,
touch_activity, resolve_address; RLock + atomic JSON; path jail on ids.
Out of scope: HTTP, speak/wait tools, glass session, glass_tail, #131 ACL.

ID conventions (design KD / C12):
- DM: ``dm:<user_id>`` (deterministic; members = [peer])
- Group: ``group:<uuid>`` (minted; members = human participants)
- Null conversation is solo/continuous work (not stored here).
"""

from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

from elyra.config import ElyraPaths
from elyra.identity.layout import (
    USER_ID_RE,
    load_json_object,
    utc_now_iso,
    validate_user_id,
    write_json_atomic,
)

# Path-safe group suffix: UUID hex or operator-seeded short ids (tests).
_GROUP_SUFFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CONVERSATION_TYPES = frozenset({"dm", "group"})

# Sentinel for update() optional kwargs (None is a valid "clear" value).
_UNSET = object()


def validate_conversation_id(conversation_id: str) -> str:
    """Return ``conversation_id`` if well-formed and path-jail safe.

    Raises ValueError for blank, wrong prefix, unsafe suffix, or traversal.
    """
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise ValueError(f"invalid conversation_id: {conversation_id!r}")
    cid = conversation_id.strip()
    if cid.startswith("dm:"):
        peer = cid[3:]
        validate_user_id(peer)
        return f"dm:{peer}"
    if cid.startswith("group:"):
        suffix = cid[6:]
        if not suffix or not _GROUP_SUFFIX_RE.fullmatch(suffix):
            raise ValueError(f"invalid conversation_id: {conversation_id!r}")
        # Reject multi-segment / absolute via Path parts (mirrors user_id jail).
        path = Path(suffix)
        if path.is_absolute() or len(path.parts) != 1:
            raise ValueError(f"invalid conversation_id: {conversation_id!r}")
        return f"group:{suffix}"
    raise ValueError(f"invalid conversation_id: {conversation_id!r}")


def conversation_id_to_filename(conversation_id: str) -> str:
    """Map ``dm:jim`` → ``dm_jim.json`` (``:`` → ``_``)."""
    safe = validate_conversation_id(conversation_id)
    return safe.replace(":", "_") + ".json"


def filename_to_conversation_id(filename: str) -> str | None:
    """Reverse filename mapping; return None if not a known type prefix."""
    name = filename
    if name.endswith(".json"):
        name = name[: -len(".json")]
    if name.startswith("dm_"):
        return f"dm:{name[3:]}"
    if name.startswith("group_"):
        return f"group:{name[6:]}"
    return None


def dm_id_for_user(user_id: str) -> str:
    """Canonical DM conversation id for ``user_id``."""
    return f"dm:{validate_user_id(user_id)}"


def _summary_row(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rec.get("id"),
        "type": rec.get("type"),
        "name": rec.get("name"),
        "members": list(rec.get("members") or []),
        "last_message_at": rec.get("last_message_at"),
        "updated_at": rec.get("updated_at"),
    }


class ConversationsStore:
    """JSON conversation ledger under ``data/conversations/``.

    Thread safety: one ``threading.RLock`` serializes load-mutate-save on this
    instance (same pattern as GoalsStore / UsersStore).
    """

    def __init__(self, paths: ElyraPaths) -> None:
        self._paths = paths
        self._lock = threading.RLock()

    # ── paths ────────────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        return self._paths.data_dir / "conversations"

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    @property
    def by_id_dir(self) -> Path:
        return self.root / "by_id"

    def _record_path(self, conversation_id: str) -> Path:
        """Resolve by_id path with path jail under ``by_id/``."""
        safe = validate_conversation_id(conversation_id)
        filename = conversation_id_to_filename(safe)
        by_id = self.by_id_dir.resolve()
        path = (by_id / filename).resolve()
        if not path.is_relative_to(by_id):
            raise ValueError(f"invalid conversation_id: {conversation_id!r}")
        return path

    # ── layout / load ────────────────────────────────────────────────────

    def ensure_layout(self) -> None:
        """Create ``data/conversations/`` + empty index if missing."""
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            self.by_id_dir.mkdir(parents=True, exist_ok=True)
            if not self.index_path.is_file():
                write_json_atomic(
                    self.index_path,
                    {"schema_version": 1, "conversations": []},
                )

    def _empty_index(self) -> dict[str, Any]:
        return {"schema_version": 1, "conversations": []}

    def _load_index(self) -> dict[str, Any]:
        data = load_json_object(self.index_path)
        if data is None:
            return self._empty_index()
        convs = data.get("conversations")
        if not isinstance(convs, list):
            data["conversations"] = []
        if "schema_version" not in data:
            data["schema_version"] = 1
        return data

    def _load_record(self, conversation_id: str) -> dict[str, Any] | None:
        path = self._record_path(conversation_id)
        data = load_json_object(path)
        if data is None:
            return None
        if not isinstance(data.get("id"), str):
            return None
        return data

    def _upsert_index_summary(self, rec: dict[str, Any]) -> None:
        """Upsert index summary for ``rec`` (caller holds lock).

        Safe to call when the by_id record already exists — used to heal
        dual-write gaps (record on disk, index missing the id).
        """
        cid = rec["id"]
        index = self._load_index()
        summaries = index.setdefault("conversations", [])
        if not isinstance(summaries, list):
            summaries = []
            index["conversations"] = summaries
        summary = _summary_row(rec)
        replaced = False
        for i, row in enumerate(summaries):
            if isinstance(row, dict) and row.get("id") == cid:
                summaries[i] = summary
                replaced = True
                break
        if not replaced:
            summaries.append(summary)
        index["schema_version"] = 1
        write_json_atomic(self.index_path, index)

    def _write_record(self, rec: dict[str, Any]) -> None:
        """Persist full record then index summary (caller holds lock).

        Order: by_id record first, then index. If the index write fails after
        the record succeeds, the exception propagates but the record remains;
        callers that re-enter (``ensure_dm``, ``list`` heal, create-exists)
        re-upsert the index so durable state converges.
        """
        cid = rec["id"]
        path = self._record_path(cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, rec)
        self._upsert_index_summary(rec)

    def _heal_index_for_record(self, rec: dict[str, Any]) -> None:
        """Ensure index lists ``rec`` (no-op if already present and current)."""
        self._upsert_index_summary(rec)

    def _scan_by_id_orphans(self) -> list[dict[str, Any]]:
        """Load by_id records whose ids are missing from the index."""
        index = self._load_index()
        known: set[str] = set()
        for row in index.get("conversations") or []:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                known.add(row["id"])
        orphans: list[dict[str, Any]] = []
        by_id = self.by_id_dir
        if not by_id.is_dir():
            return orphans
        for child in by_id.iterdir():
            if not child.is_file() or child.suffix != ".json":
                continue
            cid = filename_to_conversation_id(child.name)
            if cid is None or cid in known:
                continue
            try:
                validate_conversation_id(cid)
            except ValueError:
                continue
            data = load_json_object(child)
            if data is None or not isinstance(data.get("id"), str):
                continue
            orphans.append(data)
        return orphans

    def _heal_index_orphans(self) -> int:
        """Upsert any by_id records missing from index. Return heal count."""
        orphans = self._scan_by_id_orphans()
        for rec in orphans:
            self._upsert_index_summary(rec)
        return len(orphans)

    # ── public API ───────────────────────────────────────────────────────

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        """Return full conversation dict or None if missing/invalid."""
        with self._lock:
            try:
                rec = self._load_record(conversation_id)
            except ValueError:
                return None
            return dict(rec) if rec is not None else None

    def list(
        self,
        *,
        member_user_id: str | None = None,
        type: str | None = None,  # noqa: A002 — matches design API
    ) -> list[dict[str, Any]]:
        """List conversation summaries; optional member / type filter.

        Newest-first by ``last_message_at`` then ``updated_at``, id tie-break.
        """
        if type is not None and type not in _CONVERSATION_TYPES:
            raise ValueError(f"invalid conversation type: {type!r}")
        if member_user_id is not None:
            member_user_id = validate_user_id(member_user_id)

        with self._lock:
            self.ensure_layout()
            # Heal dual-write orphans (by_id present, index missing) so list
            # remains complete after a partial write failure.
            self._heal_index_orphans()
            index = self._load_index()
            out: list[dict[str, Any]] = []
            for row in index.get("conversations") or []:
                if not isinstance(row, dict):
                    continue
                if type is not None and row.get("type") != type:
                    continue
                if member_user_id is not None:
                    members = row.get("members") or []
                    if not isinstance(members, list) or member_user_id not in members:
                        continue
                out.append(dict(row))
            out.sort(
                key=lambda r: (
                    str(r.get("last_message_at") or r.get("updated_at") or ""),
                    str(r.get("id") or ""),
                ),
                reverse=True,
            )
            return out

    def ensure_dm(self, user_id: str) -> dict[str, Any]:
        """Idempotent: create ``dm:<user_id>`` if missing; return full record.

        When the by_id record already exists, re-upserts the index summary so
        a prior dual-write gap (record ok, index failed) is healed.
        """
        peer = validate_user_id(user_id)
        cid = dm_id_for_user(peer)
        with self._lock:
            self.ensure_layout()
            existing = self._load_record(cid)
            if existing is not None:
                self._heal_index_for_record(existing)
                return dict(existing)
            now = utc_now_iso()
            rec: dict[str, Any] = {
                "id": cid,
                "type": "dm",
                "members": [peer],
                "name": None,
                "description": None,
                "created_at": now,
                "updated_at": now,
                "last_message_at": None,
            }
            self._write_record(rec)
            return dict(rec)

    def create_group(
        self,
        *,
        name: str,
        members: list[str],
        description: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a group conversation. Members must be non-empty valid user_ids."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        if not isinstance(members, list) or not members:
            raise ValueError("members must be a non-empty list of user_ids")
        clean_members: list[str] = []
        seen: set[str] = set()
        for m in members:
            uid = validate_user_id(m)
            if uid not in seen:
                seen.add(uid)
                clean_members.append(uid)

        if conversation_id is not None:
            cid = validate_conversation_id(conversation_id)
            if not cid.startswith("group:"):
                raise ValueError(
                    f"create_group conversation_id must be group:…, got {cid!r}"
                )
        else:
            cid = f"group:{uuid.uuid4().hex}"

        with self._lock:
            self.ensure_layout()
            existing = self._load_record(cid)
            if existing is not None:
                # Heal index if dual-write left an orphan, then fail closed.
                self._heal_index_for_record(existing)
                raise ValueError(f"conversation_id already exists: {cid!r}")
            if description is None:
                desc: str | None = None
            elif isinstance(description, str):
                desc = description.strip() or None
            else:
                raise ValueError("description must be str or None")
            now = utc_now_iso()
            rec: dict[str, Any] = {
                "id": cid,
                "type": "group",
                "members": clean_members,
                "name": name.strip(),
                "description": desc,
                "created_at": now,
                "updated_at": now,
                "last_message_at": None,
            }
            self._write_record(rec)
            return dict(rec)

    def update(
        self,
        conversation_id: str,
        *,
        name: Any = _UNSET,
        description: Any = _UNSET,
        members: Any = _UNSET,
    ) -> dict[str, Any]:
        """Partial update of name / description / members. Raises if missing.

        At least one of ``name`` / ``description`` / ``members`` must be set;
        a no-op call raises ``ValueError`` (avoids churning ``updated_at``).
        """
        cid = validate_conversation_id(conversation_id)
        if name is _UNSET and description is _UNSET and members is _UNSET:
            raise ValueError("no update fields")
        with self._lock:
            self.ensure_layout()
            rec = self._load_record(cid)
            if rec is None:
                raise KeyError(f"conversation not found: {cid!r}")
            rec = dict(rec)
            if name is not _UNSET:
                if name is None:
                    if rec.get("type") == "group":
                        raise ValueError("group name cannot be null")
                    rec["name"] = None
                elif isinstance(name, str) and name.strip():
                    rec["name"] = name.strip()
                else:
                    raise ValueError("name must be a non-empty string or None")
            if description is not _UNSET:
                if description is None:
                    rec["description"] = None
                elif isinstance(description, str):
                    rec["description"] = description.strip() or None
                else:
                    raise ValueError("description must be str or None")
            if members is not _UNSET:
                if not isinstance(members, list) or not members:
                    raise ValueError("members must be a non-empty list of user_ids")
                clean: list[str] = []
                seen: set[str] = set()
                for m in members:
                    uid = validate_user_id(m)
                    if uid not in seen:
                        seen.add(uid)
                        clean.append(uid)
                if rec.get("type") == "dm":
                    # DM members remain the single peer; reject multi-member.
                    if len(clean) != 1 or clean[0] != cid[3:]:
                        raise ValueError(
                            "dm members must be exactly the peer user_id"
                        )
                rec["members"] = clean
            rec["updated_at"] = utc_now_iso()
            self._write_record(rec)
            return dict(rec)

    def touch_activity(
        self,
        conversation_id: str,
        *,
        at: str | None = None,
    ) -> None:
        """Update ``last_message_at`` + ``updated_at`` (message path)."""
        cid = validate_conversation_id(conversation_id)
        stamp = at if isinstance(at, str) and at.strip() else utc_now_iso()
        with self._lock:
            self.ensure_layout()
            rec = self._load_record(cid)
            if rec is None:
                # No-op if conversation unknown (lazy ensure is caller's job).
                return
            rec = dict(rec)
            rec["last_message_at"] = stamp
            rec["updated_at"] = stamp
            self._write_record(rec)

    def resolve_address(
        self,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> str | None:
        """Normalize speak/wait target → conversation_id or None.

        Prefer explicit ``conversation_id`` when non-blank (validated).
        Else DM shorthand from ``user_id`` (does not create the DM).
        Blank / missing both → None (solo).
        """
        if isinstance(conversation_id, str) and conversation_id.strip():
            return validate_conversation_id(conversation_id)
        if isinstance(user_id, str) and user_id.strip():
            return dm_id_for_user(user_id.strip())
        return None


# Re-export type hint helper (not used at runtime).
ConversationType = Literal["dm", "group"]

__all__ = [
    "ConversationsStore",
    "ConversationType",
    "validate_conversation_id",
    "conversation_id_to_filename",
    "filename_to_conversation_id",
    "dm_id_for_user",
    "USER_ID_RE",
]
