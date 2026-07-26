const $ = (sel) => document.querySelector(sel);

const messagesEl = $("#messages");
const form = $("#chat-form");
const input = $("#chat-input");
const sendBtn = $("#send-btn");
const attachBtn = $("#attach-btn");
const attachInput = $("#attach-input");
const attachTray = $("#attach-tray");
const dropOverlay = $("#drop-overlay");
const jumpLatestBtn = $("#jump-latest");
const chatActivity = $("#chat-activity");
const chatActivityLabel = $("#chat-activity-label");
const chatActivityDetail = $("#chat-activity-detail");
const chatActivityTrail = $("#chat-activity-trail");
const catalogMeta = $("#catalog-meta");
const toolsCountEl = $("#tools-count");
const skillsCountEl = $("#skills-count");
const catalogRefreshBtn = $("#catalog-refresh-btn");
const statusJson = $("#status-json");
const pillLlama = $("#pill-llama");
const pillSandbox = $("#pill-sandbox");
const pillWorker = $("#pill-worker");
const pillPhase = $("#pill-phase");
const pillAutopilot = $("#pill-autopilot");
const noticeEl = $("#notice");
const waitBar = $("#wait-bar");
const waitPrompt = $("#wait-prompt");
const waitChoices = $("#wait-choices");
const goalsList = $("#goals-list");
const momentsList = $("#moments-list");
const momentDetail = $("#moment-detail");
const toolsList = $("#tools-list");
const skillsList = $("#skills-list");
const identitySelf = $("#identity-self");
const identityUser = $("#identity-user");
const continuousToggles = document.querySelectorAll(
  ".continuous-toggle:not(#usage-override-toggle)"
);
const continuousMetaEls = [$("#continuous-status-rail")].filter(Boolean);
const continuousSummary = $("#continuous-summary");
const continuousBadge = $("#continuous-badge");
const continuousDetail = $("#continuous-detail");
const resetOpenBtn = $("#reset-open-btn");
const resetModal = $("#reset-modal");
const resetConfirmInput = $("#reset-confirm-input");
const resetConfirmBtn = $("#reset-confirm-btn");
const hardStopBanner = $("#hard-stop-banner");
const providerBadge = $("#provider-badge");
const providerNameEl = $("#provider-name");
const providerModelSelect = $("#provider-model-select");
const providerCredentialSelect = $("#provider-credential-select");
const providerCredentialOk = $("#provider-credential-ok");
const providerApiKeyInput = $("#provider-api-key-input");
const providerApiKeySave = $("#provider-api-key-save");
const providerApiKeyClear = $("#provider-api-key-clear");
const providerApiKeyMeta = $("#provider-api-key-meta");
const usageBadge = $("#usage-badge");
const usageWeekPct = $("#usage-week-pct");
const usageDayPct = $("#usage-day-pct");
const usageHourPct = $("#usage-hour-pct");
const usageWeekBar = $("#usage-week-bar");
const usageDayBar = $("#usage-day-bar");
const usageHourBar = $("#usage-hour-bar");
const usageDetail = $("#usage-detail");
const usageOverrideToggle = $("#usage-override-toggle");
const usageOverrideMeta = $("#usage-override-meta");
const devSpeedToggle = $("#dev-speed-toggle");
const devSpeedMeta = $("#dev-speed-meta");
const devSpeedBadge = $("#dev-speed-badge");
const devSpeedDelay = $("#dev-speed-delay");

const USER_ID = "operator";
const REASON_BUFFER_FULL = "interjection_buffer_full";

let lastPendingWaitId = null;
let noticeTimer = null;
/** True while a wait-choice POST is in flight (blocks double-submit). */
let waitReplyInFlight = false;
/** True while PATCH /api/continuous is in flight (avoid double-toggle thrash). */
let continuousToggleInFlight = false;
/** Last known continuous.enabled from status (for toggle change detection). */
let lastContinuousEnabled = false;
/** True while PATCH /api/dev-speed is in flight. */
let devSpeedInFlight = false;
/** Last known dev_speed.enabled from status. */
let lastDevSpeedEnabled = true;
/** Last known dev_speed.delay_seconds from status. */
let lastDevSpeedDelay = 8;
/** True while POST /api/reset is in flight. */
let resetInFlight = false;
/** True while PATCH /api/provider is in flight. */
let providerPatchInFlight = false;
/** True while PUT/DELETE api-key is in flight. */
let apiKeyInFlight = false;
/** True while PATCH /api/usage (hard-stop override) is in flight. */
let usageOverrideInFlight = false;
/** Last known hard_stop_override / override_active from status. */
let lastOverrideActive = false;
/** Last known usage.hard_stop value (for transition notices). */
let lastHardStop = null;
/** False until first successful status paint (skip transition notices on boot). */
let statusPrimed = false;
/** Last known model / credential_source (for select change detection). */
let lastProviderModel = null;
let lastCredentialSource = null;
/** Active nav panel name (chat | goals | moments | tools | identity | status). */
let activePanel = "chat";
/** Currently open moment detail id (null when closed). */
let selectedMomentId = null;
/** Snapshot of open moment list fields used to decide detail re-fetch. */
let selectedMomentSnapshot = null;
/** Bumped on each load/close so stale in-flight responses are ignored. */
let momentDetailLoadGen = 0;
/** Single-flight guard so overlapping setInterval ticks do not race. */
let tickInFlight = false;
/** Fingerprint of last rendered message list (avoid scroll thrash). */
let lastMessagesFp = "";
/** True when chat viewport is near the bottom (auto-stick). */
let chatStickToBottom = true;
/** Pending multimodal attachments (UI-only; sent as inventory text). */
let pendingAttachments = [];
/** Drag depth for composer drop overlay. */
let composerDragDepth = 0;
/** Fingerprint of last rendered activity trail (animate only on change). */
let lastActivityTrailFp = "";
/** Ordered event ids currently shown in the activity trail (oldest → newest). */
let activityTrailIds = [];

function setPill(el, label, mode) {
  el.textContent = label;
  el.classList.remove("pill-on", "pill-off", "pill-busy");
  el.classList.add(mode);
}

function showNotice(text, { sticky = false } = {}) {
  noticeEl.hidden = false;
  noticeEl.textContent = text;
  if (noticeTimer) clearTimeout(noticeTimer);
  if (!sticky) {
    noticeTimer = setTimeout(() => {
      noticeEl.hidden = true;
      noticeEl.textContent = "";
    }, 8000);
  }
}

async function fetchJson(url, opts) {
  const res = await fetch(url, opts);
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const msg =
      (data && (data.error || data.reason)) || text || res.statusText;
    const err = new Error(`${res.status}: ${msg}`);
    err.status = res.status;
    err.body = data;
    throw err;
  }
  return data;
}

function messagesFingerprint(messages) {
  if (!messages || !messages.length) return "empty";
  const last = messages[messages.length - 1] || {};
  return `${messages.length}|${last.id || ""}|${(last.content || "").length}|${
    last.created_at || ""
  }|${(last.reasoning || "").length}`;
}

function formatMsgTime(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return String(iso);
  }
}

function isNearBottom(el, threshold = 80) {
  if (!el) return true;
  return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
}

function scrollMessagesToBottom({ smooth = false } = {}) {
  if (!messagesEl) return;
  if (smooth) {
    messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: "smooth" });
  } else {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
  chatStickToBottom = true;
  if (jumpLatestBtn) jumpLatestBtn.hidden = true;
}

function updateJumpLatestVisibility() {
  if (!jumpLatestBtn || !messagesEl) return;
  jumpLatestBtn.hidden = isNearBottom(messagesEl);
}

/**
 * Safe markdown → HTML for chat glass (GFM-ish subset).
 * Escapes first; allows headings, emphasis, lists, quotes, code, tables, links.
 */
