"""PR9: PDF text extract always; not_inlined fallback; optional Files upload in expand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.loop.context import assemble_outer_meal
from elyra.media import MediaStore
from elyra.media.prompt import (
    expand_meal_for_provider,
    extract_pdf_text_best_effort,
    extract_text_for_attachment,
    index_glass,
)
from elyra.media.xai_files import XaiFilesClient

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "media" / "sample.pdf"
SYSTEM = "SYS"
ORIENT = (
    "orient {{NOW}}{{SELF}}{{USER}}{{WHY_NOW}}"
    "{{GOALS}}{{SKILL_CATALOG}}{{SKILL_BIAS}}"
)


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    return MediaStore(paths)


def _att_dict(att) -> dict:
    return att.to_dict()


def _glass_row(msg_id: str, *, content: str = "", attachments=None) -> dict:
    row = {"role": "user", "content": content, "id": msg_id}
    if attachments is not None:
        row["attachments"] = attachments
    return row


def test_sample_pdf_fixture_extractable():
    data = FIXTURE_PDF.read_bytes()
    assert data.startswith(b"%PDF")
    text = extract_pdf_text_best_effort(data)
    assert text is not None
    assert "Hello PDF sample" in text


def test_text_extract_pdf_tier_a(store):
    att = store.put_bytes(
        FIXTURE_PDF.read_bytes(),
        filename="sample.pdf",
        origin="user_upload",
    )
    fenced = extract_text_for_attachment(_att_dict(att), store)
    assert fenced is not None
    assert "Hello PDF sample" in fenced
    assert "sample.pdf" in fenced
    assert fenced.startswith("```")


def test_wake_pdf_extract_no_not_inlined_when_extracted(store):
    att = store.put_bytes(FIXTURE_PDF.read_bytes(), filename="sample.pdf")
    glass = [
        _glass_row("w-pdf", content="read this", attachments=[_att_dict(att)])
    ]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="w-pdf",
        wake_content="read this",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="w-pdf",
        media_store=store,
        provider="xai",
    )
    wake = next(m for m in expanded if m.get("id") == "w-pdf")
    assert isinstance(wake["content"], str)
    assert "Hello PDF sample" in wake["content"]
    assert "[attachments]" in wake["content"]
    assert "not_inlined" not in wake["content"]
    # Default: no Completions file parts (attach off).
    assert "file_id" not in wake["content"]


def test_pdf_not_inlined_when_extract_fails(store):
    # Valid PDF magic but no extractable text operators.
    emptyish = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    att = store.put_bytes(emptyish, filename="blank.pdf", mime="application/pdf")
    assert extract_text_for_attachment(_att_dict(att), store) is None

    glass = [_glass_row("w1", content="doc", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="w1",
        wake_content="doc",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="w1",
        media_store=store,
        provider="xai",
    )
    wake = next(m for m in expanded if m.get("id") == "w1")
    text = wake["content"] if isinstance(wake["content"], str) else ""
    assert "[attachments]" in text
    assert "file pdf not_inlined" in text
    # No silent multimodal file parts.
    if isinstance(wake["content"], list):
        assert not any(p.get("type") == "file" for p in wake["content"])


def test_expand_upload_files_to_xai_stores_id(store, monkeypatch):
    """Optional upload path persists xai_file_id without Completions attach."""
    monkeypatch.delenv("ELYRA_XAI_FILES_ATTACH", raising=False)
    att = store.put_bytes(FIXTURE_PDF.read_bytes(), filename="sample.pdf")

    def fake_urlopen(req, timeout=None):
        class _R:
            def read(self):
                return json.dumps(
                    {
                        "id": "file_from_expand",
                        "filename": "sample.pdf",
                        "bytes": att.byte_size,
                        "expires_at": 2_000_000_000,
                        "object": "file",
                    }
                ).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R()

    client = XaiFilesClient(bearer_token="tok", urlopen=fake_urlopen)
    glass = [_glass_row("w1", content="x", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="w1",
        wake_content="x",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="w1",
        media_store=store,
        provider="xai",
        xai_files_client=client,
        upload_files_to_xai=True,
    )
    reloaded = store.get(att.id)
    assert reloaded is not None
    assert reloaded.xai_file_id == "file_from_expand"
    wake = next(m for m in expanded if m.get("id") == "w1")
    # Attach still off → content is string (extract succeeded), no file parts.
    assert isinstance(wake["content"], str)
    assert "Hello PDF sample" in wake["content"]


def test_expand_file_attach_only_when_env_on(store, monkeypatch):
    """Completions file part only when ELYRA_XAI_FILES_ATTACH=1 + stored id."""
    monkeypatch.setenv("ELYRA_XAI_FILES_ATTACH", "1")
    # Unextractable PDF so we can see attach path without text extract masking.
    emptyish = b"%PDF-1.4\n%%EOF\n"
    att = store.put_bytes(emptyish, filename="blank.pdf")
    store.set_xai_file(att.id, xai_file_id="file_wire_1")

    glass = [_glass_row("w1", content="x", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="w1",
        wake_content="x",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="w1",
        media_store=store,
        provider="xai",
    )
    wake = next(m for m in expanded if m.get("id") == "w1")
    assert isinstance(wake["content"], list)
    file_parts = [p for p in wake["content"] if p.get("type") == "file"]
    assert len(file_parts) == 1
    assert file_parts[0]["file"]["file_id"] == "file_wire_1"
