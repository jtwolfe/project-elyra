"""PR6: STT host client + POST /api/stt (KD4, KD9, KD18) — mocked HTTP only."""

from __future__ import annotations

import io
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
from elyra.goals import GoalsStore
from elyra.llm.auth import write_stored_api_key
from elyra.llm.client import StubChatClient
from elyra.llm.queue import ChatRequestGate
from elyra.loop.doloop import DoLoopResult
from elyra.media import (
    DEFAULT_STT_MODEL,
    MAX_AUDIO_BYTES,
    MAX_MEDIA_REQUEST_BYTES,
    MediaStore,
    SttError,
    parse_stt_response,
    stt_enabled,
    stt_url,
    transcribe,
)
from elyra.media.stt import encode_stt_multipart
from elyra.moment import MomentStore
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import TimerService
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState
from elyra.settings import default_settings

FIXTURE_WAV = Path(__file__).parent / "fixtures" / "media" / "tiny.wav"

# Fixture-shaped live xAI STT JSON (docs response shape).
XAI_STT_FIXTURE = {
    "text": "The balance is $167,983.15.",
    "language": "English",
    "duration": 3.45,
    "words": [
        {"text": "The", "start": 0.24, "end": 0.48},
        {"text": "balance", "start": 0.48, "end": 0.96},
    ],
}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


# ---------------------------------------------------------------------------
# Unit: parse / encode / kill switch
# ---------------------------------------------------------------------------


def test_stt_url_joins_without_double_v1():
    assert stt_url("https://api.x.ai/v1", "/stt") == "https://api.x.ai/v1/stt"
    assert stt_url("https://api.x.ai/v1/", "stt") == "https://api.x.ai/v1/stt"


def test_parse_stt_response_primary_shape():
    r = parse_stt_response(XAI_STT_FIXTURE)
    assert r.text == XAI_STT_FIXTURE["text"]
    assert r.language == "English"
    assert r.duration_s == 3.45
    assert "words" in r.raw


def test_parse_stt_response_empty_text_errors():
    with pytest.raises(SttError) as ei:
        parse_stt_response({"text": "   "})
    assert ei.value.reason == "stt_empty_text"


def test_parse_stt_response_alternate_transcript_key():
    r = parse_stt_response({"transcript": "hello there"})
    assert r.text == "hello there"


def test_encode_stt_multipart_model_before_file():
    body, ctype = encode_stt_multipart(
        b"\x00\x01",
        filename="tiny.wav",
        mime="audio/wav",
        model=DEFAULT_STT_MODEL,
        language="en",
    )
    assert "multipart/form-data" in ctype
    # Field order: model, language, then file (xAI streamable note).
    model_pos = body.find(b'name="model"')
    lang_pos = body.find(b'name="language"')
    file_pos = body.find(b'name="file"')
    assert 0 <= model_pos < lang_pos < file_pos
    assert DEFAULT_STT_MODEL.encode() in body


def test_stt_enabled_kill_switch(monkeypatch):
    assert stt_enabled({}) is True
    assert stt_enabled({"ELYRA_STT": "1"}) is True
    assert stt_enabled({"ELYRA_STT": "0"}) is False
    assert stt_enabled({"ELYRA_STT": "false"}) is False


# ---------------------------------------------------------------------------
# Unit: transcribe with mocked urlopen
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._raw = json.dumps(payload).encode("utf-8")
        self.status = status
        self.code = status

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def test_transcribe_mocked_success():
    audio = FIXTURE_WAV.read_bytes()
    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = req.data
        return _FakeResp(XAI_STT_FIXTURE)

    result = transcribe(
        audio,
        filename="tiny.wav",
        mime="audio/wav",
        bearer_token="test-secret-token",
        urlopen=fake_urlopen,
    )
    assert result.text == XAI_STT_FIXTURE["text"]
    assert result.language == "English"
    assert captured["url"] == "https://api.x.ai/v1/stt"
    assert captured["method"] == "POST"
    assert captured["headers"]["authorization"] == "Bearer test-secret-token"
    assert DEFAULT_STT_MODEL.encode() in captured["body"]
    # Secret must not appear in exception path (smoke: token not in result text)
    assert "test-secret-token" not in result.text


def test_transcribe_http_error_maps_reason():
    def boom(req, timeout=0):  # noqa: ARG001
        raise urllib.error.HTTPError(
            url="https://api.x.ai/v1/stt",
            code=401,
            msg="nope",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error":"unauthorized"}'),
        )

    with pytest.raises(SttError) as ei:
        transcribe(
            b"abc",
            filename="a.wav",
            mime="audio/wav",
            bearer_token="tok",
            urlopen=boom,
        )
    assert ei.value.reason == "stt_http_401"
    assert ei.value.http_status == 401


