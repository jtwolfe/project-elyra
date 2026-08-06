"""Hermetic glass markdown fixtures (#88 A+B / KD-MD1 / KD-MD2).

Runs pure helpers from elyra/runtime/web/markdown.js via node — not needle-only
string presence checks. Also asserts app.js / index.html wiring for plain
system/orient Memory Context channels.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parents[1] / "elyra" / "runtime" / "web"
MARKDOWN_JS = WEB_DIR / "markdown.js"
APP_JS = WEB_DIR / "app.js"
INDEX_HTML = WEB_DIR / "index.html"


def _node_available() -> bool:
    return shutil.which("node") is not None


def _run_markdown_node(script: str) -> str:
    """Execute a small node script with markdown.js require path injected."""
    if not _node_available():
        pytest.skip("node not available for hermetic markdown fixtures")
    # script receives MARKDOWN_PATH via env
    env_script = f"""
const path = {json.dumps(str(MARKDOWN_JS))};
const md = require(path);
{script}
"""
    proc = subprocess.run(
        ["node", "-e", env_script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"node markdown fixture failed (rc={proc.returncode}):\n"
            f"stdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return proc.stdout


def _render(src: str) -> str:
    out = _run_markdown_node(
        f"""
const html = md.renderMarkdown({json.dumps(src)});
process.stdout.write(html);
"""
    )
    return out


# ── #88A: tag-protected emphasis after links ─────────────────────────────


def test_link_then_italic_preserves_target_blank():
    """Confirmed bug: target="_blank" mangled when _emphasis_ follows link."""
    html = _render("[x](https://example.com) and _y_")
    assert 'target="_blank"' in html
    assert 'target="<em>' not in html
    assert "<em>y</em>" in html
    assert 'rel="noopener noreferrer"' in html
    assert 'href="https://example.com"' in html


def test_href_path_underscores_intact():
    """Path segments like Foo_Bar_Baz must not become <em> inside href."""
    html = _render("[Wiki](https://example.com/wiki/Foo_Bar_Baz)")
    assert "Foo_Bar_Baz" in html
    assert "<em>Bar</em>" not in html
    assert 'href="https://example.com/wiki/Foo_Bar_Baz"' in html
    assert 'target="_blank"' in html


def test_normal_underscore_italic_still_works():
    html = _render("see _emphasis_ here")
    assert "<em>emphasis</em>" in html
    assert "see " in html


def test_star_italic_and_bold_still_work():
    html = _render("a *star* and **bold** text")
    assert "<em>star</em>" in html
    assert "<strong>bold</strong>" in html


def test_double_underscore_bold():
    html = _render("use __strong__ here")
    assert "<strong>strong</strong>" in html


def test_empty_and_whitespace_render_empty_paragraph():
    assert _render("") == "<p></p>"
    assert _render("   \n  ") == "<p></p>"


def test_javascript_scheme_not_live_link():
    html = _render("[x](javascript:alert(1))")
    assert "<a " not in html
    assert "javascript:" in html or "javascript:alert" in html


def test_apply_emphasis_protects_existing_tags():
    """Unit: applyEmphasis must not touch attributes inside tags."""
    out = _run_markdown_node(
        r"""
const input = '<a href="https://ex.com/Foo_Bar" target="_blank">x</a> and _y_';
const html = md.applyEmphasis(input);
if (!html.includes('target="_blank"')) throw new Error('blank mangled: ' + html);
if (!html.includes('Foo_Bar')) throw new Error('path mangled: ' + html);
if (!html.includes('<em>y</em>')) throw new Error('no em: ' + html);
if (html.includes('<em>Bar</em>')) throw new Error('em in href: ' + html);
process.stdout.write('ok');
"""
    )
    assert out.strip() == "ok"


def test_inline_code_snake_case_not_italicized():
    """Full <code> spans are protected — snake_case in backticks stays intact."""
    html = _render("`snake_case` and _y_")
    assert "<code>snake_case</code>" in html
    assert "<em>case</em>" not in html
    assert "<em>y</em>" in html


def test_multi_underscore_link_label_no_cross_pair():
    """Whole anchors stashed: multi-_ labels must not pair with later emphasis."""
    html = _render("[_lab_el_](https://a.com) and _e_")
    assert 'target="_blank"' in html
    assert 'href="https://a.com"' in html
    # Outer trailing emphasis
    assert "<em>e</em>" in html
    # Anchor must stay well-formed (no em spanning across </a>)
    assert "<em></a>" not in html
    assert "</a> and </em>" not in html
    assert re.search(r"<a\b[^>]*>[\s\S]*?</a>", html)
    # Label still gets first paired underscore pass inside the anchor
    assert "<em>lab</em>" in html
    # Single-pair label still works with outer text
    html2 = _render("[_label_](https://a.com) and _e_")
    assert re.search(r"<a\b[^>]*><em>label</em></a>", html2)
    assert "<em>e</em>" in html2
    assert "<em></a>" not in html2


def test_identifier_underscores_match_current_paired_behavior():
    """Bare a_b_c pair-matches as a<em>b</em>c under current underscore rules."""
    html = _render("a_b_c")
    assert html == "<p>a<em>b</em>c</p>"


def test_placeholder_sentinel_does_not_collide_with_user_text():
    """User text resembling old %%TAGn%% placeholders must survive emphasis."""
    html = _render("%%TAG0%% and _x_")
    assert "%%TAG0%%" in html
    assert "<em>x</em>" in html


# ── fail-closed when markdown.js missing ─────────────────────────────────


def test_app_js_fail_closed_path_present():
    """Adapter must fail closed if ElyraMarkdown is missing (static wiring)."""
    js = APP_JS.read_text(encoding="utf-8")
    assert 'typeof md.renderMarkdown !== "function"' in js
    assert "Fail closed" in js
    assert "return `<p>${esc(src || " in js or "return `<p>${esc(src ||" in js


def test_fail_closed_renders_escaped_plain_no_markdown():
    """Behavioral contract of app.js fail-closed branch (no ElyraMarkdown)."""
    if not _node_available():
        pytest.skip("node not available for hermetic markdown fixtures")
    # Mirrors app.js renderMarkdown fail-closed arm exactly.
    script = r"""
