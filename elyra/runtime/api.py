"""HTTP API and static Web UI.

Scope: REST JSON + SPA fallthrough for operator glass.
In scope: status, messages, wait reply routing via resolve_user_input.
Out of scope: glass panels (goals/moments UI polish), tool catalog endpoints.
"""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from elyra.config import ElyraPaths
from elyra.llm.queue import LlamaServerGate
from elyra.messages import append_message, list_messages
from elyra.presence.interject import REASON_BUFFER_FULL
from elyra.presence.worker import PresenceWorker
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState

WEB_DIR = Path(__file__).resolve().parent / "web"


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


class ElyraApiHandler(BaseHTTPRequestHandler):
    paths: ElyraPaths
    gate: LlamaServerGate
    state: RuntimeState
    worker: PresenceWorker
    config: RuntimeConfig

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
            qs = parse_qs(parsed.query)
            limit = int((qs.get("limit") or ["200"])[0])
            self._json(200, {"messages": list_messages(limit=limit, paths=self.paths)})
            return

        if path == "/api/health":
            self._json(200, {"ok": True})
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

        self._json(404, {"error": "not found"})

    def _post_messages(self, body: dict[str, Any]) -> None:
        """POST /api/messages — glass chat → resolve_user_input (from_wait_api=False).

        Routing matrix (worker phase + pending wait):
        - in_moment → interject buffer (overflow → wake + reason)
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


def start_api_server(
    config: RuntimeConfig,
    *,
    paths: ElyraPaths,
    gate: LlamaServerGate,
    state: RuntimeState,
    worker: PresenceWorker,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = type(
        "BoundHandler",
        (ElyraApiHandler,),
        {
            "paths": paths,
            "gate": gate,
            "state": state,
            "worker": worker,
            "config": config,
        },
    )
    server = ThreadingHTTPServer((config.api_host, config.api_port), handler)
    thread = threading.Thread(target=server.serve_forever, name="elyra-api", daemon=True)
    thread.start()
    return server, thread
