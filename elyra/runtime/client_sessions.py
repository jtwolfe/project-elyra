"""Per-client glass session registry (concurrent dogfood principals).

Scope: durable map ``data/runtime/client_sessions.json`` keyed by client_id →
``{user_id, conversation_id, view_mode, updated_at}``. One-shot legacy import
from ``glass_session.json`` (KD22). Endpoint-class create gating is caller's
job (KD25); this module provides load/normalize/put/prune under RLock.

Out of scope: HTTP headers, speak/wait conversation stamp, #131 real auth.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from elyra.config import ElyraPaths
from elyra.identity.layout import (
    load_json_object,
    utc_now_iso,
    validate_user_id,
    write_json_atomic,
)

_LOG = logging.getLogger(__name__)

CLIENT_SESSIONS_REL = Path("runtime") / "client_sessions.json"
LEGACY_GLASS_SESSION_REL = Path("runtime") / "glass_session.json"

SCHEMA_VERSION = 1
DEFAULT_USER_ID = "operator"
DEFAULT_VIEW_MODE = "conversation"
VALID_VIEW_MODES = frozenset({"conversation", "all"})

# Dogfood prune (design §7A.3).
CLIENT_SESSION_MAX = 32
CLIENT_SESSION_TTL_DAYS = 7

# client_id: UUID or path-safe token; reject empty / overly long / traversal.
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CLIENT_ID_MAX_LEN = 80


class InvalidClientId(ValueError):
    """Header present but not a valid client_id (→ HTTP 400)."""


def validate_client_id(client_id: str) -> str:
    """Return stripped client_id if valid; raise InvalidClientId otherwise."""
    if not isinstance(client_id, str):
        raise InvalidClientId("invalid_client_id")
    raw = client_id.strip()
    if not raw or len(raw) > _CLIENT_ID_MAX_LEN:
        raise InvalidClientId("invalid_client_id")
    if not _CLIENT_ID_RE.fullmatch(raw):
        raise InvalidClientId("invalid_client_id")
    path = Path(raw)
    if path.is_absolute() or len(path.parts) != 1:
        raise InvalidClientId("invalid_client_id")
    return raw


def parse_client_id_header(raw: str | None) -> str | None:
    """Parse ``X-Elyra-Client`` value.

    Returns:
      - validated client_id string when present and valid
      - None when missing / blank (treat as missing header)

    Raises:
      InvalidClientId when present but malformed (path, length, charset).
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise InvalidClientId("invalid_client_id")
    stripped = raw.strip()
    if not stripped:
        return None
    return validate_client_id(stripped)


def mint_client_id() -> str:
    """Server-side UUID v4 for missing-header bind paths (KD25)."""
    return str(uuid.uuid4())


def _empty_store() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "clients": {}}


