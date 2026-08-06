/**
 * Glass markdown pure helpers (#88 / BUG-chat-03 / KD-MD1).
 *
 * Browser: attaches to globalThis.ElyraMarkdown (loaded before app.js).
 * Node/CJS: module.exports for hermetic pytest fixtures via node.
 *
 * Display-only. Emphasis runs after link/image/code substitution on a
 * tag-protected string so target="_blank" and href path underscores survive.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  if (root && typeof root === "object") {
    root.ElyraMarkdown = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /**
   * Protect HTML tags with placeholders, run fn, restore tags.
   * Prevents underscore/star emphasis from mangling attributes (target="_blank")
   * and path underscores inside href.
   */
  function withProtectedTags(html, fn) {
    const tags = [];
    let t = String(html == null ? "" : html).replace(/<[^>]+>/g, (m) => {
      const i = tags.length;
      tags.push(m);
      return `%%TAG${i}%%`;
    });
    t = fn(t);
    return t.replace(/%%TAG(\d+)%%/g, (_, i) => {
      const idx = Number(i);
      return Number.isFinite(idx) && tags[idx] != null ? tags[idx] : "";
    });
  }

  /** Bold/italic after links/code — tags must already be present as HTML. */
  function applyEmphasis(html) {
    return withProtectedTags(html, (t) => {
      t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      t = t.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
      t = t.replace(/__([^_]+)__/g, "<strong>$1</strong>");
      t = t.replace(/(^|[^_])_([^_]+)_/g, "$1<em>$2</em>");
      return t;
    });
  }

  /**
   * Default media/link resolver for hermetic tests (no attachment scheme).
   * Rejects javascript:; allows http(s) and data:image.
   */
  function defaultResolveMediaUrl(url) {
    const u = String(url || "").trim();
    if (!u) return null;
    if (/^javascript:/i.test(u) || /^vbscript:/i.test(u)) return null;
    if (/^https?:\/\//i.test(u)) return u;
    if (/^data:image\//i.test(u)) return u;
    return null;
  }

  /**
   * Inline markdown: escape → images → links → code → tag-protected emphasis.
   * @param {string} s
   * @param {(url: string) => string|null} [resolveMediaUrl]
   */
  function inlineMarkdown(s, resolveMediaUrl) {
    const resolve =
      typeof resolveMediaUrl === "function"
        ? resolveMediaUrl
        : defaultResolveMediaUrl;
    const escape = escapeHtml;
    let t = escape(s);

    // images ![alt](url)
    t = t.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, url) => {
      const resolved = resolve(url);
      if (!resolved) return escape(`![${alt}](${url})`);
      if (/^data:/i.test(resolved) && !/^data:image\//i.test(resolved)) {
        return escape(`![${alt}](${url})`);
      }
      return `<img class="md-img" src="${escape(resolved)}" alt="${escape(
        alt
      )}" loading="lazy" />`;
    });

    // links [text](url)
    t = t.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
      const resolved = resolve(url);
      if (!resolved || /^data:/i.test(resolved)) {
        const u = String(url || "").trim();
        if (/^https?:\/\//i.test(u)) {
          return `<a href="${escape(u)}" target="_blank" rel="noopener noreferrer">${escape(
            label
          )}</a>`;
        }
        return escape(`[${label}](${url})`);
      }
      const external = /^https?:\/\//i.test(resolved);
      if (external) {
        return `<a href="${escape(resolved)}" target="_blank" rel="noopener noreferrer">${escape(
          label
        )}</a>`;
      }
      return `<a class="md-att-link" href="${escape(
        resolved
      )}" target="_blank" rel="noopener noreferrer">${escape(label)}</a>`;
    });

    // inline code
    t = t.replace(/`([^`]+)`/g, (_, code) => `<code>${escape(code)}</code>`);
    // bold / italic — tag-protected (KD-MD1 / #88A)
    t = applyEmphasis(t);
    return t;
  }

  /**
   * Full GFM-ish markdown → HTML (parity with glass chat renderer).
   * @param {string} src
   * @param {{
   *   resolveMediaUrl?: (url: string) => string|null,
   *   renderKatexHtml?: (tex: string, display: boolean, escape: function) => string,
   * }} [opts]
   */
  function renderMarkdown(src, opts) {
    opts = opts || {};
    const resolve =
      typeof opts.resolveMediaUrl === "function"
        ? opts.resolveMediaUrl
        : defaultResolveMediaUrl;
    const katexFn =
      typeof opts.renderKatexHtml === "function" ? opts.renderKatexHtml : null;

    const raw = String(src || "");
    if (!raw.trim()) return "<p></p>";

    const fences = [];
    const math = [];
    let text = raw.replace(/\r\n/g, "\n");

    text = text.replace(/```([^\n`]*)\n([\s\S]*?)```/g, (_, lang, code) => {
      const i = fences.length;
      fences.push({
        lang: String(lang || "").trim(),
        code: code.replace(/\n$/, ""),
      });
      return `\n\n%%FENCE${i}%%\n\n`;
    });

    const pushMath = (tex, display) => {
      const i = math.length;
      math.push({ tex: String(tex || "").trim(), display: Boolean(display) });
      return `%%MATH${i}%%`;
    };
    text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => `\n\n${pushMath(tex, true)}\n\n`);
    text = text.replace(/\\\[([\s\S]+?)\\\]/g, (_, tex) => `\n\n${pushMath(tex, true)}\n\n`);
    text = text.replace(/\\\(([\s\S]+?)\\\)/g, (_, tex) => pushMath(tex, false));
    text = text.replace(
      /(?<!\\)\$(?!\$)((?:\\.|[^$\n\\\s])+)(?<!\\)\$(?!\$)/g,
      (full, tex) => {
        const body = String(tex || "").trim();
        if (!body) return full;
        if (/^\d+([.,]\d+)?$/.test(body)) return full;
        return pushMath(body, false);
      }
    );

    const escape = escapeHtml;
    const inline = (s) => inlineMarkdown(s, resolve);

    const lines = text.split("\n");
    const out = [];
    let i = 0;
    let para = [];

    const flushPara = () => {
      if (!para.length) return;
      if (para.length === 1 && /^%%MATH\d+%%$/.test(para[0])) {
        out.push(para[0]);
        para = [];
        return;
      }
      out.push(`<p>${para.map((line) => inline(line)).join("<br>")}</p>`);
      para = [];
    };

    const isTableSep = (line) =>
      /^\s*\|?[\s:-]+\|[\s|:-]+\|?\s*$/.test(line) && line.includes("-");

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      if (!trimmed) {
        flushPara();
        i += 1;
        continue;
      }

      const fenceMatch = trimmed.match(/^%%FENCE(\d+)%%$/);
      if (fenceMatch) {
        flushPara();
        const f = fences[Number(fenceMatch[1])];
        if (f) {
          const lang = f.lang ? escape(f.lang) : "";
          out.push(
            `<div class="md-code-wrap"><button type="button" class="md-copy">Copy</button><pre><code class="language-${lang}">${escape(
              f.code
            )}</code></pre></div>`
          );
        }
        i += 1;
        continue;
      }

      if (/^%%MATH\d+%%$/.test(trimmed)) {
        flushPara();
        out.push(trimmed);
        i += 1;
        continue;
      }

      if (/^---+$/.test(trimmed) || /^\*\*\*+$/.test(trimmed)) {
        flushPara();
        out.push("<hr />");
        i += 1;
        continue;
      }

      const hm = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if (hm) {
        flushPara();
        const level = hm[1].length;
        out.push(`<h${level}>${inline(hm[2])}</h${level}>`);
        i += 1;
        continue;
      }

      if (trimmed.startsWith(">")) {
        flushPara();
        const quote = [];
        while (i < lines.length && lines[i].trim().startsWith(">")) {
          quote.push(lines[i].trim().replace(/^>\s?/, ""));
          i += 1;
        }
        out.push(
          `<blockquote>${quote.map((q) => inline(q)).join("<br>")}</blockquote>`
        );
        continue;
      }

      if (
        trimmed.includes("|") &&
        i + 1 < lines.length &&
        isTableSep(lines[i + 1])
      ) {
        flushPara();
        const splitRow = (row) =>
          row
            .trim()
            .replace(/^\|/, "")
            .replace(/\|$/, "")
            .split("|")
            .map((c) => c.trim());
        const header = splitRow(lines[i]);
        i += 2;
        const bodyRows = [];
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
          bodyRows.push(splitRow(lines[i]));
          i += 1;
        }
        let table = "<table><thead><tr>";
        header.forEach((h) => {
          table += `<th>${inline(h)}</th>`;
        });
        table += "</tr></thead><tbody>";
        bodyRows.forEach((row) => {
          table += "<tr>";
          header.forEach((_, idx) => {
            table += `<td>${inline(row[idx] || "")}</td>`;
          });
          table += "</tr>";
        });
        table += "</tbody></table>";
        out.push(table);
        continue;
      }

      const ul = trimmed.match(/^[-*+]\s+(.+)$/);
      const ol = trimmed.match(/^\d+\.\s+(.+)$/);
      if (ul || ol) {
        flushPara();
        const ordered = Boolean(ol);
        const items = [];
        while (i < lines.length) {
          const t = lines[i].trim();
          const m = ordered
            ? t.match(/^\d+\.\s+(.+)$/)
            : t.match(/^[-*+]\s+(.+)$/);
          if (!m) break;
          items.push(`<li>${inline(m[1])}</li>`);
          i += 1;
        }
        out.push(
          ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`
        );
        continue;
      }

      para.push(trimmed);
      i += 1;
    }
    flushPara();

    let html = out.join("\n") || "<p></p>";
    html = html.replace(/%%MATH(\d+)%%/g, (_, idx) => {
      const m = math[Number(idx)];
      if (!m) return "";
      if (katexFn) return katexFn(m.tex, m.display, escape);
      return `<code class="md-math-fallback">${escape(m.tex)}</code>`;
    });
    return html;
  }

  /** Fixed Memory Context channels that must render as plain text (KD-MD2 / #88B). */
  const PLAIN_MEMORY_CHANNELS = Object.freeze(["system", "orient"]);

  function isPlainMemoryChannel(ch) {
    return PLAIN_MEMORY_CHANNELS.indexOf(String(ch || "")) !== -1;
  }

  return {
    escapeHtml,
    withProtectedTags,
    applyEmphasis,
    defaultResolveMediaUrl,
    inlineMarkdown,
    renderMarkdown,
    PLAIN_MEMORY_CHANNELS,
    isPlainMemoryChannel,
  };
});
