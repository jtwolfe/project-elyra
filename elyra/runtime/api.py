"""HTTP API and static Web UI.

Scope: REST JSON + SPA fallthrough for operator glass.
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
from elyra.loop.worker import PresenceWorker
from elyra.messages import append_message, list_messages
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState

WEB_DIR = Path(__file__).resolve().parent / "web"


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
            snap.update(
                {
                    "home": str(self.paths.home),
                    "llama_busy": self.gate.busy,
                    "llama_operation": self.gate.current_label,
                    "worker_busy": self.worker.busy,
                    "worker_pending": self.worker.pending,
                    "worker_error": self.worker.last_error,
                    "active_moment_id": self.worker.active_moment_id,
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
            content = str(body.get("content") or "").strip()
            user_id = str(body.get("user_id") or "operator")
            if not content:
                self._json(400, {"error": "content required"})
                return
            msg = append_message(
                "user", content, user_id=user_id, paths=self.paths
            )
            self.worker.enqueue_user_message(
                content, user_id=user_id, message_id=msg.id
            )
            self._json(200, {"message": msg.__dict__})
            return

        self._json(404, {"error": "not found"})

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