function renderMarkdown(src) {
  const raw = String(src || "");
  if (!raw.trim()) return "<p></p>";

  const fences = [];
  let text = raw.replace(/\r\n/g, "\n");
  text = text.replace(/```([^\n`]*)\n([\s\S]*?)```/g, (_, lang, code) => {
    const i = fences.length;
    fences.push({ lang: String(lang || "").trim(), code: code.replace(/\n$/, "") });
    return `\n\n%%FENCE${i}%%\n\n`;
  });

  const escape = (s) =>
    String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const inline = (s) => {
    let t = escape(s);
    // images ![alt](url) — only http(s) or data:image
    t = t.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, url) => {
      const u = url.trim();
      if (!/^(https?:|data:image\/)/i.test(u)) return escape(`![${alt}](${url})`);
      return `<img src="${escape(u)}" alt="${escape(alt)}" loading="lazy" style="max-width:100%;border-radius:8px;margin:0.35rem 0" />`;
    });
    // links [text](url)
    t = t.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
      const u = url.trim();
      if (!/^https?:\/\//i.test(u)) return escape(`[${label}](${url})`);
      return `<a href="${escape(u)}" target="_blank" rel="noopener noreferrer">${escape(label)}</a>`;
    });
    // inline code
    t = t.replace(/`([^`]+)`/g, (_, code) => `<code>${escape(code)}</code>`);
    // bold / italic
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
    t = t.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    t = t.replace(/(^|[^_])_([^_]+)_/g, "$1<em>$2</em>");
    return t;
  };

  const lines = text.split("\n");
  const out = [];
  let i = 0;
  let para = [];

  const flushPara = () => {
    if (!para.length) return;
    out.push(`<p>${inline(para.join(" "))}</p>`);
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
      out.push(`<blockquote>${inline(quote.join(" "))}</blockquote>`);
      continue;
    }

    // GFM table
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
      i += 2; // skip header + separator
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

    // lists
    const ul = trimmed.match(/^[-*+]\s+(.+)$/);
    const ol = trimmed.match(/^\d+\.\s+(.+)$/);
    if (ul || ol) {
      flushPara();
      const ordered = Boolean(ol);
      const items = [];
      while (i < lines.length) {
        const t = lines[i].trim();
        const m = ordered ? t.match(/^\d+\.\s+(.+)$/) : t.match(/^[-*+]\s+(.+)$/);
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
  return out.join("\n") || "<p></p>";
}

function wireMessageBodyInteractions(root) {
  if (!root) return;
  root.querySelectorAll(".md-copy").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const codeEl = btn.parentElement && btn.parentElement.querySelector("code");
      const text = codeEl ? codeEl.textContent || "" : "";
      try {
        await navigator.clipboard.writeText(text);
        const prev = btn.textContent;
        btn.textContent = "Copied";
        setTimeout(() => {
          btn.textContent = prev || "Copy";
        }, 1200);
      } catch {
        showNotice("Copy failed — select the code manually.");
      }
    });
  });
}

function renderMessages(messages, { force = false } = {}) {
  if (!messagesEl) return;
  const list = Array.isArray(messages) ? messages : [];
  const fp = messagesFingerprint(list);
  if (!force && fp === lastMessagesFp) {
    updateJumpLatestVisibility();
    return;
  }
  const stick = chatStickToBottom || isNearBottom(messagesEl);
  lastMessagesFp = fp;
  messagesEl.innerHTML = "";
  for (const m of list) {
    const div = document.createElement("div");
    const role = m.role === "user" ? "user" : "assistant";
    div.className = `msg ${role}`;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML = `<span class="role-chip">${escapeHtml(
      role
    )}</span><span>${escapeHtml(formatMsgTime(m.created_at))}</span>`;
    div.appendChild(meta);
    const body = document.createElement("div");
    body.className = "msg-body";
    body.innerHTML = renderMarkdown(m.content || "");
    div.appendChild(body);
    wireMessageBodyInteractions(body);
    if (m.reasoning) {
      const details = document.createElement("details");
      details.className = "reason-fold";
      const summary = document.createElement("summary");
      summary.textContent = "reasoning";
      details.appendChild(summary);
      const r = document.createElement("div");
      r.className = "reason";
      r.textContent = m.reasoning;
      details.appendChild(r);
      div.appendChild(details);
    }
    messagesEl.appendChild(div);
  }
  if (stick) scrollMessagesToBottom();
  else updateJumpLatestVisibility();
}

function setWaitChoicesDisabled(disabled) {
  waitChoices.querySelectorAll("button.choice-btn").forEach((btn) => {
    btn.disabled = disabled;
  });
}

function renderWaitBar(pending) {
  if (!pending || pending.status !== "pending") {
    waitBar.hidden = true;
    waitChoices.innerHTML = "";
    waitPrompt.textContent = "";
    lastPendingWaitId = null;
    waitReplyInFlight = false;
    return;
  }
  const wid = pending.id || pending.wait_id || "";
  const choices = Array.isArray(pending.choices) ? pending.choices : [];
  waitBar.hidden = false;
  waitPrompt.textContent = pending.prompt
    ? `Waiting: ${pending.prompt}`
    : "Waiting for your reply…";

  // Rebuild buttons only when wait id changes (avoid focus thrash).
  if (wid !== lastPendingWaitId) {
    lastPendingWaitId = wid;
    waitReplyInFlight = false;
    waitChoices.innerHTML = "";
    for (const c of choices) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "choice-btn";
      btn.textContent = c;
      btn.addEventListener("click", () => sendWaitChoice(c));
      waitChoices.appendChild(btn);
    }
    if (!choices.length) {
      const hint = document.createElement("span");
      hint.className = "muted";
      hint.textContent = "Free-text reply via the composer.";
      waitChoices.appendChild(hint);
    }
  }
  // Keep buttons disabled while a reply is in flight (even if poll re-renders).
  if (waitReplyInFlight) {
    setWaitChoicesDisabled(true);
  }
}

async function sendWaitChoice(choice) {
  if (waitReplyInFlight) return;
  waitReplyInFlight = true;
  setWaitChoicesDisabled(true);
  try {
    const data = await fetchJson("/api/wait/reply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ choice, user_id: USER_ID }),
    });
    if (data && data.routed && data.routed !== "wait_reply") {
      showNotice(`Wait reply routed as ${data.routed} (wait may have already cleared).`);
    }
    await Promise.all([refreshMessages(), refreshStatus()]);
  } catch (err) {
    waitReplyInFlight = false;
    setWaitChoicesDisabled(false);
    showNotice(String(err.message || err));
  }
}

async function refreshMessages(opts = {}) {
  const data = await fetchJson("/api/messages?limit=200");
  renderMessages(data.messages || [], opts);
}

function formatContinuousMeta(c) {
  if (!c || !c.enabled) return "off";
  const streak = Number(c.streak) || 0;
  const max = Number(c.max_streak) || 0;
  const pending = Number(c.pending_moment_continues) || 0;
  const parts = [`streak ${streak}/${max}`];
  if (pending > 0) parts.push(`pending ${pending}`);
  if (c.last_skip_reason) parts.push(`skip ${c.last_skip_reason}`);
  return parts.join(" · ");
}

function formatPctRemaining(frac) {
  if (frac == null || Number.isNaN(Number(frac))) return "—";
  const pct = Math.max(0, Math.min(100, Math.round(Number(frac) * 100)));
  return `${pct}%`;
}

function setUsageBar(barEl, frac) {
  if (!barEl) return;
  const f = Math.max(0, Math.min(1, Number(frac) || 0));
  barEl.style.width = `${Math.round(f * 100)}%`;
  barEl.classList.remove("usage-bar-warn", "usage-bar-crit");
  if (f <= 0.05) barEl.classList.add("usage-bar-crit");
  else if (f <= 0.2) barEl.classList.add("usage-bar-warn");
}

/**
 * Provider-aware rail pill (keeps id pill-llama for less churn).
 * xai ready/busy/auth/limit/ovrd; local llama; stub.
 */