def _safe_user_id(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip():
        try:
            return validate_user_id(raw.strip())
        except ValueError:
            pass
    return DEFAULT_USER_ID


def _safe_view_mode(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip() in VALID_VIEW_MODES:
        return raw.strip()
    return DEFAULT_VIEW_MODE


class ClientSessionsRegistry:
    """In-memory + disk map of client_id → session fields.

    Thread safety: one ``threading.RLock`` serializes load-mutate-save.
    Callers pass ``allow_create`` per KD25 endpoint class.
    """

    def __init__(
        self,
        paths: ElyraPaths,
        *,
        ensure_dm: Callable[[str], dict[str, Any]] | None = None,
        max_clients: int = CLIENT_SESSION_MAX,
        ttl_days: int = CLIENT_SESSION_TTL_DAYS,
    ) -> None:
        self._paths = paths
        self._lock = threading.RLock()
        self._ensure_dm = ensure_dm
        self._max_clients = max(1, int(max_clients))
        self._ttl_days = max(0, int(ttl_days))
        # In-process cache; RMW with disk under lock. Reload when file mtime
        # changes (full reset / external clear) so handler instance stays coherent.
        self._clients: dict[str, dict[str, Any]] = {}
        self._file_mtime_ns: int | None = None

    @property
    def path(self) -> Path:
        return self._paths.data_dir / CLIENT_SESSIONS_REL

    @property
    def legacy_path(self) -> Path:
        return self._paths.data_dir / LEGACY_GLASS_SESSION_REL

    def _dm_id(self, user_id: str) -> str:
        uid = _safe_user_id(user_id)
        if self._ensure_dm is not None:
            try:
                rec = self._ensure_dm(uid)
                cid = rec.get("id") if isinstance(rec, dict) else None
                if isinstance(cid, str) and cid.strip():
                    return cid.strip()
            except Exception as exc:  # noqa: BLE001 — soft; fall back
                _LOG.warning("ensure_dm failed for %s: %s", uid, exc)
        return f"dm:{uid}"

    def _normalize_entry(
        self,
        entry: dict[str, Any] | None,
        *,
        user_id: str | None = None,
        conversation_id: str | None = None,
        view_mode: str | None = None,
        touch: bool = True,
    ) -> dict[str, Any]:
        """Return full session object (never partial wipe)."""
        base = dict(entry) if isinstance(entry, dict) else {}
        uid = _safe_user_id(user_id if user_id is not None else base.get("user_id"))
        vm = (
            _safe_view_mode(view_mode)
            if view_mode is not None
            else _safe_view_mode(base.get("view_mode"))
        )

        raw_cid = (
            conversation_id
            if conversation_id is not None
            else base.get("conversation_id")
        )
        if isinstance(raw_cid, str) and raw_cid.strip():
            cid = raw_cid.strip()
            # Lazy validate: if clearly broken, re-default to dm.
            if cid.startswith("dm:") or cid.startswith("group:"):
                pass
            else:
                cid = self._dm_id(uid)
        else:
            cid = self._dm_id(uid)

        # If user switched and conversation still dm:old, auto-switch (KD18).
        # Applied only when conversation_id not explicitly patched in this call
        # and existing was dm for a different user.
        if conversation_id is None and user_id is not None:
            old_uid = base.get("user_id")
            old_cid = base.get("conversation_id")
            if (
                isinstance(old_uid, str)
                and old_uid != uid
                and isinstance(old_cid, str)
                and old_cid == f"dm:{old_uid}"
            ):
                cid = self._dm_id(uid)
            elif (
                isinstance(old_cid, str)
                and old_cid.startswith("group:")
                and user_id is not None
            ):
                # Keep group unless we can see membership later; store keeps group.
                cid = old_cid

        out = {
            "user_id": uid,
            "conversation_id": cid,
            "view_mode": vm,
            "updated_at": utc_now_iso() if touch else (
                base.get("updated_at")
                if isinstance(base.get("updated_at"), str)
                else utc_now_iso()
            ),
        }
        return out

    def _disk_mtime_ns(self) -> int | None:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def _load_unlocked(self) -> None:
        mtime = self._disk_mtime_ns()
        if self._file_mtime_ns is not None and mtime == self._file_mtime_ns:
            return
        data = load_json_object(self.path)
        clients: dict[str, dict[str, Any]] = {}
        if isinstance(data, dict):
            raw = data.get("clients")
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if not isinstance(k, str) or not isinstance(v, dict):
                        continue
                    try:
                        cid = validate_client_id(k)
                    except InvalidClientId:
                        continue
                    clients[cid] = dict(v)
        self._clients = clients
        self._file_mtime_ns = mtime

    def _prune_unlocked(self) -> None:
        """Drop oldest by updated_at when over cap; optional TTL age prune."""
        if not self._clients:
            return
        # TTL prune
        if self._ttl_days > 0:
            from datetime import UTC, datetime, timedelta

            cutoff = datetime.now(UTC) - timedelta(days=self._ttl_days)
            drop: list[str] = []
            for cid, ent in self._clients.items():
                raw = ent.get("updated_at") if isinstance(ent, dict) else None
                if not isinstance(raw, str) or not raw.strip():
                    continue
                try:
                    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    if ts < cutoff:
                        drop.append(cid)
                except ValueError:
                    continue
            for cid in drop:
                self._clients.pop(cid, None)
        # Cap prune (oldest updated_at first)
        while len(self._clients) > self._max_clients:
            oldest_key = min(
                self._clients.keys(),
                key=lambda k: str(
                    (self._clients[k] or {}).get("updated_at") or ""
                ),
            )
            self._clients.pop(oldest_key, None)

    def _persist_unlocked(self) -> None:
        self._prune_unlocked()
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            path,
            {"schema_version": SCHEMA_VERSION, "clients": dict(self._clients)},
        )
        self._file_mtime_ns = self._disk_mtime_ns()

    def _read_legacy_seed_unlocked(self) -> dict[str, Any] | None:
        """Return seed fields from legacy glass_session.json if importable."""
        path = self.legacy_path
        data = load_json_object(path)
        if not isinstance(data, dict):
            return None
        if data.get("migrated") is True:
            return None
        uid_raw = data.get("user_id")
        if not isinstance(uid_raw, str) or not uid_raw.strip():
            return None
        try:
            uid = validate_user_id(uid_raw.strip())
        except ValueError:
            return None
        seed: dict[str, Any] = {"user_id": uid}
        cid = data.get("conversation_id")
        if isinstance(cid, str) and cid.strip():
            seed["conversation_id"] = cid.strip()
        vm = data.get("view_mode")
        if isinstance(vm, str) and vm.strip() in VALID_VIEW_MODES:
            seed["view_mode"] = vm.strip()
        return seed

    def _write_legacy_stub_unlocked(self) -> None:
        path = self.legacy_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(
                path,
                {
                    "migrated": True,
                    "note": "use client_sessions.json",
                    "migrated_at": utc_now_iso(),
                },
            )
        except OSError as exc:
            _LOG.warning("legacy glass_session stub write failed: %s", exc)

    def _create_entry_unlocked(
        self,
        client_id: str,
        *,
        seed: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create map entry; one-shot legacy import when map empty (KD22)."""
        use_seed = seed
        if not self._clients and use_seed is None:
            use_seed = self._read_legacy_seed_unlocked()
            legacy_imported = use_seed is not None
        else:
            legacy_imported = False

        entry = self._normalize_entry(use_seed)
        self._clients[client_id] = entry
        self._persist_unlocked()
        if legacy_imported:
            self._write_legacy_stub_unlocked()
        return dict(entry)

    def client_count(self) -> int:
        with self._lock:
            self._load_unlocked()
            return len(self._clients)

    def list_client_ids(self) -> list[str]:
        with self._lock:
            self._load_unlocked()
            return list(self._clients.keys())

    def get_raw(self, client_id: str) -> dict[str, Any] | None:
        """Return stored entry or None (no create). Normalizes if present."""
        try:
            cid = validate_client_id(client_id)
        except InvalidClientId:
            return None
        with self._lock:
            self._load_unlocked()
            ent = self._clients.get(cid)
            if ent is None:
                return None
            # Normalize missing fields for known client (may persist).
            normalized = self._normalize_entry(ent, touch=False)
            # Persist if fields were healed.
            if normalized != {
                "user_id": ent.get("user_id"),
                "conversation_id": ent.get("conversation_id"),
                "view_mode": ent.get("view_mode"),
                "updated_at": ent.get("updated_at"),
            }:
                # Keep updated_at if only field heal without semantic change of time
                # unless conversation/user/view actually changed.
                changed = any(
                    normalized[k] != ent.get(k)
                    for k in ("user_id", "conversation_id", "view_mode")
                )
                if changed:
                    normalized = self._normalize_entry(ent, touch=True)
                    self._clients[cid] = normalized
                    self._persist_unlocked()
                    return dict(normalized)
                # Soft fill without touch if only defaults filled identically
                self._clients[cid] = {
                    **normalized,
                    "updated_at": ent.get("updated_at") or normalized["updated_at"],
                }
            return dict(self._clients[cid])

    def resolve(
        self,
        client_id: str | None,
        *,
        allow_create: bool,
    ) -> tuple[str | None, dict[str, Any] | None, bool]:
        """Resolve session for a request.

        Args:
            client_id: validated id or None if header missing.
            allow_create: True for session-bind / social-mutate (KD25).

        Returns:
            ``(client_id, session_dict | None, minted)``
            - known client: (id, session, False)
            - unknown + allow_create: create (id, session, False) or mint id
            - missing + allow_create: mint id, create (id, session, True)
            - unknown/missing + !allow_create: (client_id or None, None, False)
        """
        with self._lock:
            self._load_unlocked()
            if client_id is not None:
                cid = validate_client_id(client_id)
                ent = self._clients.get(cid)
                if ent is not None:
                    normalized = self._normalize_entry(ent, touch=False)
                    changed = any(
                        normalized[k] != ent.get(k)
                        for k in ("user_id", "conversation_id", "view_mode")
                    )
                    if changed:
                        normalized = self._normalize_entry(ent, touch=True)
                        self._clients[cid] = normalized
                        self._persist_unlocked()
                        return cid, dict(normalized), False
                    return cid, dict(ent if ent else normalized), False
                if not allow_create:
                    return cid, None, False
                entry = self._create_entry_unlocked(cid)
                return cid, entry, False

            # Missing header
            if not allow_create:
                return None, None, False
            new_id = mint_client_id()
            entry = self._create_entry_unlocked(new_id)
            return new_id, entry, True

    def put(
        self,
        client_id: str,
        *,
        user_id: str | None = None,
        conversation_id: str | None = None,
        view_mode: str | None = None,
        create_if_missing: bool = True,
    ) -> dict[str, Any]:
        """Full RMW put for one client key. Never wipes other clients."""
        cid = validate_client_id(client_id)
        with self._lock:
            self._load_unlocked()
            existing = self._clients.get(cid)
            if existing is None:
                if not create_if_missing:
                    raise KeyError(cid)
                entry = self._create_entry_unlocked(cid)
                # Apply explicit patches on top of create defaults / legacy seed.
                if (
                    user_id is not None
                    or conversation_id is not None
                    or view_mode is not None
                ):
                    entry = self._normalize_entry(
                        entry,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        view_mode=view_mode,
                    )
                    self._clients[cid] = entry
                    self._persist_unlocked()
                return dict(entry)

            # Existing: RMW merge (user switch auto-DM handled in normalize).
            entry = self._normalize_entry(
                existing,
                user_id=user_id,
                conversation_id=conversation_id,
                view_mode=view_mode,
            )
            self._clients[cid] = entry
            self._persist_unlocked()
            return dict(entry)

    def clear(self) -> dict[str, Any]:
        """Wipe registry file + memory (reset path)."""
        with self._lock:
            self._clients = {}
            path = self.path
            existed = path.is_file()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(path, _empty_store())
                self._file_mtime_ns = self._disk_mtime_ns()
            except OSError as exc:
                _LOG.warning("client_sessions clear failed: %s", exc)
                self._file_mtime_ns = None
            return {"step": "client_sessions", "existed": existed, "clients": 0}


# Convenience for reset without constructing ConversationsStore.
def clear_client_sessions(paths: ElyraPaths) -> dict[str, Any]:
    """Reset helper: empty ``client_sessions.json`` (KD9 / KD21)."""
    reg = ClientSessionsRegistry(paths)
    return reg.clear()


__all__ = [
    "CLIENT_SESSIONS_REL",
    "ClientSessionsRegistry",
    "InvalidClientId",
    "LEGACY_GLASS_SESSION_REL",
    "clear_client_sessions",
    "mint_client_id",
    "parse_client_id_header",
    "validate_client_id",
    "DEFAULT_USER_ID",
    "DEFAULT_VIEW_MODE",
]