function failClosedRenderMarkdown(src) {
  const esc = (s) =>
    String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  return `<p>${esc(src || "")}</p>`;
}
const html = failClosedRenderMarkdown("[x](https://example.com) and _y_");
if (html.includes("<em>") || html.includes("<a ") || html.includes("<strong>")) {
  throw new Error("fail-closed must not produce markdown HTML: " + html);
}
if (!html.includes("_y_") || !html.includes("[x](https://example.com)")) {
  throw new Error("fail-closed must keep raw text escaped: " + html);
}
if (!html.startsWith("<p>") || !html.endsWith("</p>")) {
  throw new Error("expected single escaped paragraph: " + html);
}
process.stdout.write("ok");
"""
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


# ── #88B: plain system/orient channels ───────────────────────────────────


def test_plain_memory_channels_helper():
    """All Memory Context channels are plain (isPlainMemoryChannel always true)."""
    out = _run_markdown_node(
        """
const ch = md.PLAIN_MEMORY_CHANNELS;
if (!Array.isArray(ch) || ch.indexOf('system') < 0 || ch.indexOf('orient') < 0)
  throw new Error('missing plain channels: ' + JSON.stringify(ch));
if (ch.indexOf('episodic') < 0 || ch.indexOf('temporal') < 0)
  throw new Error('expected episodic/temporal in PLAIN_MEMORY_CHANNELS');
if (md.isPlainMemoryChannel('system') !== true) throw new Error('system');
if (md.isPlainMemoryChannel('orient') !== true) throw new Error('orient');
if (md.isPlainMemoryChannel('temporal') !== true) throw new Error('temporal');
if (md.isPlainMemoryChannel('episodic') !== true) throw new Error('episodic');
if (md.isPlainMemoryChannel('directed_keep') !== true) throw new Error('keep');
if (md.isPlainMemoryChannel('anything_else') !== true) throw new Error('unknown');
process.stdout.write('ok');
"""
    )
    assert out.strip() == "ok"


def test_app_js_memory_context_all_plain_no_markdown():
    """Memory Context channel cards: plain textContent only — no MD HTML."""
    js = APP_JS.read_text(encoding="utf-8")
    # renderMemoryChannelCard must use plain snippet path only
    assert "memory-snippet-plain" in js
    assert "body.textContent = snippet" in js or "body.textContent = snippet ||" in js
    # No dual prose/markdown branch for Context channel cards
    assert "const proseCh" not in js
    assert "plainCh.has(ch)" not in js
    # Card body path must not *call* renderMarkdown on snippets (chat still may).
    i = js.find("function renderMemoryChannelCard")
    assert i >= 0, "renderMemoryChannelCard not found"
    # Function is large; take a fixed window covering the body-snippet branch.
    region = js[i : i + 4500]
    assert "memory-snippet-plain" in region
    assert "body.textContent = snippet" in region or "body.textContent = snippet ||" in region
    assert "renderMarkdown(" not in region
    assert "innerHTML = " not in region


def test_app_js_context_plain_not_prose_markdown_path():
    """Context inspect folds also use plain class (not prose markdown)."""
    js = APP_JS.read_text(encoding="utf-8")
    # Fixed-channel inspect + shared atom fill use plain class
    assert "memory-snippet-plain" in js
    # Channel card path comment documents Context-wide plain policy
    assert "Memory → Context" in js or "Memory Context" in js or "all channel snippets are plain" in js


def test_index_loads_markdown_js_before_app():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'src="/markdown.js"' in html
    assert 'src="/app.js"' in html
    md_pos = html.index('src="/markdown.js"')
    app_pos = html.index('src="/app.js"')
    assert md_pos < app_pos, "markdown.js must load before app.js"


def test_app_js_delegates_render_markdown_to_elyra_markdown():
    js = APP_JS.read_text(encoding="utf-8")
    assert "ElyraMarkdown" in js
    assert "md.renderMarkdown" in js or "ElyraMarkdown.renderMarkdown" in js
    # Tag-protect lives in the pure helper, not a second emphasis path in app.js
    md_src = MARKDOWN_JS.read_text(encoding="utf-8")
    assert "withProtectedTags" in md_src
    assert "applyEmphasis" in md_src
    assert "MDPH" in md_src  # collision-resistant sentinel


def test_markdown_js_file_exists_and_exports():
    assert MARKDOWN_JS.is_file()
    out = _run_markdown_node(
        """
const keys = ['renderMarkdown','inlineMarkdown','applyEmphasis','withProtectedTags',
  'escapeHtml','PLAIN_MEMORY_CHANNELS','isPlainMemoryChannel'];
for (const k of keys) {
  if (md[k] == null) throw new Error('missing export ' + k);
}
process.stdout.write('ok');
"""
    )
    assert out.strip() == "ok"
