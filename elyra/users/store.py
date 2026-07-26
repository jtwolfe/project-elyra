"""Per-user profile digest store (versioned current/draft/versions + meta).

Scope: read/write ``data/users/<id>/`` layout; path jail; create provisional users.
In scope: profile (current only, never draft); ensure_layout migrate;
write_draft / promote / get / display_label / list_user_ids / create_user;
mint_user_id (K18); name_nudge; RLock + atomic writes.
Out of scope: promote gates, model tools, glass, cross-user inject.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Any, Literal

from elyra.config import ElyraPaths
from elyra.identity.layout import (
    VERSION_ID_RE,
    VERSION_GC_LIMIT,
    archive_index_entry,
    check_body_size,
    content_sha256,
    delete_version_files,
    full_name_change_requires_force,
    heal_versions_index,
    load_json_object,
    mint_user_id,
    mint_version_id,
    read_text_or_empty,
    strip_operational_keys,
    trim_versions_index,
    utc_now_iso,
    validate_user_id,
    write_atomic,
    write_json_atomic,
)

logger = logging.getLogger(__name__)

_SEED_OPERATOR = Path("seeds") / "users" / "operator" / "profile.md"

# Placeholder is literal; substituted via str.replace (not .format) to avoid
# user-influenced format-string footguns if the template gains fields later.
_PROVISIONAL_BODY = (
    "# {goes_by}\n"
    "\n"
    "Provisional guest profile. Real name not yet confirmed.\n"
    "\n"
    "## Relationship notes\n"
    "\n"
    "- Met this session. Prefer asking their name once, then stop nagging.\n"
)


class UsersStore:
    """Versioned per-user identity under ``data/users/<id>/``."""

    def __init__(self, paths: ElyraPaths) -> None:
        self._paths = paths
        self._lock = threading.RLock()

    # ── paths ────────────────────────────────────────────────────────────

    @property
    def users_root(self) -> Path:
        return self._paths.data_dir / "users"

    def _user_dir(self, user_id: str) -> Path:
        safe_id = validate_user_id(user_id)
        users_root = self.users_root.resolve()
        path = (users_root / safe_id).resolve()
        if not path.is_relative_to(users_root):
            raise ValueError(f"invalid user_id: {user_id!r}")
        return path

    def current_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "current.md"

    def draft_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "draft.md"

    def meta_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "meta.json"

    def versions_dir(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "versions"

    def profile_path(self, user_id: str) -> Path:
        """Deprecated alias → current_path (tests/API transition).

        Still validates user_id jail. Prefer ``current_path`` for new code.
        Legacy ``profile.md`` remains readable via ``profile()`` compat.
        """
        return self.current_path(user_id)

    def legacy_profile_path(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "profile.md"

    def _resolved_live_path(self, user_id: str) -> Path | None:
        cur = self.current_path(user_id)
        if cur.is_file():
            return cur
        legacy = self.legacy_profile_path(user_id)
        if legacy.is_file():
            return legacy
        return None

    def _user_exists(self, user_id: str) -> bool:
        """True when user was created/seeded (current, legacy profile, or meta).

        Does not treat an empty dir as existence — path probes must not invent
        users (see ensure_layout / write_draft).
        """
        return (
            self.current_path(user_id).is_file()
            or self.legacy_profile_path(user_id).is_file()
            or self.meta_path(user_id).is_file()
        )

    # ── read (orient) ────────────────────────────────────────────────────

    def profile(self, user_id: str) -> str:
        """Return current body for ``user_id``, or empty string if missing.

        Compat: current.md else legacy profile.md. Never draft.
        Raises ValueError for unsafe ``user_id`` (path-jail).
        """
        path = self._resolved_live_path(user_id)
        if path is None:
            return ""
        return read_text_or_empty(path)

    def display_label(self, user_id: str) -> str:
        """goes_by or display_name or user_id. Safe if meta missing → user_id."""
        try:
            safe = validate_user_id(user_id)
        except ValueError:
            return user_id if isinstance(user_id, str) else ""
        meta = load_json_object(self.meta_path(safe))
        if meta is None:
            return safe
        for key in ("goes_by", "display_name"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return safe

    def list_user_ids(self) -> list[str]:
        """Scan data/users/* dirs with valid ids; skip junk."""
        root = self.users_root
        if not root.is_dir():
            return []
        ids: list[str] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            name = child.name
            try:
                validate_user_id(name)
            except ValueError:
                continue
            ids.append(name)
        return ids

    def get_meta(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            data = load_json_object(self.meta_path(user_id))
            if data is None:
                return self._default_meta(
                    user_id=user_id, body=self.profile(user_id)
                )
            return data

    def has_draft(self, user_id: str) -> bool:
        return self.draft_path(user_id).is_file()

    def get(
        self,
        user_id: str,
        *,
        which: Literal["current", "draft", "version"] = "current",
        version_id: str | None = None,
        list_versions: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            # Validate jail early.
            validate_user_id(user_id)
            live = self._resolved_live_path(user_id)
            if live is None and which != "draft":
                # User dir may exist without body; treat as not found for version
                # but allow empty current.
                pass

            meta = load_json_object(self.meta_path(user_id))
            if meta is None:
                meta = self._default_meta(
                    user_id=user_id, body=self.profile(user_id)
                )

            result: dict[str, Any] = {
                "ok": True,
                "actor": "user",
                "user_id": user_id,
                "meta": meta,
                "has_draft": self.draft_path(user_id).is_file(),
            }

            if which == "current":
                result["body"] = self.profile(user_id)
                result["which"] = "current"
            elif which == "draft":
                draft = self.draft_path(user_id)
                if not draft.is_file():
                    return {
                        "ok": False,
                        "error": "draft_missing",
                        "actor": "user",
                        "user_id": user_id,
                    }
                result["body"] = read_text_or_empty(draft)
                result["which"] = "draft"
            elif which == "version":
                if not version_id or not VERSION_ID_RE.fullmatch(version_id):
                    return {
                        "ok": False,
                        "error": "version_not_found",
                        "actor": "user",
                        "user_id": user_id,
                    }
                vpath = self.versions_dir(user_id) / f"{version_id}.md"
                if not vpath.is_file():
                    return {
                        "ok": False,
                        "error": "version_not_found",
                        "actor": "user",
                        "user_id": user_id,
                    }
                result["body"] = read_text_or_empty(vpath)
                result["which"] = "version"
                result["version_id"] = version_id
            else:
                return {
                    "ok": False,
                    "error": "invalid_which",
                    "actor": "user",
                    "user_id": user_id,
                }

            if list_versions:
                versions = meta.get("versions") or []
                if not isinstance(versions, list):
                    versions = []
                out_list: list[dict[str, Any]] = []
                vdir = self.versions_dir(user_id)
                for row in versions:
                    if not isinstance(row, dict):
                        continue
                    vid = row.get("version_id")
                    if not isinstance(vid, str):
                        continue
                    out_list.append(
                        {
                            "version_id": vid,
                            "path": str(vdir / f"{vid}.md"),
                            "promoted_at": row.get("promoted_at"),
                            "sha256": row.get("sha256"),
                            "bytes": row.get("bytes"),
                        }
                    )
                result["versions"] = out_list

            return result

    # ── create / mint ────────────────────────────────────────────────────

    def create_user(
        self,
        goes_by: str,
        *,
        user_id: str | None = None,
        provisional: bool = True,
        full_name: str | None = None,
        real_name_known: bool = False,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Mint/validate user_id (K18), write current.md + meta.json."""
        if not isinstance(goes_by, str) or not goes_by.strip():
            return {"ok": False, "error": "missing_goes_by"}

        goes_by = goes_by.strip()

        with self._lock:
            existing = set(self.list_user_ids())
            try:
                uid = mint_user_id(goes_by, existing, user_id=user_id)
            except ValueError as exc:
                msg = str(exc)
                if msg.startswith("user_id_exists"):
                    return {
                        "ok": False,
                        "error": "user_id_exists",
                        "user_id": user_id.strip() if user_id else None,
                    }
                if msg.startswith("invalid user_id"):
                    return {
                        "ok": False,
                        "error": "invalid_user_id",
                        "user_id": user_id,
                    }
                return {"ok": False, "error": "invalid_user_id", "detail": msg}

            # Double-check dir not already present with content.
            if uid in existing:
                return {"ok": False, "error": "user_id_exists", "user_id": uid}

            udir = self._user_dir(uid)
            udir.mkdir(parents=True, exist_ok=True)
            (udir / "versions").mkdir(parents=True, exist_ok=True)

            if body is not None and isinstance(body, str) and body.strip():
                text = body
            else:
                text = _PROVISIONAL_BODY.replace("{goes_by}", goes_by)

            size_err = check_body_size(text)
            if size_err:
                return {"ok": False, "error": size_err}

            # full_name at create: allowed without force (initial set on new user).
            write_atomic(self.current_path(uid), text)
            meta = self._default_meta(
                user_id=uid,
                body=text,
                goes_by=goes_by,
                display_name=goes_by,
                full_name=full_name,
                real_name_known=real_name_known,
                provisional=provisional,
            )
            write_json_atomic(self.meta_path(uid), meta)

            return {
                "ok": True,
                "user_id": uid,
                "goes_by": goes_by,
                "provisional": provisional,
                "meta": meta,
                "path": str(self.current_path(uid)),
            }

    # ── write_draft / promote / name_nudge ───────────────────────────────

    def write_draft(
        self,
        user_id: str,
        body: str | None,
        *,
        meta_patch: dict[str, Any] | None = None,
        reason: str,
        moment_id: str | None = None,
    ) -> dict[str, Any]:
        """Write draft.md + meta.draft_meta; force_full_name; optional nudge.

        ``record_name_nudge`` requires ``moment_id`` and updates live name_nudge
        (never draft_meta). Body may be omitted for nudge-only or meta-only
        patches; nudge-only with missing moment_id fails closed.
        """
        if not isinstance(reason, str) or not reason.strip():
            return {
                "ok": False,
                "error": "missing_reason",
                "actor": "user",
                "user_id": user_id,
            }

        with self._lock:
            try:
                validate_user_id(user_id)
            except ValueError:
                return {
                    "ok": False,
                    "error": "invalid_user_id",
                    "actor": "user",
                    "user_id": user_id,
                }

            # Existence before ensure — ensure must not invent empty user trees.
            if not self._user_exists(user_id):
                return {
                    "ok": False,
                    "error": "user_not_found",
                    "actor": "user",
                    "user_id": user_id,
                }

            self.ensure_layout(user_id)

            draft_fields, ops = strip_operational_keys(meta_patch)
            want_nudge = ops.get("record_name_nudge") is True

            if want_nudge:
                if not isinstance(moment_id, str) or not moment_id.strip():
                    return {
                        "ok": False,
                        "error": "missing_moment_id",
                        "actor": "user",
                        "user_id": user_id,
                    }

            if "full_name" in draft_fields:
                meta = load_json_object(self.meta_path(user_id)) or self._default_meta(
                    user_id=user_id, body=self.profile(user_id)
                )
                if full_name_change_requires_force(
                    meta.get("full_name"), draft_fields["full_name"]
                ):
                    if ops.get("force_full_name") is not True:
                        return {
                            "ok": False,
                            "error": "full_name_force_required",
                            "actor": "user",
                            "user_id": user_id,
                        }

            has_body = body is not None
            if has_body:
                if not isinstance(body, str) or not body.strip():
                    return {
                        "ok": False,
                        "error": "empty_body",
                        "actor": "user",
                        "user_id": user_id,
                    }
                size_err = check_body_size(body)
                if size_err:
                    return {
                        "ok": False,
                        "error": size_err,
                        "actor": "user",
                        "user_id": user_id,
                    }
            else:
                # body optional for meta-only or record_name_nudge-only patches.
                if not draft_fields and not want_nudge:
                    return {
                        "ok": False,
                        "error": "empty_body",
                        "actor": "user",
                        "user_id": user_id,
                    }

            # Live nudge first (independent of draft body/meta).
            nudge_result: dict[str, Any] | None = None
            if want_nudge:
                assert moment_id is not None  # validated above
                nudge_result = self.record_name_nudge(user_id, moment_id.strip())
                if not nudge_result.get("ok"):
                    return {
                        "ok": False,
                        "error": nudge_result.get("error", "name_nudge_failed"),
                        "actor": "user",
                        "user_id": user_id,
                    }

            # Nudge-only: nothing else to persist.
            if not has_body and not draft_fields:
                return {
                    "ok": True,
                    "actor": "user",
                    "user_id": user_id,
                    "has_draft": self.draft_path(user_id).is_file(),
                    "draft_meta": (
                        (load_json_object(self.meta_path(user_id)) or {}).get(
                            "draft_meta"
                        )
                    ),
                    "name_nudge": (nudge_result or {}).get("name_nudge"),
                    "reason": reason.strip(),
                }

            if has_body:
                write_atomic(self.draft_path(user_id), body)

            meta = load_json_object(self.meta_path(user_id)) or self._default_meta(
                user_id=user_id, body=self.profile(user_id)
            )
            existing_dm = meta.get("draft_meta")
            if not isinstance(existing_dm, dict):
                existing_dm = {}
            if draft_fields:
                merged_dm = dict(existing_dm)
                merged_dm.update(draft_fields)
                meta["draft_meta"] = merged_dm
            # Strip any non-allowed keys from draft_meta.
            if isinstance(meta.get("draft_meta"), dict):
                for k in list(meta["draft_meta"].keys()):
                    if k not in (
                        "display_name",
                        "goes_by",
                        "full_name",
                        "real_name_known",
                        "provisional",
                    ):
                        del meta["draft_meta"][k]
            if has_body or draft_fields:
                meta["draft_updated_at"] = utc_now_iso()
            write_json_atomic(self.meta_path(user_id), meta)

            out: dict[str, Any] = {
                "ok": True,
                "actor": "user",
                "user_id": user_id,
                "has_draft": self.draft_path(user_id).is_file(),
                "draft_meta": meta.get("draft_meta"),
                "draft_updated_at": meta.get("draft_updated_at"),
                "reason": reason.strip(),
            }
            if nudge_result is not None:
                out["name_nudge"] = nudge_result.get("name_nudge")
            return out

    def promote(
        self,
        user_id: str,
        *,
        reason: str,
        expected_draft_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Archive current → versions/, draft → current; name_nudge reset.

        Order: archive → write current → write meta → unlink draft → GC files.
        Idempotent when draft already equals current and draft_meta is null.
        """
        if not isinstance(reason, str) or not reason.strip():
            return {
                "ok": False,
                "error": "missing_reason",
                "actor": "user",
                "user_id": user_id,
            }

        with self._lock:
            try:
                validate_user_id(user_id)
            except ValueError:
                return {
                    "ok": False,
                    "error": "invalid_user_id",
                    "actor": "user",
                    "user_id": user_id,
                }

            if not self._user_exists(user_id):
                return {
                    "ok": False,
                    "error": "user_not_found",
                    "actor": "user",
                    "user_id": user_id,
                }

            self.ensure_layout(user_id)
            draft_path = self.draft_path(user_id)
            if not draft_path.is_file():
                return {
                    "ok": False,
                    "error": "draft_missing",
                    "actor": "user",
                    "user_id": user_id,
                }

            draft_body = read_text_or_empty(draft_path)
            if not draft_body.strip():
                return {
                    "ok": False,
                    "error": "draft_missing",
                    "actor": "user",
                    "user_id": user_id,
                }

            draft_sha = content_sha256(draft_body)
            if (
                expected_draft_sha256 is not None
                and expected_draft_sha256 != draft_sha
            ):
                return {
                    "ok": False,
                    "error": "draft_hash_mismatch",
                    "actor": "user",
                    "user_id": user_id,
                }

            meta = load_json_object(self.meta_path(user_id)) or self._default_meta(
                user_id=user_id, body=self.profile(user_id)
            )
            current_path = self.current_path(user_id)
            current_body = self.profile(user_id)

            # Idempotent completion of partial promote (draft left after meta).
            if (
                meta.get("draft_meta") is None
                and current_path.is_file()
                and draft_body == current_body
                and meta.get("current_content_sha256") == draft_sha
            ):
                try:
                    draft_path.unlink(missing_ok=True)
                except OSError:
                    return {
                        "ok": False,
                        "error": "promote_failed:draft_unlink",
                        "actor": "user",
                        "user_id": user_id,
                    }
                return {
                    "ok": True,
                    "actor": "user",
                    "user_id": user_id,
                    "current_version_id": meta.get("current_version_id"),
                    "promote_count": int(meta.get("promote_count") or 0),
                    "reason": reason.strip(),
                    "meta": meta,
                    "idempotent": True,
                }

            now = utc_now_iso()
            vdir = self.versions_dir(user_id)
            vdir.mkdir(parents=True, exist_ok=True)

            pre_goes_by = meta.get("goes_by")
            pre_real = meta.get("real_name_known")

            versions = list(meta.get("versions") or [])
            if not isinstance(versions, list):
                versions = []
            drop_later: list[dict[str, Any]] = []

            if current_body and (
                current_path.is_file() or self.legacy_profile_path(user_id).is_file()
            ):
                archive_id = meta.get("current_version_id")
                if not (
                    isinstance(archive_id, str)
                    and VERSION_ID_RE.fullmatch(archive_id)
                ):
                    archive_id = mint_version_id()
                archive_path = vdir / f"{archive_id}.md"
                if not archive_path.is_file():
                    write_atomic(archive_path, current_body)
                if not any(
                    isinstance(r, dict) and r.get("version_id") == archive_id
                    for r in versions
                ):
                    versions.append(
                        archive_index_entry(
                            archive_path,
                            version_id=archive_id,
                            promoted_at=now,
                        )
                    )
                versions, drop_later = trim_versions_index(
                    versions, limit=VERSION_GC_LIMIT
                )

            write_atomic(current_path, draft_body)

            draft_meta = meta.get("draft_meta")
            if isinstance(draft_meta, dict):
                for key in (
                    "display_name",
                    "goes_by",
                    "full_name",
                    "real_name_known",
                    "provisional",
                ):
                    if key in draft_meta:
                        meta[key] = draft_meta[key]

            # name_nudge reset when goes_by or real_name_known change.
            new_goes_by = meta.get("goes_by")
            new_real = meta.get("real_name_known")
            if _norm_str(new_goes_by) != _norm_str(pre_goes_by) or _norm_bool(
                new_real
            ) != _norm_bool(pre_real):
                meta["name_nudge"] = {
                    "last_moment_id": None,
                    "last_at": None,
                    "count": 0,
                }

            new_vid = mint_version_id()
            meta["current_version_id"] = new_vid
            meta["draft_meta"] = None
            meta["draft_updated_at"] = None
            meta["current_promoted_at"] = now
            meta["current_content_sha256"] = draft_sha
            meta["promote_count"] = int(meta.get("promote_count") or 0) + 1
            meta["versions"] = versions
            # Clear provisional when name known after promote (product rule soft).
            if meta.get("real_name_known") is True and meta.get("provisional"):
                meta["provisional"] = False

            write_json_atomic(self.meta_path(user_id), meta)

            try:
                draft_path.unlink(missing_ok=True)
            except OSError:
                return {
                    "ok": False,
                    "error": "promote_failed:draft_unlink",
                    "actor": "user",
                    "user_id": user_id,
                    "current_version_id": new_vid,
                    "promote_count": meta["promote_count"],
                    "meta": meta,
                }

            delete_version_files(drop_later, vdir)

            return {
                "ok": True,
                "actor": "user",
                "user_id": user_id,
                "current_version_id": new_vid,
                "promote_count": meta["promote_count"],
                "reason": reason.strip(),
                "meta": meta,
            }

    def record_name_nudge(self, user_id: str, moment_id: str) -> dict[str, Any]:
        """Live meta.name_nudge update (not draft)."""
        with self._lock:
            try:
                validate_user_id(user_id)
            except ValueError:
                return {
                    "ok": False,
                    "error": "invalid_user_id",
                    "user_id": user_id,
                }
            if not isinstance(moment_id, str) or not moment_id.strip():
                return {
                    "ok": False,
                    "error": "missing_moment_id",
                    "user_id": user_id,
                }

            if not self._user_exists(user_id):
                return {
                    "ok": False,
                    "error": "user_not_found",
                    "user_id": user_id,
                }
            self.ensure_layout(user_id)
            meta = load_json_object(self.meta_path(user_id))
            if meta is None:
                return {
                    "ok": False,
                    "error": "user_not_found",
                    "user_id": user_id,
                }

            nudge = meta.get("name_nudge")
            if not isinstance(nudge, dict):
                nudge = {
                    "last_moment_id": None,
                    "last_at": None,
                    "count": 0,
                }
            nudge = dict(nudge)
            nudge["last_moment_id"] = moment_id.strip()
            nudge["last_at"] = utc_now_iso()
            nudge["count"] = int(nudge.get("count") or 0) + 1
            meta["name_nudge"] = nudge
            write_json_atomic(self.meta_path(user_id), meta)
            return {
                "ok": True,
                "user_id": user_id,
                "name_nudge": nudge,
            }

    # ── ensure / migrate ─────────────────────────────────────────────────

    def ensure_layout(self, user_id: str | None = None) -> None:
        """Migrate profile.md → current.md for one or all users; seed operator."""
        with self._lock:
            self.users_root.mkdir(parents=True, exist_ok=True)

            if user_id is not None:
                self._ensure_one(user_id)
                return

            # Seed operator when no users at all / operator missing.
            op_dir = self.users_root / "operator"
            op_current = op_dir / "current.md"
            op_legacy = op_dir / "profile.md"
            if not op_current.is_file() and not op_legacy.is_file():
                self._seed_operator()

            # Ensure operator if present or just seeded.
            if (self.users_root / "operator").is_dir():
                self._ensure_one("operator")

            for uid in self.list_user_ids():
                if uid == "operator":
                    continue
                self._ensure_one(uid)

    def _ensure_one(self, user_id: str) -> None:
        try:
            validate_user_id(user_id)
        except ValueError:
            return

        current = self.current_path(user_id)
        legacy = self.legacy_profile_path(user_id)
        meta_path = self.meta_path(user_id)

        # Do not invent empty user trees for unknown ids (path probe safety).
        if not (
            current.is_file() or legacy.is_file() or meta_path.is_file()
        ):
            return

        udir = self._user_dir(user_id)
        udir.mkdir(parents=True, exist_ok=True)
        vdir = self.versions_dir(user_id)
        vdir.mkdir(parents=True, exist_ok=True)

        if not current.is_file() and legacy.is_file():
            write_atomic(current, legacy.read_text(encoding="utf-8"))

        if current.is_file() or legacy.is_file():
            if not meta_path.is_file():
                body = self.profile(user_id)
                write_json_atomic(
                    meta_path,
                    self._default_meta(
                        user_id=user_id,
                        body=body,
                        goes_by=_default_goes_by(user_id),
                        display_name=_default_goes_by(user_id),
                    ),
                )

        if meta_path.is_file():
            meta = load_json_object(meta_path)
            if meta is not None:
                before = list(meta.get("versions") or [])
                healed = heal_versions_index(meta, vdir)
                after = list(healed.get("versions") or [])
                if before != after:
                    write_json_atomic(meta_path, healed)

    def _seed_operator(self) -> None:
        dest = self.current_path("operator")
        if dest.exists():
            if not dest.is_file():
                raise FileExistsError(
                    f"seed dest exists but is not a file: {dest}"
                )
            return
        src = self._paths.resolve_seed(_SEED_OPERATOR)
        if src is None:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        (dest.parent / "versions").mkdir(parents=True, exist_ok=True)

    def _default_meta(
        self,
        *,
        user_id: str,
        body: str,
        goes_by: str | None = None,
        display_name: str | None = None,
        full_name: str | None = None,
        real_name_known: bool = False,
        provisional: bool = False,
    ) -> dict[str, Any]:
        vid = mint_version_id()
        now = utc_now_iso()
        label = goes_by if goes_by else _default_goes_by(user_id)
        return {
            "schema_version": 1,
            "actor": "user",
            "user_id": user_id,
            "display_name": display_name if display_name else label,
            "full_name": full_name,
            "goes_by": label,
            "real_name_known": real_name_known,
            "provisional": provisional,
            "created_at": now,
            "current_version_id": vid,
            "draft_updated_at": None,
            "draft_meta": None,
            "current_promoted_at": now,
            "current_content_sha256": content_sha256(body) if body else None,
            "promote_count": 0,
            "versions": [],
            "name_nudge": {
                "last_moment_id": None,
                "last_at": None,
                "count": 0,
            },
            "notes": {},
        }


def _default_goes_by(user_id: str) -> str:
    if user_id == "operator":
        return "Operator"
    return user_id


def _norm_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    s = value.strip()
    return s if s else None


def _norm_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)
