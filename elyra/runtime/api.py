"""HTTP API and static Web UI.

Scope: REST JSON + SPA fallthrough for operator glass.
In scope: status, messages, wait reply, continuous toggle, full reset,
  lean glass catalogs (goals, moments, tools, skills, identity/users),
  multi-user session + identity panel (grants, promote, list/create users),
  provider/model/credential mutators, live usage + hard-stop override.
Out of scope: Glass draft editors, auth/IdP, multi-party chat protocol.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

_LOG = logging.getLogger(__name__)

from elyra.config import ElyraPaths
from elyra.goals import GoalsStore
from elyra.identity import (
    IdentityStore,
    PromoteContext,
    consume_grant,
    evaluate_promote_gate,
    first_active_token,
    load_active_token_set,
    mint_grant,
)
from elyra.identity.layout import content_sha256, read_text_or_empty, write_json_atomic
from elyra.llm.auth import VALID_SOURCES
from elyra.llm.queue import LlamaServerGate
from elyra.messages import list_messages
from elyra.moment import MomentStore
from elyra.presence.interject import REASON_BUFFER_FULL
from elyra.presence.worker import PresenceWorker
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState
from elyra.skills.catalog import SkillCatalog
from elyra.tools.registry import ToolRegistry
from elyra.users import UsersStore

WEB_DIR = Path(__file__).resolve().parent / "web"

# Path params: single safe segment (matches users/moment id style).
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Local dogfood session (not auth) — under data/runtime/.
_GLASS_SESSION_REL = Path("runtime") / "glass_session.json"
_DEFAULT_SESSION_USER = "operator"


def _route_payload(result: dict[str, Any], *, message: Any | None = None) -> dict[str, Any]:
    """Shape resolve_user_input result for HTTP clients."""
    out: dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "routed": result.get("routed"),
    }
    for key in (
        "reason",
        "wake_id",
        "wait_id",
        "message_id",
        "answer_wait_id",
        "cancel_stale_wait",
    ):
        if key in result and result[key] is not None:
            out[key] = result[key]
    if message is not None:
        out["message"] = (
            message if isinstance(message, dict) else getattr(message, "__dict__", message)
        )
    return out


def _safe_segment(raw: str) -> str | None:
    """Return path segment if safe, else None."""
    if not raw or not _SEGMENT_RE.fullmatch(raw):
        return None
    return raw


class ElyraApiHandler(BaseHTTPRequestHandler):
    paths: ElyraPaths
    gate: LlamaServerGate
    state: RuntimeState
    worker: PresenceWorker
    config: RuntimeConfig
    # Bound by start_api_server; None in legacy tests (PR6 wires routes).
    provider: Any = None
    # Lean catalog stores (file-backed; constructed once at server start).
    goals: GoalsStore
    moments: MomentStore
    identity: IdentityStore
    users: UsersStore
    tools: ToolRegistry | None
    skills: SkillCatalog | None
    # Glass multi-user session (shared across requests; bound at server start).
    glass_session: dict[str, Any]
    glass_session_lock: threading.RLock

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/status":
            snap = self.state.snapshot()
            # PresenceWorker.status_snapshot: phase, hop_count, last_tool,
            # pending_wait, continue_injects, queue_depth_by_band, …
            snap.update(self.worker.status_snapshot())
            snap.update(
                {
                    "home": str(self.paths.home),
                    "llama_busy": self.gate.busy,
                    "llama_operation": self.gate.current_label,
                    "api": f"http://{self.config.api_host}:{self.config.api_port}/",
                }
            )
            # Live provider + usage (meter.snapshot every GET — no secrets).
            if self.provider is not None:
                snap.update(self.provider.status_provider_fields())
                snap["usage"] = self.provider.usage_status_block()
            # Sandbox readiness (H2c): no secrets, no host absolute paths.
            snap["sandbox"] = self._sandbox_status_block()
            self._json(200, snap)
            return

        if path == "/api/messages":
            limit = int((qs.get("limit") or ["200"])[0])
            self._json(200, {"messages": list_messages(limit=limit, paths=self.paths)})
            return

        if path == "/api/health":
            self._json(200, {"ok": True})
            return

        if path == "/api/goals":
            status = (qs.get("status") or [None])[0]
            try:
                goals = self.goals.list_goals(status=status if status else None)
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
                return
            self._json(200, {"goals": goals})
            return

        if path == "/api/moments":
            limit_raw = (qs.get("limit") or ["50"])[0]
            try:
                limit = int(limit_raw)
            except (TypeError, ValueError):
                limit = 50
            # Negative would mean "no slice" in list_moments; clamp to empty.
            if limit < 0:
                limit = 0
            open_only = (qs.get("open") or ["0"])[0] in ("1", "true", "yes")
            moments = self.moments.list_moments(limit=limit, open_only=open_only)
            self._json(200, {"moments": moments})
            return

        if path.startswith("/api/moments/"):
            mid = _safe_segment(unquote(path[len("/api/moments/") :]))
            if mid is None:
                self._json(400, {"ok": False, "error": "invalid moment id"})
                return
            try:
                meta = self.moments.get_moment(mid)
            except ValueError:
                self._json(400, {"ok": False, "error": "invalid moment id"})
                return
            if meta is None:
                self._json(404, {"ok": False, "error": "moment not found"})
                return
            beats = self.moments.list_beats(mid)
            self._json(200, {"moment": meta, "beats": beats})
            return

        if path == "/api/identity":
            include_draft = (qs.get("include_draft") or ["0"])[0] in (
                "1",
                "true",
                "yes",
            )
            self._json(200, self._identity_self_payload(include_draft=include_draft))
            return

        if path == "/api/users":
            self._json(200, {"users": self._list_users_summary()})
            return

        if path == "/api/session":
            self._json(200, self._session_payload())
            return

        if path.startswith("/api/users/"):
            rest = unquote(path[len("/api/users/") :])
            # Promote is POST only; GET is single-segment user id.
            if "/" in rest:
                self._json(404, {"error": "not found"})
                return
            uid = _safe_segment(rest)
            if uid is None:
                self._json(400, {"ok": False, "error": "invalid user id"})
                return
            try:
                payload = self._identity_user_payload(uid)
            except ValueError:
                self._json(400, {"ok": False, "error": "invalid user id"})
                return
            self._json(200, payload)
            return

        if path == "/api/tools":
            if self.tools is None:
                self._json(200, {"tools": [], "error": "tools catalog unavailable"})
                return
            # Rescan disk so promote/delete/external edits match the glass catalog.
            try:
                self.tools.reload()
            except Exception as exc:  # noqa: BLE001 — catalog still serves last known
                _LOG.warning("tools.reload on GET /api/tools failed: %s", exc)
            catalog = []
            for name in self.tools.names():
                pkg = self.tools.get(name)
                if pkg is None:
                    continue
                catalog.append(
                    {
                        "name": pkg.meta.name,
                        "description": pkg.meta.description,
                        "kind": pkg.meta.kind,
                        "source": pkg.source,
                    }
                )
            self._json(200, {"tools": catalog})
            return

        if path == "/api/skills":
            if self.skills is None:
                self._json(200, {"skills": [], "error": "skills catalog unavailable"})
                return
            try:
                self.skills.reload()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("skills.reload on GET /api/skills failed: %s", exc)
            items = self.skills.catalog()
            # Enrich with source when available.
            enriched = []
            for item in items:
                meta = self.skills.get(item["name"])
                row = dict(item)
                if meta is not None:
                    row["source"] = meta.source
                enriched.append(row)
            self._json(200, {"skills": enriched})
            return

        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json()

        if path == "/api/messages":
            self._post_messages(body)
            return

        if path == "/api/wait/reply":
            self._post_wait_reply(body)
            return

        if path == "/api/goals":
            self._post_goals(body)
            return

        if path == "/api/reset":
            self._post_reset(body)
            return

        if path == "/api/users":
            self._post_users(body)
            return

        if path == "/api/identity/grants":
            self._post_identity_grants(body)
            return

        if path == "/api/identity/promote":
            self._post_identity_promote(body)
            return

        if path.startswith("/api/users/") and path.endswith("/promote"):
            mid = path[len("/api/users/") : -len("/promote")]
            uid = _safe_segment(unquote(mid))
            if uid is None or "/" in mid:
                self._json(400, {"ok": False, "error": "invalid user id"})
                return
            self._post_user_promote(uid, body)
            return

        self._json(404, {"error": "not found"})

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json()

        if path == "/api/continuous":
            self._patch_continuous(body)
            return

        if path == "/api/dev-speed":
            self._patch_dev_speed(body)
            return

        if path == "/api/provider":
            self._patch_provider(body)
            return

        if path == "/api/usage":
            self._patch_usage(body)
            return

        self._json(404, {"error": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json()

        if path == "/api/provider/api-key":
            self._put_api_key(body)
            return

        if path == "/api/session":
            self._put_session(body)
            return

        self._json(404, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/provider/api-key":
            self._delete_api_key()
            return

        self._json(404, {"error": "not found"})

    def _sandbox_status_block(self) -> dict[str, Any]:
        """Sandbox readiness for glass/API — no secrets, no host absolute paths.

        Prefer supervisor.sandbox_status() when available so async-warm state
        matches the install thread.
        """
        sup = getattr(self, "supervisor", None)
        if sup is not None and hasattr(sup, "sandbox_status"):
            try:
                return sup.sandbox_status()
            except Exception:  # noqa: BLE001 — fall through to direct status
                pass
        from elyra.sandbox.status import sandbox_status_block

        return sandbox_status_block(self.paths)

    def _provider_unavailable(self) -> bool:
        """True when provider runtime is not bound (legacy / incomplete start)."""
        return self.provider is None

    def _reject_if_resetting(self) -> bool:
        """Send 503 resetting when full reset is in progress; return True if rejected."""
        # PresenceWorker.is_resetting is a @property (bool), not a method.
        if bool(getattr(self.worker, "is_resetting", False)):
            self._json(503, {"ok": False, "error": "resetting"})
            return True
        return False

    # ── Glass session + identity panel helpers ───────────────────────────

    def _session_path(self) -> Path:
        return self.paths.data_dir / _GLASS_SESSION_REL

    def _load_session_user_id(self) -> str:
        """Return active glass session user_id (memory + optional file)."""
        lock = getattr(self, "glass_session_lock", None)
        sess = getattr(self, "glass_session", None)
        if lock is None or sess is None:
            return _DEFAULT_SESSION_USER
        with lock:
            uid = sess.get("user_id")
            if isinstance(uid, str) and uid.strip():
                return uid.strip()
            # Cold start: try disk.
            try:
                raw = self._session_path().read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    disk_uid = data.get("user_id")
                    if isinstance(disk_uid, str) and disk_uid.strip():
                        sess["user_id"] = disk_uid.strip()
                        return sess["user_id"]
            except (OSError, json.JSONDecodeError, TypeError):
                pass
            sess["user_id"] = _DEFAULT_SESSION_USER
            return _DEFAULT_SESSION_USER

    def _save_session_user_id(self, user_id: str) -> None:
        lock = getattr(self, "glass_session_lock", None)
        sess = getattr(self, "glass_session", None)
        if lock is None or sess is None:
            return
        with lock:
            sess["user_id"] = user_id
            path = self._session_path()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(path, {"user_id": user_id})
            except OSError as exc:
                _LOG.warning("glass_session write failed: %s", exc)

    def _session_payload(self) -> dict[str, Any]:
        uid = self._load_session_user_id()
        goes_by = uid
        try:
            goes_by = self.users.display_label(uid)
        except ValueError:
            goes_by = uid
        return {
            "user_id": uid,
            "goes_by": goes_by,
            "self_display_name": self.identity.display_name(),
        }

    def _versions_summary_from_get(self, got: dict[str, Any]) -> list[dict[str, Any]]:
        versions = got.get("versions") or []
        if not isinstance(versions, list):
            return []
        out: list[dict[str, Any]] = []
        for row in versions:
            if not isinstance(row, dict):
                continue
            vid = row.get("version_id")
            if not isinstance(vid, str):
                continue
            out.append(
                {
                    "version_id": vid,
                    "promoted_at": row.get("promoted_at"),
                    "sha256": row.get("sha256"),
                    "bytes": row.get("bytes"),
                }
            )
        return out

    def _identity_self_payload(self, *, include_draft: bool = False) -> dict[str, Any]:
        got = self.identity.get(which="current", list_versions=True)
        digest = got.get("body") if got.get("ok") else self.identity.self_digest()
        if not isinstance(digest, str):
            digest = self.identity.self_digest()
        live = self.identity.current_path()
        if not live.is_file():
            live = self.identity.self_path
        has_draft = bool(got.get("has_draft")) if got.get("ok") else self.identity.has_draft()
        meta = got.get("meta") if got.get("ok") else self.identity.get_meta()
        self_block: dict[str, Any] = {
            "path": str(live),
            "digest": digest,
            "body": digest,
            "meta": meta if isinstance(meta, dict) else {},
            "has_draft": has_draft,
            "versions": self._versions_summary_from_get(got if got.get("ok") else {}),
            "display_name": self.identity.display_name(),
        }
        # Glass identity panel needs draft preview for promote UX; always attach
        # when present. Query ``?include_draft=1`` remains accepted (design).
        if has_draft:
            draft_path = self.identity.draft_path()
            if draft_path.is_file():
                self_block["draft_body"] = read_text_or_empty(draft_path)
                self_block["draft_sha256"] = content_sha256(self_block["draft_body"])
        elif include_draft:
            self_block["draft_body"] = None
        return {"self": self_block}

    def _identity_user_payload(self, user_id: str) -> dict[str, Any]:
        """Richer user identity for glass; raises ValueError on bad id."""
        got = self.users.get(user_id, which="current", list_versions=True)
        profile = got.get("body") if got.get("ok") else self.users.profile(user_id)
        if not isinstance(profile, str):
            profile = self.users.profile(user_id)
        has_draft = (
            bool(got.get("has_draft")) if got.get("ok") else self.users.has_draft(user_id)
        )
        meta = got.get("meta") if got.get("ok") else self.users.get_meta(user_id)
        path = self.users.profile_path(user_id)
        payload: dict[str, Any] = {
            "ok": True,
            "user_id": user_id,
            "profile": profile,
            "body": profile,
            "path": str(path),
            "meta": meta if isinstance(meta, dict) else {},
            "has_draft": has_draft,
            "versions": self._versions_summary_from_get(got if got.get("ok") else {}),
            "goes_by": self.users.display_label(user_id),
        }
        if has_draft:
            draft_path = self.users.draft_path(user_id)
            if draft_path.is_file():
                payload["draft_body"] = read_text_or_empty(draft_path)
                payload["draft_sha256"] = content_sha256(payload["draft_body"])
        return payload

    def _list_users_summary(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for uid in self.users.list_user_ids():
            try:
                meta = self.users.get_meta(uid)
            except ValueError:
                continue
            goes_by = ""
            if isinstance(meta, dict):
                gb = meta.get("goes_by") or meta.get("display_name")
                if isinstance(gb, str) and gb.strip():
                    goes_by = gb.strip()
            if not goes_by:
                goes_by = self.users.display_label(uid)
            rows.append(
                {
                    "user_id": uid,
                    "goes_by": goes_by,
                    "provisional": bool(meta.get("provisional"))
                    if isinstance(meta, dict)
                    else False,
                    "real_name_known": bool(meta.get("real_name_known"))
                    if isinstance(meta, dict)
                    else False,
                }
            )
        return rows

    def _draft_sha_self(self) -> str | None:
        path = self.identity.draft_path()
        if not path.is_file():
            return None
        body = read_text_or_empty(path)
        if not body.strip():
            return None
        return content_sha256(body)

    def _draft_sha_user(self, user_id: str) -> str | None:
        path = self.users.draft_path(user_id)
        if not path.is_file():
            return None
        body = read_text_or_empty(path)
        if not body.strip():
            return None
        return content_sha256(body)

    def _put_session(self, body: dict[str, Any]) -> None:
        """PUT /api/session — ``{ user_id }`` switch active local profile."""
        if self._reject_if_resetting():
            return
        user_id = body.get("user_id")
        if not isinstance(user_id, str) or not user_id.strip():
            self._json(400, {"ok": False, "error": "user_id required"})
            return
        uid = user_id.strip()
        if _safe_segment(uid) is None:
            self._json(400, {"ok": False, "error": "invalid_user_id"})
            return
        # Prefer known users; allow switch to any jail-valid id that exists on disk.
        known = set(self.users.list_user_ids())
        if uid not in known:
            # Existence: current/legacy/meta under users root.
            try:
                live = self.users.profile(uid)
                meta_path = self.users.meta_path(uid)
            except ValueError:
                self._json(400, {"ok": False, "error": "invalid_user_id"})
                return
            if not live and not meta_path.is_file():
                self._json(404, {"ok": False, "error": "user_not_found", "user_id": uid})
                return
        self._save_session_user_id(uid)
        payload = self._session_payload()
        payload["ok"] = True
        self._json(200, payload)

    def _post_users(self, body: dict[str, Any]) -> None:
        """POST /api/users — create provisional user (K18 mint)."""
        if self._reject_if_resetting():
            return
        goes_by = body.get("goes_by")
        if not isinstance(goes_by, str) or not goes_by.strip():
            self._json(400, {"ok": False, "error": "missing_goes_by"})
            return
        user_id = body.get("user_id")
        if user_id is not None and not isinstance(user_id, str):
            self._json(400, {"ok": False, "error": "invalid_user_id"})
            return
        uid_arg = user_id.strip() if isinstance(user_id, str) and user_id.strip() else None
        result = self.users.create_user(goes_by.strip(), user_id=uid_arg, provisional=True)
        if not result.get("ok"):
            err = str(result.get("error") or "create_failed")
            code = 400
            if err == "user_id_exists":
                code = 400
            self._json(code, result)
            return
        self._json(201, result)

    def _post_identity_grants(self, body: dict[str, Any]) -> None:
        """POST /api/identity/grants — mint one-time self-promote grant (K14)."""
        if self._reject_if_resetting():
            return
        note = body.get("note")
        if note is not None and not isinstance(note, str):
            note = str(note)
        expires_at = body.get("expires_at")
        if expires_at is not None and not isinstance(expires_at, str):
            expires_at = None
        uses = body.get("uses", 1)
        try:
            uses_i = int(uses)
        except (TypeError, ValueError):
            uses_i = 1
        result = mint_grant(
            self.paths,
            note=note if isinstance(note, str) else None,
            expires_at=expires_at if isinstance(expires_at, str) else None,
            uses=uses_i,
        )
        if not result.get("ok"):
            self._json(400, result)
            return
        self._json(200, result)

    def _post_identity_promote(self, body: dict[str, Any]) -> None:
        """POST /api/identity/promote — Glass self promote (resolve→gate→consume→promote)."""
        if self._reject_if_resetting():
            return
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            self._json(400, {"ok": False, "error": "missing_reason"})
            return
        reason = reason.strip()
        expected = body.get("expected_draft_sha256")
        if expected is not None and not isinstance(expected, str):
            expected = None

        grant_token = body.get("grant_token")
        if isinstance(grant_token, str) and grant_token.strip():
            resolved = grant_token.strip()
        else:
            # Glass resolve: first active file token (env only if body supplies it).
            resolved = first_active_token(self.paths, include_env=False)

        if not resolved:
            self._json(400, {"ok": False, "error": "self_grant_required", "actor": "self"})
            return

        has_draft = self.identity.has_draft()
        draft_sha = self._draft_sha_self()
        operator_tokens = load_active_token_set(self.paths)

        gate = evaluate_promote_gate(
            PromoteContext(
                actor="self",
                target_user_id=None,
                session_user_id=self._load_session_user_id(),
                wake_kind=None,
                moment_id="",
                reason=reason,
                grant_token=resolved,
                has_draft=has_draft and draft_sha is not None,
                draft_sha256=draft_sha,
                expected_draft_sha256=expected,
                identity_promote_user_ok=False,
                identity_promote_any_user=False,
                operator_grant_tokens=operator_tokens,
                allow_self_promote_without_grant=False,
            )
        )
        if not gate.allowed:
            self._json(
                400,
                {
                    "ok": False,
                    "error": gate.error_reason or "promote_denied",
                    "detail": gate.detail,
                    "actor": "self",
                },
            )
            return

        consumed = consume_grant(self.paths, resolved)
        if not consumed.get("ok"):
            self._json(
                400,
                {
                    "ok": False,
                    "error": str(consumed.get("error") or "grant_exhausted"),
                    "actor": "self",
                },
            )
            return

        try:
            result = self.identity.promote(
                reason=reason,
                expected_draft_sha256=expected,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("glass self promote failed after grant consume")
            self._json(
                500,
                {
                    "ok": False,
                    "error": f"promote_failed:{type(exc).__name__}",
                    "actor": "self",
                    "grant_consumed": True,
                },
            )
            return

        if not result.get("ok"):
            out = dict(result)
            out["grant_consumed"] = True
            self._json(400, out)
            return
        self._json(200, result)

    def _post_user_promote(self, user_id: str, body: dict[str, Any]) -> None:
        """POST /api/users/<id>/promote — Glass medium promote (admin panel)."""
        if self._reject_if_resetting():
            return
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            self._json(400, {"ok": False, "error": "missing_reason"})
            return
        reason = reason.strip()
        expected = body.get("expected_draft_sha256")
        if expected is not None and not isinstance(expected, str):
            expected = None

        try:
            known = user_id in set(self.users.list_user_ids())
            if not known:
                # Soft existence check via profile/meta
                if not self.users.meta_path(user_id).is_file() and not self.users.profile(
                    user_id
                ):
                    self._json(
                        404,
                        {"ok": False, "error": "user_not_found", "user_id": user_id},
                    )
                    return
        except ValueError:
            self._json(400, {"ok": False, "error": "invalid_user_id"})
            return

        has_draft = self.users.has_draft(user_id)
        draft_sha = self._draft_sha_user(user_id)
        session_uid = self._load_session_user_id()

        # Glass panel: operator may promote any user (identity_promote_any_user)
        # and without social wake (identity_promote_user_ok).
        gate = evaluate_promote_gate(
            PromoteContext(
                actor="user",
                target_user_id=user_id,
                session_user_id=session_uid,
                wake_kind=None,
                moment_id="",
                reason=reason,
                grant_token=None,
                has_draft=has_draft and draft_sha is not None,
                draft_sha256=draft_sha,
                expected_draft_sha256=expected,
                identity_promote_user_ok=True,
                identity_promote_any_user=True,
                operator_grant_tokens=frozenset(),
                allow_self_promote_without_grant=False,
                target_user_exists=True,
            )
        )
        if not gate.allowed:
            self._json(
                400,
                {
                    "ok": False,
                    "error": gate.error_reason or "promote_denied",
                    "detail": gate.detail,
                    "actor": "user",
                    "user_id": user_id,
                },
            )
            return

        try:
            result = self.users.promote(
                user_id,
                reason=reason,
                expected_draft_sha256=expected,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("glass user promote failed for %s", user_id)
            self._json(
                500,
                {
                    "ok": False,
                    "error": f"promote_failed:{type(exc).__name__}",
                    "actor": "user",
                    "user_id": user_id,
                },
            )
            return

        if not result.get("ok"):
            self._json(400, result)
            return
        self._json(200, result)

    def _provider_response_fields(self) -> dict[str, Any]:
        """Non-secret provider + credential fields for mutator responses."""
        assert self.provider is not None
        fields = self.provider.status_provider_fields()
        return {
            "ok": True,
            "model": fields.get("model"),
            "model_label": fields.get("model_label"),
            "credential_source": fields.get("credential_source"),
            "credential_ok": fields.get("credential_ok"),
            "credential_detail": fields.get("credential_detail"),
            "credential_expires_at": fields.get("credential_expires_at"),
            "credential_email": fields.get("credential_email"),
            "api_key_configured": fields.get("api_key_configured"),
            "provider": fields.get("provider"),
            "models_available": fields.get("models_available"),
        }

    def _patch_provider(self, body: dict[str, Any]) -> None:
        """PATCH /api/provider — ``{ model?, credential_source? }`` (at least one).

        Successful model/credential changes persist prefs and rebuild stack when
        needed (see ProviderRuntime.apply_*). Never echoes secrets.
        """
        if self._provider_unavailable():
            self._json(503, {"ok": False, "error": "provider unavailable"})
            return
        if self._reject_if_resetting():
            return

        has_model = "model" in body
        has_source = "credential_source" in body
        if not has_model and not has_source:
            self._json(
                400,
                {"ok": False, "error": "model or credential_source required"},
            )
            return

        provider = self.provider
        assert provider is not None

        if has_model:
            model = body.get("model")
            if not isinstance(model, str) or not model.strip():
                self._json(400, {"ok": False, "error": "unknown_model"})
                return
            mid = model.strip()
            available = list(provider.status_provider_fields().get("models_available") or [])
            # Empty list (pre-refresh / local cold): allow any non-empty wire id.
            if available and mid not in available:
                self._json(400, {"ok": False, "error": "unknown_model"})
                return
            try:
                provider.apply_model(mid)
            except ValueError:
                self._json(400, {"ok": False, "error": "unknown_model"})
                return

        if has_source:
            source = body.get("credential_source")
            if not isinstance(source, str) or source.strip() not in VALID_SOURCES:
                self._json(400, {"ok": False, "error": "invalid_credential_source"})
                return
            resolution = provider.apply_credential_source(source.strip())
            if not getattr(resolution, "ok", False):
                # Previous source + stack left intact by apply_credential_source.
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "credential_unavailable",
                        "credential_detail": getattr(resolution, "detail", None),
                        "credential_source": provider.status_provider_fields().get(
                            "credential_source"
                        ),
                    },
                )
                return

        self._json(200, self._provider_response_fields())

    def _put_api_key(self, body: dict[str, Any]) -> None:
        """PUT /api/provider/api-key — write-only; never echo the key.

        Does not auto-switch credential_source. Rebuilds when active source is
        already ``api_key`` so cold-start Failing clients become live.
        """
        if self._provider_unavailable():
            self._json(503, {"ok": False, "error": "provider unavailable"})
            return
        if self._reject_if_resetting():
            return

        api_key = body.get("api_key")
        if not isinstance(api_key, str) or not api_key.strip():
            self._json(400, {"ok": False, "error": "api_key required"})
            return

        provider = self.provider
        assert provider is not None
        try:
            provider.put_api_key(api_key.strip())
        except ValueError as exc:
            # empty_api_key from auth store
            self._json(400, {"ok": False, "error": str(exc) or "api_key required"})
            return

        fields = provider.status_provider_fields()
        self._json(
            200,
            {
                "ok": True,
                "api_key_configured": bool(fields.get("api_key_configured")),
                "credential_ok": bool(fields.get("credential_ok")),
                "credential_source": fields.get("credential_source"),
                "credential_detail": fields.get("credential_detail"),
            },
        )

    def _delete_api_key(self) -> None:
        """DELETE /api/provider/api-key — remove stored key; no silent source switch."""
        if self._provider_unavailable():
            self._json(503, {"ok": False, "error": "provider unavailable"})
            return
        if self._reject_if_resetting():
            return

        provider = self.provider
        assert provider is not None
        provider.delete_api_key()
        fields = provider.status_provider_fields()
        self._json(
            200,
            {
                "ok": True,
                "api_key_configured": bool(fields.get("api_key_configured")),
                "credential_ok": bool(fields.get("credential_ok")),
                "credential_source": fields.get("credential_source"),
                "credential_detail": fields.get("credential_detail"),
            },
        )

    def _patch_usage(self, body: dict[str, Any]) -> None:
        """PATCH /api/usage — ``{ "hard_stop_override": bool }`` only.

        Does not reset counters or change credentials. Override default is OFF.
        """
        if self._provider_unavailable():
            self._json(503, {"ok": False, "error": "provider unavailable"})
            return
        if self._reject_if_resetting():
            return

        if "hard_stop_override" not in body:
            self._json(400, {"ok": False, "error": "hard_stop_override required"})
            return
        value = body["hard_stop_override"]
        if not isinstance(value, bool):
            self._json(
                400,
                {"ok": False, "error": "hard_stop_override must be a boolean"},
            )
            return

        provider = self.provider
        assert provider is not None
        if provider.meter is None:
            self._json(503, {"ok": False, "error": "meter unavailable"})
            return

        usage = provider.set_hard_stop_override(value)
        self._json(200, {"ok": True, "usage": usage})

    def _patch_continuous(self, body: dict[str, Any]) -> None:
        """PATCH /api/continuous — ``{ "enabled": bool }`` (K17).

        Calls ``worker.set_continuous_enabled``: persists
        ``data/runtime/continuous.json``; OFF cancels pending
        ``moment_continue`` only (not task_ready / timers / user).
        """
        if "enabled" not in body:
            self._json(400, {"ok": False, "error": "enabled required"})
            return
        enabled = body["enabled"]
        if not isinstance(enabled, bool):
            self._json(400, {"ok": False, "error": "enabled must be a boolean"})
            return
        result = self.worker.set_continuous_enabled(enabled)
        if result.get("error") == "resetting":
            self._json(503, result)
            return
        self._json(200, result)

    def _patch_dev_speed(self, body: dict[str, Any]) -> None:
        """PATCH /api/dev-speed — ``{ "enabled"?: bool, "delay_seconds"?: number }``.

        Inter-hop pause for followable glass. Default product ON (8s, 5–15 band).
        """
        if "enabled" not in body and "delay_seconds" not in body:
            self._json(
                400,
                {"ok": False, "error": "enabled or delay_seconds required"},
            )
            return
        enabled = body.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            self._json(400, {"ok": False, "error": "enabled must be a boolean"})
            return
        delay = body.get("delay_seconds")
        if delay is not None:
            if isinstance(delay, bool) or not isinstance(delay, (int, float)):
                self._json(
                    400,
                    {"ok": False, "error": "delay_seconds must be a number"},
                )
                return
        result = self.worker.set_dev_speed(
            enabled=enabled if isinstance(enabled, bool) else None,
            delay_seconds=float(delay) if delay is not None else None,
        )
        if result.get("error") == "resetting":
            self._json(503, result)
            return
        self._json(200, result)

    def _post_reset(self, body: dict[str, Any]) -> None:
        """POST /api/reset — full runtime reset with confirm body (design F).

        Body: ``{"confirm": "RESET", "clear_sandbox": true, ...}``.
        Routes to ``worker.reset_runtime_state`` only (worker-owned lock).
        """
        confirm = body.get("confirm")
        if confirm != "RESET":
            self._json(
                400,
                {
                    "ok": False,
                    "error": "confirm required",
                    "detail": 'body.confirm must be the string "RESET"',
                },
            )
            return
        flags = {
            k: body[k]
            for k in (
                "clear_sandbox",
                "clear_drafts",
                "clear_local_tools",
            )
            if k in body
        }
        # Unsupported flags (skills wipe / reseed) are ignored by normalize;
        # do not pass them through the allowlist.
        result = self.worker.reset_runtime_state(flags if flags else None)
        if result.get("ok"):
            self._json(200, result)
            return
        err = result.get("error")
        if err == "worker_busy":
            self._json(409, result)
            return
        if err == "resetting":
            self._json(503, result)
            return
        if err == "partial_reset":
            self._json(500, result)
            return
        self._json(500, result)

    def _post_goals(self, body: dict[str, Any]) -> None:
        """POST /api/goals — create a goal (lean glass / operator).

        Gated on worker reset: 503 while full reset is in progress so goals.json
        cannot be repopulated mid-clear.
        """
        title = str(body.get("title") or "").strip()
        if not title:
            self._json(400, {"ok": False, "error": "title required"})
            return
        acceptance = body.get("acceptance")
        if acceptance is not None:
            acceptance = str(acceptance)
        status = str(body.get("status") or "open")
        try:
            goal, err = self.worker.create_goal_if_allowed(
                title,
                acceptance=acceptance,
                status=status,
            )
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        if err is not None:
            code = 503 if err.get("error") == "resetting" else 400
            self._json(code, err)
            return
        # Keep catalog store in sync when API was constructed with a separate
        # GoalsStore instance (same path; create already persisted).
        self._json(200, {"ok": True, "goal": goal})

    def _post_messages(self, body: dict[str, Any]) -> None:
        """POST /api/messages — glass chat → resolve_user_input (from_wait_api=False).

        Append is gated through ``worker.append_message_if_allowed`` (check +
        write under worker lock) so concurrent full reset cannot leave chat
        residue after ``ok: true``.

        Routing matrix (worker phase + pending wait):
        - in_moment → interject buffer
        - in_moment + buffer full → still ``routed=interject``, ``ok=false``,
          ``reason=interjection_buffer_full``, ``wake_id`` set (overflow wake;
          HTTP 200 for glass notice — do not key only on ``routed``)
        - waiting (+ matching wait) → wait_reply
        - idle → user_message (cancel stale wait for user when present)
        """
        content = str(body.get("content") or "").strip()
        user_id = str(body.get("user_id") or "operator")
        if not content:
            self._json(400, {"ok": False, "error": "content required", "reason": "empty_content"})
            return
        msg, err = self.worker.append_message_if_allowed(
            "user", content, user_id=user_id
        )
        if err is not None:
            self._json(self._status_for_route(err), err)
            return
        assert msg is not None
        result = self.worker.resolve_user_input(
            content,
            user_id=user_id,
            message_id=msg.id,
            from_wait_api=False,
        )
        payload = _route_payload(result, message=msg)
        self._json(self._status_for_route(result), payload)

    def _post_wait_reply(self, body: dict[str, Any]) -> None:
        """POST /api/wait/reply — explicit wait answer (choice and/or free text).

        Always sets from_wait_api=True so a durable pending wait for the user
        routes to wait_reply even if phase briefly reads as idle.
        Message append is reset-gated (same as ``/api/messages``).
        """
        content_raw = body.get("content")
        content = str(content_raw).strip() if content_raw is not None else ""
        choice_raw = body.get("choice")
        choice: str | None
        if choice_raw is None:
            choice = None
        else:
            choice = str(choice_raw).strip() if isinstance(choice_raw, str) else str(choice_raw)
            if not choice:
                choice = None
        user_id = str(body.get("user_id") or "operator")

        if not content and not choice:
            self._json(
                400,
                {
                    "ok": False,
                    "error": "content or choice required",
                    "reason": "empty_content",
                },
            )
            return

        display = content or (choice or "")
        msg, err = self.worker.append_message_if_allowed(
            "user", display, user_id=user_id
        )
        if err is not None:
            self._json(self._status_for_route(err), err)
            return
        assert msg is not None
        result = self.worker.resolve_user_input(
            content or (choice or ""),
            user_id=user_id,
            choice=choice,
            from_wait_api=True,
            message_id=msg.id,
        )
        payload = _route_payload(result, message=msg)
        self._json(self._status_for_route(result), payload)

    @staticmethod
    def _status_for_route(result: dict[str, Any]) -> int:
        """HTTP status for a resolve_user_input result.

        - ok → 200
        - interjection_buffer_full → 200 (message enqueued as wake; glass notice)
        - resetting → 503 (temporary; full reset in progress)
        - empty / other client errors → 400
        """
        if result.get("ok"):
            return 200
        if result.get("reason") == REASON_BUFFER_FULL:
            return 200
        if result.get("error") == "resetting" or result.get("reason") == "resetting":
            return 503
        return 400

    def _serve_static(self, path: str) -> None:
        if path == "/" or path == "":
            rel = "index.html"
        else:
            rel = path.lstrip("/")
        # Prevent path escape.
        candidate = (WEB_DIR / rel).resolve()
        if not str(candidate).startswith(str(WEB_DIR.resolve())):
            self._send(403, b"forbidden", "text/plain")
            return
        if not candidate.is_file():
            # SPA-style: unknown paths → index
            candidate = WEB_DIR / "index.html"
        data = candidate.read_bytes()
        ctype = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self._send(200, data, ctype)


def _try_tool_registry(paths: ElyraPaths) -> ToolRegistry | None:
    try:
        return ToolRegistry(paths)
    except Exception:  # noqa: BLE001 — catalog optional for glass
        return None


def _try_skill_catalog(paths: ElyraPaths) -> SkillCatalog | None:
    try:
        return SkillCatalog(paths)
    except Exception:  # noqa: BLE001 — catalog optional for glass
        return None


def start_api_server(
    config: RuntimeConfig,
    *,
    paths: ElyraPaths,
    gate: LlamaServerGate,
    state: RuntimeState,
    worker: PresenceWorker,
    provider: Any = None,
    supervisor: Any = None,
    goals: GoalsStore | None = None,
    moments: MomentStore | None = None,
    identity: IdentityStore | None = None,
    users: UsersStore | None = None,
    tools: ToolRegistry | None = ...,  # type: ignore[assignment]
    skills: SkillCatalog | None = ...,  # type: ignore[assignment]
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Start ThreadingHTTPServer serving REST + static glass.

    Catalog stores default from ``paths``. Pass ``tools=None`` / ``skills=None``
    to skip disk scan (tests without bundled roots). Omit (ellipsis) to auto-build.
    ``provider`` is the shared ProviderRuntime used by status + provider routes.
    ``supervisor`` optional: used for live sandbox status (async warm).
    """
    if tools is ...:
        tools = _try_tool_registry(paths)
    if skills is ...:
        skills = _try_skill_catalog(paths)

    # Default session user: operator if present, else first known, else "operator".
    users_store = users or UsersStore(paths)
    known_ids = users_store.list_user_ids()
    default_uid = _DEFAULT_SESSION_USER
    if default_uid not in known_ids and known_ids:
        default_uid = known_ids[0]
    # Prefer disk session if valid.
    session_path = paths.data_dir / _GLASS_SESSION_REL
    try:
        raw = session_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            disk_uid = data.get("user_id")
            if isinstance(disk_uid, str) and disk_uid.strip():
                candidate = disk_uid.strip()
                if candidate in known_ids or not known_ids:
                    default_uid = candidate
    except (OSError, json.JSONDecodeError, TypeError):
        pass

    handler = type(
        "BoundHandler",
        (ElyraApiHandler,),
        {
            "paths": paths,
            "gate": gate,
            "state": state,
            "worker": worker,
            "config": config,
            "provider": provider,
            "supervisor": supervisor,
            "goals": goals or GoalsStore(paths),
            "moments": moments or MomentStore(paths),
            "identity": identity or IdentityStore(paths),
            "users": users_store,
            "tools": tools,
            "skills": skills,
            "glass_session": {"user_id": default_uid},
            "glass_session_lock": threading.RLock(),
        },
    )
    server = ThreadingHTTPServer((config.api_host, config.api_port), handler)
    thread = threading.Thread(target=server.serve_forever, name="elyra-api", daemon=True)
    thread.start()
    return server, thread
