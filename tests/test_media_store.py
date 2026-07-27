"""Content-addressed media store + Attachment schema (PR1 / KD1, KD12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.media import (
    Attachment,
    MediaStore,
    ensure_media_dirs,
    put_bytes,
    sniff_mime_and_kind,
)
from elyra.media.store import _atomic_write_bytes
from elyra.media.types import EMBEDDING_STATUSES


FIXTURE_PNG = Path(__file__).parent / "fixtures" / "media" / "1x1.png"


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    return MediaStore(paths)


def test_ensure_media_dirs_layout(paths):
    root = ensure_media_dirs(paths)
    assert root == paths.data_dir / "media"
    for sub in ("blobs", "meta", "tts", "by_message", "tmp"):
        assert (root / sub).is_dir()


def test_put_bytes_png_sniff_and_dedupe(store, paths):
    data = FIXTURE_PNG.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"

    att1 = store.put_bytes(
        data,
        filename="shot.png",
        origin="user_upload",
        uploader_user_id="operator",
    )
    assert att1.id.startswith("att_")
    assert att1.kind == "image"
    assert att1.mime == "image/png"
    assert att1.byte_size == len(data)
    assert att1.bound_message_id is None
    assert att1.embedding_status == "none"
    assert att1.embedding_ref is None
    assert att1.sandbox_relpath == f"media/{att1.id}/shot.png"
    assert att1.sha256
    assert store.blob_path(att1.sha256).is_file()
    assert store.meta_path(att1.id).is_file()

    raw_meta = json.loads(store.meta_path(att1.id).read_text(encoding="utf-8"))
    assert raw_meta["embedding_status"] == "none"
    assert raw_meta["embedding_ref"] is None
    assert raw_meta["bound_message_id"] is None
    assert set(EMBEDDING_STATUSES) >= {raw_meta["embedding_status"]}

    # Same bytes → same blob path; new att id.
    att2 = store.put_bytes(data, filename="again.png", origin="user_upload")
    assert att2.id != att1.id
    assert att2.sha256 == att1.sha256
    assert store.blob_path(att1.sha256).read_bytes() == data
    assert store.read_bytes(att1.id) == data
    assert store.read_bytes(att2.id) == data


def test_bind_message_and_reject_rebinding(store):
    att = store.put_bytes(b"hello text", filename="note.txt", origin="tool")
    assert att.kind == "file"
    assert att.mime in ("text/plain", "application/octet-stream")

    bound = store.bind_message(att.id, "msg-1")
    assert bound.bound_message_id == "msg-1"
    # Idempotent same message
    again = store.bind_message(att.id, "msg-1")
    assert again.bound_message_id == "msg-1"
    with pytest.raises(ValueError, match="already bound"):
        store.bind_message(att.id, "msg-2")
    with pytest.raises(ValueError, match="invalid message_id"):
        store.bind_message(att.id, "")
    with pytest.raises(ValueError, match="invalid message_id"):
        store.bind_message(att.id, "   ")


def test_magic_mime_prefers_sniff_over_claimed(store):
    """Confident magic hit stores sniffed mime, not a bogus client claim."""
    data = FIXTURE_PNG.read_bytes()
    att = store.put_bytes(
        data,
        filename="shot.bin",
        mime="application/octet-stream",
        origin="user_upload",
    )
    assert att.mime == "image/png"
    assert att.kind == "image"
    # Explicit kind= still overrides kind for product "treat image as file".
    att2 = store.put_bytes(
        data,
        filename="as-file.png",
        mime="application/octet-stream",
        kind="file",
        origin="system",
    )
    assert att2.mime == "image/png"
    assert att2.kind == "file"


def test_put_bytes_att_id_idempotent_or_reject(store):
    data = b"same-payload-id-reuse"
    att = store.put_bytes(
        data, filename="a.txt", origin="system", att_id="att_fixedid001"
    )
    # Same id + same bytes → idempotent return.
    again = store.put_bytes(
        data, filename="b.txt", origin="system", att_id="att_fixedid001"
    )
    assert again.id == att.id
    assert again.sha256 == att.sha256
    # Same id + different bytes → reject (no orphan prior-blob overwrite).
    with pytest.raises(ValueError, match="already exists"):
        store.put_bytes(
            b"other-payload",
            filename="c.txt",
            origin="system",
            att_id="att_fixedid001",
        )
    assert store.read_bytes(att.id) == data


def test_role_hint_unknown_defaults_to_primary(store):
    att = store.put_bytes(
        b"x",
        filename="x.txt",
        origin="system",
        role_hint="not-a-hint",
    )
    assert att.role_hint == "primary"
    att2 = store.put_bytes(
        b"y",
        filename="y.txt",
        origin="system",
        role_hint="inline",
    )
    assert att2.role_hint == "inline"


def test_get_missing_and_invalid_id(store):
    assert store.get("att_doesnotexist000000000000000000") is None
    assert store.get("../escape") is None
    with pytest.raises(ValueError):
        store.meta_path("..")


def test_atomic_write_cleans_temp_on_failure(paths, monkeypatch):
    """Failed replace must not leave a durable final path; temp is cleaned."""
    ensure_media_dirs(paths)
    dest = paths.data_dir / "media" / "blobs" / "aa" / "deadbeef"
    tmp_dir = paths.data_dir / "media" / "tmp"

    def boom_replace(self, target):  # noqa: ANN001
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom_replace)
    with pytest.raises(OSError, match="disk full"):
        _atomic_write_bytes(dest, b"payload", tmp_dir=tmp_dir)
    assert not dest.exists()
    # No leftover .part files
    parts = list(tmp_dir.glob("*.part"))
    assert parts == []


def test_put_bytes_meta_failure_no_half_meta(store, monkeypatch):
    """If meta write fails after blob, no corrupt meta file remains."""
    data = b"unique-payload-for-meta-fail"
    calls = {"n": 0}
    real = store._write_meta

    def fail_once(att):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("meta fail")
        return real(att)

    monkeypatch.setattr(store, "_write_meta", fail_once)
    with pytest.raises(OSError, match="meta fail"):
        store.put_bytes(data, filename="x.bin", origin="system")
    assert store.list_meta_ids() == []


def test_sniff_jpeg_and_pdf():
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 16
    mime, kind = sniff_mime_and_kind(jpeg)
    assert mime == "image/jpeg" and kind == "image"

    pdf = b"%PDF-1.4\n%"
    mime, kind = sniff_mime_and_kind(pdf, filename="doc.pdf")
    assert mime == "application/pdf" and kind == "file"


def test_module_put_bytes_wrapper(paths):
    att = put_bytes(b"abc", filename="a.txt", paths=paths, origin="system")
    assert isinstance(att, Attachment)
    assert MediaStore(paths).get(att.id) is not None


def test_delete_attachment_orphan_blob(store):
    data = b"orphan-me"
    a = store.put_bytes(data, filename="o.txt", origin="system")
    b = store.put_bytes(data, filename="o2.txt", origin="system")
    assert a.sha256 == b.sha256
    assert store.delete_attachment(a.id) is True
    # blob still held by b
    assert store.blob_path(a.sha256).is_file()
    assert store.delete_attachment(b.id) is True
    assert not store.blob_path(a.sha256).is_file()


def test_attachment_from_dict_defaults_embedding():
    att = Attachment.from_dict(
        {
            "id": "att_x",
            "kind": "image",
            "origin": "user_upload",
            "filename": "x.png",
            "mime": "image/png",
            "byte_size": 1,
            "sha256": "a" * 64,
            "created_at": "2026-01-01T00:00:00Z",
            "embedding_status": "bogus",
        }
    )
    assert att.embedding_status == "none"
    assert att.bound_message_id is None


def test_put_bytes_projects_sandbox_media(store, paths):
    """PR2: put_bytes projects into sandboxes/sandbox0/media/<id>/<name>."""
    data = FIXTURE_PNG.read_bytes()
    att = store.put_bytes(data, filename="shot.png", origin="user_upload")
    mirror = (
        paths.home / "sandboxes" / "sandbox0" / "media" / att.id / "shot.png"
    )
    assert mirror.is_file()
    assert mirror.read_bytes() == data


def test_idempotent_put_reprojects_missing_mirror(store, paths):
    """Idempotent put (same att_id + sha) re-projects if sandbox mirror is gone.

    Repro: put → clear_sandbox (mirror wiped, meta+blob remain) → put same id
    must heal the mirror (L3 disposable projection).
    """
    from elyra.runtime.reset import clear_sandbox

    data = b"idempotent-reproject-payload"
    att = store.put_bytes(
        data, filename="heal.txt", origin="system", att_id="att_heal_reproject"
    )
    mirror = (
        paths.home
        / "sandboxes"
        / "sandbox0"
        / "media"
        / att.id
        / "heal.txt"
    )
    assert mirror.is_file()
    clear_sandbox(paths)
    assert not mirror.exists()
    # Meta + blob still durable.
    assert store.get(att.id) is not None
    assert store.blob_path(att.sha256).is_file()

    again = store.put_bytes(
        data, filename="heal.txt", origin="system", att_id=att.id
    )
    assert again.id == att.id
    assert again.sha256 == att.sha256
    assert mirror.is_file()
    assert mirror.read_bytes() == data


def test_project_attachment_hardlink_then_copy_fallback(store, paths, monkeypatch):
    """Projection tries os.link; on OSError falls back to copy2 + chmod 0o444."""
    from elyra.media.project import project_attachment

    data = b"project-payload-unique"
    att = store.put_bytes(data, filename="p.txt", origin="system")
    blob = store.blob_path(att.sha256)
    assert blob.is_file()

    # Force copy path.
    def boom_link(src, dst):  # noqa: ANN001
        raise OSError("cross-device link")

    monkeypatch.setattr("elyra.media.project.os.link", boom_link)
    dest = project_attachment(att, blob, paths=paths)
    assert dest.is_file()
    assert dest.read_bytes() == data
    # copy path applies 0o444
    assert dest.stat().st_mode & 0o777 == 0o444