def test_transcribe_on_remote_success_network_ok_only():
    """Success → on_remote_success('stt'); HTTP failure → never called."""
    audio = FIXTURE_WAV.read_bytes()
    seen: list[str] = []

    def ok_urlopen(req, timeout=0):  # noqa: ARG001
        return _FakeResp(XAI_STT_FIXTURE)

    result = transcribe(
        audio,
        filename="tiny.wav",
        mime="audio/wav",
        bearer_token="tok",
        urlopen=ok_urlopen,
        on_remote_success=seen.append,
    )
    assert result.text
    assert seen == ["stt"]

    def boom(req, timeout=0):  # noqa: ARG001
        raise urllib.error.HTTPError(
            url="https://api.x.ai/v1/stt",
            code=429,
            msg="rate",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b"{}"),
        )

    with pytest.raises(SttError):
        transcribe(
            b"abc",
            filename="a.wav",
            mime="audio/wav",
            bearer_token="tok",
            urlopen=boom,
            on_remote_success=seen.append,
        )
    assert seen == ["stt"]  # failure did not append


def test_transcribe_rejects_empty_audio():
    with pytest.raises(SttError) as ei:
        transcribe(
            b"",
            filename="a.wav",
            mime="audio/wav",
            bearer_token="tok",
            urlopen=lambda *a, **k: None,
        )
    assert ei.value.reason == "stt_invalid_audio"


# ---------------------------------------------------------------------------
# API harness
# ---------------------------------------------------------------------------


def _fake_registry() -> MagicMock:
    reg = MagicMock()
    reg.openai_tools.return_value = []
    reg.execute.return_value = MagicMock(ok=True, payload={}, ends_moment=False)
    return reg


def _stub_loop(**kwargs: Any) -> DoLoopResult:
    ctx = kwargs.get("ctx")
    mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
    return DoLoopResult(
        stop_reason="no_tools",
        hop_count=1,
        moment_id=mid,
        spoke=False,
    )


def _make_worker(paths):
    stop = threading.Event()
    queue = WakeQueue(paths)
    timers = TimerService(paths, queue)
    moments = MomentStore(paths)
    goals = GoalsStore(paths)
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
        goals=goals,
        run_do_loop_fn=_stub_loop,
    )
    return worker, stop


class _MockProvider:
    """Minimal provider surface for STT route (xAI fail-closed tests)."""

    def __init__(
        self,
        *,
        paths,
        provider_name: str = "xai",
        credential_source: str = "api_key",
        base_url: str = "https://api.x.ai/v1",
    ) -> None:
        self.provider_name = provider_name
        self.credential_source = credential_source
        self.data_dir = paths.data_dir
        self.base_url = base_url
        self.grok_auth_path = None
        self.request_timeout_s = 30.0
        self.meter = None

    def status_provider_fields(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": "grok-test",
            "credential_source": self.credential_source,
            "credential_ok": True,
        }

    def usage_status_block(self) -> dict[str, Any]:
        return {"enabled": False}


class _Harness:
    def __init__(self, paths, *, provider: Any | None = None) -> None:
        self.paths = paths
        self.worker, self._stop = _make_worker(paths)
        config = RuntimeConfig(api_host="127.0.0.1", api_port=0)
        self.state = RuntimeState()
        self.gate = ChatRequestGate()
        self.provider = provider
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

    def post_multipart(
        self,
        path: str,
        files: list[tuple[str, str, bytes, str]],
        *,
        fields: dict[str, str] | None = None,
        content_length_override: int | None = None,
        omit_body: bool = False,
    ) -> tuple[int, Any]:
        boundary = "----ElyraSttTestBoundary"
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
# API: fail-closed + success with mocked transcribe
# ---------------------------------------------------------------------------


def test_api_stt_provider_unavailable(paths):
    h = _Harness(paths, provider=None)
    try:
        code, body = h.post_multipart(
            "/api/stt",
            [("file", "tiny.wav", FIXTURE_WAV.read_bytes(), "audio/wav")],
        )
        assert code == 503
        assert body["reason"] == "provider_unavailable"
    finally:
        h.close()


def test_api_stt_local_provider_fail_closed(paths):
    prov = _MockProvider(paths=paths, provider_name="local")
    h = _Harness(paths, provider=prov)
    try:
        code, body = h.post_multipart(
            "/api/stt",
            [("file", "tiny.wav", FIXTURE_WAV.read_bytes(), "audio/wav")],
        )
        assert code == 503
        assert body["reason"] == "provider_unsupported"
    finally:
        h.close()


def test_api_stt_disabled_env(paths, monkeypatch):
    monkeypatch.setenv("ELYRA_STT", "0")
    prov = _MockProvider(paths=paths)
    h = _Harness(paths, provider=prov)
    try:
        code, body = h.post_multipart(
            "/api/stt",
            [("file", "tiny.wav", FIXTURE_WAV.read_bytes(), "audio/wav")],
        )
        assert code == 503
        assert body["reason"] == "stt_disabled"
    finally:
        h.close()


def test_api_stt_credential_unavailable(paths):
    prov = _MockProvider(paths=paths, credential_source="api_key")
    # No stored key, no env → credential_unavailable
    h = _Harness(paths, provider=prov)
    try:
        code, body = h.post_multipart(
            "/api/stt",
            [("file", "tiny.wav", FIXTURE_WAV.read_bytes(), "audio/wav")],
        )
        assert code == 503
        assert body["reason"] == "credential_unavailable"
    finally:
        h.close()