function renderProviderPill(s) {
  if (!pillLlama) return;
  const provider = (s && s.provider) || null;
  const usage = (s && s.usage) || {};
  const hardStop = usage.hard_stop || null;
  const overrideActive = Boolean(usage.override_active);
  const credentialOk = s && s.credential_ok !== false;
  const workerBusy = Boolean(s && s.worker_busy);
  const phase = (s && s.phase) || "";
  const busy = workerBusy || phase === "in_moment";

  // Legacy / no provider field: fall back to llama-centric display.
  if (!provider) {
    if (s && s.llama_ready) {
      setPill(
        pillLlama,
        s.llama_busy ? "llama busy" : "llama ready",
        s.llama_busy ? "pill-busy" : "pill-on"
      );
    } else if (s && s.llama_error === "stub_llm") {
      setPill(pillLlama, "stub llm", "pill-off");
    } else {
      setPill(
        pillLlama,
        s && s.llama_error ? "llama error" : "llama off",
        "pill-off"
      );
    }
    return;
  }

  if (provider === "local") {
    if (s.llama_ready) {
      setPill(
        pillLlama,
        s.llama_busy || busy ? "llama busy" : "llama ready",
        s.llama_busy || busy ? "pill-busy" : "pill-on"
      );
    } else if (s.llama_error === "stub_llm") {
      setPill(pillLlama, "stub llm", "pill-off");
    } else {
      setPill(
        pillLlama,
        s.llama_error ? "llama error" : "llama off",
        "pill-off"
      );
    }
    return;
  }

  // xai (and any non-local remote)
  if (s.llama_error === "stub_llm" && !s.credential_ok && !s.model) {
    setPill(pillLlama, "stub llm", "pill-off");
    return;
  }
  if (!credentialOk) {
    setPill(pillLlama, `${provider} auth`, "pill-off");
    return;
  }
  if (hardStop && !overrideActive) {
    setPill(pillLlama, `${provider} limit`, "pill-off");
    return;
  }
  if (hardStop && overrideActive) {
    setPill(pillLlama, `${provider} ovrd`, "pill-busy");
    return;
  }
  setPill(
    pillLlama,
    busy ? `${provider} busy` : `${provider} ready`,
    busy ? "pill-busy" : "pill-on"
  );
}

function renderHardStopBanner(s) {
  if (!hardStopBanner) return;
  const usage = (s && s.usage) || {};
  const hardStop = usage.hard_stop || null;
  const overrideActive = Boolean(usage.override_active);
  const credentialOk = !(s && s.provider === "xai" && s.credential_ok === false);

  if (!credentialOk) {
    hardStopBanner.hidden = false;
    hardStopBanner.className = "hard-stop-banner hard-stop-auth";
    const detail = (s && s.credential_detail) || "credential missing";
    hardStopBanner.textContent = `Auth paused — ${detail}. Model moments will not open until credentials resolve.`;
    return;
  }

  if (hardStop && !overrideActive) {
    hardStopBanner.hidden = false;
    hardStopBanner.className = "hard-stop-banner hard-stop-limit";
    const reason = usage.hard_stop_reason || hardStop;
    hardStopBanner.textContent = `Usage hard stop (${hardStop}) — queue paused for budget. ${reason}`;
    return;
  }

  if (hardStop && overrideActive) {
    hardStopBanner.hidden = false;
    hardStopBanner.className = "hard-stop-banner hard-stop-override";
    hardStopBanner.textContent = `Over budget (${hardStop}) — hard-stop override ON. Model calls continue; usage still recorded.`;
    return;
  }

  hardStopBanner.hidden = true;
  hardStopBanner.textContent = "";
  hardStopBanner.className = "hard-stop-banner";
}

function fillModelSelect(models, current) {
  if (!providerModelSelect) return;
  const list = Array.isArray(models) ? models.slice() : [];
  if (current && !list.includes(current)) list.unshift(current);
  const prev = providerModelSelect.value;
  // Skip rebuild if options + selection unchanged (preserve focus).
  const existing = Array.from(providerModelSelect.options).map((o) => o.value);
  const same =
    existing.length === list.length &&
    existing.every((v, i) => v === list[i]) &&
    providerModelSelect.value === (current || "");
  if (same) return;
  providerModelSelect.innerHTML = "";
  if (!list.length) {
    const opt = document.createElement("option");
    opt.value = current || "";
    opt.textContent = current || "—";
    providerModelSelect.appendChild(opt);
  } else {
    for (const mid of list) {
      const opt = document.createElement("option");
      opt.value = mid;
      opt.textContent = mid;
      providerModelSelect.appendChild(opt);
    }
  }
  const pick = current || prev || (list[0] || "");
  if (pick) providerModelSelect.value = pick;
}

function renderProviderCard(s) {
  const provider = (s && s.provider) || null;
  if (providerNameEl) {
    providerNameEl.textContent = provider
      ? `${provider}${s.model_label ? ` · ${s.model_label}` : s.model ? ` · ${s.model}` : ""}`
      : "— (legacy)";
  }
  if (providerBadge) {
    if (!provider) {
      providerBadge.textContent = "legacy";
      providerBadge.classList.remove("badge-open", "badge-bad");
    } else if (s.credential_ok) {
      providerBadge.textContent = "ok";
      providerBadge.classList.add("badge-open");
      providerBadge.classList.remove("badge-bad");
    } else {
      providerBadge.textContent = "auth";
      providerBadge.classList.remove("badge-open");
      providerBadge.classList.add("badge-bad");
    }
  }
  if (!providerPatchInFlight) {
    fillModelSelect(s && s.models_available, s && s.model);
    lastProviderModel = (s && s.model) || null;
    if (providerCredentialSelect && s && s.credential_source) {
      providerCredentialSelect.value = s.credential_source;
      lastCredentialSource = s.credential_source;
    }
  }
  if (providerCredentialOk) {
    if (!provider) {
      providerCredentialOk.textContent = "—";
    } else {
      const ok = Boolean(s.credential_ok);
      const detail = s.credential_detail || "";
      const email = s.credential_email || "";
      const parts = [ok ? "yes" : "no"];
      if (detail) parts.push(detail);
      if (email) parts.push(email);
      if (s.credential_expires_at) parts.push(`exp ${s.credential_expires_at}`);
      providerCredentialOk.textContent = parts.join(" · ");
      providerCredentialOk.classList.toggle("status-ok", ok);
      providerCredentialOk.classList.toggle("status-bad", !ok);
    }
  }
  if (providerApiKeyMeta) {
    const configured = Boolean(s && s.api_key_configured);
    providerApiKeyMeta.textContent = configured
      ? "API key configured (secret not shown)"
      : "not configured";
  }
}

function renderUsageCard(s) {
  const usage = (s && s.usage) || null;
  const enabled = Boolean(usage && usage.enabled);
  const overrideActive = Boolean(usage && usage.override_active);
  const hardStop = (usage && usage.hard_stop) || null;

  if (usageBadge) {
    if (!usage) {
      usageBadge.textContent = "n/a";
      usageBadge.classList.remove("badge-open", "badge-bad");
    } else if (!enabled) {
      usageBadge.textContent = "off";
      usageBadge.classList.remove("badge-open", "badge-bad");
    } else if (hardStop && !overrideActive) {
      usageBadge.textContent = `stop · ${hardStop}`;
      usageBadge.classList.remove("badge-open");
      usageBadge.classList.add("badge-bad");
    } else if (hardStop && overrideActive) {
      usageBadge.textContent = "override";
      usageBadge.classList.add("badge-open");
      usageBadge.classList.remove("badge-bad");
    } else {
      usageBadge.textContent = "ok";
      usageBadge.classList.add("badge-open");
      usageBadge.classList.remove("badge-bad");
    }
  }

  const week = usage ? usage.week_remaining_fraction : null;
  const day = usage ? usage.day_remaining_fraction : null;
  const hour = usage ? usage.hour_remaining_fraction : null;
  if (usageWeekPct) usageWeekPct.textContent = formatPctRemaining(week);
  if (usageDayPct) usageDayPct.textContent = formatPctRemaining(day);
  if (usageHourPct) usageHourPct.textContent = formatPctRemaining(hour);
  setUsageBar(usageWeekBar, week);
  setUsageBar(usageDayBar, day);
  setUsageBar(usageHourBar, hour);

  if (usageDetail) {
    if (!usage) {
      usageDetail.textContent = "Usage meter not bound.";
    } else if (!enabled) {
      usageDetail.textContent = "Usage meter disabled.";
    } else {
      const parts = [];
      if (usage.week_used_tokens != null) {
        parts.push(
          `week ${usage.week_used_tokens}/${usage.week_limit_tokens ?? "—"}`
        );
      }
      if (usage.day_used_tokens != null) {
        parts.push(
          `day ${usage.day_used_tokens}/${usage.day_limit_tokens ?? "—"}`
        );
      }
      if (usage.hour_used_tokens != null) {
        parts.push(
          `hour ${usage.hour_used_tokens}/${usage.hour_limit_tokens ?? "—"}`
        );
      }
      if (usage.last_record_at) parts.push(`last ${usage.last_record_at}`);
      usageDetail.textContent = parts.length ? parts.join(" · ") : "no usage yet";
    }
  }

  if (usageOverrideToggle && !usageOverrideInFlight) {
    usageOverrideToggle.checked = overrideActive;
    usageOverrideToggle.disabled = !enabled;
  }
  lastOverrideActive = overrideActive;
  if (usageOverrideMeta) {
    usageOverrideMeta.textContent = overrideActive
      ? "override ON"
      : "default off";
  }
}

