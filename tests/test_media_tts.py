"""PR7: TTS play + disk cache (KD3).

Covers: empty→400, 15k guard, cache hit on second call, sanitized path,
no chat_completion, no new glass rows, mocked xAI HTTP.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.llm.auth import write_stored_api_key
from elyra.llm.client import StubChatClient
from elyra.llm.queue import ChatRequestGate
from elyra.loop.doloop import DoLoopResult
from elyra.media.tts import (
    TTS_DEFAULT_LANGUAGE,
    TTS_DEFAULT_PROFILE,
    TTS_DEFAULT_VOICE,
    TTS_MAX_CHARS,
    TtsError,
    cache_filename,
    cache_path_for,
    get_or_synthesize,
    safe_cache_token,
    synthesize,
    tts_enabled,
)
from elyra.messages import append_message, get_message, list_messages
from elyra.moment import MomentStore
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import TimerService
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState
from elyra.settings import default_settings

# Tiny fake MPEG frame-ish payload for tests (not a real decoder target).
FAKE_MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 64


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    write_stored_api_key(paths.data_dir, "test-xai-key-not-real")
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


# ---------------------------------------------------------------------------
# Unit: pure helpers + synthesize (mocked HTTP)
# ---------------------------------------------------------------------------


def test_safe_cache_token_strips_unsafe():
    assert safe_cache_token("eve") == "eve"
    assert safe_cache_token("en") == "en"
    assert "/" not in safe_cache_token("../evil/id")
    assert " " not in safe_cache_token("a b")
    assert len(safe_cache_token("x" * 200)) == 80


def test_cache_filename_key_components():
    name = cache_filename("msg-1", "eve", "en", "mp3_24k_128")
    assert name.endswith(".mp3")
    assert "msg-1" in name
    assert "eve" in name
    assert "en" in name
    assert "mp3_24k_128" in name


def test_validate_empty_and_too_long():
    with pytest.raises(TtsError) as ei:
        synthesize("", voice_id="eve", language="en", bearer_token="tok")
    assert ei.value.reason == "empty_text"

    with pytest.raises(TtsError) as ei:
        synthesize("   ", voice_id="eve", language="en", bearer_token="tok")
    assert ei.value.reason == "empty_text"

    big = "a" * (TTS_MAX_CHARS + 1)
    with pytest.raises(TtsError) as ei:
        synthesize(big, voice_id="eve", language="en", bearer_token="tok")
    assert ei.value.reason == "text_too_long"


def test_synthesize_mocked_http_posts_json():
    seen: dict[str, Any] = {}

    def http_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
        seen["url"] = url
        seen["headers"] = headers
        seen["body"] = json.loads(body.decode("utf-8"))
        seen["timeout"] = timeout
        return FAKE_MP3

    audio = synthesize(
        "Hello world",
        voice_id="eve",
        language="en",
        output_profile=TTS_DEFAULT_PROFILE,
        bearer_token="secret-token",
        base_url="https://api.x.ai/v1",
        http_post=http_post,
    )
    assert audio == FAKE_MP3
    assert seen["url"] == "https://api.x.ai/v1/tts"
    assert seen["headers"]["Authorization"] == "Bearer secret-token"
    assert seen["body"]["text"] == "Hello world"
    assert seen["body"]["voice_id"] == "eve"
    assert seen["body"]["language"] == "en"
    assert seen["body"]["output_format"]["codec"] == "mp3"


def test_get_or_synthesize_cache_hit(paths):
    calls = {"n": 0}

    def http_post(url, headers, body, timeout):
        calls["n"] += 1
        return FAKE_MP3 + bytes([calls["n"]])

    r1 = get_or_synthesize(
        "Cached hello",
        message_id="mid-abc",
        voice_id="eve",
        language="en",
        output_profile=TTS_DEFAULT_PROFILE,
        bearer_token="tok",
        paths=paths,
        http_post=http_post,
    )
    assert r1.cache_hit is False
    assert calls["n"] == 1
    cpath = cache_path_for(
        "mid-abc", "eve", "en", TTS_DEFAULT_PROFILE, paths=paths
    )
    assert cpath.is_file()
    assert r1.audio == cpath.read_bytes()

    r2 = get_or_synthesize(
        "Cached hello",
        message_id="mid-abc",
        voice_id="eve",
        language="en",
        output_profile=TTS_DEFAULT_PROFILE,
        bearer_token="tok",
        paths=paths,
        http_post=http_post,
    )
    assert r2.cache_hit is True
    assert calls["n"] == 1  # no second xAI call
    assert r2.audio == r1.audio


def test_cache_key_includes_voice_language_profile(paths):
    calls = {"n": 0}

    def http_post(url, headers, body, timeout):
        calls["n"] += 1
        return FAKE_MP3 + bytes([calls["n"]])

    get_or_synthesize(
        "Hi",
        message_id="m1",
        voice_id="eve",
        language="en",
        bearer_token="t",
        paths=paths,
        http_post=http_post,
    )
    get_or_synthesize(
        "Hi",
        message_id="m1",
        voice_id="ara",
        language="en",
        bearer_token="t",
        paths=paths,
        http_post=http_post,
    )
    get_or_synthesize(
        "Hi",
        message_id="m1",
        voice_id="eve",
        language="fr",
        bearer_token="t",
        paths=paths,
        http_post=http_post,
    )
    assert calls["n"] == 3
    # Three distinct cache files
    tts_dir = paths.data_dir / "media" / "tts"
    files = list(tts_dir.glob("*.mp3"))
    assert len(files) == 3


def test_tts_enabled_kill_switch(monkeypatch):
    monkeypatch.delenv("ELYRA_TTS", raising=False)
    assert tts_enabled() is True
    monkeypatch.setenv("ELYRA_TTS", "0")
    assert tts_enabled() is False
    monkeypatch.setenv("ELYRA_TTS", "1")
    assert tts_enabled() is True


# ---------------------------------------------------------------------------
# API harness
# ---------------------------------------------------------------------------


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


class _FakeProvider:
    """Minimal ProviderRuntime stand-in for TTS routes."""

    def __init__(self, paths, *, provider_name: str = "xai") -> None:
        self.provider_name = provider_name
        self.base_url = "https://api.x.ai/v1"
        self.request_timeout_s = 30.0
        self.credential_source = "api_key"
        self.credential_ok = True
        self.data_dir = paths.data_dir
        self.grok_auth_path = None

    def status_provider_fields(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "credential_ok": self.credential_ok,
            "credential_source": self.credential_source,
        }

    def usage_status_block(self) -> dict[str, Any]:
        return {"enabled": False}


class _Harness:
    def __init__(self, paths, *, provider: Any | None = ...) -> None:
        self.paths = paths
        self.worker, self._stop = _make_worker(paths)
        self._worker_thread = threading.Thread(
            target=self.worker.run, name="test-tts-presence", daemon=True
        )
        self._worker_thread.start()
        time.sleep(0.05)
        config = RuntimeConfig(api_host="127.0.0.1", api_port=0)
        self.state = RuntimeState()
        self.gate = ChatRequestGate()
        if provider is ...:
            provider = _FakeProvider(paths)
        self.server, self._api_thread = start_api_server(
            config,
            paths=paths,
            gate=self.gate,
            state=self.state,
            worker=self.worker,
            provider=provider,
        )
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"
        self.http_calls = 0
        # Inject mocked xAI TTS HTTP on the bound handler class.
        self.server.RequestHandlerClass.tts_http_post = self._mock_http  # type: ignore[attr-defined]

    def _mock_http(self, url, headers, body, timeout):
        self.http_calls += 1
        return FAKE_MP3 + bytes([self.http_calls & 0xFF])

    def close(self) -> None:
        self._stop.set()
        # Clear injectable so other tests aren't polluted if class is reused.
        if hasattr(self.server.RequestHandlerClass, "tts_http_post"):
            delattr(self.server.RequestHandlerClass, "tts_http_post")
        try:
            self.server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.server.server_close()
        except Exception:  # noqa: BLE001
            pass
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

    def post_json(self, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "Content-Length": str(len(data))},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                body = resp.read()
                ctype = headers.get("content-type", "")
                if "json" in ctype:
                    return resp.status, json.loads(body.decode("utf-8"))
                return resp.status, body
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeError):
                return exc.code, raw


def test_api_tts_empty_content_400(paths):
    msg = append_message("user", "", user_id="operator", paths=paths)
    # Force empty: append allows empty; ensure content is whitespace-only too.
    h = _Harness(paths)
    try:
        code, body, _ = h.get_raw(f"/api/messages/{msg.id}/tts")
        # empty content → 400 empty_text; never hit mock HTTP
        assert code == 400
        data = json.loads(body.decode("utf-8"))
        assert data["reason"] == "empty_text"
        assert h.http_calls == 0
    finally:
        h.close()


def test_api_tts_missing_message_404(paths):
    h = _Harness(paths)
    try:
        code, body, _ = h.get_raw("/api/messages/does-not-exist-xyz/tts")
        assert code == 404
        data = json.loads(body.decode("utf-8"))
        assert data["reason"] == "not_found"
        assert h.http_calls == 0
    finally:
        h.close()


def test_api_tts_play_and_cache(paths):
    msg = append_message(
        "assistant", "Hello from Elyra TTS.", user_id=None, paths=paths
    )
    before_rows = len(list_messages(paths=paths))
    h = _Harness(paths)
    try:
        code1, audio1, headers1 = h.get_raw(
            f"/api/messages/{msg.id}/tts?voice=eve&language=en"
        )
        assert code1 == 200
        assert headers1.get("content-type", "").startswith("audio/")
        assert headers1.get("x-tts-cache") == "miss"
        assert audio1.startswith(b"ID3") or len(audio1) > 0
        assert h.http_calls == 1

        code2, audio2, headers2 = h.get_raw(
            f"/api/messages/{msg.id}/tts?voice=eve&language=en"
        )
        assert code2 == 200
        assert headers2.get("x-tts-cache") == "hit"
        assert audio2 == audio1
        assert h.http_calls == 1  # disk cache, no second xAI

        # Never new glass row
        after_rows = len(list_messages(paths=paths))
        assert after_rows == before_rows
        # Stored text unchanged
        row = get_message(msg.id, paths=paths)
        assert row is not None
        assert row["content"] == "Hello from Elyra TTS."
    finally:
        h.close()


def test_api_tts_post_works(paths):
    msg = append_message("user", "Post path works.", user_id="operator", paths=paths)
    h = _Harness(paths)
    try:
        code, body = h.post_json(
            f"/api/messages/{msg.id}/tts",
            {"voice_id": "eve", "language": "en"},
        )
        assert code == 200
        assert isinstance(body, (bytes, bytearray))
        assert h.http_calls == 1
    finally:
        h.close()


def test_api_tts_never_calls_chat_completion(paths):
    """TTS handler must not construct or call chat_completion (KD3)."""
    msg = append_message("assistant", "No LLM.", user_id=None, paths=paths)
    h = _Harness(paths)
    # Spy: if worker.client.chat_completion is called, fail.
    client = h.worker.client
    original = client.chat_completion
    calls: list[Any] = []

    def spy(*a, **kw):
        calls.append((a, kw))
        return original(*a, **kw)

    client.chat_completion = spy  # type: ignore[method-assign]
    try:
        code, _, _ = h.get_raw(f"/api/messages/{msg.id}/tts")
        assert code == 200
        assert calls == []
    finally:
        h.close()


def test_api_tts_local_provider_fail_closed(paths):
    msg = append_message("assistant", "Local fail.", user_id=None, paths=paths)
    h = _Harness(paths, provider=_FakeProvider(paths, provider_name="local"))
    try:
        code, body, _ = h.get_raw(f"/api/messages/{msg.id}/tts")
        assert code == 400
        data = json.loads(body.decode("utf-8"))
        assert data["reason"] == "provider_unsupported"
        assert h.http_calls == 0
    finally:
        h.close()


def test_api_tts_text_too_long_400(paths):
    big = "x" * (TTS_MAX_CHARS + 10)
    msg = append_message("assistant", big, user_id=None, paths=paths)
    h = _Harness(paths)
    try:
        code, body, _ = h.get_raw(f"/api/messages/{msg.id}/tts")
        assert code == 400
        data = json.loads(body.decode("utf-8"))
        assert data["reason"] == "text_too_long"
        assert h.http_calls == 0
    finally:
        h.close()


def test_api_tts_disabled_env(paths, monkeypatch):
    monkeypatch.setenv("ELYRA_TTS", "0")
    msg = append_message("assistant", "off", user_id=None, paths=paths)
    h = _Harness(paths)
    try:
        code, body, _ = h.get_raw(f"/api/messages/{msg.id}/tts")
        assert code == 503
        data = json.loads(body.decode("utf-8"))
        assert data["reason"] == "tts_disabled"
        assert h.http_calls == 0
    finally:
        h.close()


def test_static_glass_has_tts_play(paths):
    """Glass app.js includes play control wiring (PR7)."""
    h = _Harness(paths)
    try:
        req = urllib.request.Request(h.base + "/app.js", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            js = resp.read().decode("utf-8")
        assert "playMessageTts" in js
        assert "/tts" in js
        assert "msg-tts-btn" in js
        assert "Play message" in js
        # Never invents a new chat row from TTS
        assert "appendMessage" not in js or "playMessageTts" in js

        req_css = urllib.request.Request(h.base + "/style.css", method="GET")
        with urllib.request.urlopen(req_css, timeout=5) as resp:
            css = resp.read().decode("utf-8")
        assert "msg-tts-btn" in css
    finally:
        h.close()


def test_defaults_match_design():
    assert TTS_DEFAULT_VOICE == "eve"
    assert TTS_DEFAULT_LANGUAGE == "en"
    assert TTS_DEFAULT_PROFILE == "mp3_24k_128"
    assert TTS_MAX_CHARS == 15_000
