"""PR3: media upload/serve API + message attachment_ids (KD15, KD18, KD23).

Covers: media-only post, oversized Content-Length 413 (no body alloc),
serve unknown 404, path jail, bind under lock, size/mime.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest

from elyra.config import resolve_paths
from elyra.llm.client import StubChatClient
from elyra.llm.queue import ChatRequestGate
from elyra.loop.doloop import DoLoopResult
from elyra.media import MediaStore, MAX_MEDIA_REQUEST_BYTES, get_attachment
from elyra.messages import get_message, list_messages
from elyra.moment import MomentStore
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import TimerService
from elyra.presence.user_input import PHASE_IDLE, ROUTE_USER_MESSAGE, resolve_user_input
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState
from elyra.settings import default_settings

FIXTURE_PNG = Path(__file__).parent / "fixtures" / "media" / "1x1.png"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


def _fake_registry() -> MagicMock:
    reg = MagicMock()
    reg.openai_tools.return_value = []
    reg.execute.return_value = MagicMock(ok=True, payload={}, ends_moment=False)
    return reg


def _stub_loop(**kwargs: Any) -> Any:
    def _fn(**kw: Any) -> DoLoopResult:
        ctx = kw.get("ctx")
        mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            arm_wait=None,
            spoke=False,
            moment_id=mid,
            continue_injects=0,
            error=None,
        )

    return _fn


def _make_worker(paths) -> tuple[PresenceWorker, threading.Event]:
    stop = threading.Event()
    queue = WakeQueue(paths)
    timers = TimerService(paths, queue)
    moments = MomentStore(paths)
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=stop,
        poll_seconds=0.05,
        settings=default_settings(),
        queue=queue,
        timers=timers,
        moments=moments,
        registry=_fake_registry(),
        run_do_loop_fn=_stub_loop(),
    )
    return worker, stop


class _Harness:
    def __init__(self, paths, *, start_worker: bool = True) -> None:
        self.paths = paths
        self.worker, self._stop = _make_worker(paths)
        self._worker_thread: threading.Thread | None = None
        if start_worker:
            self._worker_thread = threading.Thread(
                target=self.worker.run, name="test-media-presence", daemon=True
            )
            self._worker_thread.start()
            time.sleep(0.05)
        config = RuntimeConfig(api_host="127.0.0.1", api_port=0)
        self.state = RuntimeState()
        self.gate = ChatRequestGate()
        self.server, self._api_thread = start_api_server(
            config,
            paths=paths,
            gate=self.gate,
            state=self.state,
            worker=self.worker,
        )
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def close(self) -> None:
        self._stop.set()
        try:
            self.server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.server.server_close()
        except Exception:  # noqa: BLE001
            pass
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=2.0)

    def get_raw(self, path: str) -> tuple[int, bytes, dict[str, str]]:
        req = urllib.request.Request(self.base + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, resp.read(), headers
        except urllib.error.HTTPError as exc:
            headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            return exc.code, exc.read(), headers

    def get_json(self, path: str) -> tuple[int, Any]:
        code, raw, _ = self.get_raw(path)
        try:
            return code, json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeError):
            return code, raw

    def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body

    def post_multipart(
        self,
        path: str,
        files: list[tuple[str, str, bytes, str]],
        *,
        fields: dict[str, str] | None = None,
        content_length_override: int | None = None,
        omit_body: bool = False,
    ) -> tuple[int, Any]:
        """Build multipart body. files: (field, filename, data, content_type)."""
        boundary = "----ElyraTestBoundary7MA4YWxk"
        parts: list[bytes] = []
        for name, value in (fields or {}).items():
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )
        for field, filename, data, ctype in files:
            head = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field}"; '
                f'filename="{filename}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n"
            ).encode("utf-8")
            parts.append(head + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        cl = content_length_override if content_length_override is not None else len(body)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(cl),
        }
        data = b"" if omit_body else body
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw


# ---------------------------------------------------------------------------
# Pure routing: media-only empty content
# ---------------------------------------------------------------------------


def test_resolve_user_input_allows_empty_with_attachments():
    d = resolve_user_input(
        "",
        "operator",
        phase=PHASE_IDLE,
        pending_wait=None,
        has_attachments=True,
    )
    assert d["ok"] is True
    assert d["routed"] == ROUTE_USER_MESSAGE


def test_resolve_user_input_still_rejects_empty_without_attachments():
    d = resolve_user_input(
        "   ",
        "operator",
        phase=PHASE_IDLE,
        pending_wait=None,
        has_attachments=False,
    )
    assert d["ok"] is False
    assert d["reason"] == "empty_content"


# ---------------------------------------------------------------------------
# Upload + serve
# ---------------------------------------------------------------------------


def test_post_media_upload_png_and_serve(paths):
    png = FIXTURE_PNG.read_bytes()
    h = _Harness(paths, start_worker=False)
    try:
        code, body = h.post_multipart(
            "/api/media",
            [("file", "shot.png", png, "image/png")],
            fields={"user_id": "operator"},
        )
        assert code == 200, body
        assert body["ok"] is True
        assert len(body["attachments"]) == 1
        att = body["attachments"][0]
        assert att["kind"] == "image"
        assert att["mime"] == "image/png"
        assert att["filename"] == "shot.png"
        assert att["bound_message_id"] is None
        assert att["byte_size"] == len(png)
        att_id = att["id"]

        # Durable meta + blob
        loaded = get_attachment(att_id, paths=paths)
        assert loaded is not None
        assert loaded.sha256 == att["sha256"]

        # Serve bytes
        code, raw, headers = h.get_raw(f"/api/media/{att_id}")
        assert code == 200
        assert raw == png
        assert "image/png" in headers.get("content-type", "")

        # Meta endpoint
        code, meta = h.get_json(f"/api/media/{att_id}/meta")
        assert code == 200
        assert meta["attachment"]["id"] == att_id
    finally:
        h.close()


def test_serve_unknown_attachment_404(paths):
    h = _Harness(paths, start_worker=False)
    try:
        code, body = h.get_json("/api/media/att_deadbeefdeadbeefdeadbeefdeadbeef")
        assert code == 404
        assert body["ok"] is False
        assert body["reason"] == "not_found"
    finally:
        h.close()


def test_serve_path_jail_rejects_traversal(paths):
    h = _Harness(paths, start_worker=False)
    try:
        for bad in (
            "/api/media/../messages",
            "/api/media/..%2F..%2Fetc%2Fpasswd",
            "/api/media/att_x/../../etc/passwd",
            "/api/media/%2e%2e%2fetc",
        ):
            code, body = h.get_json(bad)
            # Path jail → 400 invalid id, or 404 static/SPA — never 200 with secrets.
            assert code in (400, 404), (bad, code, body)
            if isinstance(body, dict) and code == 400:
                assert body.get("reason") in (
                    "invalid_attachment_id",
                    None,
                ) or "invalid" in str(body.get("error", "")).lower()
    finally:
        h.close()


def test_media_oversized_content_length_413_without_body(paths):
    """Oversized Content-Length returns 413 without requiring the claimed body."""
    h = _Harness(paths, start_worker=False)
    try:
        # http.client lets us send Content-Length without the body (urllib rewrites CL).
        parsed = urlparse(h.base)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            conn.putrequest("POST", "/api/media")
            conn.putheader(
                "Content-Type",
                "multipart/form-data; boundary=----ElyraTestBoundary",
            )
            conn.putheader("Content-Length", str(MAX_MEDIA_REQUEST_BYTES + 1))
            conn.endheaders()
            # Do not write body — handler must reject on header alone.
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            body = json.loads(raw)
            assert resp.status == 413, body
            assert body["ok"] is False
            assert body["reason"] == "content_length"
            assert body["max_bytes"] == MAX_MEDIA_REQUEST_BYTES
        finally:
            conn.close()
    finally:
        h.close()


def test_json_oversized_content_length_413(paths):
    h = _Harness(paths, start_worker=False)
    try:
        parsed = urlparse(h.base)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            conn.putrequest("POST", "/api/messages")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(2 * 1024 * 1024))
            conn.endheaders()
            resp = conn.getresponse()
            body = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 413
            assert body["reason"] == "content_length"
        finally:
            conn.close()
    finally:
        h.close()


# ---------------------------------------------------------------------------
# Message attachment_ids + media-only post
# ---------------------------------------------------------------------------


def test_media_only_message_post(paths):
    png = FIXTURE_PNG.read_bytes()
    h = _Harness(paths, start_worker=True)
    try:
        code, up = h.post_multipart(
            "/api/media",
            [("file", "only.png", png, "image/png")],
        )
        assert code == 200
        att_id = up["attachments"][0]["id"]

        code, body = h.post_json(
            "/api/messages",
            {"content": "", "user_id": "operator", "attachment_ids": [att_id]},
        )
        assert code == 200, body
        assert body["ok"] is True
        assert body["routed"] == ROUTE_USER_MESSAGE
        msg = body["message"]
        # Message may be dataclass __dict__
        if isinstance(msg, dict):
            assert msg.get("content") == ""
            atts = msg.get("attachments") or []
            assert len(atts) == 1
            assert atts[0]["id"] == att_id
            mid = msg["id"]
        else:
            mid = body["message_id"]

        row = get_message(mid, paths=paths)
        assert row is not None
        assert row["content"] == ""
        assert row["attachments"][0]["id"] == att_id

        bound = get_attachment(att_id, paths=paths)
        assert bound is not None
        assert bound.bound_message_id == mid

        # Projected into sandbox media RO tree
        projected = (
            paths.home / "sandboxes" / "sandbox0" / "media" / att_id / "only.png"
        )
        assert projected.is_file()
        assert projected.read_bytes() == png
    finally:
        h.close()


def test_message_with_text_and_attachment(paths):
    png = FIXTURE_PNG.read_bytes()
    h = _Harness(paths, start_worker=False)
    try:
        code, up = h.post_multipart(
            "/api/media",
            [("file", "cap.png", png, "image/png")],
        )
        att_id = up["attachments"][0]["id"]
        code, body = h.post_json(
            "/api/messages",
            {
                "content": "see this",
                "user_id": "operator",
                "attachment_ids": [att_id],
            },
        )
        assert code == 200, body
        assert body["ok"] is True
        rows = list_messages(paths=paths)
        assert rows[-1]["content"] == "see this"
        assert rows[-1]["attachments"][0]["id"] == att_id
    finally:
        h.close()


def test_message_unknown_attachment_id(paths):
    h = _Harness(paths, start_worker=False)
    try:
        code, body = h.post_json(
            "/api/messages",
            {
                "content": "hi",
                "attachment_ids": ["att_00000000000000000000000000000000"],
            },
        )
        assert code == 400
        assert body["reason"] == "attachment_not_found"
    finally:
        h.close()


def test_message_rejects_already_bound_attachment(paths):
    png = FIXTURE_PNG.read_bytes()
    h = _Harness(paths, start_worker=False)
    try:
        code, up = h.post_multipart(
            "/api/media",
            [("file", "once.png", png, "image/png")],
        )
        att_id = up["attachments"][0]["id"]
        code, first = h.post_json(
            "/api/messages",
            {"content": "first", "attachment_ids": [att_id]},
        )
        assert code == 200, first
        code, second = h.post_json(
            "/api/messages",
            {"content": "second", "attachment_ids": [att_id]},
        )
        assert code == 400
        assert second["reason"] == "attachment_bound"
    finally:
        h.close()


def test_message_too_many_attachments(paths):
    h = _Harness(paths, start_worker=False)
    try:
        ids = [f"att_{i:032x}" for i in range(9)]
        code, body = h.post_json(
            "/api/messages",
            {"content": "x", "attachment_ids": ids},
        )
        assert code == 400
        assert body["reason"] == "too_many_attachments"
    finally:
        h.close()


def test_put_bytes_then_bind_via_store(paths):
    """Worker bind path: store put → append_message_if_allowed binds."""
    store = MediaStore(paths)
    att = store.put_bytes(b"hello", filename="n.txt", origin="user_upload")
    w, stop = _make_worker(paths)
    try:
        msg, err = w.append_message_if_allowed(
            "user",
            "",
            user_id="operator",
            bind_attachment_ids=[att.id],
        )
        assert err is None
        assert msg is not None
        assert msg.content == ""
        assert msg.attachments and msg.attachments[0]["id"] == att.id
        again = store.get(att.id)
        assert again is not None
        assert again.bound_message_id == msg.id
    finally:
        stop.set()
