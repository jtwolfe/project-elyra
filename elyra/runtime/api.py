"""HTTP API and static Web UI.

Scope: REST JSON + SPA fallthrough for operator glass.
In scope: status, messages, wait reply, continuous toggle,
  lean glass catalogs (goals, moments, tools, skills, identity/users).
Out of scope: promote/verify admin, multi-user glass, write identity, full reset.
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from elyra.config import ElyraPaths
from elyra.goals import GoalsStore
from elyra.identity import IdentityStore
from elyra.llm.queue import LlamaServerGate
from elyra.messages import append_message, list_messages
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
    # Lean catalog stores (file-backed; constructed once at server start).
    goals: GoalsStore
    moments: MomentStore
    identity: IdentityStore
    users: UsersStore
    tools: ToolRegistry | None
    skills: SkillCatalog | None

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
            digest = self.identity.self_digest()
            self._json(
                200,
                {
                    "self": {
                        "path": str(self.identity.self_path),
                        "digest": digest,
                    }
                },
            )
            return

        if path.startswith("/api/users/"):
            uid = _safe_segment(unquote(path[len("/api/users/") :]))
            if uid is None:
                self._json(400, {"ok": False, "error": "invalid user id"})
                return
            try:
                profile = self.users.profile(uid)
            except ValueError:
                self._json(400, {"ok": False, "error": "invalid user id"})
                return
            self._json(
                200,
                {
                    "user_id": uid,
                    "profile": profile,
                    "path": str(self.users.profile_path(uid)),
                },
            )
            return

        if path == "/api/tools":
            if self.tools is None:
                self._json(200, {"tools": [], "error": "tools catalog unavailable"})
                return
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

        self._json(404, {"error": "not found"})

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json()

        if path == "/api/continuous":
            self._patch_continuous(body)
            return

        self._json(404, {"error": "not found"})

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
        self._json(200, result)

    def _post_goals(self, body: dict[str, Any]) -> None:
        """POST /api/goals — create a goal (lean glass / operator)."""
        title = str(body.get("title") or "").strip()
        if not title:
            self._json(400, {"ok": False, "error": "title required"})
            return
        acceptance = body.get("acceptance")
        if acceptance is not None:
            acceptance = str(acceptance)
        status = str(body.get("status") or "open")
        try:
            goal = self.goals.create_goal(
                title,
                acceptance=acceptance,
                status=status,
            )
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        self._json(200, {"ok": True, "goal": goal})

    def _post_messages(self, body: dict[str, Any]) -> None:
        """POST /api/messages — glass chat → resolve_user_input (from_wait_api=False).

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
        msg = append_message("user", content, user_id=user_id, paths=self.paths)
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
        msg = append_message("user", display, user_id=user_id, paths=self.paths)
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
        - empty / other client errors → 400
        """
        if result.get("ok"):
            return 200
        if result.get("reason") == REASON_BUFFER_FULL:
            return 200
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
    """
    if tools is ...:
        tools = _try_tool_registry(paths)
    if skills is ...:
        skills = _try_skill_catalog(paths)

    handler = type(
        "BoundHandler",
        (ElyraApiHandler,),
        {
            "paths": paths,
            "gate": gate,
            "state": state,
            "worker": worker,
            "config": config,
            "goals": goals or GoalsStore(paths),
            "moments": moments or MomentStore(paths),
            "identity": identity or IdentityStore(paths),
            "users": users or UsersStore(paths),
            "tools": tools,
            "skills": skills,
        },
    )
    server = ThreadingHTTPServer((config.api_host, config.api_port), handler)
    thread = threading.Thread(target=server.serve_forever, name="elyra-api", daemon=True)
    thread.start()
    return server, thread
