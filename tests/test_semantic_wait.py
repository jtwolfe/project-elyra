"""Semantic wait-for-select: runtime JSON, clamp, status block, glass wiring."""

from __future__ import annotations

from pathlib import Path

from elyra.memory.config import (
    SEMANTIC_WAIT_MAX_MS_DEFAULT,
    MemorySettings,
    clamp_semantic_wait_max_ms,
    effective_semantic_wait_max_ms,
    semantic_ann_deadline_ms,
    semantic_wait_enabled,
    snappy_ann_max_ms,
)
from elyra.runtime.semantic_wait import (
    DEFAULT_ENABLED,
    DEFAULT_MAX_MS,
    SEMANTIC_WAIT_APPLIES_TO_PR1A,
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
    assert s.max_ms == DEFAULT_MAX_MS == SEMANTIC_WAIT_MAX_MS_DEFAULT == 15_000
    assert effective_select_max_ms(s) == 15_000


def test_clamp_band() -> None:
    assert clamp_wait_max_ms(100) == 1_000
    assert clamp_wait_max_ms(200_000) == 120_000
    assert clamp_wait_max_ms(8_000) == 8_000
    # Single source: runtime re-exports memory.config clamp.
    assert clamp_wait_max_ms(50) == clamp_semantic_wait_max_ms(50)


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
    block = semantic_wait_status_block(loaded, snappy_max_ms=40)
    assert block["enabled"] is False
    assert block["max_ms"] == 12_000
    assert block["snappy_select_max_ms"] == 40
    assert block["effective_select_max_ms"] == 40  # snappy when off


def test_missing_file_uses_product_defaults(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    loaded = load_semantic_wait_runtime(data)
    assert loaded.enabled is True
    assert loaded.max_ms == 15_000


def test_missing_file_seeds_from_memory_settings(tmp_path: Path) -> None:
    """elyra.toml knobs affect live path until operator writes runtime JSON."""
    data = tmp_path / "data"
    data.mkdir()
    defaults = MemorySettings(
        semantic_wait_for_select=False,
        semantic_wait_max_ms=8_000,
    )
    loaded = load_semantic_wait_runtime(data, defaults=defaults)
    assert loaded.enabled is False
    assert loaded.max_ms == 8_000
    # JSON still wins over settings when present.
    save_semantic_wait_runtime(data, enabled=True, max_ms=20_000)
    reloaded = load_semantic_wait_runtime(data, defaults=defaults)
    assert reloaded.enabled is True
    assert reloaded.max_ms == 20_000


def test_save_preserves_max_ms_when_only_toggling(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    save_semantic_wait_runtime(data, enabled=True, max_ms=20_000)
    save_semantic_wait_runtime(data, enabled=False, max_ms=None)
    loaded = load_semantic_wait_runtime(data)
    assert loaded.enabled is False
    assert loaded.max_ms == 20_000


def test_status_block_shape() -> None:
    block = semantic_wait_status_block(
        SemanticWaitState(enabled=True, max_ms=9_000),
        snappy_max_ms=50,
    )
    assert block == {
        "enabled": True,
        "max_ms": 9_000,
        "min_max_ms": 1_000,
        "max_max_ms": 120_000,
        "snappy_select_max_ms": 50,
        "effective_select_max_ms": 9_000,
        "applies_to": list(SEMANTIC_WAIT_APPLIES_TO_PR1A),
    }
    assert "meal_select" in block["applies_to"]
    assert "traverse_start" in block["applies_to"]
    assert "traverse_step_semantic" in block["applies_to"]
    assert "http_neighbors_opt_in" in block["applies_to"]
    # PR1b site not yet listed in PR1a.
    assert "speak_recalls_deferred" not in block["applies_to"]


def test_effective_semantic_wait_helper_runtime_overlay() -> None:
    """Helper prefers runtime_state over settings for ceiling identity."""
    settings = MemorySettings(
        semantic_wait_for_select=True,
        semantic_wait_max_ms=8_000,
        semantic_select_max_ms=40,
        traverse_expand_max_ms=120,
    )
    assert semantic_wait_enabled(settings) is True
    assert effective_semantic_wait_max_ms(settings) == 8_000
    runtime = SemanticWaitState(enabled=True, max_ms=30_000)
    assert effective_semantic_wait_max_ms(settings, runtime_state=runtime) == 30_000
    runtime_off = SemanticWaitState(enabled=False, max_ms=30_000)
    assert semantic_wait_enabled(settings, runtime_state=runtime_off) is False
    # Wait off → snappy table, not 40ms island or start_ms 250.
    assert snappy_ann_max_ms(settings, "meal") == 40
    assert snappy_ann_max_ms(settings, "traverse") == min(120, 40)
    assert snappy_ann_max_ms(settings, "http") == min(120, 40)
    assert snappy_ann_max_ms(settings, "recalls") == min(40, 100)
    assert semantic_ann_deadline_ms(settings, "traverse") == 8_000
    assert (
        semantic_ann_deadline_ms(
            settings, "traverse", runtime_state=runtime_off
        )
        == min(120, 40)
    )


def test_snappy_ann_empty_settings_defaults() -> None:
    """Zero-state / None settings → product snappy defaults (no crash)."""
    assert snappy_ann_max_ms(None, "meal") == 50
    assert snappy_ann_max_ms(None, "traverse") == min(120, 50)
    assert snappy_ann_max_ms(None, "http") == min(120, 50)
    assert snappy_ann_max_ms(None, "recalls") == min(50, 100)
    assert semantic_wait_enabled(None) is True
    assert effective_semantic_wait_max_ms(None) == SEMANTIC_WAIT_MAX_MS_DEFAULT


def test_effective_wait_clamps_band() -> None:
    settings = MemorySettings(semantic_wait_max_ms=50)
    assert effective_semantic_wait_max_ms(settings) == 1_000
    settings_hi = MemorySettings(semantic_wait_max_ms=999_999)
    assert effective_semantic_wait_max_ms(settings_hi) == 120_000


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
    # Off copy uses live effective/snappy from status — not hardcode-only 50.
    assert "effective_select_max_ms" in js
    assert "snappy omit" in js


def test_glass_assets_wire_semantic_context_dedupe_note() -> None:
    """Memory Context panel shows semantic omit/dedupe note (not silent empty)."""
    root = Path(__file__).resolve().parents[1]
    web = root / "elyra" / "runtime" / "web"
    js = (web / "app.js").read_text(encoding="utf-8")
    css = (web / "style.css").read_text(encoding="utf-8")

    assert "function formatSemanticSelectLine" in js
    assert "function renderSemanticChannelNote" in js
    assert "renderSemanticChannelNote(meal)" in js
    assert "memory-semantic-note-deduped" in js
    assert "already in temporal/episodic" in js
    assert "memory-semantic-note-deduped" in css
    assert ".memory-semantic-note" in css
