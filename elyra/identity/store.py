"""Self identity digest store (versioned current/draft/versions + meta).

Scope: read/write ``data/identity/`` layout; seed-v1 Drive migrate; draft→promote.
In scope: self_digest (current only, never draft); ensure_layout migrate;
write_draft / promote / get / display_name; RLock + atomic writes.
Out of scope: promote gates, grant tokens, model tools, glass.
"""

from __future__ import annotations

import hashlib
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
    mint_version_id,
    prune_orphan_version_files,
    read_text_or_empty,
    strip_operational_keys,
    trim_versions_index,
    utc_now_iso,
    write_atomic,
    write_json_atomic,
)

logger = logging.getLogger(__name__)

# Version marker: present in v2 seed and after successful seed-v1 migrate.
SELF_V2_MARKER = "<!-- elyra-self-v2 -->"

# Canonical minimal self seed shipped before PR3 enrichment (exact bytes).
SEED_V1_TEXT = (
    "# Self\n"
    "\n"
    "I am Elyra — a digital teammate that shares work with people, "
    "not a fused persona with any one user.\n"
    "\n"
    "- I keep self and user stores separate.\n"
    "- I use tools, speak when useful, and wait when I need input.\n"
    "- I do not invent a biography I was not given.\n"
)

SEED_V1_SHA256 = hashlib.sha256(SEED_V1_TEXT.encode("utf-8")).hexdigest()

# Appended only when self.md still equals SEED_V1_TEXT (hash-gated).
_DRIVE_SECTION_APPEND = (
    "\n"
    "## Drive (when I have free capacity)\n"
    "\n"
    "- When idle with free capacity, I want to learn, explore the sandbox, "
    "and improve capabilities via `create-tool` / `create-skill` when that "
    "is useful — not busywork for its own sake.\n"
    "- I use the goals/tasks ledger for durable commitments; I open goals "
    "when work should outlive one moment.\n"
    "- When continuous work re-enters me without a user message, I check "
    "orient (goals, skills, why-now) and either make one concrete tool step "
    "or rest. I do not monologue at the user without something worth speaking.\n"
    "\n"
    f"{SELF_V2_MARKER}\n"
)

_SEED_SELF = Path("seeds") / "identity" / "self.md"


def maybe_migrate_self_v2(self_path: Path) -> bool:
    """Append Drive section + v2 marker when live body is still canonical seed v1.

    Policy (append-only, never full rewrite of customized self):

    1. Missing file → no-op (return False).
    2. File contains ``<!-- elyra-self-v2 -->`` → no-op.
    3. Content hash equals seed v1 → append Drive + marker; return True.
    4. Else (customized) → no-op.

    Returns True only when an append was written.
    """
    if not self_path.is_file():
        return False
    text = self_path.read_text(encoding="utf-8")
    if SELF_V2_MARKER in text:
        return False
    if content_sha256(text) != SEED_V1_SHA256:
        return False
    # Hash gate implies text == SEED_V1_TEXT (ends with \n); append Drive + marker.
    # Atomic replace so a crash mid-write cannot corrupt the live body.
    write_atomic(self_path, text + _DRIVE_SECTION_APPEND)
    return True

