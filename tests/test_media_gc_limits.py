"""PR10: unbound attachment GC, mirror reconcile, STT/TTS rate limits."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.llm.auth import write_stored_api_key
from elyra.llm.client import StubChatClient
from elyra.llm.queue import ChatRequestGate
from elyra.loop.doloop import DoLoopResult
from elyra.media.activity import (
    clear_media_activity_for_tests,
    note_media_activity,
    recent_media_activity,
)
from elyra.media.gc import (
    UNBOUND_MAX_BYTES,
    ensure_media,
    gc_unbound_attachments,
    list_unbound,
    media_stats,
    reconcile_mirrors,
)
from elyra.media.limits import (
    STT_MAX_PER_MINUTE,
    TTS_MAX_PER_MINUTE,
    SlidingWindowLimiter,
    allow_stt,
    allow_tts,
    reset_rate_limits_for_tests,
)
from elyra.media.store import MediaStore
from elyra.messages import append_message
from elyra.moment import MomentStore
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import TimerService
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState
from elyra.settings import default_settings

FAKE_MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 64


@pytest.fixture
def paths(tmp_path: Path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    return MediaStore(paths)


def _set_created_at(store: MediaStore, att_id: str, created_at: str) -> None:
    path = store.meta_path(att_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["created_at"] = created_at
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Unbound GC (KD23)
# ---------------------------------------------------------------------------


def test_gc_deletes_unbound_older_than_24h(store, paths):
    old = store.put_bytes(b"old-bytes", filename="old.txt", origin="user_upload")
    young = store.put_bytes(b"young-bytes", filename="young.txt", origin="user_upload")
    bound = store.put_bytes(b"bound-bytes", filename="bound.txt", origin="user_upload")
    store.bind_message(bound.id, "msg-keep")

    past = (datetime.now(UTC) - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
    recent = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    _set_created_at(store, old.id, past)
    _set_created_at(store, young.id, recent)
    _set_created_at(store, bound.id, past)  # bound must survive even if old

    mirror_old = paths.home / "sandboxes" / "sandbox0" / "media" / old.id / "old.txt"
    assert mirror_old.is_file()

    summary = gc_unbound_attachments(store)
    assert old.id in summary["deleted_ids"]
    assert young.id not in summary["deleted_ids"]
    assert bound.id not in summary["deleted_ids"]
    assert store.get(old.id) is None
    assert store.get(young.id) is not None
    assert store.get(bound.id) is not None
    assert store.get(bound.id).bound_message_id == "msg-keep"
    assert not mirror_old.exists()


def test_gc_byte_budget_deletes_oldest_first(store):
    """When unbound total > 256 MiB, delete oldest until under budget."""
    # Use tiny max_bytes so we don't write hundreds of MiB in CI.
    a = store.put_bytes(b"aaa", filename="a.bin", origin="user_upload")
    b = store.put_bytes(b"bbbb", filename="b.bin", origin="user_upload")
    c = store.put_bytes(b"ccccc", filename="c.bin", origin="user_upload")
    t0 = datetime(2020, 1, 1, tzinfo=UTC)
    _set_created_at(store, a.id, t0.isoformat().replace("+00:00", "Z"))
    _set_created_at(
        store, b.id, (t0 + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    )
    _set_created_at(
        store, c.id, (t0 + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    )
    # Make all "young" so only byte budget applies.
    now = datetime.now(UTC)
    summary = gc_unbound_attachments(
        store,
        now=now,
        max_age=timedelta(days=3650),  # effectively no age deletes
        max_bytes=6,  # keep at most ~6 bytes unbound
    )
    # Total was 3+4+5=12; delete oldest until <=6 → delete a (3) →9, b (4) →5.
    assert a.id in summary["deleted_ids"]
    assert b.id in summary["deleted_ids"]
    assert c.id not in summary["deleted_ids"]
    assert store.get(c.id) is not None
    assert summary["unbound_bytes"] <= 6


def test_gc_does_not_delete_bound_or_shared_blob_prematurely(store):
    data = b"shared-payload"
    u = store.put_bytes(data, filename="u.txt", origin="user_upload")
    v = store.put_bytes(data, filename="v.txt", origin="user_upload")
    store.bind_message(v.id, "msg-v")
    past = (datetime.now(UTC) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    _set_created_at(store, u.id, past)

    gc_unbound_attachments(store)
    assert store.get(u.id) is None
    assert store.get(v.id) is not None
    # Blob still held by bound v.
    assert store.blob_path(v.sha256).is_file()


def test_reconcile_reprojects_missing_mirror(store, paths):
    att = store.put_bytes(b"mirror-me", filename="m.txt", origin="system")
    mirror = paths.home / "sandboxes" / "sandbox0" / "media" / att.id / "m.txt"
    assert mirror.is_file()
    mirror.unlink()
    assert not mirror.exists()

    result = reconcile_mirrors(store)
    assert result["projected"] >= 1
    assert mirror.is_file()
    assert mirror.read_bytes() == b"mirror-me"


def test_ensure_media_runs_reconcile_and_gc(paths, store):
    att = store.put_bytes(b"x", filename="x.txt", origin="user_upload")
    past = (datetime.now(UTC) - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    _set_created_at(store, att.id, past)
    mirror = paths.home / "sandboxes" / "sandbox0" / "media" / att.id / "x.txt"
    # Wipe mirror then ensure should GC (old unbound) — no reproject needed after delete.
    if mirror.exists():
        mirror.unlink()
    ensure_media(paths, reconcile=True, gc=True)
    assert store.get(att.id) is None


def test_media_stats_counts(store):
    a = store.put_bytes(b"aa", filename="a.txt", origin="user_upload")
    b = store.put_bytes(b"bbb", filename="b.txt", origin="user_upload")
    store.bind_message(b.id, "m1")
    stats = media_stats(store)
    assert stats["count"] == 2
    assert stats["bytes_total"] == 2 + 3
    assert stats["unbound_count"] == 1
    assert stats["unbound_bytes"] == 2
    assert list_unbound(store)[0].id == a.id


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------


def test_sliding_window_limiter_unit():
    lim = SlidingWindowLimiter(max_events=3, window_s=60.0)
    t0 = 1000.0
    assert lim.allow(now=t0) is True
    assert lim.allow(now=t0 + 1) is True
    assert lim.allow(now=t0 + 2) is True
    assert lim.allow(now=t0 + 3) is False
    # After window advances, slots free.
    assert lim.allow(now=t0 + 61) is True


def test_stt_and_tts_process_limits():
    reset_rate_limits_for_tests()
    for _ in range(STT_MAX_PER_MINUTE):
        assert allow_stt() is True
    assert allow_stt() is False

    for _ in range(TTS_MAX_PER_MINUTE):
        assert allow_tts() is True
    assert allow_tts() is False

    reset_rate_limits_for_tests()
    assert allow_stt() is True
    assert allow_tts() is True


def test_media_activity_trail_kinds():
    clear_media_activity_for_tests()
    note_media_activity("upload", label="upload")
    note_media_activity("stt", label="stt")
    note_media_activity("tts", label="tts")
    recent = recent_media_activity()
    kinds = [e["kind"] for e in recent]
    assert kinds == ["upload", "stt", "tts"]
    clear_media_activity_for_tests()


# ---------------------------------------------------------------------------
# API: TTS 429 when over limit
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
    def __init__(self, paths) -> None:
        self.paths = paths
        self.worker, self._stop = _make_worker(paths)
        self._worker_thread = threading.Thread(
            target=self.worker.run, name="test-gc-presence", daemon=True
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
            provider=_FakeProvider(paths),
        )
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"
        self.server.RequestHandlerClass.tts_http_post = self._mock_http  # type: ignore[attr-defined]

    def _mock_http(self, url, headers, body, timeout):
        return FAKE_MP3

    def close(self) -> None:
        self._stop.set()
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
        import urllib.error
        import urllib.request

        req = urllib.request.Request(self.base + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, resp.read(), headers
        except urllib.error.HTTPError as exc:
            headers = (
                {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            )
            return exc.code, exc.read(), headers


def test_api_tts_rate_limited_429(paths):
    write_stored_api_key(paths.data_dir, "test-xai-key-not-real")
    msg = append_message(
        "assistant",
        "Hello rate limit world",
        paths=paths,
    )
    h = _Harness(paths)
    try:
        reset_rate_limits_for_tests()
        for _ in range(TTS_MAX_PER_MINUTE):
            assert allow_tts() is True
        code, body, _ = h.get_raw(f"/api/messages/{msg.id}/tts?voice=eve&language=en")
        assert code == 429
        data = json.loads(body.decode("utf-8"))
        assert data.get("ok") is False
        assert data.get("reason") == "rate_limited"
    finally:
        h.close()


def test_api_status_includes_media_stats(paths):
    MediaStore(paths).put_bytes(b"zz", filename="z.txt", origin="user_upload")
    clear_media_activity_for_tests()
    note_media_activity("upload", label="upload")
    h = _Harness(paths)
    try:
        code, body, _ = h.get_raw("/api/status")
        assert code == 200
        data = json.loads(body.decode("utf-8"))
        assert "media" in data
        assert data["media"]["count"] >= 1
        assert data["media"]["unbound_count"] >= 1
        assert any(
            e.get("kind") == "upload" for e in (data.get("media_activity") or [])
        )
    finally:
        h.close()


def test_unbound_max_bytes_constant():
    assert UNBOUND_MAX_BYTES == 256 * 1024 * 1024
