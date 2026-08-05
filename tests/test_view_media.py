"""Hermetic tests for view_media host tool (path / att_id / url dual-source).

Covers: path ingest origin=view, att_id, url SSRF-safe fetch, dual-source
same/conflict sha, list/drop/clear, missing_source, no_open_moment, promote
first-wins, modality honesty, registry discovery.
"""

from __future__ import annotations

import hashlib
import io
import socket
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.media.store import MediaStore
from elyra.media.types import ATTACHMENT_ORIGINS
from elyra.media.viewing import ViewingEntry, list_viewing_att_ids
from elyra.memory.config import MemorySettings
from elyra.memory.store import open_memory_store
from elyra.sandbox import Sandbox
from elyra.settings import Settings
from elyra.tools import ToolContext, ToolRegistry, ToolResult
from elyra.tools.builtin.media_view import view_media
from elyra.tools.policy import resolve_bundled_tools_root

FIXTURE_PNG = Path(__file__).parent / "fixtures" / "media" / "1x1.png"
FIXTURE_WAV = Path(__file__).parent / "fixtures" / "media" / "tiny.wav"


class _FakeHeaders(dict):
    pass


class _FakeResp:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._buf = io.BytesIO(body)
        self.status = status
        self.headers = _FakeHeaders(headers or {})

    def getcode(self) -> int:
        return self.status

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def close(self) -> None:
        self._buf.close()


def _public_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any):
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            0,
            "",
            ("93.184.216.34", port if isinstance(port, int) else 443),
        )
    ]


def _png_urlopen(body: bytes | None = None):
    data = body if body is not None else FIXTURE_PNG.read_bytes()

    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        return _FakeResp(
            data,
            headers={
                "Content-Type": "image/png",
                "Content-Length": str(len(data)),
            },
        )

    return urlopen


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


@pytest.fixture
def sandbox(paths) -> Sandbox:
    return Sandbox(paths)


@pytest.fixture
def media(paths) -> MediaStore:
    return MediaStore(paths)


@pytest.fixture
def mem_settings() -> MemorySettings:
    return MemorySettings(write_atoms=True, enabled=True, backend="jsonl")


@pytest.fixture
def mem_store(paths, mem_settings):
    return open_memory_store(paths, mem_settings)


@pytest.fixture
def viewing() -> dict[str, ViewingEntry]:
    return {}


@pytest.fixture
def dirty_flag() -> list[bool]:
    return [False]


@pytest.fixture
def ctx(paths, sandbox, viewing, dirty_flag, mem_store, mem_settings) -> ToolContext:
    return ToolContext(
        paths=paths,
        sandbox=sandbox,
        settings=Settings(memory=mem_settings),
        moment_id="moment-view-1",
        user_id="operator",
        extras={
            "moment_viewing": viewing,
            "set_viewing_dirty": lambda: dirty_flag.__setitem__(0, True),
            "viewing_dirty": dirty_flag,
            "memory_store": mem_store,
        },
    )