async function patchProvider(body) {
  if (providerPatchInFlight) return;
  providerPatchInFlight = true;
  if (providerModelSelect) providerModelSelect.disabled = true;
  if (providerCredentialSelect) providerCredentialSelect.disabled = true;
  try {
    await fetchJson("/api/provider", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await refreshStatus();
  } catch (err) {
    if (providerModelSelect && lastProviderModel != null) {
      providerModelSelect.value = lastProviderModel;
    }
    if (providerCredentialSelect && lastCredentialSource != null) {
      providerCredentialSelect.value = lastCredentialSource;
    }
    showNotice(String(err.message || err));
  } finally {
    providerPatchInFlight = false;
    if (providerModelSelect) providerModelSelect.disabled = false;
    if (providerCredentialSelect) providerCredentialSelect.disabled = false;
  }
}

async function saveApiKey() {
  if (apiKeyInFlight || !providerApiKeyInput) return;
  const api_key = providerApiKeyInput.value.trim();
  if (!api_key) {
    showNotice("API key required.");
    return;
  }
  apiKeyInFlight = true;
  if (providerApiKeySave) providerApiKeySave.disabled = true;
  if (providerApiKeyClear) providerApiKeyClear.disabled = true;
  try {
    await fetchJson("/api/provider/api-key", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key }),
    });
    // Never re-display secret.
    providerApiKeyInput.value = "";
    showNotice("API key saved.");
    await refreshStatus();
  } catch (err) {
    showNotice(String(err.message || err));
  } finally {
    apiKeyInFlight = false;
    if (providerApiKeySave) providerApiKeySave.disabled = false;
    if (providerApiKeyClear) providerApiKeyClear.disabled = false;
  }
}

async function clearApiKey() {
  if (apiKeyInFlight) return;
  apiKeyInFlight = true;
  if (providerApiKeySave) providerApiKeySave.disabled = true;
  if (providerApiKeyClear) providerApiKeyClear.disabled = true;
  try {
    await fetchJson("/api/provider/api-key", { method: "DELETE" });
    if (providerApiKeyInput) providerApiKeyInput.value = "";
    showNotice("API key cleared.");
    await refreshStatus();
  } catch (err) {
    showNotice(String(err.message || err));
  } finally {
    apiKeyInFlight = false;
    if (providerApiKeySave) providerApiKeySave.disabled = false;
    if (providerApiKeyClear) providerApiKeyClear.disabled = false;
  }
}

async function setHardStopOverride(active) {
  if (usageOverrideInFlight) return;
  usageOverrideInFlight = true;
  if (usageOverrideToggle) {
    usageOverrideToggle.disabled = true;
    usageOverrideToggle.checked = active;
  }
  try {
    await fetchJson("/api/usage", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hard_stop_override: Boolean(active) }),
    });
    if (active) {
      showNotice("Hard-stop override ON — model calls continue past budget.");
    }
    await refreshStatus();
  } catch (err) {
    if (usageOverrideToggle) usageOverrideToggle.checked = lastOverrideActive;
    showNotice(String(err.message || err));
  } finally {
    usageOverrideInFlight = false;
    if (usageOverrideToggle) usageOverrideToggle.disabled = false;
  }
}

function maybeNoticeHardStopTransition(s) {
  const usage = (s && s.usage) || {};
  const hardStop = usage.hard_stop || null;
  const overrideActive = Boolean(usage.override_active);
  // First paint: seed without notice.
  if (!statusPrimed) {
    lastHardStop = hardStop;
    lastOverrideActive = overrideActive;
    statusPrimed = true;
    return;
  }
  if (hardStop && hardStop !== lastHardStop && !overrideActive) {
    showNotice(
      `Usage hard stop (${hardStop}) — queue paused until budget resets or override is enabled.`,
      { sticky: true }
    );
  }
  lastHardStop = hardStop;
}

function renderContinuous(s) {
  const c = (s && s.continuous) || {};
  const enabled = Boolean(c.enabled);
  lastContinuousEnabled = enabled;

  if (!continuousToggleInFlight) {
    continuousToggles.forEach((el) => {
      el.checked = enabled;
    });
  }

  const meta = formatContinuousMeta(c);
  continuousMetaEls.forEach((el) => {
    el.textContent = meta;
  });

  if (continuousSummary) {
    continuousSummary.hidden = false;
    if (continuousBadge) {
      continuousBadge.textContent = enabled ? "on" : "off";
      continuousBadge.classList.toggle("badge-open", enabled);
    }
    if (continuousDetail) {
      const lines = [
        `enabled: ${enabled}`,
        `streak: ${c.streak ?? 0} / ${c.max_streak ?? "—"}`,
        `cooldown: ${c.cooldown_seconds ?? "—"}s`,
        `pending continues: ${c.pending_moment_continues ?? 0}`,
        `last enqueue: ${c.last_enqueue_at || "—"}`,
        `last skip: ${c.last_skip_reason || "—"}`,
      ];
      continuousDetail.textContent = lines.join(" · ");
    }
  }

  // Optional autopilot pill: on when continuous enabled; busy if pending continue.
  if (pillAutopilot) {
    if (enabled) {
      pillAutopilot.hidden = false;
      const pending = Number(c.pending_moment_continues) || 0;
      if (pending > 0) {
        setPill(pillAutopilot, `autopilot · ${pending}`, "pill-busy");
      } else {
        setPill(pillAutopilot, "autopilot", "pill-on");
      }
    } else {
      pillAutopilot.hidden = true;
      setPill(pillAutopilot, "autopilot", "pill-off");
    }
  }
}

async function setContinuousEnabled(enabled) {
  if (continuousToggleInFlight) return;
  continuousToggleInFlight = true;
  continuousToggles.forEach((el) => {
    el.disabled = true;
    el.checked = enabled;
  });
  try {
    const data = await fetchJson("/api/continuous", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: Boolean(enabled) }),
    });
    const cancelled = (data && data.cancelled_moment_continues) || [];
    if (!enabled && cancelled.length) {
      showNotice(
        `Continuous off — cancelled ${cancelled.length} pending continue${
          cancelled.length === 1 ? "" : "s"
        }.`
      );
    }
    await refreshStatus();
  } catch (err) {
    continuousToggles.forEach((el) => {
      el.checked = lastContinuousEnabled;
    });
    showNotice(String(err.message || err));
  } finally {
    continuousToggleInFlight = false;
    continuousToggles.forEach((el) => {
      el.disabled = false;
    });
  }
}

function renderDevSpeed(s) {
  const d = (s && s.dev_speed) || {};
  const enabled = d.enabled !== undefined ? Boolean(d.enabled) : true;
  const delay =
    typeof d.delay_seconds === "number" && !Number.isNaN(d.delay_seconds)
      ? d.delay_seconds
      : 8;
  lastDevSpeedEnabled = enabled;
  lastDevSpeedDelay = delay;

  if (!devSpeedInFlight) {
    if (devSpeedToggle) devSpeedToggle.checked = enabled;
    if (devSpeedDelay && document.activeElement !== devSpeedDelay) {
      devSpeedDelay.value = String(Math.round(delay));
    }
  }
  if (devSpeedBadge) {
    devSpeedBadge.textContent = enabled ? "on" : "off";
    devSpeedBadge.classList.toggle("badge-open", enabled);
  }
  if (devSpeedMeta) {
    devSpeedMeta.textContent = enabled
      ? `${delay}s between hops`
      : "off — full speed";
  }
}

