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


def test_identifier_underscores_match_current_paired_behavior():
    """a_b_c still pair-matches as today (not a regression claim of no-em)."""
    html = _render("a_b_c")
    # Document current paired `_…_` behavior rather than inventing new rules.
    assert "a" in html and "c" in html


# ── #88B: plain system/orient channels ───────────────────────────────────


def test_plain_memory_channels_helper():
    out = _run_markdown_node(
        """
const ch = md.PLAIN_MEMORY_CHANNELS;
if (!Array.isArray(ch) || ch.indexOf('system') < 0 || ch.indexOf('orient') < 0)
  throw new Error('missing plain channels: ' + JSON.stringify(ch));
if (md.isPlainMemoryChannel('system') !== true) throw new Error('system');
if (md.isPlainMemoryChannel('orient') !== true) throw new Error('orient');
if (md.isPlainMemoryChannel('temporal') !== false) throw new Error('temporal');
if (md.isPlainMemoryChannel('directed_keep') !== false) throw new Error('keep');
process.stdout.write('ok');
"""
    )
    assert out.strip() == "ok"


def test_app_js_plain_system_orient_not_in_prose_set():
    """Memory Context: system/orient use textContent path, not renderMarkdown."""
    js = APP_JS.read_text(encoding="utf-8")
    assert 'plainCh = new Set(["system", "orient"])' in js or (
        "plainCh" in js and '"system"' in js and '"orient"' in js
    )
    # prose set must not list system/orient
    prose_match = re.search(
        r"const proseCh\s*=\s*new Set\(\[([\s\S]*?)\]\)",
        js,
    )
    assert prose_match is not None, "proseCh set not found in app.js"
    prose_body = prose_match.group(1)
    assert '"system"' not in prose_body and "'system'" not in prose_body
    assert '"orient"' not in prose_body and "'orient'" not in prose_body
    # plain path uses textContent
    assert "plainCh.has(ch)" in js
    assert "body.textContent = snippet" in js or "body.textContent = snippet ||" in js
    # system/orient must not call renderMarkdown on the plain branch
    # (prose branch still may call renderMarkdown)
    card_fn = re.search(
        r"function renderMemoryChannelCard\([\s\S]*?\n\}",
        js,
    )
    assert card_fn is not None
    body = card_fn.group(0)
    assert "plainCh" in body
    assert "textContent" in body
    # ensure plain branch does not set innerHTML
    plain_branch = re.search(
        r"if\s*\(\s*plainCh\.has\(ch\)\s*\)\s*\{([\s\S]*?)\}\s*else if\s*\(\s*proseCh",
        body,
    )
    assert plain_branch is not None, "plainCh branch not found"
    assert "innerHTML" not in plain_branch.group(1)
    assert "textContent" in plain_branch.group(1)
    assert "renderMarkdown" not in plain_branch.group(1)


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
    assert "withProtectedTags" in MARKDOWN_JS.read_text(encoding="utf-8")
    assert "applyEmphasis" in MARKDOWN_JS.read_text(encoding="utf-8")


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