def _write_sandbox_bytes(sandbox: Sandbox, rel: str, data: bytes) -> Path:
    dest = sandbox.resolve(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def _write_sandbox_png(sandbox: Sandbox, rel: str = "tmp/animal.png") -> Path:
    return _write_sandbox_bytes(sandbox, rel, FIXTURE_PNG.read_bytes())


# ---------------------------------------------------------------------------
# Origin vocabulary
# ---------------------------------------------------------------------------


def test_view_origin_in_attachment_origins() -> None:
    assert "view" in ATTACHMENT_ORIGINS


# ---------------------------------------------------------------------------
# path / att_id happy paths
# ---------------------------------------------------------------------------


def test_view_path_ingests_origin_view_and_dirties(
    ctx: ToolContext,
    sandbox: Sandbox,
    paths,
    viewing: dict,
    dirty_flag: list[bool],
    media: MediaStore,
) -> None:
    _write_sandbox_png(sandbox, "tmp/animal.png")
    result = view_media({"path": "tmp/animal.png"}, ctx)
    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert result.error_reason is None
    p = result.payload
    assert p["op"] == "view"
    assert p["source"] == "path"
    assert p["kind"] == "image"
    assert p["mime"].startswith("image/")
    assert p["expand_next_hop"] is True
    assert p["viewing_dirty"] is True
    assert p["presentation"] == "image_url"
    assert p["perception"] is True
    assert p["viewing_count"] == 1
    aid = p["att_id"]
    assert aid.startswith("att_")
    assert p["viewing"] == [aid]
    assert dirty_flag[0] is True
    assert list_viewing_att_ids(viewing) == [aid]

    att = media.get(aid)
    assert att is not None
    assert att.origin == "view"
    assert att.sha256  # content-addressed


def test_view_att_id_existing(
    ctx: ToolContext,
    media: MediaStore,
    viewing: dict,
    dirty_flag: list[bool],
) -> None:
    att = media.put_bytes(
        FIXTURE_PNG.read_bytes(),
        filename="prior.png",
        origin="user_upload",
    )
    result = view_media({"att_id": att.id}, ctx)
    assert result.ok is True
    assert result.payload["source"] == "att_id"
    assert result.payload["att_id"] == att.id
    assert result.payload["viewing"] == [att.id]
    assert dirty_flag[0] is True
    assert att.id in viewing


def test_view_path_via_registry(paths, sandbox, viewing, dirty_flag, mem_store, mem_settings) -> None:
    reg = ToolRegistry(paths, bundled_root=resolve_bundled_tools_root())
    assert "view_media" in reg.names()
    _write_sandbox_png(sandbox, "tmp/via_reg.png")
    ctx = ToolContext(
        paths=paths,
        sandbox=sandbox,
        settings=Settings(memory=mem_settings),
        moment_id="m-reg",
        user_id="operator",
        extras={
            "moment_viewing": viewing,
            "set_viewing_dirty": lambda: dirty_flag.__setitem__(0, True),
            "memory_store": mem_store,
        },
    )
    result = reg.execute("view_media", {"path": "tmp/via_reg.png"}, ctx)
    assert result.ok is True
    assert result.payload["kind"] == "image"


# ---------------------------------------------------------------------------
# Dual-source KD-V14
# ---------------------------------------------------------------------------


def test_dual_source_same_sha_ok(
    ctx: ToolContext,
    sandbox: Sandbox,
    media: MediaStore,
) -> None:
    data = FIXTURE_PNG.read_bytes()
    existing = media.put_bytes(data, filename="same.png", origin="user_upload")
    _write_sandbox_bytes(sandbox, "tmp/same.png", data)
    result = view_media({"path": "tmp/same.png", "att_id": existing.id}, ctx)
    assert result.ok is True
    assert result.payload["source"] == "path+att_id"
    # Prefer explicit att_id when sha matches.
    assert result.payload["att_id"] == existing.id
    assert result.payload["viewing"] == [existing.id]


def test_dual_source_different_sha_ambiguous(
    ctx: ToolContext,
    sandbox: Sandbox,
    media: MediaStore,
    viewing: dict,
) -> None:
    a = media.put_bytes(b"bytes-a-not-png-xxx", filename="a.bin", origin="tool")
    meta_before = set(media.list_meta_ids())
    _write_sandbox_png(sandbox, "tmp/other.png")
    result = view_media({"path": "tmp/other.png", "att_id": a.id}, ctx)
    assert result.ok is False
    assert result.error_reason == "ambiguous_source"
    assert result.payload["reason"] == "ambiguous_source"
    # Conflict must not pollute viewing set.
    assert list_viewing_att_ids(viewing) == []
    # No orphan path ingest on conflict (sha compared before put).
    assert set(media.list_meta_ids()) == meta_before


def test_dual_source_path_ok_missing_att_uses_path(
    ctx: ToolContext,
    sandbox: Sandbox,
) -> None:
    """KD-V14 soft multi-source: only path resolves → use path."""
    _write_sandbox_png(sandbox, "tmp/soft.png")
    missing = "att_" + "f" * 32
    result = view_media({"path": "tmp/soft.png", "att_id": missing}, ctx)
    assert result.ok is True
    assert result.payload["source"] == "path"
    assert result.payload["att_id"].startswith("att_")


def test_dual_source_att_ok_missing_path_uses_att(
    ctx: ToolContext,
    media: MediaStore,
) -> None:
    """KD-V14 soft multi-source: only att_id resolves → use att_id."""
    att = media.put_bytes(FIXTURE_PNG.read_bytes(), filename="x.png", origin="tool")
    result = view_media(
        {"path": "tmp/does-not-exist-soft.png", "att_id": att.id},
        ctx,
    )
    assert result.ok is True
    assert result.payload["source"] == "att_id"
    assert result.payload["att_id"] == att.id


def test_path_re_view_reuses_same_att_id(
    ctx: ToolContext,
    sandbox: Sandbox,
    media: MediaStore,
    mem_store,
    viewing: dict,
) -> None:
    """Path re-view is content-idempotent (reuse meta by sha)."""
    _write_sandbox_png(sandbox, "tmp/reuse.png")
    r1 = view_media({"path": "tmp/reuse.png"}, ctx)
    assert r1.ok is True
    aid = r1.payload["att_id"]
    assert r1.payload["promoted"] is True

    r2 = view_media({"path": "tmp/reuse.png"}, ctx)
    assert r2.ok is True
    assert r2.payload["att_id"] == aid
    assert r2.payload["viewing"] == [aid]
    assert r2.payload["viewing_count"] == 1
    assert r2.payload["promoted"] is False  # first-wins same media_ids
    # Only one durable meta for these bytes (plus no extras).
    matches = [
        media.get(i)
        for i in media.list_meta_ids()
        if (m := media.get(i)) is not None and m.sha256 == media.get(aid).sha256  # type: ignore[union-attr]
    ]
    assert len([m for m in matches if m is not None]) == 1
    atoms = mem_store.list_by_moment("moment-view-1", kinds=["observation"])
    assert len(atoms) == 1


# ---------------------------------------------------------------------------
# list / drop / clear
# ---------------------------------------------------------------------------


def test_list_drop_clear(
    ctx: ToolContext,
    media: MediaStore,
    viewing: dict,
    dirty_flag: list[bool],
) -> None:
    a = media.put_bytes(FIXTURE_PNG.read_bytes(), filename="a.png", origin="tool")
    b = media.put_bytes(b"wav-like-bytes-not-really", filename="b.bin", origin="tool")

    r1 = view_media({"att_id": a.id}, ctx)
    assert r1.ok is True
    dirty_flag[0] = False
    r2 = view_media({"att_id": b.id}, ctx)
    assert r2.ok is True
    assert r2.payload["viewing_count"] == 2

    listed = view_media({"op": "list"}, ctx)
    assert listed.ok is True
    assert set(listed.payload["viewing"]) == {a.id, b.id}
    assert listed.payload["viewing_count"] == 2

    dirty_flag[0] = False
    dropped = view_media({"op": "drop", "att_id": a.id}, ctx)
    assert dropped.ok is True
    assert dropped.payload["removed"] is True
    assert dropped.payload["viewing"] == [b.id]
    assert dirty_flag[0] is True

    # Drop missing is ok but not dirty.
    dirty_flag[0] = False
    dropped2 = view_media({"op": "drop", "att_id": a.id}, ctx)
    assert dropped2.ok is True
    assert dropped2.payload["removed"] is False
    assert dirty_flag[0] is False

    cleared = view_media({"op": "clear"}, ctx)
    assert cleared.ok is True
    assert cleared.payload["cleared"] == 1
    assert cleared.payload["viewing"] == []
    assert cleared.payload["viewing_count"] == 0
    assert list_viewing_att_ids(viewing) == []


def test_clear_empty_does_not_dirty(
    ctx: ToolContext,
    dirty_flag: list[bool],
) -> None:
    dirty_flag[0] = False
    result = view_media({"op": "clear"}, ctx)
    assert result.ok is True
    assert result.payload["cleared"] == 0
    assert result.payload["viewing_dirty"] is False
    assert dirty_flag[0] is False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_missing_source(ctx: ToolContext) -> None:
    result = view_media({}, ctx)
    assert result.ok is False
    assert result.error_reason == "missing_source"


def test_no_open_moment(paths, sandbox, viewing) -> None:
    ctx = ToolContext(
        paths=paths,
        sandbox=sandbox,
        moment_id="",
        extras={"moment_viewing": viewing},
    )
    result = view_media({"path": "tmp/x.png"}, ctx)
    assert result.ok is False
    assert result.error_reason == "no_open_moment"


def test_not_found_att_id(ctx: ToolContext) -> None:
    result = view_media({"att_id": "att_" + "0" * 32}, ctx)
    assert result.ok is False
    assert result.error_reason == "not_found"


def test_invalid_att_id(ctx: ToolContext) -> None:
    result = view_media({"att_id": "../etc/passwd"}, ctx)
    assert result.ok is False
    assert result.error_reason == "invalid_att_id"


def test_path_not_found(ctx: ToolContext) -> None:
    result = view_media({"path": "tmp/nope-missing.png"}, ctx)
    assert result.ok is False
    assert result.error_reason == "not_found"


def test_path_escape(ctx: ToolContext) -> None:
    result = view_media({"path": "../etc/passwd"}, ctx)
    assert result.ok is False
    assert result.error_reason == "path_escape"


def test_path_escape_absolute(ctx: ToolContext) -> None:
    result = view_media({"path": "/etc/passwd"}, ctx)
    assert result.ok is False
    assert result.error_reason == "path_escape"


def test_unsupported_kind_tts_cache(ctx: ToolContext, media: MediaStore) -> None:
    att = media.put_bytes(
        b"fake-tts-audio",
        filename="voice.wav",
        kind="tts_cache",
        origin="tts_cache",
        mime="audio/wav",
    )
    result = view_media({"att_id": att.id}, ctx)
    assert result.ok is False
    assert result.error_reason == "unsupported_kind"


def test_view_url_success(
    ctx: ToolContext,
    viewing: dict,
    dirty_flag: list[bool],
    media: MediaStore,
) -> None:
    data = FIXTURE_PNG.read_bytes()
    ctx.extras["urlopen"] = _png_urlopen(data)
    ctx.extras["getaddrinfo"] = _public_getaddrinfo
    result = view_media({"url": "https://example.com/cat.png"}, ctx)
    assert result.ok is True
    assert result.payload["source"] == "url"
    assert result.payload["kind"] == "image"
    assert result.payload["perception"] is True
    assert result.payload["source_url"] == "https://example.com/cat.png"
    assert dirty_flag[0] is True
    aid = result.payload["att_id"]
    assert list_viewing_att_ids(viewing) == [aid]
    att = media.get(aid)
    assert att is not None
    assert att.origin == "view"


def test_view_url_ssrf_loopback_blocked(ctx: ToolContext, viewing: dict) -> None:
    # Literal private IP — no mock needed; must fail before open.
    result = view_media({"url": "https://127.0.0.1/secret.png"}, ctx)
    assert result.ok is False
    assert result.error_reason == "url_ssrf_blocked"
    assert list_viewing_att_ids(viewing) == []


def test_view_url_ssrf_10_x_blocked(ctx: ToolContext) -> None:
    result = view_media({"url": "https://10.0.0.8/x.png"}, ctx)
    assert result.ok is False
    assert result.error_reason == "url_ssrf_blocked"


def test_view_url_ssrf_metadata_blocked(ctx: ToolContext) -> None:
    result = view_media({"url": "https://169.254.169.254/latest/meta-data/"}, ctx)
    assert result.ok is False
    assert result.error_reason == "url_ssrf_blocked"


def test_view_url_http_invalid(ctx: ToolContext) -> None:
    result = view_media({"url": "http://example.com/a.png"}, ctx)
    assert result.ok is False
    assert result.error_reason == "url_invalid"


def test_view_url_size_cap(ctx: ToolContext) -> None:
    # Content-Length over URL_MAX_BYTES (48 MiB) aborts before body read.
    def urlopen_cl(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        return _FakeResp(
            b"x",
            headers={
                "Content-Type": "image/png",
                "Content-Length": str(60 * 1024 * 1024),
            },
        )

    ctx.extras["urlopen"] = urlopen_cl
    ctx.extras["getaddrinfo"] = _public_getaddrinfo
    result = view_media({"url": "https://example.com/huge.png"}, ctx)
    assert result.ok is False
    assert result.error_reason == "url_too_large"


def test_view_url_timeout(ctx: ToolContext) -> None:
    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        raise TimeoutError("simulated")

    ctx.extras["urlopen"] = urlopen
    ctx.extras["getaddrinfo"] = _public_getaddrinfo
    result = view_media({"url": "https://example.com/slow.png"}, ctx)
    assert result.ok is False
    assert result.error_reason == "url_timeout"


def test_dual_source_path_url_same_sha(
    ctx: ToolContext,
    sandbox: Sandbox,
    media: MediaStore,
) -> None:
    data = FIXTURE_PNG.read_bytes()
    _write_sandbox_bytes(sandbox, "tmp/same.png", data)
    ctx.extras["urlopen"] = _png_urlopen(data)
    ctx.extras["getaddrinfo"] = _public_getaddrinfo
    meta_before = set(media.list_meta_ids())
    result = view_media(
        {"path": "tmp/same.png", "url": "https://example.com/same.png"},
        ctx,
    )
    assert result.ok is True
    assert result.payload["source"] == "path+url"
    assert result.payload["kind"] == "image"
    after = set(media.list_meta_ids())
    new_ids = after - meta_before
    assert len(new_ids) == 1
    att = media.get(result.payload["att_id"])
    assert att is not None
    assert att.sha256 == hashlib.sha256(data).hexdigest()


def test_dual_source_path_url_conflict(
    ctx: ToolContext,
    sandbox: Sandbox,
    viewing: dict,
    media: MediaStore,
) -> None:
    _write_sandbox_png(sandbox, "tmp/local.png")
    other = b"\x89PNG\r\n\x1a\n" + b"DIFFERENT-BYTES"
    ctx.extras["urlopen"] = _png_urlopen(other)
    ctx.extras["getaddrinfo"] = _public_getaddrinfo
    meta_before = set(media.list_meta_ids())
    result = view_media(
        {"path": "tmp/local.png", "url": "https://example.com/other.png"},
        ctx,
    )
    assert result.ok is False
    assert result.error_reason == "ambiguous_source"
    assert list_viewing_att_ids(viewing) == []
    # No orphan URL put on conflict.
    assert set(media.list_meta_ids()) == meta_before


def test_dual_source_url_with_att_id_same_sha(
    ctx: ToolContext,
    media: MediaStore,
) -> None:
    data = FIXTURE_PNG.read_bytes()
    existing = media.put_bytes(data, filename="prior.png", origin="user_upload")
    ctx.extras["urlopen"] = _png_urlopen(data)
    ctx.extras["getaddrinfo"] = _public_getaddrinfo
    result = view_media(
        {"att_id": existing.id, "url": "https://example.com/prior.png"},
        ctx,
    )
    assert result.ok is True
    assert result.payload["att_id"] == existing.id
    assert "url" in result.payload["source"]
    assert "att_id" in result.payload["source"]


def test_promote_source_url_redacted(
    ctx: ToolContext,
    mem_store,
) -> None:
    data = FIXTURE_PNG.read_bytes()
    ctx.extras["urlopen"] = _png_urlopen(data)
    ctx.extras["getaddrinfo"] = _public_getaddrinfo
    result = view_media(
        {"url": "https://example.com/cat.png?token=supersecret", "note": "from web"},
        ctx,
    )
    assert result.ok is True
    assert "token" not in result.payload.get("source_url", "")
    atoms = mem_store.list_by_moment("moment-view-1", kinds=["observation"])
    assert len(atoms) == 1
    assert atoms[0].meta.get("source_url") == "https://example.com/cat.png"
    assert "supersecret" not in str(atoms[0].meta)


def test_media_disabled(ctx: ToolContext, sandbox: Sandbox, monkeypatch) -> None:
    monkeypatch.setenv("ELYRA_MEDIA", "0")
    _write_sandbox_png(sandbox)
    result = view_media({"path": "tmp/animal.png"}, ctx)
    assert result.ok is False
    assert result.error_reason == "media_disabled"


def test_viewing_unavailable_without_extras(paths, sandbox) -> None:
    ctx = ToolContext(paths=paths, sandbox=sandbox, moment_id="m1", extras={})
    result = view_media({"op": "list"}, ctx)
    assert result.ok is False
    assert result.error_reason == "viewing_unavailable"


# ---------------------------------------------------------------------------
# Promote first-wins (KD-V11 / KD-V16)
# ---------------------------------------------------------------------------


def test_promote_first_wins_no_wake_message_id(
    ctx: ToolContext,
    media: MediaStore,
    mem_store,
    viewing: dict,
) -> None:
    att = media.put_bytes(FIXTURE_PNG.read_bytes(), filename="p.png", origin="tool")
    r1 = view_media({"att_id": att.id, "note": "first look"}, ctx)
    assert r1.ok is True
    assert r1.payload["promoted"] is True

    atoms = mem_store.list_by_moment("moment-view-1", kinds=["observation"])
    assert len(atoms) == 1
    atom = atoms[0]
    assert att.id in atom.media_ids
    assert atom.meta.get("source") == "view_media"
    assert atom.meta.get("view") is True
    assert "wake_message_id" not in (atom.meta or {})
    assert atom.content_text == "first look"

    # Re-view same att — first-wins, no second atom.
    r2 = view_media({"att_id": att.id, "note": "second look ignored"}, ctx)
    assert r2.ok is True
    assert r2.payload["promoted"] is False
    assert r2.payload["viewing_dirty"] is True  # re-view still dirties
    atoms2 = mem_store.list_by_moment("moment-view-1", kinds=["observation"])
    assert len(atoms2) == 1
    assert atoms2[0].content_text == "first look"


def test_promote_skipped_without_memory_store(
    paths, sandbox, viewing, dirty_flag, mem_settings
) -> None:
    _write_sandbox_png(sandbox)
    ctx = ToolContext(
        paths=paths,
        sandbox=sandbox,
        settings=Settings(memory=mem_settings),
        moment_id="m-no-store",
        extras={
            "moment_viewing": viewing,
            "set_viewing_dirty": lambda: dirty_flag.__setitem__(0, True),
            # no memory_store
        },
    )
    result = view_media({"path": "tmp/animal.png"}, ctx)
    assert result.ok is True
    assert result.payload["promoted"] is False
    assert dirty_flag[0] is True


# ---------------------------------------------------------------------------
# Modality honesty (AV inventory until PR4)
# ---------------------------------------------------------------------------


def test_audio_perception_false_soft_warnings(
    ctx: ToolContext,
    media: MediaStore,
) -> None:
    data = FIXTURE_WAV.read_bytes() if FIXTURE_WAV.is_file() else b"RIFF....WAVEfmt "
    att = media.put_bytes(
        data,
        filename="clip.wav",
        mime="audio/wav",
        kind="audio",
        origin="tool",
    )
    result = view_media({"att_id": att.id}, ctx)
    assert result.ok is True
    assert result.payload["kind"] == "audio"
    assert result.payload["presentation"] == "inventory"
    assert result.payload["perception"] is False
    assert result.payload.get("skip_reason") == "av_expand_not_yet_wired"
    assert result.payload.get("soft_warnings")
    assert any("audio" in w.lower() or "short" in w.lower() for w in result.payload["soft_warnings"])


def test_image_perception_true(ctx: ToolContext, media: MediaStore) -> None:
    att = media.put_bytes(FIXTURE_PNG.read_bytes(), filename="i.png", origin="tool")
    result = view_media({"att_id": att.id}, ctx)
    assert result.ok is True
    assert result.payload["perception"] is True
    assert result.payload["presentation"] == "image_url"
    assert "skip_reason" not in result.payload


# ---------------------------------------------------------------------------
# mark_viewing port (worker-style)
# ---------------------------------------------------------------------------


def test_mark_viewing_port_used(paths, sandbox, media) -> None:
    """When mark_viewing is injected, tool prefers it over raw map mutate."""
    calls: list[dict[str, Any]] = []
    viewing: dict[str, ViewingEntry] = {}

    def mark(att_id: str, **kwargs: Any) -> list[str]:
        calls.append({"att_id": att_id, **kwargs})
        from elyra.media.viewing import add_viewing

        add_viewing(viewing, att_id, **kwargs)
        return list(viewing.keys())

    att = media.put_bytes(FIXTURE_PNG.read_bytes(), filename="m.png", origin="tool")
    ctx = ToolContext(
        paths=paths,
        sandbox=sandbox,
        moment_id="m-mark",
        extras={
            "moment_viewing": viewing,
            "mark_viewing": mark,
        },
    )
    result = view_media({"att_id": att.id}, ctx)
    assert result.ok is True
    assert len(calls) == 1
    assert calls[0]["att_id"] == att.id
    assert att.id in viewing


def test_drop_clear_ports_used(paths, sandbox, media) -> None:
    """Locked drop_viewing / clear_viewing ports are preferred when injected."""
    viewing: dict[str, ViewingEntry] = {}
    drop_calls: list[str] = []
    clear_calls: list[int] = []

    def drop_port(att_id: str) -> bool:
        drop_calls.append(att_id)
        from elyra.media.viewing import drop_viewing as dv

        return dv(viewing, att_id)

    def clear_port() -> int:
        from elyra.media.viewing import clear_viewing as cv

        n = cv(viewing)
        clear_calls.append(n)
        return n

    att = media.put_bytes(FIXTURE_PNG.read_bytes(), filename="p.png", origin="tool")
    from elyra.media.viewing import add_viewing

    add_viewing(viewing, att.id, kind="image")
    ctx = ToolContext(
        paths=paths,
        sandbox=sandbox,
        moment_id="m-ports",
        extras={
            "moment_viewing": viewing,
            "drop_viewing": drop_port,
            "clear_viewing": clear_port,
        },
    )
    dropped = view_media({"op": "drop", "att_id": att.id}, ctx)
    assert dropped.ok is True
    assert drop_calls == [att.id]
    assert dropped.payload["removed"] is True

    add_viewing(viewing, att.id, kind="image")
    cleared = view_media({"op": "clear"}, ctx)
    assert cleared.ok is True
    assert clear_calls == [1]
    assert cleared.payload["cleared"] == 1


def test_invalid_op(ctx: ToolContext) -> None:
    result = view_media({"op": "explode"}, ctx)
    assert result.ok is False
    assert result.error_reason == "invalid_op"
