"""HTTP API and static Web UI.

Scope: REST JSON + SPA fallthrough for operator glass.
In scope: status, messages, wait reply, continuous toggle, full reset,
  lean glass catalogs (goals, moments, tools, skills, identity/users),
  tool/skill package inspector (GET detail + package VCS versions, read-only),
  multi-user session + identity panel (grants, promote, list/create users),
  provider/model/credential mutators, live usage + hard-stop override,
  media upload/serve + message attachment_ids (PR3 / KD15, KD18, KD23),
  STT proxy POST /api/stt (PR6 / KD4, KD9, KD18),
  named secrets store GET/PUT/DELETE + grants (PR5 / IK10),
  xAI OAuth device login/logout GET/POST /api/auth/xai/* (PR3 — server-polled
  device-code; never returns tokens or device_code; optional loopback Origin),
  memory inspect GET /api/memory/* (PR9 — meal context + atoms, read-only;
  Phase 2 PR7 — vectors health/status/neighbors;
  MM #124 PR4 — POST vectors/neighbors media-as-query (att_id ± q);
  Phase 2a PR-A5 — graph overview/session/neighbors + optional debug POST).
Out of scope: Glass draft editors, multi-party chat protocol,
  TTS, vision expand, glass UI rewrite, glass promote/revert for packages.
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
from elyra.llm.auth import VALID_SOURCES, resolve_bearer
from elyra.llm.oauth_store import public_meta as oauth_public_meta
from elyra.llm.queue import ChatRequestGate
from elyra.media.tts import (
    TTS_DEFAULT_LANGUAGE,
    TTS_DEFAULT_PROFILE,
    TTS_DEFAULT_VOICE,
    get_or_synthesize,
)
from elyra.media.limits import allow_stt, allow_tts
from elyra.media import (
    ATTACHMENT_ORIGINS,
    DEFAULT_STT_MODEL,
    MAX_ATTACHMENTS_PER_MESSAGE,
    MAX_AUDIO_BYTES,
    MAX_CONCURRENT_UPLOADS,
    MAX_JSON_BODY_BYTES,
    MAX_MEDIA_REQUEST_BYTES,
    MediaStore,
    SttError,
    TtsError,
    allow_stt,
    allow_tts,
    ensure_media_dirs,
    max_bytes_for_kind,
    parse_content_length,
    parse_multipart_fields,
    parse_multipart_files,
    sniff_mime_kind_source,
    stream_to_temp,
    stt_enabled,
    synthesize,
    transcribe,
    tts_enabled,
    validate_att_id,
)
from elyra.messages import get_message, list_messages
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

# In-process concurrent upload cap (KD15); shared across handler instances.
_UPLOAD_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_UPLOADS)

# Loopback hosts for optional Origin/Referer CSRF check on auth mutators (PR3).
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


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
    gate: ChatRequestGate
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
        """Write a complete response. Client disconnect is soft (hard reload)."""
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Browser aborted (hard reload / navigation). Not a handler fault.
            return

    def _json(self, code: int, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8")

    def _read_json(self) -> dict[str, Any] | None:
        """Read a JSON object body with Content-Length pre-check (PR3).

        Rejects missing/invalid Content-Length (400) and bodies over 1 MiB
        (413) **before** reading. Returns ``None`` when an error response was
        already sent. Empty body (``Content-Length: 0``) → ``{}``.
        """
        length = parse_content_length(self.headers.get("Content-Length"))
        if length is None:
            self._json(
                400,
                {
                    "ok": False,
                    "error": "content_length_required",
                    "reason": "content_length_required",
                },
            )
            return None
        if length > MAX_JSON_BODY_BYTES:
            self._json(
                413,
                {
                    "ok": False,
                    "error": "payload_too_large",
                    "reason": "content_length",
                    "max_bytes": MAX_JSON_BODY_BYTES,
                },
            )
            return None
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # GET /api/messages/{id}/tts — play saved text (PR7 / KD3).
        if path.startswith("/api/messages/") and path.endswith("/tts"):
            self._message_tts(path, qs=qs, body=None)
            return

        if path == "/api/status":
            snap = self.state.snapshot()
            # PresenceWorker.status_snapshot: phase, hop_count, last_tool,
            # pending_wait, continue_injects, queue_depth_by_band, …
            snap.update(self.worker.status_snapshot())
            try:
                from elyra.media.activity import recent_media_activity
                from elyra.media.gc import media_stats
                from elyra.media import MediaStore
                snap["media"] = media_stats(MediaStore(self.paths))
                snap["media_activity"] = recent_media_activity(limit=8)
            except Exception:
                snap["media"] = {"error": "unavailable"}
                snap["media_activity"] = []
            snap.update(
                {
                    "home": str(self.paths.home),
                    "chat_busy": self.gate.busy,
                    "chat_operation": self.gate.current_label,
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

        # Memory inspect (PR9 + Phase 2 PR7) — read-only; no secrets; fail closed.
        if path == "/api/memory" or path == "/api/memory/":
            self._get_memory_overview()
            return
        if path == "/api/memory/context":
            self._get_memory_context(qs)
            return
        if path == "/api/memory/atoms":
            self._get_memory_atoms(qs)
            return
        if path.startswith("/api/memory/atoms/"):
            aid = _safe_segment(unquote(path[len("/api/memory/atoms/") :]))
            if aid is None:
                self._json(400, {"ok": False, "error": "invalid atom id"})
                return
            self._get_memory_atom(aid)
            return
        if path == "/api/memory/vectors" or path == "/api/memory/vectors/":
            self._get_memory_vectors()
            return
        if path == "/api/memory/vectors/atoms":
            self._get_memory_vectors_atoms(qs)
            return
        if path == "/api/memory/vectors/neighbors":
            self._get_memory_vectors_neighbors(qs)
            return
        # Phase 2a Graph tab (PR-A5) — overview / session / neighbors.
        if path == "/api/memory/graph" or path == "/api/memory/graph/":
            self._get_memory_graph()
            return
        if path == "/api/memory/graph/session":
            self._get_memory_graph_session(qs)
            return
        if path == "/api/memory/graph/neighbors":
            self._get_memory_graph_neighbors(qs)
            return
        if path.startswith("/api/memory/"):
            self._json(404, {"ok": False, "error": "not found"})
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

        if path == "/api/secrets":
            self._get_secrets()
            return

        # xAI OAuth device login (PR3) — public meta / status; never tokens.
        if path == "/api/auth/xai" or path == "/api/auth/xai/":
            self._get_auth_xai()
            return
        if path == "/api/auth/xai/device/status":
            self._get_auth_xai_device_status()
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

        if path.startswith("/api/tools/"):
            self._get_tool_detail(path, qs)
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

        if path.startswith("/api/skills/"):
            self._get_skill_detail(path, qs)
            return

        if path.startswith("/api/media/") or path == "/api/media":
            self._get_media(path)
            return

        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/media":
            self._post_media()
            return


        # POST /api/messages/{id}/tts — same as GET; optional JSON body params.
        if path.startswith("/api/messages/") and path.endswith("/tts"):
            clen = self.headers.get("Content-Length")
            body = {}
            if clen and clen.isdigit() and int(clen) > 0:
                raw = self.rfile.read(int(clen))
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    body = {}
            self._message_tts(path, qs=qs, body=body or {})
            return

        if path == "/api/imagine":
            # PR9 / KD10: Grok Imagine productization deferred — stub only.
            self._json(
                501,
                {
                    "ok": False,
                    "error": "not implemented",
                    "reason": "not_implemented",
                    "hint": "Grok Imagine deferred",
                },
            )
            return
        if path == "/api/stt":
            self._post_stt()
            return

        body = self._read_json()
        if body is None:
            return

        if path == "/api/messages":
            self._post_messages(body)
            return

        if path == "/api/wait/reply":
            self._post_wait_reply(body)
            return

        # xAI OAuth device login mutators (PR3) — never return tokens/device_code.
        if path == "/api/auth/xai/device/start":
            self._post_auth_xai_device_start(body)
            return
        if path == "/api/auth/xai/device/cancel":
            self._post_auth_xai_device_cancel(body)
            return
        if path == "/api/auth/xai/logout":
            self._post_auth_xai_logout(body)
            return

        if path == "/api/goals":
            self._post_goals(body)
            return

        if path == "/api/reset":
            self._post_reset(body)
            return

        if path == "/api/memory/vectors/rebuild":
            self._post_memory_vectors_rebuild(body)
            return

        if path == "/api/memory/vectors/neighbors":
            self._post_memory_vectors_neighbors(body)
            return

        if path == "/api/memory/ladder/rebuild":
            self._post_memory_ladder_rebuild(body)
            return

        if path == "/api/memory/graph/traverse":
            self._post_memory_graph_traverse(body)
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
        if body is None:
            return

        if path == "/api/continuous":
            self._patch_continuous(body)
            return

        if path == "/api/dev-speed":
            self._patch_dev_speed(body)
            return

        if path == "/api/semantic-wait":
            self._patch_semantic_wait(body)
            return

        if path == "/api/meal-budget":
            self._patch_meal_budget(body)
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
        if body is None:
            return

        if path == "/api/provider/api-key":
            self._put_api_key(body)
            return

        if path == "/api/secrets":
            self._put_secret(body)
            return

        if path.startswith("/api/secrets/") and path.endswith("/grants"):
            rest = unquote(path[len("/api/secrets/") : -len("/grants")])
            if rest.endswith("/"):
                rest = rest[:-1]
            name = _safe_segment(rest)
            if name is None or "/" in rest:
                self._json(400, {"ok": False, "error": "invalid secret name"})
                return
            self._put_secret_grants(name, body)
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

        if path.startswith("/api/secrets/"):
            rest = unquote(path[len("/api/secrets/") :])
            name = _safe_segment(rest)
            if name is None or "/" in rest:
                self._json(400, {"ok": False, "error": "invalid secret name"})
                return
            self._delete_secret(name)
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

    def _origin_is_loopback_ok(self) -> bool:
        """Optional cheap CSRF: if Origin/Referer present, host must be loopback.

        Missing Origin and Referer → allow (curl / CLI). Non-loopback host → deny.
        KD23 residual: accepted for loopback-only bind; full CSRF token later if
        bind opens off-loopback.
        """
        origin = self.headers.get("Origin") or self.headers.get("Referer")
        if not origin or not isinstance(origin, str) or not origin.strip():
            return True
        try:
            parsed = urlparse(origin.strip())
        except Exception:  # noqa: BLE001
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            # Opaque / non-http origin — reject when present but unparseable.
            return False
        if host in _LOOPBACK_HOSTS:
            return True
        # Also accept literal IPv6 without brackets from some parsers.
        if host in {"0:0:0:0:0:0:0:1"}:
            return True
        return False

    def _reject_if_auth_origin_bad(self) -> bool:
        """Send 403 when Origin/Referer is present and not loopback."""
        if self._origin_is_loopback_ok():
            return False
        self._json(
            403,
            {
                "ok": False,
                "error": "origin_not_allowed",
                "detail": "auth mutators require loopback Origin/Referer when set",
            },
        )
        return True

    @staticmethod
    def _strip_auth_secrets(payload: dict[str, Any]) -> dict[str, Any]:
        """Defensive: never return tokens or device_code from auth endpoints."""
        banned = {
            "access_token",
            "refresh_token",
            "device_code",
            "id_token",
            "token",
        }
        return {k: v for k, v in payload.items() if k not in banned}

    def _reject_if_resetting(self) -> bool:
        """Send 503 resetting when full reset is in progress; return True if rejected."""
        # PresenceWorker.is_resetting is a @property (bool), not a method.
        if bool(getattr(self.worker, "is_resetting", False)):
            self._json(503, {"ok": False, "error": "resetting"})
            return True
        return False

    # ── Memory inspect (PR9) ─────────────────────────────────────────────

    def _memory_flags_block(self) -> dict[str, Any]:
        """Flags + store health from worker status (no secrets)."""
        try:
            snap = self.worker.status_snapshot()
        except Exception as exc:  # noqa: BLE001
            return {
                "enabled": False,
                "write_atoms": False,
                "backend": "unknown",
                "store_open": False,
                "ok": False,
                "error": str(exc) or type(exc).__name__,
            }
        mem = snap.get("memory") if isinstance(snap, dict) else None
        if not isinstance(mem, dict):
            mem = {}
        out = dict(mem)
        out["active_moment_id"] = snap.get("active_moment_id") if isinstance(snap, dict) else None
        out["phase"] = snap.get("phase") if isinstance(snap, dict) else None
        return out

    def _get_memory_overview(self) -> None:
        """GET /api/memory — flags, store health, whether a last meal exists."""
        block = self._memory_flags_block()
        ok = bool(block.get("ok"))
        payload: dict[str, Any] = {
            "ok": ok,
            "memory": block,
            "has_last_meal": bool(block.get("has_last_meal")),
            "tabs": {
                "context": True,
                "atoms": True,
                "vectors": {"stub": False, "phase": "2"},
                "graph": {"stub": False, "phase": "2a"},
            },
        }
        if not ok and block.get("error"):
            payload["error"] = block.get("error")
        self._json(200, payload)

    def _get_memory_context(self, qs: dict[str, list[str]]) -> None:
        """GET /api/memory/context — last/current meal by channel labels.

        Prefer last compose snapshot from the worker. Optional
        ``?compose=1`` rebuilds on demand for the open/active moment when
        the store is healthy (careful path; may be empty without atoms).
        """
        flags = self._memory_flags_block()
        force_compose = (qs.get("compose") or ["0"])[0] in ("1", "true", "yes")
        snap = None
        if hasattr(self.worker, "last_meal_snapshot"):
            try:
                snap = self.worker.last_meal_snapshot()
            except Exception:  # noqa: BLE001
                snap = None

        if snap and not force_compose:
            self._json(
                200,
                {
                    "ok": True,
                    "meal": snap,
                    "memory": flags,
                    "source": snap.get("source") or "last_compose",
                },
            )
            return

        # Fail closed when store not usable and no snapshot to show.
        store_ok = bool(flags.get("ok")) and bool(flags.get("store_open"))
        if not store_ok and not snap:
            self._json(
                200,
                {
                    "ok": False,
                    "error": flags.get("error") or "store_unavailable",
                    "meal": None,
                    "memory": flags,
                },
            )
            return

        if force_compose or not snap:
            composed = self._compose_meal_for_inspect(flags)
            if composed is not None:
                self._json(
                    200,
                    {
                        "ok": True,
                        "meal": composed,
                        "memory": flags,
                        "source": composed.get("source") or "on_demand",
                    },
                )
                return
            if snap:
                self._json(
                    200,
                    {
                        "ok": True,
                        "meal": snap,
                        "memory": flags,
                        "source": snap.get("source") or "last_compose",
                        "compose_error": "on_demand_failed",
                    },
                )
                return
            self._json(
                200,
                {
                    "ok": False,
                    "error": "compose_failed",
                    "meal": None,
                    "memory": flags,
                },
            )
            return

        self._json(
            200,
            {
                "ok": True,
                "meal": snap,
                "memory": flags,
                "source": snap.get("source") or "last_compose",
            },
        )

    def _compose_meal_for_inspect(self, flags: dict[str, Any]) -> dict[str, Any] | None:
        """Best-effort on-demand compose for open/active moment (glass only)."""
        try:
            store = self.worker._ensure_memory_store()  # noqa: SLF001 — inspect path
        except Exception:  # noqa: BLE001
            return None
        if store is None:
            return None
        try:
            health = store.health()
            if isinstance(health, dict) and not health.get("ok", True):
                return None
        except Exception:  # noqa: BLE001
            return None

        open_mid = flags.get("active_moment_id")
        if not open_mid:
            # Prefer most recent open moment from moment store.
            try:
                opens = self.moments.list_moments(limit=5, open_only=True)
                if opens:
                    open_mid = opens[0].get("id") or opens[0].get("moment_id")
            except Exception:  # noqa: BLE001
                open_mid = None

        try:
            from elyra.memory.inspect import meal_package_to_inspect
            from elyra.memory.meal import compose_meal
            from elyra.memory.types import utc_now_iso

            mem_cfg = self.worker.settings.memory
            # Policy A: inspect uses same effective product meal budget.
            try:
                from elyra.runtime.meal_budget import (
                    effective_meal_budget_tokens,
                )

                budget = int(
                    effective_meal_budget_tokens(
                        self.worker.settings,
                        self.worker._meal_budget,  # noqa: SLF001
                    )
                )
            except Exception:  # noqa: BLE001
                loop = self.worker.settings.loop
                budget = int(getattr(loop, "sliding_input_tokens", 250_000))
            # Avoid loading full prompts on inspect poll — empty fixed cost.
            # Include registry tray keep so Context can show directed_keep (B5b).
            dk_ids: list[str] = []
            dk_summary: str | None = None
            tray_block: dict[str, Any] | None = None
            try:
                dk_ids, dk_summary = self.worker._last_confirmed_keep_for_meal(  # noqa: SLF001
                    str(open_mid) if open_mid else None
                )
            except Exception:  # noqa: BLE001
                dk_ids, dk_summary = [], None
            try:
                tray_block = self.worker.traversal.get_tray_inspect()
            except Exception:  # noqa: BLE001
                tray_block = None
            package = compose_meal(
                store,
                open_moment_id=str(open_mid) if open_mid else None,
                budget_tokens=budget,
                system_text="",
                orient_text="",
                settings=mem_cfg,
                directed_keep_ids=dk_ids or None,
                directed_keep_summary=dk_summary,
            )
            snap = meal_package_to_inspect(
                package,
                system_text="",
                orient_text="",
                budget_tokens=budget,
                source="on_demand",
                recorded_at=utc_now_iso(),
            )
            if tray_block is not None:
                snap["directed_keep_tray"] = tray_block
            return snap
        except Exception:  # noqa: BLE001
            _LOG.exception("on-demand memory meal compose failed")
            return None

    def _get_memory_atoms(self, qs: dict[str, list[str]]) -> None:
        """GET /api/memory/atoms — filterable recent atom list (read-only)."""
        flags = self._memory_flags_block()
        try:
            store = self.worker._ensure_memory_store()  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            self._json(
                200,
                {
                    "ok": False,
                    "error": str(exc) or "store_unavailable",
                    "atoms": [],
                    "memory": flags,
                },
            )
            return
        if store is None:
            self._json(
                200,
                {
                    "ok": False,
                    "error": flags.get("error") or "store_unavailable",
                    "atoms": [],
                    "memory": flags,
                },
            )
            return

        kind = (qs.get("kind") or [None])[0]
        moment_id = (qs.get("moment_id") or [None])[0]
        limit_raw = (qs.get("limit") or ["50"])[0]
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))

        try:
            from elyra.memory.inspect import atom_to_list_row, list_atoms_for_glass

            atoms = list_atoms_for_glass(
                store,
                kind=kind if isinstance(kind, str) else None,
                moment_id=moment_id if isinstance(moment_id, str) else None,
                limit=limit,
            )
            rows = [atom_to_list_row(a) for a in atoms]
            self._json(
                200,
                {
                    "ok": True,
                    "atoms": rows,
                    "count": len(rows),
                    "limit": limit,
                    "filters": {
                        "kind": kind if kind else None,
                        "moment_id": moment_id if moment_id else None,
                    },
                    "memory": flags,
                },
            )
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc), "atoms": [], "memory": flags})
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("list memory atoms failed")
            self._json(
                200,
                {
                    "ok": False,
                    "error": str(exc) or type(exc).__name__,
                    "atoms": [],
                    "memory": flags,
                },
            )

    def _get_memory_atom(self, atom_id: str) -> None:
        """GET /api/memory/atoms/{id} — single atom drill-down (read-only)."""
        flags = self._memory_flags_block()
        try:
            store = self.worker._ensure_memory_store()  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            self._json(
                200,
                {
                    "ok": False,
                    "error": str(exc) or "store_unavailable",
                    "atom": None,
                    "memory": flags,
                },
            )
            return
        if store is None:
            self._json(
                200,
                {
                    "ok": False,
                    "error": flags.get("error") or "store_unavailable",
                    "atom": None,
                    "memory": flags,
                },
            )
            return
        try:
            from elyra.memory.inspect import atom_to_detail

            atom = store.get_atom(atom_id)
            if atom is None:
                self._json(
                    404,
                    {
                        "ok": False,
                        "error": "atom not found",
                        "atom": None,
                        "memory": flags,
                    },
                )
                return
            # Best-effort media inventory fill (KD-M3 / PR2 inspect enrichment).
            media_store = None
            try:
                media_store = MediaStore(self.paths)
            except Exception:  # noqa: BLE001 — inventory is optional
                media_store = None
            self._json(
                200,
                {
                    "ok": True,
                    "atom": atom_to_detail(atom, media_store=media_store),
                    "memory": flags,
                },
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("get memory atom failed")
            self._json(
                200,
                {
                    "ok": False,
                    "error": str(exc) or type(exc).__name__,
                    "atom": None,
                    "memory": flags,
                },
            )

    def _vectors_worker_handles(self) -> tuple[Any | None, Any | None, Any | None]:
        """Best-effort (embedder, queue, index) from presence worker — never raises."""
        embedder = getattr(self.worker, "_embedder", None)
        queue = getattr(self.worker, "_encode_queue", None)
        index = getattr(self.worker, "_embedding_index", None)
        # Warm index if store is open (Null for JSONL); glass path only.
        if index is None:
            ensure_idx = getattr(self.worker, "_ensure_embedding_index", None)
            if callable(ensure_idx):
                try:
                    index = ensure_idx()
                except Exception:  # noqa: BLE001
                    index = None
        return embedder, queue, index

    def _get_memory_vectors(self) -> None:
        """GET /api/memory/vectors — encoder + index health (read-only)."""
        from elyra.memory.inspect import encoder_health_block, index_health_block

        flags = self._memory_flags_block()
        mem_cfg = getattr(getattr(self.worker, "settings", None), "memory", None)
        embedder, queue, index = self._vectors_worker_handles()
        # presence= for continuous-encode worker + gate metrics (PR4; no secrets).
        encoder = encoder_health_block(
            settings=mem_cfg,
            embedder=embedder,
            queue=queue,
            presence=self.worker,
        )
        index_h = index_health_block(index)
        # Overview is always 200; ok when store flags ok (index may still be null).
        ok = bool(flags.get("ok")) or bool(encoder.get("ok")) or bool(index_h.get("ok"))
        self._json(
            200,
            {
                "ok": ok,
                "encoder": encoder,
                "index": index_h,
                "memory": flags,
                "tabs": {
                    "vectors": {"stub": False, "phase": "2"},
                    "graph": {"stub": False, "phase": "2a"},
                },
            },
        )

    def _post_memory_ladder_rebuild(self, body: dict[str, Any]) -> None:
        """POST /api/memory/ladder/rebuild — force-refresh episodic period tips.

        Operator path for Glass Context **Rebuild episodic summaries**.
        Optional body: ``max_hours``, ``max_ms``, ``max_llm_calls`` (ints).
        """
        flags = self._memory_flags_block()
        payload = body if isinstance(body, dict) else {}

        def _opt_int(key: str) -> int | None:
            if key not in payload or payload.get(key) is None:
                return None
            try:
                v = int(payload[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be an int") from None
            if v < 0:
                raise ValueError(f"{key} must be >= 0")
            return v

        try:
            max_hours = _opt_int("max_hours")
            max_ms = _opt_int("max_ms")
            max_llm_calls = _opt_int("max_llm_calls")
        except ValueError as exc:
            self._json(
                400,
                {"ok": False, "error": str(exc), "memory": flags},
            )
            return

        rebuild = getattr(self.worker, "rebuild_episodic_summaries", None)
        if not callable(rebuild):
            self._json(
                501,
                {
                    "ok": False,
                    "error": "rebuild_episodic_summaries not available",
                    "memory": flags,
                },
            )
            return
        try:
            result = rebuild(
                max_hours=max_hours,
                max_ms=max_ms,
                max_llm_calls=max_llm_calls,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("POST /api/memory/ladder/rebuild failed")
            self._json(
                200,
                {
                    "ok": False,
                    "error": str(exc) or type(exc).__name__,
                    "memory": self._memory_flags_block(),
                },
            )
            return
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        result.setdefault("ok", True)
        result["memory"] = self._memory_flags_block()
        self._json(200, result)

    def _post_memory_vectors_rebuild(self, body: dict[str, Any]) -> None:
        """POST /api/memory/vectors/rebuild — rebuild ANN vector index.

        ANN = approximate nearest-neighbor **search index** over stored
        embeddings (not re-running Nemotron). Optional body: ``max_ms`` int.
        """
        from elyra.memory.inspect import index_health_block

        flags = self._memory_flags_block()
        max_ms = body.get("max_ms") if isinstance(body, dict) else None
        budget: int | None = None
        if max_ms is not None:
            try:
                budget = int(max_ms)
            except (TypeError, ValueError):
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "max_ms must be an int",
                        "memory": flags,
                    },
                )
                return
            if budget < 0:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "max_ms must be >= 0",
                        "memory": flags,
                    },
                )
                return

        rebuild = getattr(self.worker, "rebuild_vector_index", None)
        if not callable(rebuild):
            err = "rebuild_vector_index not available"
            self._json(
                501,
                {
                    "ok": False,
                    "error": err,
                    "notes": [err],
                    "note": err,
                    "memory": flags,
                },
            )
            return
        try:
            result = rebuild(max_ms=budget)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("POST /api/memory/vectors/rebuild failed")
            err = str(exc) or type(exc).__name__
            self._json(
                200,
                {
                    "ok": False,
                    "error": err,
                    "notes": [err],
                    "note": err,
                    "memory": flags,
                },
            )
            return
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        # KD-R3: rebuild honesty — notes[] (keep note as join for one release).
        notes = result.get("notes")
        if not isinstance(notes, list):
            legacy = result.get("note")
            notes = [str(legacy)] if legacy else []
            result["notes"] = notes
        if not result.get("note"):
            result["note"] = "; ".join(str(n) for n in notes) if notes else ""
        # Attach fresh index health for glass.
        try:
            _emb, _q, index = self._vectors_worker_handles()
            result["index"] = index_health_block(index)
        except Exception:  # noqa: BLE001
            result.setdefault("index", {})
        result["memory"] = flags
        # 200 even on optimized:false so glass can show notes without throwing.
        self._json(200, result)

    def _get_memory_vectors_atoms(self, qs: dict[str, list[str]]) -> None:
        """GET /api/memory/vectors/atoms — embedding status list (read-only)."""
        flags = self._memory_flags_block()
        try:
            store = self.worker._ensure_memory_store()  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            self._json(
                200,
                {
                    "ok": False,
                    "error": str(exc) or "store_unavailable",
                    "atoms": [],
                    "memory": flags,
                },
            )
            return
        if store is None:
            self._json(
                200,
                {
                    "ok": False,
                    "error": flags.get("error") or "store_unavailable",
                    "atoms": [],
                    "memory": flags,
                },
            )
            return

        status = (qs.get("status") or [None])[0]
        limit_raw = (qs.get("limit") or ["50"])[0]
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))

        try:
            from elyra.memory.inspect import (
                atom_to_vector_row,
                list_atoms_by_embedding_status,
            )

            atoms = list_atoms_by_embedding_status(
                store,
                status=status if isinstance(status, str) else None,
                limit=limit,
            )
            rows = [atom_to_vector_row(a) for a in atoms]
            self._json(
                200,
                {
                    "ok": True,
                    "atoms": rows,
                    "count": len(rows),
                    "limit": limit,
                    "filters": {
                        "status": status if status else None,
                    },
                    "memory": flags,
                },
            )
        except ValueError as exc:
            self._json(
                400,
                {"ok": False, "error": str(exc), "atoms": [], "memory": flags},
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("list memory vectors atoms failed")
            self._json(
                200,
                {
                    "ok": False,
                    "error": str(exc) or type(exc).__name__,
                    "atoms": [],
                    "memory": flags,
                },
            )

    def _get_memory_vectors_neighbors(self, qs: dict[str, list[str]]) -> None:
        """GET /api/memory/vectors/neighbors — top-k by atom_id or free-text q.

        Media-as-query uses POST (KD-M17). Shared body with
        :meth:`_neighbors_search`.
        """
        from elyra.memory.inspect import resolve_neighbor_k

        atom_id_raw = (qs.get("atom_id") or [None])[0]
        q_raw = (qs.get("q") or [None])[0]
        channel_req = (
            (qs.get("channel") or ["auto"])[0] or "auto"
        ).strip() or "auto"
        k = resolve_neighbor_k((qs.get("k") or ["16"])[0])
        atom_id = (
            atom_id_raw.strip()
            if isinstance(atom_id_raw, str) and atom_id_raw.strip()
            else None
        )
        query_text = (
            q_raw.strip() if isinstance(q_raw, str) and q_raw.strip() else None
        )
        self._neighbors_search(
            atom_id=atom_id,
            query_text=query_text,
            att_id=None,
            channel_req=channel_req,
            k=k,
        )

    def _post_memory_vectors_neighbors(self, body: dict[str, Any]) -> None:
        """POST /api/memory/vectors/neighbors — media-as-query + text/atom seeds.

        Preferred path for ``att_id`` (KD-M16/M17). JSON body:
        ``{q?, att_id?, atom_id?, channel?, k?}``. Thin parse only — resolve
        via shared ``resolve_one_media`` (KD-M21); no MIME/path/size logic here.
        """
        from elyra.memory.inspect import resolve_neighbor_k

        if not isinstance(body, dict):
            body = {}

        def _str_field(key: str) -> str | None:
            raw = body.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
            return None

        atom_id = _str_field("atom_id")
        query_text = _str_field("q")
        att_id = _str_field("att_id")
        channel_raw = body.get("channel")
        channel_req = (
            str(channel_raw).strip()
            if isinstance(channel_raw, str) and str(channel_raw).strip()
            else "auto"
        ) or "auto"
        k = resolve_neighbor_k(body.get("k", 16))
        self._neighbors_search(
            atom_id=atom_id,
            query_text=query_text,
            att_id=att_id,
            channel_req=channel_req,
            k=k,
        )

    def _neighbors_search(
        self,
        *,
        atom_id: str | None,
        query_text: str | None,
        att_id: str | None,
        channel_req: str,
        k: int,
    ) -> None:
        """Shared GET/POST neighbors implementation (KD-M15–M17, M20–M21).

        Read-only ANN. No raw 2048-d vectors. Operational unavailability →
        200 + ``omitted_reason``; client input errors → 400 (OQ-M5).
        Never silently demotes media queries to empty-text search.
        """
        from elyra.memory.embed.encode import resolve_one_media
        from elyra.memory.embed.types import ModalityParts
        from elyra.memory.index import resolve_search_channel
        from elyra.memory.inspect import (
            index_health_block,
            neighbor_hit_to_inspect,
            query_vector_for_atom,
        )

        flags = self._memory_flags_block()
        channel_req = (channel_req or "auto").strip() or "auto"

        has_atom = bool(atom_id)
        has_q = bool(query_text)
        has_att = bool(att_id)
        if not has_atom and not has_q and not has_att:
            self._json(
                400,
                {
                    "ok": False,
                    "error": "query_required",
                    "omitted_reason": "query_required",
                    "neighbors": [],
                    "memory": flags,
                },
            )
            return

        # Prefer atom_id when combined with other seeds (stored-vector path).
        # Media+text without atom_id is the multimodal query path (KD-M15).
        use_atom = has_atom
        use_media = has_att and not use_atom
        use_text = has_q and not use_atom

        # ── att_id shape validation (400 before store work) ──────────────
        if has_att:
            try:
                att_id = validate_att_id(str(att_id))
            except ValueError:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "invalid_att_id",
                        "omitted_reason": "invalid_att_id",
                        "neighbors": [],
                        "memory": flags,
                    },
                )
                return

        try:
            store = self.worker._ensure_memory_store()  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            self._json(
                200,
                {
                    "ok": False,
                    "error": str(exc) or "store_unavailable",
                    "neighbors": [],
                    "memory": flags,
                },
            )
            return

        _embedder, _queue, index = self._vectors_worker_handles()
        idx_health = index_health_block(index)
        vectors_by_channel = idx_health.get("vectors_by_channel") or {}
        joint_repair_remaining = int(idx_health.get("joint_repair_remaining") or 0)

        # Seed channel hints for KD-M20 (filled after media resolve).
        seed_channels: list[str] | None = None
        query_modality: str | None = None
        media_input: bytes | str | None = None
        source = "atom" if use_atom else ("text" if use_text and not use_media else "media")
        omit_reason: str | None = None
        query_vec: list[float] | None = None
        seed_atom_id: str | None = atom_id if use_atom else None
        resolved_channel = "joint"
        channel_reason = "auto_empty"
        searched = False

        def _query_block(
            *,
            source_val: str,
            extra: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            block: dict[str, Any] = {
                "atom_id": atom_id if use_atom else None,
                "q": query_text,
                "att_id": att_id if use_media else None,
                "query_modality": query_modality,
                "channel": channel_req,
                "resolved_channel": resolved_channel,
                "channel_reason": channel_reason,
                "k": k,
                "source": source_val,
            }
            if extra:
                block.update(extra)
            return block

        def _index_block() -> dict[str, Any]:
            return {
                "search_mode": idx_health.get("search_mode"),
                "ann_index_built": idx_health.get("ann_index_built"),
                "vectors_by_channel": idx_health.get("vectors_by_channel"),
                "joint_repair_remaining": idx_health.get("joint_repair_remaining"),
                "vectors_ready": idx_health.get("vectors_ready"),
            }

        def _respond_200(
            *,
            neighbors: list[dict[str, Any]],
            source_val: str,
            omit: str | None,
            ok: bool = True,
            error: str | None = None,
        ) -> None:
            payload: dict[str, Any] = {
                "ok": ok,
                "neighbors": neighbors,
                "count": len(neighbors),
                "omitted_reason": omit if not neighbors else None,
                "query": _query_block(source_val=source_val),
                "index": _index_block(),
                "memory": flags,
            }
            if error:
                payload["error"] = error
            self._json(200, payload)

        # ── Media resolve (shared helper only — KD-M21) ──────────────────
        if use_media:
            assert att_id is not None
            mem_cfg = getattr(getattr(self.worker, "settings", None), "memory", None)
            max_bytes = int(
                getattr(mem_cfg, "embed_media_max_bytes", 8_000_000) or 8_000_000
            )
            media_store = MediaStore(self.paths)
            try:
                one = resolve_one_media(media_store, att_id, max_bytes=max_bytes)
            except Exception:  # noqa: BLE001
                _LOG.debug("resolve_one_media failed att_id=%s", att_id, exc_info=True)
                one = {
                    "modality": None,
                    "input": None,
                    "skipped": f"{att_id}:error",
                }
            skipped = one.get("skipped")
            modality = one.get("modality")
            media_input = one.get("input")

            if skipped:
                sk = str(skipped)
                # Map resolve tokens → HTTP policy (OQ-M5 / design table).
                if ":oversize_bytes" in sk or sk.endswith(":oversize"):
                    self._json(
                        400,
                        {
                            "ok": False,
                            "error": "media_oversize",
                            "omitted_reason": "media_oversize",
                            "neighbors": [],
                            "memory": flags,
                            "query": {
                                "atom_id": None,
                                "q": query_text,
                                "att_id": att_id,
                                "query_modality": None,
                                "channel": channel_req,
                                "resolved_channel": None,
                                "channel_reason": None,
                                "k": k,
                                "source": "media",
                            },
                        },
                    )
                    return
                if ":unknown_type" in sk:
                    self._json(
                        400,
                        {
                            "ok": False,
                            "error": "media_unsupported_type",
                            "omitted_reason": "media_unsupported_type",
                            "neighbors": [],
                            "memory": flags,
                            "query": {
                                "atom_id": None,
                                "q": query_text,
                                "att_id": att_id,
                                "query_modality": None,
                                "channel": channel_req,
                                "resolved_channel": None,
                                "channel_reason": None,
                                "k": k,
                                "source": "media",
                            },
                        },
                    )
                    return
                # missing / no_path / error → soft 200 omit (no 404 on media path)
                omit_tok = "media_missing"
                if ":no_path" in sk or ":unresolved" in sk:
                    omit_tok = "media_missing"
                omit_reason = omit_tok
                source = "text+media" if has_q else "media"
                # Still resolve channel for response honesty (no seed modality).
                resolved_channel, channel_reason = resolve_search_channel(
                    channel_req,
                    vectors_by_channel=vectors_by_channel,
                    joint_repair_remaining=joint_repair_remaining,
                )
                _respond_200(neighbors=[], source_val=source, omit=omit_reason)
                return

            if not modality or media_input is None:
                omit_reason = "media_missing"
                source = "text+media" if has_q else "media"
                resolved_channel, channel_reason = resolve_search_channel(
                    channel_req,
                    vectors_by_channel=vectors_by_channel,
                    joint_repair_remaining=joint_repair_remaining,
                )
                _respond_200(neighbors=[], source_val=source, omit=omit_reason)
                return

            query_modality = str(modality)
            if has_q:
                source = "text+media"
                seed_channels = ["text", query_modality]
            else:
                source = "media"
                seed_channels = [query_modality]

        elif use_text:
            source = "text"
            seed_channels = ["text"]
            query_modality = "text"
        else:
            source = "atom"
            seed_channels = None  # atom path: joint-primary auto (existing)

        # Resolve channel once (KD-R16 / KD-M20).
        resolved_channel, channel_reason = resolve_search_channel(
            channel_req,
            vectors_by_channel=vectors_by_channel,
            joint_repair_remaining=joint_repair_remaining,
            seed_channels=seed_channels,
        )

        # ── Query vector ─────────────────────────────────────────────────
        if use_atom:
            assert atom_id is not None
            # Stored emb for concrete resolved channel only (PR-R5 Issue 1).
            query_vec, omit_reason = query_vector_for_atom(
                atom_id,
                index=index,
                store=store,
                channel=resolved_channel,
            )
            if query_vec is None:
                omit_reason = omit_reason or "no_vector"
                if store is not None:
                    try:
                        atom = store.get_atom(atom_id)
                    except Exception:  # noqa: BLE001
                        atom = None
                    if atom is None:
                        self._json(
                            404,
                            {
                                "ok": False,
                                "error": "atom not found",
                                "neighbors": [],
                                "memory": flags,
                                "query": _query_block(source_val=source),
                            },
                        )
                        return
        else:
            # Consumer encode via gated embedder (KD-E5). Never raw _embedder.
            gated: Any | None = None
            ensure_emb = getattr(self.worker, "_ensure_embedder", None)
            if callable(ensure_emb):
                try:
                    gated = ensure_emb()  # role=consumer → GatedEmbedder | None
                except Exception:  # noqa: BLE001
                    gated = None
            if gated is None:
                omit_reason = "encoder"
            else:
                try:
                    emb_health = (
                        gated.health() if hasattr(gated, "health") else {}
                    )
                    if not isinstance(emb_health, dict):
                        emb_health = {}
                    if emb_health.get("ok") is False:
                        omit_reason = "encoder"
                    elif use_media:
                        # Fail closed when media_encode unavailable (never empty text).
                        media_ok = emb_health.get("media_encode")
                        if media_ok is False or media_ok is None:
                            # None (unknown) also fail-closed for media query honesty.
                            if media_ok is not True:
                                omit_reason = "media_encode_unavailable"
                        if omit_reason is None:
                            assert query_modality is not None
                            if has_q and query_text:
                                # Joint query: text + media (KD-M15 table).
                                parts = ModalityParts(
                                    text=str(query_text),
                                    image=media_input if query_modality == "image" else None,
                                    audio=media_input if query_modality == "audio" else None,
                                    video=media_input if query_modality == "video" else None,
                                )
                                encode_joint = getattr(gated, "encode_joint", None)
                                if callable(encode_joint):
                                    query_vec = list(encode_joint(parts))
                                else:
                                    # Fallback: modality-only encode if joint missing.
                                    enc_name = f"encode_{query_modality}"
                                    enc_fn = getattr(gated, enc_name, None)
                                    if not callable(enc_fn):
                                        omit_reason = "encode_failed"
                                    else:
                                        query_vec = list(enc_fn(media_input))
                            else:
                                enc_name = f"encode_{query_modality}"
                                enc_fn = getattr(gated, enc_name, None)
                                if not callable(enc_fn):
                                    omit_reason = "encode_failed"
                                else:
                                    query_vec = list(enc_fn(media_input))
                    else:
                        # Free-text only.
                        query_vec = list(gated.encode_text(str(query_text)))
                except Exception:  # noqa: BLE001 — gate timeout / encode fail
                    omit_reason = "encode_failed"
                    query_vec = None

        neighbors: list[dict[str, Any]] = []
        if query_vec is not None and index is not None:
            exclude: set[str] = set()
            if seed_atom_id:
                exclude.add(seed_atom_id)
            try:
                fetch_k = k + (1 if exclude else 0)
                hits = index.search(
                    query_vec,
                    k=fetch_k,
                    channel=resolved_channel,
                    exclude_atom_ids=exclude or None,
                )
                searched = True
                for hit in hits:
                    if seed_atom_id and getattr(hit, "atom_id", None) == seed_atom_id:
                        continue
                    neighbors.append(neighbor_hit_to_inspect(hit))
                    if len(neighbors) >= k:
                        break
            except Exception as exc:  # noqa: BLE001
                _LOG.exception("memory vectors neighbor search failed")
                self._json(
                    200,
                    {
                        "ok": False,
                        "error": str(exc) or type(exc).__name__,
                        "neighbors": [],
                        "omitted_reason": "search_failed",
                        "memory": flags,
                        "index": idx_health,
                        "query": _query_block(source_val=source),
                    },
                )
                return
        elif query_vec is not None and index is None:
            omit_reason = omit_reason or "no_index"
        elif query_vec is None and omit_reason is None:
            omit_reason = "no_vector"

        if not neighbors:
            if omit_reason is None and searched:
                omit_reason = "no_hits"
            elif omit_reason is None:
                omit_reason = "no_vector"

        # GET free-text: keep prior query shape (att_id/query_modality may be null).
        if source == "text" and query_modality is None:
            query_modality = "text"

        _respond_200(neighbors=neighbors, source_val=source, omit=omit_reason)

    # ── Phase 2a Graph tab (PR-A5) ────────────────────────────────────────

    def _traversal_registry(self) -> Any | None:
        """Best-effort TraversalRegistry from worker — never raises."""
        trav = getattr(self.worker, "traversal", None)
        if trav is not None:
            return trav
        return getattr(self.worker, "_traversal", None)

    def _graph_view_for_api(self) -> Any | None:
        """Worker GraphView factory (structural always; semantic if warm)."""
        factory = getattr(self.worker, "graph_view", None)
        if not callable(factory):
            return None
        try:
            return factory()
        except Exception:  # noqa: BLE001
            _LOG.exception("graph_view factory failed for glass")
            return None

    def _memory_settings(self) -> Any | None:
        return getattr(getattr(self.worker, "settings", None), "memory", None)

    def _memory_settings_with_wait(self) -> Any | None:
        """MemorySettings with runtime semantic_wait overlay when worker has it."""
        fn = getattr(self.worker, "_memory_settings_with_wait", None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                _LOG.debug("worker _memory_settings_with_wait failed", exc_info=True)
        return self._memory_settings()

    def _get_memory_graph(self) -> None:
        """GET /api/memory/graph — overview: flags, session presence, legend."""
        from elyra.memory.inspect import (
            directed_traversal_flags,
            edge_kind_legend,
        )

        flags = self._memory_flags_block()
        mem_cfg = self._memory_settings_with_wait()
        trav_flags = directed_traversal_flags(mem_cfg)
        reg = self._traversal_registry()
        has_active = False
        has_last = False
        meal_keep_count = 0
        if reg is not None:
            try:
                # Bind wait-overlaid settings so flag/ceiling honesty matches live.
                bind = getattr(reg, "bind_settings", None)
                if callable(bind) and mem_cfg is not None:
                    bind(mem_cfg)
                view = reg.get_graph_session_view()
                has_active = bool(getattr(view, "has_active", False))
                has_last = bool(getattr(view, "has_last_session", False))
                meal_keep_count = int(getattr(view, "meal_keep_count", 0) or 0)
            except Exception:  # noqa: BLE001
                _LOG.exception("graph overview session peek failed")

        # EdgeStore health for glass honesty (counts; empty store still ok).
        edge_count = 0
        edges_by_kind: dict[str, int] = {}
        edge_backend: str | None = None
        edge_ok = False
        ensure_edges = getattr(self.worker, "_ensure_edge_store", None)
        if callable(ensure_edges):
            try:
                estore = ensure_edges()
                if estore is not None and hasattr(estore, "health"):
                    eh = estore.health() or {}
                    if isinstance(eh, dict):
                        edge_ok = bool(eh.get("ok"))
                        edge_count = int(eh.get("edge_count") or 0)
                        raw_by = eh.get("edges_by_kind") or {}
                        if isinstance(raw_by, dict):
                            edges_by_kind = {
                                str(k): int(v)
                                for k, v in raw_by.items()
                                if v is not None
                            }
                        edge_backend = (
                            str(eh.get("backend")) if eh.get("backend") else None
                        )
            except Exception:  # noqa: BLE001
                _LOG.exception("graph overview edge health peek failed")

        # Overview is always 200; ok tracks store health (flags may be off).
        # EdgeStore empty → free-browse/neighbors still work via projected
        # structural (+ optional semantic_hop); durable kinds absent (#61 / PR8).
        durable_on = bool(trav_flags.get("durable_edges_enabled"))
        edge_store_empty = edge_count == 0
        honesty_notes: list[str] = []
        if not trav_flags.get("directed_traversal_enabled"):
            honesty_notes.append(
                "directed_traversal_enabled is off — tools/POST fail closed; "
                "structural neighbor probe still available when store is open"
            )
        elif not has_active and not has_last:
            honesty_notes.append(
                "no active or last walk yet — start via traverse tools "
                "or debug POST"
            )
        if edge_store_empty:
            honesty_notes.append(
                "EdgeStore empty (edge_count=0) — free-browse/neighbors show "
                "projected structural edges (+ optional semantic_hop) only; "
                "durable kinds absent until edge writes land"
            )
        elif not durable_on:
            honesty_notes.append(
                "durable_edges_enabled is off — durable EdgeStore rows are not "
                "written by promote; expand still unions any existing rows"
            )
        self._json(
            200,
            {
                "ok": bool(flags.get("ok")),
                "has_active": has_active,
                "has_last_session": has_last,
                "meal_keep_count": meal_keep_count,
                "edge_kind_legend": edge_kind_legend(),
                "edge_count": edge_count,
                "edges_by_kind": edges_by_kind,
                "edge_store": {
                    "ok": edge_ok,
                    "backend": edge_backend,
                    "edge_count": edge_count,
                    "edges_by_kind": edges_by_kind,
                    "durable_edges_enabled": durable_on,
                },
                "traversal": trav_flags,
                "memory": flags,
                "tabs": {
                    "vectors": {"stub": False, "phase": "2"},
                    "graph": {"stub": False, "phase": "2a"},
                    # #61 free-browse canvas reuses neighbors + legend (no graph DB).
                    "graph_free_browse": {
                        "stub": False,
                        "phase": "2a",
                        "api": [
                            "GET /api/memory/graph",
                            "GET /api/memory/graph/neighbors",
                            "GET /api/memory/graph/session",
                        ],
                    },
                },
                "honesty": {
                    "flag_off": not bool(
                        trav_flags.get("directed_traversal_enabled")
                    ),
                    "no_session": not has_active and not has_last,
                    "durable_edges_enabled": durable_on,
                    "edge_store_empty": edge_store_empty,
                    "projected_edges_only": edge_store_empty,
                    "note": (
                        " · ".join(honesty_notes) if honesty_notes else None
                    ),
                },
            },
        )

    def _get_memory_graph_session(self, qs: dict[str, list[str]]) -> None:
        """GET /api/memory/graph/session — active else last_session (KD-A19).

        Query ``?which=active|last|meal`` optional. Never meal-thin-only for
        the default/session body — meal ids only as side fields.
        """
        from elyra.memory.inspect import (
            directed_traversal_flags,
            graph_session_view_to_inspect,
        )

        flags = self._memory_flags_block()
        mem_cfg = self._memory_settings_with_wait()
        trav_flags = directed_traversal_flags(mem_cfg)
        which_raw = (qs.get("which") or [None])[0]
        which = (
            which_raw.strip().lower()
            if isinstance(which_raw, str) and which_raw.strip()
            else None
        )
        if which is not None and which not in ("active", "last", "meal"):
            self._json(
                400,
                {
                    "ok": False,
                    "error": "which must be active|last|meal",
                    "session": None,
                    "memory": flags,
                    "traversal": trav_flags,
                },
            )
            return

        reg = self._traversal_registry()
        if reg is None:
            self._json(
                200,
                {
                    "ok": True,
                    "which": "none",
                    "session": None,
                    "has_active": False,
                    "has_last_session": False,
                    "meal_keep_count": 0,
                    "meal_keep_ids": [],
                    "memory": flags,
                    "traversal": trav_flags,
                    "honesty": {
                        "flag_off": not trav_flags["directed_traversal_enabled"],
                        "no_session": True,
                        "note": "traversal registry unavailable",
                    },
                },
            )
            return

        try:
            bind = getattr(reg, "bind_settings", None)
            if callable(bind) and mem_cfg is not None:
                bind(mem_cfg)
            view = reg.get_graph_session_view(which=which)
            payload = graph_session_view_to_inspect(view)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("GET /api/memory/graph/session failed")
            self._json(
                200,
                {
                    "ok": False,
                    "error": str(exc) or type(exc).__name__,
                    "which": "none",
                    "session": None,
                    "has_active": False,
                    "has_last_session": False,
                    "meal_keep_count": 0,
                    "meal_keep_ids": [],
                    "memory": flags,
                    "traversal": trav_flags,
                },
            )
            return

        sess = payload.get("session")
        no_session = sess is None and which != "meal"
        note = None
        if not trav_flags["directed_traversal_enabled"]:
            note = (
                "directed_traversal_enabled is off — showing sticky last walk "
                "if any; new walks disabled"
            )
        elif no_session:
            note = "no walk session yet (active or last)"
        elif which == "meal":
            note = "meal-thin keep ids only (not full glass last walk)"

        payload.update(
            {
                "ok": True,
                "memory": flags,
                "traversal": trav_flags,
                "honesty": {
                    "flag_off": not trav_flags["directed_traversal_enabled"],
                    "no_session": no_session,
                    "note": note,
                },
            }
        )
        self._json(200, payload)

    def _get_memory_graph_neighbors(self, qs: dict[str, list[str]]) -> None:
        """GET /api/memory/graph/neighbors?atom_id= — 1-hop multi-kind expand.

        Structural always (when store open). Semantic hops only if
        ``allow_semantic=1`` **and** index + warm encoder.

        Defaults (polish1 KD-P0-http): ``allow_semantic=0`` (structural-first
        free-browse). When semantic is on, ANN uses snappy http budget unless
        ``semantic_wait=1`` opts into the unified wait ceiling. Never full wait
        by default.
        """
        from elyra.memory.config import (
            effective_semantic_wait_max_ms,
            semantic_wait_enabled,
            snappy_ann_max_ms,
        )
        from elyra.memory.inspect import (
            directed_traversal_flags,
            graph_edge_to_inspect,
            resolve_neighbor_k,
        )

        flags = self._memory_flags_block()
        # Prefer worker overlay so wait max tracks glass set_semantic_wait.
        mem_cfg = self._memory_settings_with_wait()
        trav_flags = directed_traversal_flags(mem_cfg)
        atom_id_raw = (qs.get("atom_id") or [None])[0]
        atom_id = (
            atom_id_raw.strip()
            if isinstance(atom_id_raw, str) and atom_id_raw.strip()
            else None
        )
        k = resolve_neighbor_k((qs.get("k") or ["16"])[0])
        # Product default allow_semantic=0 (structural-first glass / free-browse).
        allow_sem_raw = (qs.get("allow_semantic") or ["0"])[0]
        allow_semantic = str(allow_sem_raw).strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
            "",
        )
        wait_raw = (qs.get("semantic_wait") or ["0"])[0]
        use_full_wait = str(wait_raw).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        # Dual deadlines: structural under traverse_expand_max_ms; ANN snappy
        # unless explicit semantic_wait=1 and wait enabled.
        try:
            expand_ms = int(
                getattr(mem_cfg, "traverse_expand_max_ms", 120) or 120
            )
        except (TypeError, ValueError):
            expand_ms = 120
        if allow_semantic:
            if use_full_wait and semantic_wait_enabled(mem_cfg):
                semantic_ms = effective_semantic_wait_max_ms(mem_cfg)
            else:
                semantic_ms = snappy_ann_max_ms(mem_cfg, "http")
        else:
            semantic_ms = 0

        query_echo = {
            "atom_id": atom_id,
            "k": k,
            "allow_semantic": allow_semantic,
            "semantic_wait": use_full_wait,
            "expand_deadline_ms": expand_ms,
            "semantic_deadline_ms": semantic_ms if allow_semantic else 0,
        }

        if not atom_id:
            self._json(
                400,
                {
                    "ok": False,
                    "error": "atom_id required",
                    "neighbors": [],
                    "memory": flags,
                    "traversal": trav_flags,
                },
            )
            return

        graph = self._graph_view_for_api()
        if graph is None:
            # Fall back: try store health message.
            err = flags.get("error") or "store_unavailable"
            self._json(
                200,
                {
                    "ok": False,
                    "error": err,
                    "neighbors": [],
                    "count": 0,
                    "omitted_reason": "store_unavailable",
                    "query": query_echo,
                    "memory": flags,
                    "traversal": trav_flags,
                },
            )
            return

        store = getattr(graph, "_store", None)
        # 404 only when atom itself is missing.
        if store is not None:
            try:
                atom = store.get_atom(atom_id)
            except Exception:  # noqa: BLE001
                atom = None
            if atom is None:
                self._json(
                    404,
                    {
                        "ok": False,
                        "error": "atom not found",
                        "neighbors": [],
                        "query": query_echo,
                        "memory": flags,
                        "traversal": trav_flags,
                    },
                )
                return

        try:
            edges = graph.neighbors(
                atom_id,
                k=k,
                allow_semantic=allow_semantic,
                expand_deadline_ms=expand_ms,
                semantic_deadline_ms=semantic_ms if allow_semantic else 0,
            )
            expand_meta = dict(getattr(graph, "last_expand_meta", None) or {})
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("memory graph neighbor expand failed")
            self._json(
                200,
                {
                    "ok": False,
                    "error": str(exc) or type(exc).__name__,
                    "neighbors": [],
                    "count": 0,
                    "omitted_reason": "expand_failed",
                    "query": query_echo,
                    "memory": flags,
                    "traversal": trav_flags,
                },
            )
            return

        neighbors = [graph_edge_to_inspect(e, store) for e in edges]
        omit: str | None = None
        if not neighbors:
            omit = (
                expand_meta.get("error")
                or expand_meta.get("semantic_reason")
                or "no_hits"
            )
            # Prefer structural-empty honesty over semantic-only reason.
            if expand_meta.get("error") == "atom_not_found":
                omit = "atom_not_found"

        self._json(
            200,
            {
                "ok": True,
                "neighbors": neighbors,
                "count": len(neighbors),
                "omitted_reason": omit if not neighbors else None,
                "expand_meta": {
                    "expand_truncated": bool(expand_meta.get("expand_truncated")),
                    "structural_truncated": bool(
                        expand_meta.get("structural_truncated")
                    ),
                    "semantic_truncated": bool(
                        expand_meta.get("semantic_truncated")
                    ),
                    "elapsed_ms": expand_meta.get("elapsed_ms"),
                    "structural_ms_budget": expand_meta.get(
                        "structural_ms_budget"
                    ),
                    "structural_ms_spent": expand_meta.get(
                        "structural_ms_spent"
                    ),
                    "semantic_ms_budget": expand_meta.get("semantic_ms_budget"),
                    "semantic_ms_spent": expand_meta.get("semantic_ms_spent"),
                    "semantic_reason": expand_meta.get("semantic_reason"),
                    "parent_of_reason": expand_meta.get("parent_of_reason"),
                    "dual_deadline": bool(expand_meta.get("dual_deadline")),
                },
                "query": query_echo,
                "memory": flags,
                "traversal": trav_flags,
            },
        )

    def _post_memory_graph_traverse(self, body: dict[str, Any]) -> None:
        """POST /api/memory/graph/traverse — optional operator debug walk.

        Same validation + budgets as tools. Flags-off fail-closed
        (``ok: false``, ``error_reason: traverse_disabled``) — no budget bypass.
        Body: ``action`` = start|step|finish|abandon|inspect (+ action fields).
        """
        from elyra.memory.inspect import (
            directed_traversal_flags,
            enrich_session_for_glass,
        )
        from elyra.memory.traverse import (
            ERROR_TRAVERSE_DISABLED,
            inspect_atoms,
        )

        flags = self._memory_flags_block()
        # Wait overlay so POST traverse start/step ANN ceilings track glass.
        mem_cfg = self._memory_settings_with_wait()
        trav_flags = directed_traversal_flags(mem_cfg)
        if not isinstance(body, dict):
            body = {}

        action = str(body.get("action") or body.get("op") or "").strip().lower()
        if action not in ("start", "step", "finish", "abandon", "inspect"):
            self._json(
                400,
                {
                    "ok": False,
                    "error": "action must be start|step|finish|abandon|inspect",
                    "error_reason": "bad_action",
                    "memory": flags,
                    "traversal": trav_flags,
                },
            )
            return

        reg = self._traversal_registry()
        if reg is None:
            self._json(
                200,
                {
                    "ok": False,
                    "error_reason": "traverse_unavailable",
                    "error": "traversal registry unavailable",
                    "memory": flags,
                    "traversal": trav_flags,
                },
            )
            return

        # Always bind wait-overlaid settings before enable check / mutate.
        try:
            bind = getattr(reg, "bind_settings", None)
            if callable(bind) and mem_cfg is not None:
                bind(mem_cfg)
        except Exception:  # noqa: BLE001
            pass

        # Fail closed when directed traversal is off (parity with tools).
        if not reg.enabled():
            self._json(
                200,
                {
                    "ok": False,
                    "error_reason": ERROR_TRAVERSE_DISABLED,
                    "status": "disabled",
                    "memory": flags,
                    "traversal": trav_flags,
                },
            )
            return

        # Inspect is read-only against the store (still requires flag on so
        # glass debug cannot bypass the same gate as tools).
        if action == "inspect":
            raw_ids = body.get("atom_ids") or body.get("ids") or []
            if not isinstance(raw_ids, list):
                raw_ids = []
            try:
                store = self.worker._ensure_memory_store()  # noqa: SLF001
            except Exception as exc:  # noqa: BLE001
                self._json(
                    200,
                    {
                        "ok": False,
                        "error_reason": "store_unavailable",
                        "error": str(exc) or "store_unavailable",
                        "previews": [],
                        "memory": flags,
                        "traversal": trav_flags,
                    },
                )
                return
            if store is None:
                self._json(
                    200,
                    {
                        "ok": False,
                        "error_reason": "store_unavailable",
                        "error": flags.get("error") or "store_unavailable",
                        "previews": [],
                        "memory": flags,
                        "traversal": trav_flags,
                    },
                )
                return
            previews = inspect_atoms(store, raw_ids, settings=mem_cfg)
            self._json(
                200,
                {
                    "ok": True,
                    "action": "inspect",
                    "previews": [p.to_dict() for p in previews],
                    "memory": flags,
                    "traversal": trav_flags,
                },
            )
            return

        graph = self._graph_view_for_api()
        if graph is None and action in ("start", "step", "finish"):
            # finish may run without graph when keep_adjacent is off; still try.
            if action != "finish":
                self._json(
                    200,
                    {
                        "ok": False,
                        "error_reason": "store_unavailable",
                        "error": flags.get("error") or "store_unavailable",
                        "memory": flags,
                        "traversal": trav_flags,
                    },
                )
                return

        session_id = body.get("session_id")
        if session_id is not None:
            session_id = str(session_id)

        try:
            if action == "start":
                goal = str(body.get("goal") or "explore")
                seed_query = body.get("seed_query")
                if seed_query is not None:
                    seed_query = str(seed_query)
                seed_ids = body.get("seed_atom_ids") or body.get("seed_ids") or []
                if not isinstance(seed_ids, list):
                    seed_ids = []
                moment_id = body.get("moment_id")
                if moment_id is not None:
                    moment_id = str(moment_id)
                # Optional budget overrides (cannot exceed settings hard max —
                # TraversalRegistry clamps via min()).
                overrides: dict[str, int] = {}
                for key in ("max_steps", "max_nodes", "max_depth", "max_keep"):
                    if key in body and body[key] is not None:
                        try:
                            overrides[key] = int(body[key])
                        except (TypeError, ValueError):
                            pass
                result = reg.start(
                    graph,
                    goal=goal,
                    seed_query=seed_query,
                    seed_atom_ids=[str(x) for x in seed_ids],
                    moment_id=moment_id,
                    budget_overrides=overrides or None,
                )
            elif action == "step":
                expand_ids = body.get("expand_ids") or []
                keep_ids = body.get("keep_ids") or []
                if not isinstance(expand_ids, list):
                    expand_ids = []
                if not isinstance(keep_ids, list):
                    keep_ids = []
                scratchpad = body.get("scratchpad")
                if scratchpad is not None:
                    scratchpad = str(scratchpad)
                result = reg.step(
                    graph,
                    session_id=session_id,
                    expand_ids=[str(x) for x in expand_ids],
                    keep_ids=[str(x) for x in keep_ids],
                    scratchpad=scratchpad,
                )
            elif action == "finish":
                keep_ids = body.get("keep_ids")
                if keep_ids is not None and not isinstance(keep_ids, list):
                    keep_ids = []
                summary_hint = body.get("summary_hint")
                if summary_hint is not None:
                    summary_hint = str(summary_hint)
                result = reg.finish(
                    graph,
                    session_id=session_id,
                    keep_ids=(
                        [str(x) for x in keep_ids] if keep_ids is not None else None
                    ),
                    summary_hint=summary_hint,
                )
            else:  # abandon
                reason = str(body.get("reason") or "abandoned")
                result = reg.abandon(session_id=session_id, reason=reason)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("POST /api/memory/graph/traverse action=%s failed", action)
            self._json(
                200,
                {
                    "ok": False,
                    "error_reason": "traverse_error",
                    "error": str(exc) or type(exc).__name__,
                    "action": action,
                    "memory": flags,
                    "traversal": trav_flags,
                },
            )
            return

        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        out = dict(result)
        out["action"] = action
        out["memory"] = flags
        out["traversal"] = trav_flags
        # Attach glass-enriched session view when useful (finish returns full).
        if action == "finish" and out.get("ok") and out.get("session_id"):
            out["session"] = enrich_session_for_glass(
                {k: v for k, v in out.items() if k not in ("ok", "thin_surface", "memory", "traversal", "action")}
            )
        # Also expose sticky glass view for operator after any mutating call.
        try:
            gview = reg.get_graph_session_view()
            from elyra.memory.inspect import graph_session_view_to_inspect

            out["graph_view"] = graph_session_view_to_inspect(gview)
        except Exception:  # noqa: BLE001
            pass
        self._json(200, out)

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
            "reasoning_effort": fields.get("reasoning_effort"),
            "credential_source": fields.get("credential_source"),
            "credential_ok": fields.get("credential_ok"),
            "credential_detail": fields.get("credential_detail"),
            "credential_expires_at": fields.get("credential_expires_at"),
            "credential_email": fields.get("credential_email"),
            "api_key_configured": fields.get("api_key_configured"),
            "provider": fields.get("provider"),
            "models_available": fields.get("models_available"),
        }

    # ── xAI OAuth device login (PR3) ─────────────────────────────────────

    def _get_auth_xai(self) -> None:
        """GET /api/auth/xai — public OAuth meta only (never tokens)."""
        meta = oauth_public_meta(self.paths.data_dir)
        payload: dict[str, Any] = {
            "ok": True,
            "configured": meta.configured,
            "email": meta.email,
            "expires_at": meta.expires_at,
            "updated_at": meta.updated_at,
            "auth_method": meta.auth_method,
            "reauth_required": meta.reauth_required,
        }
        if not self._provider_unavailable():
            fields = self.provider.status_provider_fields()
            payload["credential_source"] = fields.get("credential_source")
            payload["credential_ok"] = fields.get("credential_ok")
            payload["oauth_configured"] = fields.get("oauth_configured")
        else:
            payload["oauth_configured"] = meta.configured
        self._json(200, self._strip_auth_secrets(payload))

    def _get_auth_xai_device_status(self) -> None:
        """GET /api/auth/xai/device/status — session state; never tokens/device_code."""
        if self._provider_unavailable():
            self._json(503, {"ok": False, "error": "provider unavailable"})
            return
        if self._reject_if_resetting():
            return
        try:
            status = self.provider.xai_device_status()
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("xai device status failed")
            self._json(
                500,
                {"ok": False, "error": f"device_status_failed:{type(exc).__name__}"},
            )
            return
        if not isinstance(status, dict):
            status = {"ok": True, "state": "idle"}
        self._json(200, self._strip_auth_secrets(dict(status)))

    def _post_auth_xai_device_start(self, body: dict[str, Any]) -> None:
        """POST /api/auth/xai/device/start — mint user_code; server holds device_code."""
        if self._reject_if_auth_origin_bad():
            return
        if self._provider_unavailable():
            self._json(503, {"ok": False, "error": "provider unavailable"})
            return
        if self._reject_if_resetting():
            return

        activate = True
        if "activate" in body:
            raw = body.get("activate")
            if not isinstance(raw, bool):
                self._json(400, {"ok": False, "error": "invalid body", "detail": "activate must be bool"})
                return
            activate = raw

        try:
            result = self.provider.start_xai_device_login(activate=activate)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("xai device start failed")
            self._json(
                500,
                {"ok": False, "error": f"device_start_failed:{type(exc).__name__}"},
            )
            return
        if not isinstance(result, dict):
            result = {"ok": False, "error": "device_start_failed"}
        # 200 even on protocol-level start failure (ok=False + state=error).
        self._json(200, self._strip_auth_secrets(dict(result)))

    def _post_auth_xai_device_cancel(self, body: dict[str, Any]) -> None:  # noqa: ARG002
        """POST /api/auth/xai/device/cancel — stop poller; state cancelled."""
        if self._reject_if_auth_origin_bad():
            return
        if self._provider_unavailable():
            self._json(503, {"ok": False, "error": "provider unavailable"})
            return
        if self._reject_if_resetting():
            return
        try:
            result = self.provider.cancel_xai_device_login()
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("xai device cancel failed")
            self._json(
                500,
                {"ok": False, "error": f"device_cancel_failed:{type(exc).__name__}"},
            )
            return
        if not isinstance(result, dict):
            result = {"ok": True, "state": "cancelled"}
        self._json(200, self._strip_auth_secrets(dict(result)))

    def _post_auth_xai_logout(self, body: dict[str, Any]) -> None:  # noqa: ARG002
        """POST /api/auth/xai/logout — delete bundle; rebuild if source oauth."""
        if self._reject_if_auth_origin_bad():
            return
        if self._provider_unavailable():
            self._json(503, {"ok": False, "error": "provider unavailable"})
            return
        if self._reject_if_resetting():
            return
        try:
            fields = self.provider.logout_xai_oauth()
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("xai logout failed")
            self._json(
                500,
                {"ok": False, "error": f"logout_failed:{type(exc).__name__}"},
            )
            return
        if not isinstance(fields, dict):
            fields = {}
        payload = {
            "ok": True,
            "oauth_configured": bool(fields.get("oauth_configured", False)),
            "credential_source": fields.get("credential_source"),
            "credential_ok": fields.get("credential_ok"),
            "credential_detail": fields.get("credential_detail"),
            "credential_email": fields.get("credential_email"),
            "credential_expires_at": fields.get("credential_expires_at"),
        }
        self._json(200, self._strip_auth_secrets(payload))

    def _patch_provider(self, body: dict[str, Any]) -> None:
        """PATCH /api/provider — ``{ model?, credential_source?, reasoning_effort? }``.

        At least one of the three is required. Successful changes persist prefs
        and rebuild stack when needed (see ProviderRuntime.apply_*). Effort-only
        never rebuilds when http is live. Never echoes secrets. ``auto`` rejected.
        """
        if self._provider_unavailable():
            self._json(503, {"ok": False, "error": "provider unavailable"})
            return
        if self._reject_if_resetting():
            return

        has_model = "model" in body
        has_source = "credential_source" in body
        has_effort = "reasoning_effort" in body
        if not has_model and not has_source and not has_effort:
            self._json(
                400,
                {
                    "ok": False,
                    "error": "model, credential_source, or reasoning_effort required",
                },
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

        if has_effort:
            effort = body.get("reasoning_effort")
            if isinstance(effort, str):
                effort = effort.strip()
            try:
                # strict resolve raises ValueError for non-str / invalid / empty
                provider.apply_reasoning_effort(effort)  # type: ignore[arg-type]
            except ValueError:
                self._json(400, {"ok": False, "error": "invalid_reasoning_effort"})
                return

        self._json(200, self._provider_response_fields())

    # ── Tools / skills package inspector (read-only glass) ───────────────

    def _glass_tool_context(self):
        """Minimal ToolContext for package_vcs get_* (no sandbox/speak)."""
        from elyra.tools.types import ToolContext

        return ToolContext(
            paths=self.paths,
            moment_id="glass",
            user_id="operator",
            registry=self.tools,
        )

    def _package_detail_query(self, qs: dict[str, list[str]]) -> dict[str, Any]:
        """Parse which / version_id / list_versions for package inspect."""
        which_raw = (qs.get("which") or ["current"])[0]
        which = which_raw.strip() if isinstance(which_raw, str) else "current"
        if which not in ("current", "draft", "version"):
            which = "current"
        list_raw = (qs.get("list_versions") or ["1"])[0]
        list_versions = str(list_raw).strip().lower() not in ("0", "false", "no")
        vid_raw = (qs.get("version_id") or [None])[0]
        version_id = (
            vid_raw.strip()
            if isinstance(vid_raw, str) and vid_raw.strip()
            else None
        )
        return {
            "which": which,
            "list_versions": list_versions,
            "version_id": version_id,
        }

    def _get_tool_detail(self, path: str, qs: dict[str, list[str]]) -> None:
        """GET /api/tools/{name} — package summary + optional package VCS versions."""
        if self.tools is None:
            self._json(503, {"ok": False, "error": "tools catalog unavailable"})
            return
        name = _safe_segment(unquote(path[len("/api/tools/") :]))
        if name is None:
            self._json(400, {"ok": False, "error": "invalid tool name"})
            return
        q = self._package_detail_query(qs)
        args: dict[str, Any] = {
            "name": name,
            "which": q["which"],
            "list_versions": q["list_versions"],
        }
        if q["version_id"] is not None:
            args["version_id"] = q["version_id"]

        from elyra.tools.builtin.package_vcs import get_tool

        try:
            result = get_tool(args, self._glass_tool_context())
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("GET /api/tools/%s failed: %s", name, exc)
            self._json(500, {"ok": False, "error": "tool_detail_failed"})
            return

        if not result.ok:
            code = 404 if result.error_reason in (
                "package_not_found",
                "draft_missing",
                "version_not_found",
            ) else 400
            self._json(
                code,
                {
                    "ok": False,
                    "error": result.error_reason or "error",
                    "name": name,
                    "kind": "tool",
                },
            )
            return

        payload = dict(result.payload or {})
        payload["ok"] = True
        payload["kind"] = "tool"
        # Live registry meta when present (callable catalog may lag draft-only).
        try:
            self.tools.reload()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("tools.reload on GET /api/tools/%s failed: %s", name, exc)
        pkg = self.tools.get(name)
        if pkg is not None:
            payload.setdefault("description", pkg.meta.description)
            payload.setdefault("tool_kind", pkg.meta.kind)
            payload.setdefault("catalog_source", pkg.source)
            # Optional schema/runner peeks (truncated) for inspector.
            try:
                schema_path = pkg.package_dir / "schema.json"
                if schema_path.is_file():
                    raw = schema_path.read_text(encoding="utf-8")
                    if len(raw) > 4000:
                        raw = raw[:4000] + "\n…(truncated)"
                    payload["schema_preview"] = raw
            except OSError:
                pass
            try:
                runner_path = pkg.package_dir / "runner.json"
                if runner_path.is_file():
                    payload["runner"] = json.loads(
                        runner_path.read_text(encoding="utf-8")
                    )
            except (OSError, json.JSONDecodeError):
                pass
        self._json(200, payload)

    def _get_skill_detail(self, path: str, qs: dict[str, list[str]]) -> None:
        """GET /api/skills/{name} — playbook body + optional package VCS versions."""
        if self.skills is None:
            self._json(503, {"ok": False, "error": "skills catalog unavailable"})
            return
        name = _safe_segment(unquote(path[len("/api/skills/") :]))
        if name is None:
            self._json(400, {"ok": False, "error": "invalid skill name"})
            return
        q = self._package_detail_query(qs)
        args: dict[str, Any] = {
            "name": name,
            "which": q["which"],
            "list_versions": q["list_versions"],
        }
        if q["version_id"] is not None:
            args["version_id"] = q["version_id"]

        from elyra.tools.builtin.package_vcs import get_skill

        try:
            result = get_skill(args, self._glass_tool_context())
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("GET /api/skills/%s failed: %s", name, exc)
            self._json(500, {"ok": False, "error": "skill_detail_failed"})
            return

        if not result.ok:
            code = 404 if result.error_reason in (
                "package_not_found",
                "draft_missing",
                "version_not_found",
            ) else 400
            self._json(
                code,
                {
                    "ok": False,
                    "error": result.error_reason or "error",
                    "name": name,
                    "kind": "skill",
                },
            )
            return

        payload = dict(result.payload or {})
        payload["ok"] = True
        payload["kind"] = "skill"
        # Prefer full SKILL.md for current catalog load (playbooks are the point).
        if q["which"] == "current":
            try:
                self.skills.reload()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning(
                    "skills.reload on GET /api/skills/%s failed: %s", name, exc
                )
            loaded = self.skills.load(name)
            if loaded is not None:
                payload.setdefault("description", loaded.description)
                payload.setdefault("catalog_source", loaded.source)
                if loaded.body:
                    payload["skill_md"] = loaded.body
        self._json(200, payload)

    # ── Named secrets (PR5 / IK10) ───────────────────────────────────────

    def _secrets_store(self):
        """Lazy SecretsStore bound to this handler's data dir."""
        from elyra.secrets.store import SecretsStore

        return SecretsStore(self.paths.data_dir)

    def _get_secrets(self) -> None:
        """GET /api/secrets — redacted list (names + grants + timestamps only)."""
        store = self._secrets_store()
        rows = store.list_secrets()
        self._json(200, {"ok": True, "secrets": rows})

    def _put_secret(self, body: dict[str, Any]) -> None:
        """PUT /api/secrets — write-only; never echo the value."""
        if self._reject_if_resetting():
            return
        name = body.get("name")
        value = body.get("value")
        if not isinstance(name, str) or not name.strip():
            self._json(400, {"ok": False, "error": "name required"})
            return
        if not isinstance(value, str) or not value.strip():
            self._json(400, {"ok": False, "error": "value required"})
            return
        grants = body.get("grants")
        store = self._secrets_store()
        try:
            meta = store.set_secret(
                name.strip(),
                value,
                grants=grants if grants is not None else None,
            )
        except ValueError as exc:
            reason = str(exc) or "set_failed"
            self._json(400, {"ok": False, "error": reason})
            return
        # Write-only: public meta only — never value.
        self._json(
            200,
            {
                "ok": True,
                "secret": {
                    "name": meta.get("name"),
                    "managed_by": meta.get("managed_by"),
                    "grants": meta.get("grants") or [],
                    "created_at": meta.get("created_at"),
                    "updated_at": meta.get("updated_at"),
                    "last_used_at": meta.get("last_used_at"),
                },
            },
        )

    def _delete_secret(self, name: str) -> None:
        """DELETE /api/secrets/<name> — remove named secret; never echo value."""
        if self._reject_if_resetting():
            return
        store = self._secrets_store()
        try:
            deleted = store.delete_secret(name)
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc) or "invalid_secret_name"})
            return
        if not deleted:
            self._json(404, {"ok": False, "error": "secret_not_found"})
            return
        self._json(200, {"ok": True, "deleted": True, "name": name})

    def _put_secret_grants(self, name: str, body: dict[str, Any]) -> None:
        """PUT /api/secrets/<name>/grants — replace grants list."""
        if self._reject_if_resetting():
            return
        grants = body.get("grants")
        if grants is None:
            self._json(400, {"ok": False, "error": "grants required"})
            return
        store = self._secrets_store()
        try:
            meta = store.set_grants(name, grants)
        except ValueError as exc:
            reason = str(exc) or "set_grants_failed"
            code = 404 if reason == "secret_not_found" else 400
            self._json(code, {"ok": False, "error": reason})
            return
        self._json(
            200,
            {
                "ok": True,
                "secret": {
                    "name": meta.get("name"),
                    "managed_by": meta.get("managed_by"),
                    "grants": meta.get("grants") or [],
                    "created_at": meta.get("created_at"),
                    "updated_at": meta.get("updated_at"),
                    "last_used_at": meta.get("last_used_at"),
                },
            },
        )

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
        Response ``usage`` is the expanded status block (pace/burst/supergrok);
        this handler still only mutates hard_stop_override.
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

    def _patch_semantic_wait(self, body: dict[str, Any]) -> None:
        """PATCH /api/semantic-wait — ``{ "enabled"?: bool, "max_ms"?: number }``.

        Wait-for-select for meal semantic channel. Default product ON
        (15000ms, clamp 1000–120000). When on, slow query encodes are kept.
        """
        if "enabled" not in body and "max_ms" not in body:
            self._json(
                400,
                {"ok": False, "error": "enabled or max_ms required"},
            )
            return
        enabled = body.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            self._json(400, {"ok": False, "error": "enabled must be a boolean"})
            return
        max_ms = body.get("max_ms")
        if max_ms is not None:
            if isinstance(max_ms, bool) or not isinstance(max_ms, (int, float)):
                self._json(
                    400,
                    {"ok": False, "error": "max_ms must be a number"},
                )
                return
        result = self.worker.set_semantic_wait(
            enabled=enabled if isinstance(enabled, bool) else None,
            max_ms=int(max_ms) if max_ms is not None else None,
        )
        if result.get("error") == "resetting":
            self._json(503, result)
            return
        self._json(200, result)

    def _patch_meal_budget(self, body: dict[str, Any]) -> None:
        """PATCH /api/meal-budget — ``{ "fraction": 0.5 }``.

        Meal size as a fraction of model_context_window_tokens. Default 0.5
        (250k of 500k). Clamped to [min_fraction, max_fraction] where product
        default max is 0.75 (raise via ``elyra start --max-meal-override``).
        Persists data/runtime/meal_budget.json. Does not mutate frozen Settings.
        """
        if "fraction" not in body:
            self._json(400, {"ok": False, "error": "fraction required"})
            return
        fraction = body.get("fraction")
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
            self._json(
                400,
                {"ok": False, "error": "fraction must be a number"},
            )
            return
        result = self.worker.set_meal_budget(fraction=float(fraction))
        if result.get("error") == "resetting":
            self._json(503, result)
            return
        if not result.get("ok"):
            # invalid_fraction → 400; persist_failed → 500 (live state unchanged).
            err = result.get("error")
            code = 400 if err == "invalid_fraction" else 500
            self._json(code, result)
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

    def _post_media(self) -> None:
        """POST /api/media — multipart upload → store + project RO (PR3).

        Content-Length pre-checked before body read (max 64 MiB). Streams body
        to temp under data/media/tmp/; stdlib magic MIME; kind size caps.
        Concurrent uploads capped at MAX_CONCURRENT_UPLOADS (503 upload_busy).
        """
        if not _UPLOAD_SLOTS.acquire(blocking=False):
            self._json(
                503,
                {
                    "ok": False,
                    "error": "upload_busy",
                    "reason": "upload_busy",
                },
            )
            return
        tmp_path: Path | None = None
        try:
            length = parse_content_length(self.headers.get("Content-Length"))
            if length is None:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "content_length_required",
                        "reason": "content_length_required",
                    },
                )
                return
            if length > MAX_MEDIA_REQUEST_BYTES:
                self._json(
                    413,
                    {
                        "ok": False,
                        "error": "payload_too_large",
                        "reason": "content_length",
                        "max_bytes": MAX_MEDIA_REQUEST_BYTES,
                    },
                )
                return
            if length <= 0:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "empty_body",
                        "reason": "empty_body",
                    },
                )
                return

            content_type = self.headers.get("Content-Type") or ""
            if "multipart/" not in content_type.lower():
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "multipart required",
                        "reason": "multipart_required",
                    },
                )
                return

            ensure_media_dirs(self.paths)
            store = MediaStore(self.paths)
            store.ensure_dirs()
            try:
                tmp_path = stream_to_temp(self.rfile, length, store.tmp_dir)
            except OSError as exc:
                _LOG.warning("media.upload stream failed: %s", exc)
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "upload_read_failed",
                        "reason": "upload_read_failed",
                    },
                )
                return

            body = tmp_path.read_bytes()
            files = parse_multipart_files(body, content_type)
            fields = parse_multipart_fields(body, content_type)
            if not files:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "no files",
                        "reason": "no_files",
                    },
                )
                return
            if len(files) > MAX_ATTACHMENTS_PER_MESSAGE:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "too many attachments",
                        "reason": "too_many_attachments",
                        "max": MAX_ATTACHMENTS_PER_MESSAGE,
                    },
                )
                return

            origin = (fields.get("origin") or "user_upload").strip()
            if origin not in ATTACHMENT_ORIGINS:
                origin = "user_upload"
            uploader = (fields.get("user_id") or "operator").strip() or "operator"

            attachments: list[dict[str, Any]] = []
            for part in files:
                _mime, kind, _src = sniff_mime_kind_source(
                    part.data,
                    filename=part.filename,
                    claimed_mime=part.content_type,
                )
                limit = max_bytes_for_kind(kind)
                if len(part.data) > limit:
                    self._json(
                        413,
                        {
                            "ok": False,
                            "error": "file_too_large",
                            "reason": "file_too_large",
                            "kind": kind,
                            "max_bytes": limit,
                            "filename": part.filename,
                        },
                    )
                    return
                try:
                    att = store.put_bytes(
                        part.data,
                        filename=part.filename,
                        mime=part.content_type,
                        origin=origin,
                        uploader_user_id=uploader,
                    )
                except (TypeError, ValueError) as exc:
                    self._json(
                        400,
                        {
                            "ok": False,
                            "error": str(exc),
                            "reason": "store_rejected",
                        },
                    )
                    return
                attachments.append(att.to_dict())
                _LOG.info(
                    "media.upload id=%s kind=%s bytes=%s",
                    att.id,
                    att.kind,
                    att.byte_size,
                )

            self._json(200, {"ok": True, "attachments": attachments})
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            _UPLOAD_SLOTS.release()

    def _parse_message_tts_id(self, path: str) -> str | None:
        """Extract message id from ``/api/messages/{id}/tts`` or None if invalid."""
        # path is absolute path without query: /api/messages/<id>/tts
        prefix = "/api/messages/"
        suffix = "/tts"
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        mid = unquote(path[len(prefix) : -len(suffix)])
        if not mid or "/" in mid or mid in (".", "..") or "\\" in mid:
            return None
        if not _SEGMENT_RE.fullmatch(mid):
            return None
        return mid


    def _message_tts(
        self,
        path: str,
        *,
        qs: dict[str, list[str]],
        body: dict[str, Any] | None,
    ) -> None:
        """GET/POST /api/messages/{id}/tts — TTS of stored content only (PR7 / KD3).

        - Loads text via ``get_message`` only (never chat_completion, never append).
        - Empty content → 400 ``empty_text``.
        - Cache key: (message_id, voice, language, profile).
        - xAI provider only; local / missing creds fail closed.
        """
        if not allow_tts():
            self._json(
                429,
                {"ok": False, "error": "rate limited", "reason": "rate_limited"},
            )
            return
        message_id = self._parse_message_tts_id(path)
        if message_id is None:
            self._json(
                400,
                {
                    "ok": False,
                    "error": "invalid message id",
                    "reason": "invalid_message_id",
                },
            )
            return

        if not tts_enabled():
            self._json(
                503,
                {
                    "ok": False,
                    "error": "tts disabled",
                    "reason": "tts_disabled",
                },
            )
            return

        # Fail-closed when provider is not xAI (KD9).
        provider = self.provider
        if provider is not None:
            pname = getattr(provider, "provider_name", None) or ""
            if str(pname) != "xai":
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "tts requires xai provider",
                        "reason": "provider_unsupported",
                        "provider": str(pname),
                    },
                )
                return

        # Params: query string, then optional JSON body overrides.
        body = body if isinstance(body, dict) else {}

        def _pick(*keys: str, default: str) -> str:
            for k in keys:
                vals = qs.get(k)
                if vals and str(vals[0]).strip():
                    return str(vals[0]).strip()
            for k in keys:
                v = body.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return default

        voice = _pick("voice", "voice_id", default=TTS_DEFAULT_VOICE)
        language = _pick("language", "lang", default=TTS_DEFAULT_LANGUAGE)
        profile = _pick(
            "profile", "output_profile", default=TTS_DEFAULT_PROFILE
        )

        row = get_message(message_id, paths=self.paths)
        if row is None:
            self._json(
                404,
                {
                    "ok": False,
                    "error": "message not found",
                    "reason": "not_found",
                    "message_id": message_id,
                },
            )
            return

        content = row.get("content")
        text = content if isinstance(content, str) else (str(content) if content else "")
        if not text.strip():
            self._json(
                400,
                {
                    "ok": False,
                    "error": "empty message content",
                    "reason": "empty_text",
                    "message_id": message_id,
                },
            )
            return

        # Credentials + base URL (xAI only).
        base_url = "https://api.x.ai/v1"
        timeout = 120.0
        bearer = ""
        if provider is not None:
            base_url = str(getattr(provider, "base_url", None) or base_url)
            timeout = float(getattr(provider, "request_timeout_s", None) or timeout)
            source = str(getattr(provider, "credential_source", "grok_build") or "grok_build")
            grok_path = getattr(provider, "grok_auth_path", None)
            data_dir = getattr(provider, "data_dir", None) or self.paths.data_dir
            resolution = resolve_bearer(
                source=source,
                data_dir=Path(data_dir),
                grok_auth_path=Path(grok_path) if grok_path else None,
            )
            if not resolution.ok or not resolution.token:
                self._json(
                    503,
                    {
                        "ok": False,
                        "error": "credential unavailable",
                        "reason": "credential_unavailable",
                        "credential_detail": resolution.detail,
                    },
                )
                return
            bearer = resolution.token
        else:
            # Legacy tests / no provider: try api_key on data dir only.
            resolution = resolve_bearer(
                source="api_key",
                data_dir=self.paths.data_dir,
            )
            if not resolution.ok or not resolution.token:
                self._json(
                    503,
                    {
                        "ok": False,
                        "error": "credential unavailable",
                        "reason": "credential_unavailable",
                        "credential_detail": resolution.detail,
                    },
                )
                return
            bearer = resolution.token

        # Optional injectable for tests (handler attribute or module-level mock).
        http_post = getattr(self, "tts_http_post", None)
        on_remote = None
        if provider is not None and hasattr(provider, "media_remote_success_cb"):
            try:
                on_remote = provider.media_remote_success_cb()
            except Exception:  # noqa: BLE001
                on_remote = None

        try:
            result = get_or_synthesize(
                text,
                message_id=message_id,
                voice_id=voice,
                language=language,
                output_profile=profile,
                bearer_token=bearer,
                base_url=base_url,
                timeout=timeout,
                paths=self.paths,
                http_post=http_post,
                on_remote_success=on_remote,
            )
        except TtsError as exc:
            code = 400
            if exc.reason in ("credential_unavailable", "tts_disabled"):
                code = 503
            elif exc.reason.startswith("tts_http_"):
                code = 502
            elif exc.reason == "tts_connection_failed":
                code = 502
            elif exc.reason == "text_too_long":
                code = 400
            self._json(
                code,
                {
                    "ok": False,
                    "error": str(exc),
                    "reason": exc.reason,
                    "message_id": message_id,
                },
            )
            return
        except Exception as exc:  # noqa: BLE001 — never leak secrets
            _LOG.warning("tts.fail message_id=%s err=%s", message_id, type(exc).__name__)
            self._json(
                500,
                {
                    "ok": False,
                    "error": "tts failed",
                    "reason": "tts_error",
                    "message_id": message_id,
                },
            )
            return

        audio = result.audio
        self.send_response(200)
        self.send_header("Content-Type", result.content_type)
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "X-Tts-Cache", "hit" if result.cache_hit else "miss"
        )
        self.send_header("X-Tts-Voice", result.voice_id)
        self.send_header("X-Tts-Language", result.language)
        self.end_headers()
        self.wfile.write(audio)


    def _post_stt(self) -> None:
        """POST /api/stt — multipart audio → xAI STT → transcript (PR6 / KD4).

        Size caps before body read (Content-Length ≤ 64 MiB request; audio part
        ≤ 25 MiB). Host-only Bearer; never browser keys (KD18). Fail-closed when
        provider ≠ xAI or credentials missing (KD9). Optional keep_audio stores
        recording as attachment (origin user_recording / stt_source).
        """
        if not allow_stt():
            self._json(
                429,
                {"ok": False, "error": "rate limited", "reason": "rate_limited"},
            )
            return
        if not stt_enabled():
            self._json(
                503,
                {
                    "ok": False,
                    "error": "stt disabled",
                    "reason": "stt_disabled",
                },
            )
            return

        # xAI-only fail-closed (KD9).
        provider = self.provider
        if provider is None:
            self._json(
                503,
                {
                    "ok": False,
                    "error": "provider unavailable",
                    "reason": "provider_unavailable",
                },
            )
            return
        provider_name = str(getattr(provider, "provider_name", "") or "")
        if provider_name != "xai":
            self._json(
                503,
                {
                    "ok": False,
                    "error": "STT requires xAI provider",
                    "reason": "provider_unsupported",
                    "provider": provider_name or None,
                },
            )
            return

        if not _UPLOAD_SLOTS.acquire(blocking=False):
            self._json(
                503,
                {
                    "ok": False,
                    "error": "upload_busy",
                    "reason": "upload_busy",
                },
            )
            return
        tmp_path: Path | None = None
        try:
            length = parse_content_length(self.headers.get("Content-Length"))
            if length is None:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "content_length_required",
                        "reason": "content_length_required",
                    },
                )
                return
            # Overall request cap (multipart overhead + audio); product audio 25 MiB.
            if length > MAX_MEDIA_REQUEST_BYTES:
                self._json(
                    413,
                    {
                        "ok": False,
                        "error": "payload_too_large",
                        "reason": "content_length",
                        "max_bytes": MAX_MEDIA_REQUEST_BYTES,
                    },
                )
                return
            if length <= 0:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "empty_body",
                        "reason": "empty_body",
                    },
                )
                return

            content_type = self.headers.get("Content-Type") or ""
            if "multipart/" not in content_type.lower():
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "multipart required",
                        "reason": "multipart_required",
                    },
                )
                return

            ensure_media_dirs(self.paths)
            store = MediaStore(self.paths)
            store.ensure_dirs()
            try:
                tmp_path = stream_to_temp(self.rfile, length, store.tmp_dir)
            except OSError as exc:
                _LOG.warning("stt.upload stream failed: %s", exc)
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "upload_read_failed",
                        "reason": "upload_read_failed",
                    },
                )
                return

            body = tmp_path.read_bytes()
            files = parse_multipart_files(body, content_type)
            fields = parse_multipart_fields(body, content_type)
            if not files:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "no files",
                        "reason": "no_files",
                    },
                )
                return
            part = files[0]
            if len(part.data) > MAX_AUDIO_BYTES:
                self._json(
                    413,
                    {
                        "ok": False,
                        "error": "file_too_large",
                        "reason": "file_too_large",
                        "kind": "audio",
                        "max_bytes": MAX_AUDIO_BYTES,
                        "filename": part.filename,
                    },
                )
                return

            # Resolve host bearer (never expose to browser).
            source = str(getattr(provider, "credential_source", "") or "api_key")
            data_dir = getattr(provider, "data_dir", None) or self.paths.data_dir
            grok_auth = getattr(provider, "grok_auth_path", None)
            resolution = resolve_bearer(
                source=source,
                data_dir=Path(data_dir),
                grok_auth_path=Path(grok_auth) if grok_auth else None,
            )
            if not resolution.ok or not resolution.token:
                self._json(
                    503,
                    {
                        "ok": False,
                        "error": "credentials unavailable",
                        "reason": "credential_unavailable",
                        "detail": resolution.detail,
                    },
                )
                return

            base_url = str(
                getattr(provider, "base_url", None) or "https://api.x.ai/v1"
            )
            language = (fields.get("language") or "").strip() or None
            mime = part.content_type or "application/octet-stream"
            on_remote = None
            if provider is not None and hasattr(provider, "media_remote_success_cb"):
                try:
                    on_remote = provider.media_remote_success_cb()
                except Exception:  # noqa: BLE001
                    on_remote = None
            try:
                result = transcribe(
                    part.data,
                    filename=part.filename or "audio.bin",
                    mime=mime,
                    bearer_token=resolution.token,
                    base_url=base_url,
                    model=DEFAULT_STT_MODEL,
                    language=language,
                    timeout=float(getattr(provider, "request_timeout_s", 120.0) or 120.0),
                    on_remote_success=on_remote,
                )
            except SttError as exc:
                status = 502
                if exc.http_status == 401:
                    status = 502
                elif exc.http_status == 413:
                    status = 413
                elif exc.http_status == 429:
                    status = 429
                elif exc.reason in ("stt_invalid_audio", "stt_empty_text"):
                    status = 400
                self._json(
                    status,
                    {
                        "ok": False,
                        "error": str(exc),
                        "reason": exc.reason,
                    },
                )
                return

            keep_raw = (fields.get("keep_audio") or fields.get("keep") or "").strip().lower()
            keep = keep_raw in ("1", "true", "yes", "on")
            origin = (fields.get("origin") or "user_recording").strip()
            if origin not in ATTACHMENT_ORIGINS:
                origin = "user_recording"
            if origin not in ("user_recording", "stt_source"):
                origin = "user_recording"
            uploader = (fields.get("user_id") or "operator").strip() or "operator"

            out: dict[str, Any] = {
                "ok": True,
                "text": result.text,
                "language": result.language,
                "duration": result.duration_s,
                "model": DEFAULT_STT_MODEL,
            }
            if keep:
                try:
                    att = store.put_bytes(
                        part.data,
                        filename=part.filename or "recording.webm",
                        mime=mime,
                        kind="audio",
                        origin=origin,
                        role_hint="source",
                        uploader_user_id=uploader,
                    )
                except (TypeError, ValueError) as exc:
                    self._json(
                        400,
                        {
                            "ok": False,
                            "error": str(exc),
                            "reason": "store_rejected",
                        },
                    )
                    return
                out["attachment_id"] = att.id
                out["attachment"] = att.to_dict()

            self._json(200, out)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            _UPLOAD_SLOTS.release()

    def _get_media(self, path: str) -> None:
        """GET /api/media/{id} or /api/media/{id}/meta — path-jailed serve (PR3)."""
        if path == "/api/media" or path == "/api/media/":
            self._json(404, {"ok": False, "error": "not found", "reason": "not_found"})
            return
        rest = unquote(path[len("/api/media/") :])
        want_meta = False
        if rest.endswith("/meta"):
            want_meta = True
            rest = rest[: -len("/meta")]
        # Reject nested paths / traversal (path jail).
        if not rest or "/" in rest or rest in (".", "..") or "\\" in rest:
            self._json(
                400,
                {
                    "ok": False,
                    "error": "invalid attachment id",
                    "reason": "invalid_attachment_id",
                },
            )
            return
        try:
            att_id = validate_att_id(rest)
        except ValueError:
            self._json(
                400,
                {
                    "ok": False,
                    "error": "invalid attachment id",
                    "reason": "invalid_attachment_id",
                },
            )
            return

        store = MediaStore(self.paths)
        att = store.get(att_id)
        if att is None:
            self._json(
                404,
                {
                    "ok": False,
                    "error": "not found",
                    "reason": "not_found",
                    "attachment_id": att_id,
                },
            )
            return

        if want_meta:
            self._json(200, {"ok": True, "attachment": att.to_dict()})
            return

        try:
            data = store.read_bytes(att_id)
        except FileNotFoundError:
            self._json(
                404,
                {
                    "ok": False,
                    "error": "blob missing",
                    "reason": "not_found",
                    "attachment_id": att_id,
                },
            )
            return

        ctype = att.mime or "application/octet-stream"
        # Content-Disposition: attachment with sanitized filename only.
        fname = att.filename or "file"
        # Strip CR/LF and quotes for header safety.
        safe_disp = fname.replace('"', "").replace("\r", "").replace("\n", "")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition", f'inline; filename="{safe_disp}"'
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        _LOG.info("media.serve id=%s bytes=%s", att_id, len(data))

    def _post_messages(self, body: dict[str, Any]) -> None:
        """POST /api/messages — glass chat → resolve_user_input (from_wait_api=False).

        Append is gated through ``worker.append_message_if_allowed`` (check +
        write under worker lock) so concurrent full reset cannot leave chat
        residue after ``ok: true``.

        Body: ``{ content, user_id, attachment_ids?: string[], meta?: {} }``.
        Empty content is allowed when ``attachment_ids`` is non-empty (R1b).
        Bind order under worker lock (PR3 / KD23).

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
        raw_ids = body.get("attachment_ids")
        attachment_ids: list[str] = []
        if raw_ids is not None:
            if not isinstance(raw_ids, list):
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "attachment_ids must be a list",
                        "reason": "invalid_attachment_ids",
                    },
                )
                return
            for item in raw_ids:
                if not isinstance(item, str) or not item.strip():
                    self._json(
                        400,
                        {
                            "ok": False,
                            "error": "invalid attachment id",
                            "reason": "invalid_attachment_ids",
                        },
                    )
                    return
                attachment_ids.append(item.strip())
            # Dedupe preserving order
            seen: set[str] = set()
            deduped: list[str] = []
            for aid in attachment_ids:
                if aid not in seen:
                    seen.add(aid)
                    deduped.append(aid)
            attachment_ids = deduped
            if len(attachment_ids) > MAX_ATTACHMENTS_PER_MESSAGE:
                self._json(
                    400,
                    {
                        "ok": False,
                        "error": "too many attachments",
                        "reason": "too_many_attachments",
                        "max": MAX_ATTACHMENTS_PER_MESSAGE,
                    },
                )
                return
            for aid in attachment_ids:
                try:
                    validate_att_id(aid)
                except ValueError:
                    self._json(
                        400,
                        {
                            "ok": False,
                            "error": "invalid attachment id",
                            "reason": "invalid_attachment_ids",
                            "attachment_id": aid,
                        },
                    )
                    return

        if not content and not attachment_ids:
            self._json(
                400,
                {
                    "ok": False,
                    "error": "content required",
                    "reason": "empty_content",
                },
            )
            return

        meta = body.get("meta")
        if meta is not None and not isinstance(meta, dict):
            self._json(
                400,
                {"ok": False, "error": "meta must be an object", "reason": "invalid_meta"},
            )
            return

        msg, err = self.worker.append_message_if_allowed(
            "user",
            content,
            user_id=user_id,
            meta=meta if isinstance(meta, dict) else None,
            bind_attachment_ids=attachment_ids or None,
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
            has_attachments=bool(attachment_ids),
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
    gate: ChatRequestGate,
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