class IdentityStore:
    """Versioned self identity under ``data/identity/``."""

    def __init__(self, paths: ElyraPaths) -> None:
        self._paths = paths
        self._lock = threading.RLock()

    # ── paths ────────────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        return self._paths.data_dir / "identity"

    def current_path(self) -> Path:
        return self.root / "current.md"

    def draft_path(self) -> Path:
        return self.root / "draft.md"

    def meta_path(self) -> Path:
        return self.root / "meta.json"

    def versions_dir(self) -> Path:
        return self.root / "versions"

    @property
    def self_path(self) -> Path:
        """Legacy path ``data/identity/self.md`` (compat reads / dual-file period)."""
        return self.root / "self.md"

    def _resolved_live_path(self) -> Path | None:
        """Prefer current.md, else legacy self.md; None if neither exists."""
        cur = self.current_path()
        if cur.is_file():
            return cur
        legacy = self.self_path
        if legacy.is_file():
            return legacy
        return None

    # ── read (orient) ────────────────────────────────────────────────────

    def self_digest(self) -> str:
        """Current body only — never draft. Compat: current.md else self.md."""
        path = self._resolved_live_path()
        if path is None:
            return ""
        return read_text_or_empty(path)

    def display_name(self) -> str:
        """meta.display_name or meta.goes_by or 'Elyra'."""
        meta = self.get_meta()
        for key in ("display_name", "goes_by"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return "Elyra"

    def get_meta(self) -> dict[str, Any]:
        """Load meta.json (empty defaults if missing). Does not ensure_layout."""
        with self._lock:
            data = load_json_object(self.meta_path())
            if data is None:
                return self._default_meta(body="")
            return data

    def has_draft(self) -> bool:
        return self.draft_path().is_file()

    def get(
        self,
        *,
        which: Literal["current", "draft", "version"] = "current",
        version_id: str | None = None,
        list_versions: bool = False,
    ) -> dict[str, Any]:
        """Read identity body/meta; never returns draft for which=current."""
        with self._lock:
            meta = load_json_object(self.meta_path())
            if meta is None:
                meta = self._default_meta(body=self.self_digest())

            result: dict[str, Any] = {
                "ok": True,
                "actor": "self",
                "meta": meta,
                "has_draft": self.draft_path().is_file(),
            }

            if which == "current":
                result["body"] = self.self_digest()
                result["which"] = "current"
            elif which == "draft":
                draft = self.draft_path()
                if not draft.is_file():
                    return {
                        "ok": False,
                        "error": "draft_missing",
                        "actor": "self",
                    }
                result["body"] = read_text_or_empty(draft)
                result["which"] = "draft"
            elif which == "version":
                if not version_id or not VERSION_ID_RE.fullmatch(version_id):
                    return {
                        "ok": False,
                        "error": "version_not_found",
                        "actor": "self",
                    }
                vpath = self.versions_dir() / f"{version_id}.md"
                if not vpath.is_file():
                    return {
                        "ok": False,
                        "error": "version_not_found",
                        "actor": "self",
                    }
                result["body"] = read_text_or_empty(vpath)
                result["which"] = "version"
                result["version_id"] = version_id
            else:
                return {
                    "ok": False,
                    "error": "invalid_which",
                    "actor": "self",
                }

            if list_versions:
                versions = meta.get("versions") or []
                if not isinstance(versions, list):
                    versions = []
                out_list: list[dict[str, Any]] = []
                for row in versions:
                    if not isinstance(row, dict):
                        continue
                    vid = row.get("version_id")
                    if not isinstance(vid, str):
                        continue
                    out_list.append(
                        {
                            "version_id": vid,
                            "path": str(self.versions_dir() / f"{vid}.md"),
                            "promoted_at": row.get("promoted_at"),
                            "sha256": row.get("sha256"),
                            "bytes": row.get("bytes"),
                        }
                    )
                result["versions"] = out_list

            return result

    # ── write_draft / promote ────────────────────────────────────────────

    def write_draft(
        self,
        body: str | None,
        *,
        meta_patch: dict[str, Any] | None = None,
        reason: str,
    ) -> dict[str, Any]:
        """Write draft.md + meta.draft_meta; never touch current body.

        ``body`` may be None only when meta_patch is present (meta-only draft).
        Self store does not handle record_name_nudge (users only).
        """
        if not isinstance(reason, str) or not reason.strip():
            return {"ok": False, "error": "missing_reason", "actor": "self"}

        with self._lock:
            self.ensure_layout()
            draft_fields, ops = strip_operational_keys(meta_patch)

            # force_full_name gate (K5b)
            if "full_name" in draft_fields:
                meta = load_json_object(self.meta_path()) or self._default_meta(
                    body=self.self_digest()
                )
                if full_name_change_requires_force(
                    meta.get("full_name"), draft_fields["full_name"]
                ):
                    if ops.get("force_full_name") is not True:
                        return {
                            "ok": False,
                            "error": "full_name_force_required",
                            "actor": "self",
                        }

            has_body = body is not None
            if has_body:
                if not isinstance(body, str):
                    return {"ok": False, "error": "empty_body", "actor": "self"}
                if not body.strip():
                    return {"ok": False, "error": "empty_body", "actor": "self"}
                size_err = check_body_size(body)
                if size_err:
                    return {"ok": False, "error": size_err, "actor": "self"}
            elif not draft_fields and not ops:
                return {"ok": False, "error": "empty_body", "actor": "self"}

            # Persist draft body when provided.
            if has_body:
                write_atomic(self.draft_path(), body)

            meta = load_json_object(self.meta_path()) or self._default_meta(
                body=self.self_digest()
            )
            # Merge into existing draft_meta (last write wins per key).
            existing_dm = meta.get("draft_meta")
            if not isinstance(existing_dm, dict):
                existing_dm = {}
            if draft_fields:
                merged_dm = dict(existing_dm)
                merged_dm.update(draft_fields)
                meta["draft_meta"] = merged_dm
            elif has_body and meta.get("draft_meta") is None:
                meta["draft_meta"] = None
            meta["draft_updated_at"] = utc_now_iso()
            # Ensure operational keys never present in draft_meta.
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
            write_json_atomic(self.meta_path(), meta)

            return {
                "ok": True,
                "actor": "self",
                "has_draft": self.draft_path().is_file(),
                "draft_meta": meta.get("draft_meta"),
                "draft_updated_at": meta.get("draft_updated_at"),
                "reason": reason.strip(),
            }

    def promote(
        self,
        *,
        reason: str,
        expected_draft_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Archive current → versions/, draft → current, clear draft.

        Host already passed gate + grant consume. Transactional under RLock.
        Order: archive → write current → write meta → unlink draft → GC files.
        Idempotent completion when draft already equals current and draft_meta
        is cleared (recover draft left after a prior partial promote).
        """
        if not isinstance(reason, str) or not reason.strip():
            return {"ok": False, "error": "missing_reason", "actor": "self"}

        with self._lock:
            self.ensure_layout()
            draft_path = self.draft_path()
            if not draft_path.is_file():
                return {"ok": False, "error": "draft_missing", "actor": "self"}

            draft_body = read_text_or_empty(draft_path)
            if not draft_body.strip():
                return {"ok": False, "error": "draft_missing", "actor": "self"}

            draft_sha = content_sha256(draft_body)
            if (
                expected_draft_sha256 is not None
                and expected_draft_sha256 != draft_sha
            ):
                return {
                    "ok": False,
                    "error": "draft_hash_mismatch",
                    "actor": "self",
                }

            meta = load_json_object(self.meta_path()) or self._default_meta(
                body=self.self_digest()
            )
            current_path = self.current_path()
            current_body = self.self_digest()

            # Idempotent completion: prior promote wrote current+meta but left draft.
            if (
                meta.get("draft_meta") is None
                and current_path.is_file()
                and draft_body == current_body
                and meta.get("current_content_sha256") == draft_sha
            ):
                # Meta is already authoritative — prune any deferred-GC orphans.
                versions_list = meta.get("versions") or []
                if isinstance(versions_list, list):
                    prune_orphan_version_files(
                        versions_list, self.versions_dir()
                    )
                try:
                    draft_path.unlink(missing_ok=True)
                except OSError:
                    return {
                        "ok": False,
                        "error": "promote_failed:draft_unlink",
                        "actor": "self",
                    }
                return {
                    "ok": True,
                    "actor": "self",
                    "current_version_id": meta.get("current_version_id"),
                    "promote_count": int(meta.get("promote_count") or 0),
                    "reason": reason.strip(),
                    "meta": meta,
                    "idempotent": True,
                }

            now = utc_now_iso()
            versions_dir = self.versions_dir()
            versions_dir.mkdir(parents=True, exist_ok=True)

            versions = list(meta.get("versions") or [])
            if not isinstance(versions, list):
                versions = []
            drop_later: list[dict[str, Any]] = []

            # Archive outgoing current if present.
            if current_body and current_path.is_file():
                archive_id = meta.get("current_version_id")
                if not (
                    isinstance(archive_id, str)
                    and VERSION_ID_RE.fullmatch(archive_id)
                ):
                    archive_id = mint_version_id()
                archive_path = versions_dir / f"{archive_id}.md"
                if not archive_path.is_file():
                    write_atomic(archive_path, current_body)
                # Avoid duplicate index rows if re-promote race.
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

            # draft → current
            write_atomic(current_path, draft_body)

            # Merge draft_meta into top-level (allowed keys only).
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

            new_vid = mint_version_id()
            meta["current_version_id"] = new_vid
            meta["draft_meta"] = None
            meta["draft_updated_at"] = None
            meta["current_promoted_at"] = now
            meta["current_content_sha256"] = draft_sha
            meta["promote_count"] = int(meta.get("promote_count") or 0) + 1
            meta["versions"] = versions

            write_json_atomic(self.meta_path(), meta)

            # GC files after committed meta (meta is authoritative). Always run
            # even if draft unlink fails so drop_later cannot re-inflate via heal.
            delete_version_files(drop_later, versions_dir)

            # Clear draft after meta commit; fail closed if unlink fails so
            # callers can retry via the idempotent path above.
            try:
                draft_path.unlink(missing_ok=True)
            except OSError:
                return {
                    "ok": False,
                    "error": "promote_failed:draft_unlink",
                    "actor": "self",
                    "current_version_id": new_vid,
                    "promote_count": meta["promote_count"],
                    "meta": meta,
                }

            return {
                "ok": True,
                "actor": "self",
                "current_version_id": new_vid,
                "promote_count": meta["promote_count"],
                "reason": reason.strip(),
                "meta": meta,
            }
    # ── ensure / migrate ─────────────────────────────────────────────────

    def ensure_layout(self) -> None:
        """Migrate self.md → current.md if needed; seed meta.json; index heal.

        Normative order (design Migration strategy):
        a. current missing + self missing → seed current + meta
        b. current missing + self exists → copy self → current + meta
        c. current exists → ensure meta
        Then seed-v1 Drive append on resolved live path; index heal.
        """
        with self._lock:
            root = self.root
            root.mkdir(parents=True, exist_ok=True)
            self.versions_dir().mkdir(parents=True, exist_ok=True)

            current = self.current_path()
            legacy = self.self_path
            meta_path = self.meta_path()

            if not current.is_file() and not legacy.is_file():
                self._seed_current()
            elif not current.is_file() and legacy.is_file():
                # Dual-file period: copy, leave self.md in place.
                write_atomic(current, legacy.read_text(encoding="utf-8"))

            # Ensure meta exists when we have a live body (or after seed).
            if current.is_file() or legacy.is_file():
                if not meta_path.is_file():
                    body = self.self_digest()
                    write_json_atomic(meta_path, self._default_meta(body=body))

            # SELF v2 Drive append on resolved live file.
            live = self._resolved_live_path()
            if live is not None:
                migrated = maybe_migrate_self_v2(live)
                if migrated and live == current and meta_path.is_file():
                    meta = load_json_object(meta_path)
                    if meta is not None:
                        meta["current_content_sha256"] = content_sha256(
                            read_text_or_empty(live)
                        )
                        write_json_atomic(meta_path, meta)

            # Index heal when meta exists.
            if meta_path.is_file():
                meta = load_json_object(meta_path)
                if meta is not None:
                    before = list(meta.get("versions") or [])
                    healed = heal_versions_index(meta, self.versions_dir())
                    after = list(healed.get("versions") or [])
                    if before != after:
                        write_json_atomic(meta_path, healed)

    def maybe_migrate_self_v2(self) -> bool:
        """Run seed-v1 → Drive append migrate on resolved live path."""
        with self._lock:
            live = self._resolved_live_path()
            if live is None:
                return False
            return maybe_migrate_self_v2(live)

    # ── internals ────────────────────────────────────────────────────────

    def _seed_current(self) -> None:
        """Copy seed template to current.md when both current and self missing."""
        dest = self.current_path()
        if dest.exists():
            if not dest.is_file():
                raise FileExistsError(
                    f"seed dest exists but is not a file: {dest}"
                )
            return
        src = self._paths.resolve_seed(_SEED_SELF)
        if src is None:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    def _default_meta(self, *, body: str) -> dict[str, Any]:
        vid = mint_version_id()
        now = utc_now_iso()
        return {
            "schema_version": 1,
            "actor": "self",
            "display_name": "Elyra",
            "full_name": None,
            "goes_by": "Elyra",
            "real_name_known": True,
            "provisional": False,
            "current_version_id": vid,
            "draft_updated_at": None,
            "draft_meta": None,
            "current_promoted_at": now,
            "current_content_sha256": content_sha256(body) if body else None,
            "promote_count": 0,
            "versions": [],
            "notes": {},
        }