def test_api_stt_success_mocked_xai(paths, monkeypatch):
    write_stored_api_key(paths.data_dir, "test-api-key-not-real")
    prov = _MockProvider(paths=paths)

    def fake_transcribe(file_bytes, **kwargs):  # noqa: ARG001
        from elyra.media.stt import SttResult

        assert kwargs.get("bearer_token") == "test-api-key-not-real"
        assert kwargs.get("model") == DEFAULT_STT_MODEL
        return SttResult(
            text="hello from mic",
            language="English",
            duration_s=1.25,
            raw={"text": "hello from mic"},
        )

    monkeypatch.setattr("elyra.runtime.api.transcribe", fake_transcribe)
    h = _Harness(paths, provider=prov)
    try:
        code, body = h.post_multipart(
            "/api/stt",
            [("file", "tiny.wav", FIXTURE_WAV.read_bytes(), "audio/wav")],
            fields={"user_id": "operator"},
        )
        assert code == 200, body
        assert body["ok"] is True
        assert body["text"] == "hello from mic"
        assert body["language"] == "English"
        assert body["duration"] == 1.25
        assert body["model"] == DEFAULT_STT_MODEL
        assert "attachment_id" not in body
    finally:
        h.close()


def test_api_stt_keep_audio_stores_attachment(paths, monkeypatch):
    write_stored_api_key(paths.data_dir, "test-api-key-not-real")
    prov = _MockProvider(paths=paths)

    def fake_transcribe(file_bytes, **kwargs):  # noqa: ARG001
        from elyra.media.stt import SttResult

        return SttResult(text="kept recording", language=None, duration_s=0.5, raw={})

    monkeypatch.setattr("elyra.runtime.api.transcribe", fake_transcribe)
    audio = FIXTURE_WAV.read_bytes()
    h = _Harness(paths, provider=prov)
    try:
        code, body = h.post_multipart(
            "/api/stt",
            [("file", "rec.wav", audio, "audio/wav")],
            fields={
                "user_id": "operator",
                "keep_audio": "1",
                "origin": "user_recording",
            },
        )
        assert code == 200, body
        assert body["text"] == "kept recording"
        att_id = body["attachment_id"]
        assert att_id
        assert body["attachment"]["origin"] == "user_recording"
        assert body["attachment"]["kind"] == "audio"
        store = MediaStore(paths)
        loaded = store.get(att_id)
        assert loaded is not None
        assert loaded.byte_size == len(audio)
    finally:
        h.close()


def test_api_stt_oversized_content_length_413(paths):
    """Content-Length > max rejected before allocating claimed body."""
    write_stored_api_key(paths.data_dir, "k")
    prov = _MockProvider(paths=paths)
    h = _Harness(paths, provider=prov)
    try:
        import http.client

        host, port = h.server.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.putrequest("POST", "/api/stt")
            conn.putheader("Content-Type", "multipart/form-data; boundary=x")
            conn.putheader("Content-Length", str(MAX_MEDIA_REQUEST_BYTES + 1))
            conn.endheaders()
            # Do not send body — handler must reject on Content-Length alone.
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            body = json.loads(raw)
            assert resp.status == 413
            assert body["reason"] == "content_length"
        finally:
            conn.close()
    finally:
        h.close()


def test_api_stt_audio_part_over_25mib(paths, monkeypatch):
    write_stored_api_key(paths.data_dir, "k")
    prov = _MockProvider(paths=paths)
    called = {"n": 0}

    def should_not_call(*a, **k):  # noqa: ARG001
        called["n"] += 1
        raise AssertionError("transcribe must not run for oversized audio")

    monkeypatch.setattr("elyra.runtime.api.transcribe", should_not_call)
    huge = b"\x00" * (MAX_AUDIO_BYTES + 1)
    h = _Harness(paths, provider=prov)
    try:
        code, body = h.post_multipart(
            "/api/stt",
            [("file", "big.wav", huge, "audio/wav")],
        )
        assert code == 413
        assert body["reason"] == "file_too_large"
        assert called["n"] == 0
    finally:
        h.close()


def test_api_stt_upstream_error_reason(paths, monkeypatch):
    write_stored_api_key(paths.data_dir, "k")
    prov = _MockProvider(paths=paths)

    def fail_transcribe(*a, **k):  # noqa: ARG001
        raise SttError("stt_http_502", "upstream", http_status=502)

    monkeypatch.setattr("elyra.runtime.api.transcribe", fail_transcribe)
    h = _Harness(paths, provider=prov)
    try:
        code, body = h.post_multipart(
            "/api/stt",
            [("file", "tiny.wav", FIXTURE_WAV.read_bytes(), "audio/wav")],
        )
        assert code == 502
        assert body["reason"] == "stt_http_502"
    finally:
        h.close()
