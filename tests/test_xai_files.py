"""PR9: xAI Files upload + xai_file_id storage; Completions attach gated off."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.llm.config import XaiClientConfig
from elyra.media import MediaStore
from elyra.media.xai_files import (
    XaiFilesClient,
    completions_file_attach_enabled,
    completions_file_part,
    ensure_xai_file_id,
    is_files_tier_candidate,
    parse_upload_response,
)

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "media" / "sample.pdf"


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    return MediaStore(paths)


def test_completions_file_attach_default_off(monkeypatch):
    monkeypatch.delenv("ELYRA_XAI_FILES_ATTACH", raising=False)
    assert completions_file_attach_enabled() is False


def test_completions_file_attach_opt_in(monkeypatch):
    monkeypatch.setenv("ELYRA_XAI_FILES_ATTACH", "1")
    assert completions_file_attach_enabled() is True


def test_parse_upload_response_and_expires_iso():
    result = parse_upload_response(
        {
            "id": "file_abc123",
            "filename": "sample.pdf",
            "bytes": 597,
            "expires_at": 1_700_000_000,
            "purpose": "assistants",
            "object": "file",
        }
    )
    assert result.id == "file_abc123"
    assert result.bytes == 597
    assert result.expires_at_iso() is not None
    assert result.expires_at_iso().endswith("Z")


def test_is_files_tier_candidate_pdf():
    assert is_files_tier_candidate(
        mime="application/pdf", filename="doc.pdf", kind="file"
    )
    assert not is_files_tier_candidate(
        mime="image/png", filename="x.png", kind="image"
    )
    assert not is_files_tier_candidate(
        mime="text/plain", filename="a.txt", kind="file"
    )


def test_upload_bytes_mocked_multipart(store):
    """Upload builds multipart with expires_after before file; persists meta."""
    pdf = FIXTURE_PDF.read_bytes()
    att = store.put_bytes(pdf, filename="sample.pdf", origin="user_upload")
    assert att.mime == "application/pdf"

    captured: dict = {}

    class _Resp:
        def read(self) -> bytes:
            return json.dumps(
                {
                    "id": "file_upload_1",
                    "filename": "sample.pdf",
                    "bytes": len(pdf),
                    "expires_at": 2_000_000_000,
                    "purpose": "assistants",
                    "object": "file",
                }
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = req.data
        captured["timeout"] = timeout
        return _Resp()

    client = XaiFilesClient(
        base_url="https://api.x.ai/v1",
        bearer_token="test-secret-token",
        urlopen=fake_urlopen,
    )
    result = client.upload_bytes(
        pdf,
        filename="sample.pdf",
        content_type="application/pdf",
        expires_after=86400,
    )
    assert result.id == "file_upload_1"
    assert captured["url"] == "https://api.x.ai/v1/files"
    assert captured["method"] == "POST"
    assert "authorization" in captured["headers"]
    assert captured["headers"]["authorization"] == "Bearer test-secret-token"
    body = captured["body"]
    assert isinstance(body, (bytes, bytearray))
    # expires_after must appear before the file part (xAI ordering).
    idx_exp = body.find(b'name="expires_after"')
    idx_file = body.find(b'name="file"')
    assert idx_exp != -1 and idx_file != -1
    assert idx_exp < idx_file
    assert b"86400" in body
    assert b"sample.pdf" in body
    # Never leak token into exception paths (smoke: not in body).
    assert b"test-secret-token" not in body


def test_from_config_files_url():
    cfg = XaiClientConfig(base_url="https://api.x.ai/v1", files_path="/files")
    assert cfg.files_url == "https://api.x.ai/v1/files"
    client = XaiFilesClient.from_config(cfg, bearer_token="tok")
    assert client.files_url == "https://api.x.ai/v1/files"


def test_ensure_xai_file_id_persists_meta(store):
    pdf = FIXTURE_PDF.read_bytes()
    att = store.put_bytes(pdf, filename="sample.pdf")
    assert att.xai_file_id is None

    def fake_urlopen(req, timeout=None):
        class _R:
            def read(self):
                return json.dumps(
                    {
                        "id": "file_persist_9",
                        "filename": "sample.pdf",
                        "bytes": len(pdf),
                        "expires_at": 2_100_000_000,
                        "object": "file",
                    }
                ).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R()

    client = XaiFilesClient(bearer_token="tok", urlopen=fake_urlopen)
    fid = ensure_xai_file_id(att.id, media_store=store, client=client)
    assert fid == "file_persist_9"
    reloaded = store.get(att.id)
    assert reloaded is not None
    assert reloaded.xai_file_id == "file_persist_9"
    assert reloaded.xai_file_expires_at is not None

    # Second call reuses stored id (no re-upload) — force=False.
    calls = {"n": 0}

    def boom_urlopen(req, timeout=None):
        calls["n"] += 1
        raise AssertionError("should not re-upload")

    client2 = XaiFilesClient(bearer_token="tok", urlopen=boom_urlopen)
    fid2 = ensure_xai_file_id(att.id, media_store=store, client=client2)
    assert fid2 == "file_persist_9"
    assert calls["n"] == 0


def test_ensure_xai_file_id_network_fail_returns_none(store):
    att = store.put_bytes(FIXTURE_PDF.read_bytes(), filename="sample.pdf")

    def fail_urlopen(req, timeout=None):
        from urllib.error import URLError

        raise URLError("network down")

    client = XaiFilesClient(bearer_token="tok", urlopen=fail_urlopen)
    assert ensure_xai_file_id(att.id, media_store=store, client=client) is None
    assert store.get(att.id).xai_file_id is None


def test_set_xai_file_direct(store):
    att = store.put_bytes(b"hello", filename="a.txt", mime="text/plain")
    updated = store.set_xai_file(
        att.id, xai_file_id="file_z", xai_file_expires_at="2030-01-01T00:00:00Z"
    )
    assert updated.xai_file_id == "file_z"
    assert store.get(att.id).xai_file_id == "file_z"


def test_completions_file_part_shape():
    part = completions_file_part("file_abc")
    assert part == {"type": "file", "file": {"file_id": "file_abc"}}