async function patchDevSpeed(body) {
  if (devSpeedInFlight) return;
  devSpeedInFlight = true;
  if (devSpeedToggle) devSpeedToggle.disabled = true;
  if (devSpeedDelay) devSpeedDelay.disabled = true;
  try {
    await fetchJson("/api/dev-speed", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await refreshStatus();
  } catch (err) {
    if (devSpeedToggle) devSpeedToggle.checked = lastDevSpeedEnabled;
    if (devSpeedDelay) devSpeedDelay.value = String(lastDevSpeedDelay);
    showNotice(String(err.message || err));
  } finally {
    devSpeedInFlight = false;
    if (devSpeedToggle) devSpeedToggle.disabled = false;
    if (devSpeedDelay) devSpeedDelay.disabled = false;
  }
}

/**
 * Sandbox pill (KD27): ready / warming / unusable next to provider pill.
 * No secrets, no host paths — only coarse states from status.sandbox.
 */
function renderSandboxPill(s) {
  if (!pillSandbox) return;
  const box = (s && s.sandbox) || null;
  if (!box) {
    setPill(pillSandbox, "sandbox", "pill-off");
    return;
  }
  const pill = box.pill || null;
  if (pill === "ready" || box.ready) {
    setPill(pillSandbox, "sandbox ready", "pill-on");
  } else if (box.reason === "pyenv_not_ready") {
    // Mount OK; curated guest packages still missing — tools may still work.
    setPill(pillSandbox, "sandbox pyenv…", "pill-busy");
  } else if (pill === "warming" || box.reason === "warming") {
    setPill(pillSandbox, "sandbox warming", "pill-busy");
  } else if (pill === "off" || box.isolation_enabled === false) {
    setPill(pillSandbox, "sandbox off", "pill-off");
  } else {
    // unusable / client_unusable / degraded after warm
    setPill(pillSandbox, "sandbox unusable", "pill-off");
  }
}

async function refreshStatus() {
  const s = await fetchJson("/api/status");
  statusJson.textContent = JSON.stringify(s, null, 2);

  renderProviderPill(s);
  renderSandboxPill(s);
  renderHardStopBanner(s);
  renderProviderCard(s);
  renderUsageCard(s);
  maybeNoticeHardStopTransition(s);

  // When hard-stopped without override, surface queue pause on worker pill.
  const usage = s.usage || {};
  const queuePaused =
    Boolean(usage.hard_stop) && !usage.override_active;
  const authPaused = s.provider === "xai" && s.credential_ok === false;

  if (s.worker_busy) {
    setPill(pillWorker, "worker busy", "pill-busy");
  } else if (queuePaused) {
    setPill(pillWorker, "queue paused", "pill-off");
  } else if (authPaused) {
    setPill(pillWorker, "auth paused", "pill-off");
  } else if (s.worker_pending > 0) {
    setPill(pillWorker, `queue ${s.worker_pending}`, "pill-busy");
  } else {
    setPill(pillWorker, "worker idle", "pill-on");
  }

  const phase = s.phase || "—";
  let phaseMode = "pill-on";
  if (phase === "in_moment") phaseMode = "pill-busy";
  else if (phase === "waiting") phaseMode = "pill-busy";
  setPill(pillPhase, phase, phaseMode);

  updateChatActivity(s);
  renderContinuous(s);
  renderDevSpeed(s);
  renderWaitBar(s.pending_wait || null);
  return s;
}

function renderGoals(goals) {
  goalsList.innerHTML = "";
  if (!goals.length) {
    goalsList.innerHTML = `<p class="muted empty">No goals yet.</p>`;
    return;
  }
  for (const g of goals) {
    const card = document.createElement("article");
    card.className = "card";
    const head = document.createElement("div");
    head.className = "card-head";
    head.innerHTML = `<strong>${escapeHtml(g.title || g.id)}</strong>
      <span class="badge">${escapeHtml(g.status || "?")}</span>`;
    card.appendChild(head);
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${g.id || ""} · updated ${g.updated_at || "—"}`;
    card.appendChild(meta);
    if (g.acceptance) {
      const acc = document.createElement("p");
      acc.className = "muted";
      acc.textContent = g.acceptance;
      card.appendChild(acc);
    }
    const tasks = Array.isArray(g.tasks) ? g.tasks : [];
    if (tasks.length) {
      const ul = document.createElement("ul");
      ul.className = "task-list";
      for (const t of tasks) {
        const li = document.createElement("li");
        li.textContent = `${t.status || "?"} · ${t.title || t.id}`;
        ul.appendChild(li);
      }
      card.appendChild(ul);
    }
    goalsList.appendChild(card);
  }
}

async function refreshGoals() {
  const data = await fetchJson("/api/goals");
  renderGoals(data.goals || []);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function momentListSnapshot(m, beatCount) {
  if (!m) return null;
  return {
    id: m.id || null,
    hop_count: m.hop_count ?? 0,
    ended_at: m.ended_at || null,
    stop_reason: m.stop_reason || null,
    why_now: m.why_now || null,
    beat_count: beatCount != null ? beatCount : m.beat_count ?? null,
  };
}

function momentSnapshotChanged(prev, next) {
  if (!prev || !next) return true;
  return (
    prev.id !== next.id ||
    prev.hop_count !== next.hop_count ||
    prev.ended_at !== next.ended_at ||
    prev.stop_reason !== next.stop_reason ||
    prev.why_now !== next.why_now ||
    prev.beat_count !== next.beat_count
  );
}

/** True when last successful snapshot is for this moment id (not a prior card). */
function hasLastGoodFor(id) {
  return Boolean(selectedMomentSnapshot && selectedMomentSnapshot.id === id);
}

function renderMoments(moments) {
  momentsList.innerHTML = "";
  if (!moments.length) {
    momentsList.innerHTML = `<p class="muted empty">No moments yet.</p>`;
    return;
  }
  for (const m of moments) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "card card-btn";
    card.dataset.momentId = m.id;
    if (selectedMomentId && m.id === selectedMomentId) {
      card.classList.add("card-selected");
    }
    const open = !m.ended_at;
    card.innerHTML = `
      <div class="card-head">
        <strong>${escapeHtml(m.why_now || m.id)}</strong>
        <span class="badge ${open ? "badge-open" : ""}">${escapeHtml(
          open ? "open" : m.stop_reason || "closed"
        )}</span>
      </div>
      <div class="meta">${escapeHtml(m.id)} · hops ${m.hop_count ?? 0} · ${escapeHtml(
        m.started_at || ""
      )}</div>`;
    card.addEventListener("click", () => loadMomentDetail(m.id));
    momentsList.appendChild(card);
  }
}

function renderBeats(beats) {
  const wrap = document.createElement("div");
  wrap.className = "beats";
  if (!beats.length) {
    wrap.innerHTML = `<p class="muted">No beats.</p>`;
    return wrap;
  }
  // Tape is chronological (oldest first); show newest at top for inspection.
  const ordered = beats.slice().reverse();
  for (const b of ordered) {
    const row = document.createElement("div");
    row.className = "beat";
    const type = b.type || "beat";
    const head = document.createElement("div");
    head.className = "beat-head";
    head.innerHTML = `<span class="badge">${escapeHtml(type)}</span>
      <span class="meta">${escapeHtml(b.ts || "")}</span>`;
    row.appendChild(head);

    // Reasoning collapsed.
    if (b.reasoning) {
      const details = document.createElement("details");
      details.className = "reason-fold";
      const summary = document.createElement("summary");
      summary.textContent = "reasoning";
      details.appendChild(summary);
      const pre = document.createElement("pre");
      pre.className = "beat-body";
      pre.textContent = b.reasoning;
      details.appendChild(pre);
      row.appendChild(details);
    }

    const bodyBits = [];
    if (b.content) bodyBits.push(b.content);
    if (b.name) bodyBits.push(`tool: ${b.name}`);
    if (b.tool) bodyBits.push(`tool: ${b.tool}`);
    if (b.error_reason) bodyBits.push(`error: ${b.error_reason}`);
    if (b.stop_reason) bodyBits.push(`stop: ${b.stop_reason}`);
    if (b.payload != null && typeof b.payload === "object") {
      try {
        bodyBits.push(JSON.stringify(b.payload, null, 2).slice(0, 1200));
      } catch {
        /* ignore */
      }
    }
    // Fallback dump of remaining keys (lean).
    if (!bodyBits.length) {
      const skip = new Set(["type", "ts", "reasoning"]);
      const rest = {};
      for (const [k, v] of Object.entries(b)) {
        if (!skip.has(k)) rest[k] = v;
      }
      if (Object.keys(rest).length) {
        try {
          bodyBits.push(JSON.stringify(rest, null, 2).slice(0, 1200));
        } catch {
          /* ignore */
        }
      }
    }
    if (bodyBits.length) {
      const pre = document.createElement("pre");
      pre.className = "beat-body";
      pre.textContent = bodyBits.join("\n");
      row.appendChild(pre);
    }
    wrap.appendChild(row);
  }
  return wrap;
}

function closeMomentDetail() {
  momentDetailLoadGen += 1;
  selectedMomentId = null;
  selectedMomentSnapshot = null;
  momentDetail.hidden = true;
  momentDetail.innerHTML = "";
  // Clear selected highlight without full re-fetch.
  momentsList
    .querySelectorAll(".card-selected")
    .forEach((el) => el.classList.remove("card-selected"));
}

function renderMomentDetailChrome(titleHtml, bodyNode) {
  momentDetail.innerHTML = "";
  const head = document.createElement("div");
  head.className = "card-head";
  head.innerHTML = `${titleHtml}
    <button type="button" class="link-btn" id="close-moment-detail">close</button>`;
  momentDetail.appendChild(head);
  if (bodyNode) momentDetail.appendChild(bodyNode);
  const closeBtn = $("#close-moment-detail");
  if (closeBtn) closeBtn.addEventListener("click", closeMomentDetail);
}

function captureMomentDetailUi() {
  return {
    scrollTop: momentDetail.scrollTop,
    openFolds: Array.from(
      momentDetail.querySelectorAll("details.reason-fold")
    ).map((d) => d.open),
  };
}

function restoreMomentDetailUi(saved) {
  if (!saved) return;
  const details = momentDetail.querySelectorAll("details.reason-fold");
  // Newest-first display: new beats land at the top, so pad fold state from the front.
  let folds = Array.isArray(saved.openFolds) ? saved.openFolds.slice() : [];
  if (folds.length < details.length) {
    folds = Array(details.length - folds.length)
      .fill(false)
      .concat(folds);
  } else if (folds.length > details.length) {
    folds = folds.slice(0, details.length);
  }
  details.forEach((d, i) => {
    if (folds[i]) d.open = true;
  });
  // Keep viewport near the top (newest activity) after soft rebuilds.
  momentDetail.scrollTop = 0;
}

/**
 * Load / soft-refresh moment detail.
 * @param {string} id
 * @param {{ soft?: boolean }} opts soft=true: no loading wipe; preserve scroll/folds
 */
async function loadMomentDetail(id, opts = {}) {
  const soft = Boolean(opts && opts.soft);
  const gen = ++momentDetailLoadGen;
  selectedMomentId = id;
  // Do not commit selectedMomentSnapshot until a successful GET.
  momentDetail.hidden = false;
  const savedUi = soft ? captureMomentDetailUi() : null;
  if (!soft) {
    // Hard open: drop prior moment's last-good so soft keep/skip cannot apply
    // another card's snapshot (or leave bare "loading…" with no close).
    selectedMomentSnapshot = null;
    const loading = document.createElement("p");
    loading.className = "muted";
    loading.textContent = "loading…";
    renderMomentDetailChrome(
      `<strong>${escapeHtml(id)}</strong>`,
      loading
    );
  }
  try {
    const data = await fetchJson(`/api/moments/${encodeURIComponent(id)}`);
    // Stale: closed, switched, or a newer load superseded this one.
    if (selectedMomentId !== id || gen !== momentDetailLoadGen) return;
    const m = data.moment || {};
    const beats = data.beats || [];
    const nextSnap = momentListSnapshot(m, beats.length);
    // Soft path: skip rebuild only when closed and snapshot unchanged.
    // Open moments always re-render so live beats stream while the tape grows.
    const isOpen = !m.ended_at;
    if (
      soft &&
      !isOpen &&
      hasLastGoodFor(id) &&
      !momentSnapshotChanged(selectedMomentSnapshot, nextSnap)
    ) {
      selectedMomentSnapshot = nextSnap;
      return;
    }
    // Snapshot only after successful response so failed fetches keep retrying.
    selectedMomentSnapshot = nextSnap;
    const body = document.createDocumentFragment();
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${m.id} · stop=${m.stop_reason || (isOpen ? "open" : "—")} · hops=${
      m.hop_count ?? 0
    } · beats=${beats.length}`;
    body.appendChild(meta);
    body.appendChild(renderBeats(beats));
    renderMomentDetailChrome(
      `<strong>${escapeHtml(m.why_now || m.id)}</strong>`,
      body
    );
    if (soft) restoreMomentDetailUi(savedUi);
    // Highlight selected card after list may have re-rendered.
    momentsList.querySelectorAll(".card-btn").forEach((el) => {
      el.classList.toggle("card-selected", el.dataset.momentId === id);
    });
  } catch (err) {
    if (selectedMomentId !== id || gen !== momentDetailLoadGen) return;
    // Gone from store (reset / deleted) — dismiss rather than freeze ghost detail.
    if (err && err.status === 404) {
      closeMomentDetail();
      return;
    }
    // Soft keep only when last-good is for this same id (not a prior card / loading wipe).
    if (soft && hasLastGoodFor(id)) return;
    // Hard open, soft after hard wipe, or soft for a new id: error chrome with close.
    const msg = document.createElement("p");
    msg.className = "muted";
    msg.textContent = String(err.message || err);
    renderMomentDetailChrome(`<strong>Moment detail</strong>`, msg);
  }
}

async function refreshMoments() {
  const data = await fetchJson("/api/moments?limit=40");
  const moments = data.moments || [];
  renderMoments(moments);
  // Soft detail refresh while a moment is open.
  if (!selectedMomentId) return;
  const row = moments.find((m) => m.id === selectedMomentId);
  if (!row) {
    // Not in recent list (or wiped by reset): re-fetch by id; 404 closes.
    await loadMomentDetail(selectedMomentId, { soft: true });
    return;
  }
  // Always soft-refresh open moments (live beats); closed only when meta changes.
  if (!row.ended_at) {
    await loadMomentDetail(selectedMomentId, { soft: true });
    return;
  }
  const next = momentListSnapshot(row);
  // Do not pre-commit snapshot before load succeeds.
  if (momentSnapshotChanged(selectedMomentSnapshot, next)) {
    await loadMomentDetail(selectedMomentId, { soft: true });
  }
}

function renderCatalog(el, items, emptyLabel) {
  el.innerHTML = "";
  if (!items.length) {
    el.innerHTML = `<p class="muted empty">${emptyLabel}</p>`;
    return;
  }
  for (const t of items) {
    const card = document.createElement("article");
    card.className = "card";
    const source = t.source || t.kind || "";
    const kind = t.kind && t.source ? t.kind : "";
    card.innerHTML = `
      <div class="card-head">
        <strong>${escapeHtml(t.name)}</strong>
        <span class="badge">${escapeHtml(source)}</span>
      </div>
      ${
        kind
          ? `<div class="meta">${escapeHtml(kind)}</div>`
          : ""
      }
      <p class="muted">${escapeHtml(t.description || "")}</p>`;
    el.appendChild(card);
  }
}

async function refreshTools() {
  const [tools, skills] = await Promise.all([
    fetchJson("/api/tools"),
    fetchJson("/api/skills"),
  ]);
  const toolItems = tools.tools || [];
  const skillItems = skills.skills || [];
  renderCatalog(toolsList, toolItems, "No tools.");
  renderCatalog(skillsList, skillItems, "No skills.");
  if (toolsCountEl) toolsCountEl.textContent = String(toolItems.length);
  if (skillsCountEl) skillsCountEl.textContent = String(skillItems.length);
  if (catalogMeta) {
    const localTools = toolItems.filter((t) => t.source === "local").length;
    const localSkills = skillItems.filter((s) => s.source === "local").length;
    catalogMeta.textContent = `${toolItems.length} tools (${localTools} local) · ${skillItems.length} skills (${localSkills} local) · rescanned from disk`;
  }
}

async function refreshIdentity() {
  const [self, user] = await Promise.all([
    fetchJson("/api/identity"),
    fetchJson(`/api/users/${USER_ID}`),
  ]);
  identitySelf.textContent =
    (self.self && self.self.digest) || "(empty self digest)";
  identityUser.textContent = user.profile || "(empty profile)";
}

continuousToggles.forEach((el) => {
  el.addEventListener("change", () => {
    setContinuousEnabled(el.checked);
  });
});

if (devSpeedToggle) {
  devSpeedToggle.addEventListener("change", () => {
    patchDevSpeed({ enabled: Boolean(devSpeedToggle.checked) });
  });
}
if (devSpeedDelay) {
  devSpeedDelay.addEventListener("change", () => {
    const n = Number(devSpeedDelay.value);
    if (!Number.isFinite(n)) return;
    patchDevSpeed({ delay_seconds: n });
  });
}

if (providerModelSelect) {
  providerModelSelect.addEventListener("change", () => {
    const model = providerModelSelect.value;
    if (!model || model === lastProviderModel) return;
    patchProvider({ model });
  });
}
if (providerCredentialSelect) {
  providerCredentialSelect.addEventListener("change", () => {
    const credential_source = providerCredentialSelect.value;
    if (!credential_source || credential_source === lastCredentialSource) return;
    patchProvider({ credential_source });
  });
}
if (providerApiKeySave) {
  providerApiKeySave.addEventListener("click", () => {
    saveApiKey();
  });
}
if (providerApiKeyClear) {
  providerApiKeyClear.addEventListener("click", () => {
    clearApiKey();
  });
}
if (providerApiKeyInput) {
  providerApiKeyInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      saveApiKey();
    }
  });
}
if (usageOverrideToggle) {
  usageOverrideToggle.addEventListener("change", () => {
    setHardStopOverride(usageOverrideToggle.checked);
  });
}

