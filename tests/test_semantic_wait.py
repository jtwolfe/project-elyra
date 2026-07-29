"""Semantic wait-for-select: runtime JSON, clamp, status block, glass wiring."""

from __future__ import annotations

from pathlib import Path

from elyra.runtime.semantic_wait import (
    DEFAULT_ENABLED,
    DEFAULT_MAX_MS,
    SemanticWaitState,
    clamp_wait_max_ms,
    effective_select_max_ms,
    load_semantic_wait_runtime,
    save_semantic_wait_runtime,
    semantic_wait_status_block,
)


def test_defaults_on_with_15s() -> None:
    s = SemanticWaitState()
    assert s.enabled is DEFAULT_ENABLED is True
    assert s.max_ms == DEFAULT_MAX_MS == 15_000
    assert effective_select_max_ms(s) == 15_000


def test_clamp_band() -> None:
    assert clamp_wait_max_ms(100) == 1_000
    assert clamp_wait_max_ms(200_000) == 120_000
    assert clamp_wait_max_ms(8_000) == 8_000


def test_effective_snappy_when_off() -> None:
    s = SemanticWaitState(enabled=False, max_ms=15_000)
    assert effective_select_max_ms(s, snappy_max_ms=50) == 50
    assert effective_select_max_ms(s, snappy_max_ms=20) == 20


def test_load_save_roundtrip(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    save_semantic_wait_runtime(data, enabled=False, max_ms=12_000)
    loaded = load_semantic_wait_runtime(data)
    assert loaded.enabled is False
    assert loaded.max_ms == 12_000
    block = semantic_wait_status_block(loaded)
    assert block["enabled"] is False
    assert block["max_ms"] == 12_000
    assert block["effective_select_max_ms"] == 50  # snappy when off


def test_missing_file_uses_defaults(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    loaded = load_semantic_wait_runtime(data)
    assert loaded.enabled is True
    assert loaded.max_ms == 15_000


def test_save_preserves_max_ms_when_only_toggling(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    save_semantic_wait_runtime(data, enabled=True, max_ms=20_000)
    save_semantic_wait_runtime(data, enabled=False, max_ms=None)
    loaded = load_semantic_wait_runtime(data)
    assert loaded.enabled is False
    assert loaded.max_ms == 20_000


def test_status_block_shape() -> None:
    block = semantic_wait_status_block(SemanticWaitState(enabled=True, max_ms=9_000))
    assert block == {
        "enabled": True,
        "max_ms": 9_000,
        "min_max_ms": 1_000,
        "max_max_ms": 120_000,
        "effective_select_max_ms": 9_000,
    }


def test_glass_assets_wire_semantic_wait() -> None:
    """Static glass assets include toggle + API wiring (not just identifiers)."""
    root = Path(__file__).resolve().parents[1]
    web = root / "elyra" / "runtime" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "app.js").read_text(encoding="utf-8")

    assert 'id="semantic-wait-toggle"' in html
    assert 'id="semantic-wait-max-ms"' in html
    assert "Wait for semantic" in html
    assert "semantic_wait.json" in html

    assert "renderSemanticWait" in js
    assert "patchSemanticWait" in js
    assert 'fetchJson("/api/semantic-wait"' in js
    assert "method: \"PATCH\"" in js or 'method: "PATCH"' in js
    assert "semanticWaitToggle.addEventListener" in js
    assert "renderSemanticWait(s)" in js