function openResetModal() {
  if (!resetModal) return;
  resetModal.hidden = false;
  if (resetConfirmInput) {
    resetConfirmInput.value = "";
    resetConfirmInput.focus();
  }
  if (resetConfirmBtn) resetConfirmBtn.disabled = true;
}

function closeResetModal() {
  if (!resetModal) return;
  resetModal.hidden = true;
  if (resetConfirmInput) resetConfirmInput.value = "";
  if (resetConfirmBtn) resetConfirmBtn.disabled = true;
}

function syncResetConfirmEnabled() {
  if (!resetConfirmBtn || !resetConfirmInput) return;
  resetConfirmBtn.disabled =
    resetInFlight || resetConfirmInput.value.trim() !== "RESET";
}

async function refreshAllPanels() {
  await Promise.all([
    refreshStatus(),
    refreshMessages(),
    refreshGoals().catch(() => {}),
    refreshMoments().catch(() => {}),
    refreshTools().catch(() => {}),
    refreshIdentity().catch(() => {}),
  ]);
}

async function confirmFullReset() {
  if (resetInFlight) return;
  if (!resetConfirmInput || resetConfirmInput.value.trim() !== "RESET") return;
  resetInFlight = true;
  if (resetConfirmBtn) resetConfirmBtn.disabled = true;
  try {
    const data = await fetchJson("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        confirm: "RESET",
        clear_sandbox: true,
        clear_drafts: true,
      }),
    });
    closeResetModal();
    const cleared = (data && data.cleared) || [];
    showNotice(
      cleared.length
        ? `Full reset ok — cleared ${cleared.join(", ")}.`
        : "Full reset ok."
    );
    await refreshAllPanels();
  } catch (err) {
    const status = err && err.status;
    const body = (err && err.body) || {};
    if (status === 409) {
      showNotice(
        `Reset blocked — worker busy (phase=${body.phase || "?"} ). Wait for idle.`
      );
    } else if (status === 503) {
      showNotice("Reset already in progress.");
    } else {
      showNotice(String(err.message || err));
    }
  } finally {
    resetInFlight = false;
    syncResetConfirmEnabled();
  }
}

if (resetOpenBtn) {
  resetOpenBtn.addEventListener("click", openResetModal);
}
if (resetConfirmInput) {
  resetConfirmInput.addEventListener("input", syncResetConfirmEnabled);
  resetConfirmInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      confirmFullReset();
    }
    if (e.key === "Escape") {
      e.preventDefault();
      closeResetModal();
    }
  });
}
if (resetConfirmBtn) {
  resetConfirmBtn.addEventListener("click", confirmFullReset);
}
if (resetModal) {
  resetModal.querySelectorAll("[data-reset-dismiss]").forEach((el) => {
    el.addEventListener("click", closeResetModal);
  });
}

function formatBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / (1024 * 1024)).toFixed(1)} MB`;
}

function renderAttachTray() {
  if (!attachTray) return;
  if (!pendingAttachments.length) {
    attachTray.hidden = true;
    attachTray.innerHTML = "";
    return;
  }
  attachTray.hidden = false;
  attachTray.innerHTML = "";
  pendingAttachments.forEach((att, idx) => {
    const chip = document.createElement("div");
    chip.className = "attach-chip";
    if (att.kind === "image" && att.previewUrl) {
      const img = document.createElement("img");
      img.src = att.previewUrl;
      img.alt = att.name;
      chip.appendChild(img);
    } else {
      const icon = document.createElement("span");
      icon.textContent = "📄";
      icon.setAttribute("aria-hidden", "true");
      chip.appendChild(icon);
    }
    const meta = document.createElement("div");
    meta.className = "chip-meta";
    meta.innerHTML = `<span class="chip-name" title="${escapeHtml(
      att.name
    )}">${escapeHtml(att.name)}</span><span class="chip-sub">${escapeHtml(
      att.kind
    )} · ${escapeHtml(formatBytes(att.size))}</span>`;
    chip.appendChild(meta);
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "chip-remove";
    rm.setAttribute("aria-label", `Remove ${att.name}`);
    rm.textContent = "×";
    rm.addEventListener("click", () => {
      if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
      pendingAttachments.splice(idx, 1);
      renderAttachTray();
    });
    chip.appendChild(rm);
    attachTray.appendChild(chip);
  });
}

function addFilesAsAttachments(fileList) {
  const files = Array.from(fileList || []);
  for (const file of files) {
    if (!file || !file.name) continue;
    if (pendingAttachments.length >= 8) {
      showNotice("Attachment limit: 8 files per message.");
      break;
    }
    const isImage = String(file.type || "").startsWith("image/");
    const att = {
      name: file.name,
      size: file.size,
      type: file.type || "application/octet-stream",
      kind: isImage ? "image" : "file",
      previewUrl: isImage ? URL.createObjectURL(file) : null,
    };
    pendingAttachments.push(att);
  }
  renderAttachTray();
}

function buildAttachmentInventory() {
  if (!pendingAttachments.length) return "";
  const lines = pendingAttachments.map(
    (a, i) =>
      `${i + 1}. ${a.name} (${a.kind}, ${a.type || "unknown"}, ${formatBytes(
        a.size
      )})`
  );
  return (
    "\n\n---\n**Attachments** (listed for Elyra; binary vision/file I/O not wired yet):\n" +
    lines.join("\n")
  );
}

function clearAttachments() {
  pendingAttachments.forEach((a) => {
    if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
  });
  pendingAttachments = [];
  renderAttachTray();
}

function autosizeComposer() {
  if (!input) return;
  input.style.height = "auto";
  const next = Math.min(180, Math.max(44, input.scrollHeight));
  input.style.height = `${next}px`;
}

function activityEventKey(ev, idx) {
  if (!ev || typeof ev !== "object") return `e-${idx}`;
  return String(ev.id || `${ev.kind || "x"}:${ev.label || ""}:${idx}`);
}

/**
 * Render last ≤3 events oldest→newest (left→right).
 * On change: newest chip enters from the right; older chips reflow left.
 */
function renderActivityTrail(events) {
  if (!chatActivityTrail) return;
  const list = Array.isArray(events) ? events.slice(-3) : [];
  const ids = list.map((ev, i) => activityEventKey(ev, i));
  const fp = ids.join("|");

  if (!list.length) {
    chatActivityTrail.innerHTML = "";
    lastActivityTrailFp = "";
    activityTrailIds = [];
    return;
  }

  if (fp === lastActivityTrailFp) return;

  const prevIds = activityTrailIds.slice();
  const prevNewest = prevIds.length ? prevIds[prevIds.length - 1] : null;
  const newestId = ids[ids.length - 1];
  const newestIsNew = Boolean(newestId && newestId !== prevNewest);

  // Exit animation for the oldest chip when the window slides (3→3 replace).
  const droppedOldest =
    prevIds.length >= 3 && ids.length === 3 && !ids.includes(prevIds[0])
      ? prevIds[0]
      : null;

  chatActivityTrail.innerHTML = "";

  if (droppedOldest) {
    const ghost = document.createElement("span");
    ghost.className = "activity-chip chip-exit";
    ghost.textContent = "…";
    chatActivityTrail.appendChild(ghost);
    setTimeout(() => {
      if (ghost.parentNode) ghost.remove();
    }, 300);
  }

  list.forEach((ev, i) => {
    const chip = document.createElement("span");
    const kind = String(ev.kind || "event").replace(/[^a-z0-9_-]/gi, "");
    chip.className = `activity-chip kind-${kind}`;
    chip.dataset.eventId = ids[i];
    if (i === list.length - 1) chip.classList.add("is-newest");
    if (i === list.length - 1 && newestIsNew && prevIds.length) {
      chip.classList.add("chip-enter");
    }
    chip.textContent = String(ev.label || ev.short || ev.kind || "…");
    chip.title = [ev.kind, ev.label, ev.name, ev.error_reason, ev.hop != null ? `hop ${ev.hop}` : ""]
      .filter(Boolean)
      .join(" · ");
    chatActivityTrail.appendChild(chip);
  });

  lastActivityTrailFp = fp;
  activityTrailIds = ids;
}

function updateChatActivity(status) {
  if (!chatActivity) return;
  const phase = (status && status.phase) || "";
  const worker = status && status.worker;
  const busy =
    phase === "in_moment" ||
    phase === "waiting" ||
    (worker && worker.busy) ||
    (status && status.busy) ||
    Boolean(status && status.worker_busy);
  if (!busy) {
    chatActivity.hidden = true;
    if (chatActivityTrail) chatActivityTrail.innerHTML = "";
    lastActivityTrailFp = "";
    activityTrailIds = [];
    return;
  }

  chatActivity.hidden = false;
  const activity = (status && status.activity) || {};
  const recent = (status && status.recent_activity) || [];

  let label =
    activity.label ||
    (phase === "waiting"
      ? "waiting for you"
      : phase === "in_moment"
        ? "in moment…"
        : "working…");
  let detail = activity.detail || "";

  // Fallback detail from hop / last_tool when activity block missing (older workers).
  if (!detail) {
    const bits = [];
    if (status.hop_count) bits.push(`hop ${status.hop_count}`);
    if (status.last_tool) bits.push(String(status.last_tool));
    detail = bits.join(" · ");
  }

  if (chatActivityLabel) chatActivityLabel.textContent = label;
  if (chatActivityDetail) chatActivityDetail.textContent = detail;

  // Waiting: ensure trail ends with a waiting chip when no recent beats.
  let trail = Array.isArray(recent) ? recent.slice(-3) : [];
  if (phase === "waiting") {
    const waitChip = {
      id: "waiting-you",
      kind: "waiting",
      label: "you",
      short: "wait",
    };
    if (!trail.length || trail[trail.length - 1].kind !== "waiting") {
      trail = trail.concat([waitChip]).slice(-3);
    }
  }
  renderActivityTrail(trail);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  const inventory = buildAttachmentInventory();
  if (!text && !inventory) return;
  const content = (text + inventory).trim();
  sendBtn.disabled = true;
  try {
    const data = await fetchJson("/api/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, user_id: USER_ID }),
    });
    input.value = "";
    autosizeComposer();
    clearAttachments();
    chatStickToBottom = true;
    if (data.ok === false && data.reason === REASON_BUFFER_FULL) {
      showNotice(
        "Interjection buffer full — message queued as a wake for after this moment."
      );
    }
    await refreshMessages({ force: true });
  } catch (err) {
    showNotice(String(err.message || err));
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
});

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

input.addEventListener("input", autosizeComposer);

input.addEventListener("paste", (e) => {
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  const files = [];
  for (const item of items) {
    if (item.kind === "file") {
      const f = item.getAsFile();
      if (f) files.push(f);
    }
  }
  if (files.length) {
    e.preventDefault();
    addFilesAsAttachments(files);
  }
});

if (attachBtn && attachInput) {
  attachBtn.addEventListener("click", () => attachInput.click());
  attachInput.addEventListener("change", () => {
    addFilesAsAttachments(attachInput.files);
    attachInput.value = "";
  });
}

if (form) {
  form.addEventListener("dragenter", (e) => {
    e.preventDefault();
    composerDragDepth += 1;
    if (dropOverlay) dropOverlay.hidden = false;
  });
  form.addEventListener("dragleave", (e) => {
    e.preventDefault();
    composerDragDepth = Math.max(0, composerDragDepth - 1);
    if (composerDragDepth === 0 && dropOverlay) dropOverlay.hidden = true;
  });
  form.addEventListener("dragover", (e) => {
    e.preventDefault();
  });
  form.addEventListener("drop", (e) => {
    e.preventDefault();
    composerDragDepth = 0;
    if (dropOverlay) dropOverlay.hidden = true;
    if (e.dataTransfer && e.dataTransfer.files) {
      addFilesAsAttachments(e.dataTransfer.files);
    }
  });
}

if (messagesEl) {
  messagesEl.addEventListener("scroll", () => {
    chatStickToBottom = isNearBottom(messagesEl);
    updateJumpLatestVisibility();
  });
}

if (jumpLatestBtn) {
  jumpLatestBtn.addEventListener("click", () => {
    scrollMessagesToBottom({ smooth: true });
  });
}

if (catalogRefreshBtn) {
  catalogRefreshBtn.addEventListener("click", () => {
    refreshTools()
      .then(() => showNotice("Tools & skills rescanned from disk."))
      .catch((e) => panelLoadError("Tools", e));
  });
}

autosizeComposer();

function panelLoadError(panelName, err) {
  showNotice(`${panelName}: ${err && err.message ? err.message : err}`);
}

function refreshActivePanel() {
  const name = activePanel;
  if (name === "goals") return refreshGoals();
  if (name === "moments") return refreshMoments();
  if (name === "tools") return refreshTools();
  if (name === "identity") return refreshIdentity();
  // chat / status: covered by refreshMessages / refreshStatus
  return Promise.resolve();
}

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    const name = btn.dataset.panel;
    activePanel = name || "chat";
    const panel = document.getElementById(`panel-${name}`);
    if (panel) panel.classList.add("active");
    // Refresh panel data when opened; surface failures (parity with chat).
    if (name === "goals") refreshGoals().catch((e) => panelLoadError("Goals", e));
    if (name === "moments") refreshMoments().catch((e) => panelLoadError("Moments", e));
    if (name === "tools") refreshTools().catch((e) => panelLoadError("Tools", e));
    if (name === "identity") refreshIdentity().catch((e) => panelLoadError("Identity", e));
  });
});

async function tick() {
  // Single-flight: skip if previous tick still running (avoids list/detail races).
  if (tickInFlight) return;
  tickInFlight = true;
  try {
    const tasks = [refreshStatus(), refreshMessages()];
    // Also poll the active catalog panel so creates appear without nav re-click.
    if (
      activePanel === "goals" ||
      activePanel === "moments" ||
      activePanel === "tools" ||
      activePanel === "identity"
    ) {
      tasks.push(refreshActivePanel().catch(() => {}));
    }
    await Promise.all(tasks);
  } catch {
    /* offline */
  } finally {
    tickInFlight = false;
  }
}

tick();
setInterval(tick, 1500);
