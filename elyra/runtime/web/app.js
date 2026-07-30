const $ = (sel) => document.querySelector(sel);

const messagesEl = $("#messages");
const form = $("#chat-form");
const input = $("#chat-input");
const sendBtn = $("#send-btn");
const attachBtn = $("#attach-btn");
const attachInput = $("#attach-input");
const attachTray = $("#attach-tray");
const micBtn = $("#mic-btn");
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
const pillProvider = $("#pill-provider");
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
const memoryRefreshBtn = $("#memory-refresh-btn");
const memoryContextFlags = $("#memory-context-flags");
const memoryContextBody = $("#memory-context-body");
const memoryAtomsList = $("#memory-atoms-list");
const memoryAtomDetail = $("#memory-atom-detail");
const memoryAtomKind = $("#memory-atom-kind");
const memoryAtomMoment = $("#memory-atom-moment");
const memoryAtomsApply = $("#memory-atoms-apply");
const memoryVectorsHealth = $("#memory-vectors-health");
const memoryVectorsList = $("#memory-vectors-list");
const memoryVectorStatus = $("#memory-vector-status");
const memoryVectorsApply = $("#memory-vectors-apply");
const memoryVectorsRebuild = $("#memory-vectors-rebuild");
const memoryNeighborAtom = $("#memory-neighbor-atom");
const memoryNeighborQ = $("#memory-neighbor-q");
const memoryNeighborChannel = $("#memory-neighbor-channel");
const memoryNeighborK = $("#memory-neighbor-k");
const memoryNeighborsRun = $("#memory-neighbors-run");
const memoryNeighborsList = $("#memory-neighbors-list");
const memoryNeighborsMeta = $("#memory-neighbors-meta");
const memoryGraphOverview = $("#memory-graph-overview");
const memoryGraphHonesty = $("#memory-graph-honesty");
const memoryGraphSessionBadge = $("#memory-graph-session-badge");
const memoryGraphSessionBody = $("#memory-graph-session-body");
const memoryGraphConsidered = $("#memory-graph-considered");
const memoryGraphKept = $("#memory-graph-kept");
const memoryGraphFrontier = $("#memory-graph-frontier");
const memoryGraphNeighborAtom = $("#memory-graph-neighbor-atom");
const memoryGraphNeighborK = $("#memory-graph-neighbor-k");
const memoryGraphNeighborSem = $("#memory-graph-neighbor-sem");
const memoryGraphNeighborsRun = $("#memory-graph-neighbors-run");
const memoryGraphNeighborsList = $("#memory-graph-neighbors-list");
const memoryGraphNeighborsMeta = $("#memory-graph-neighbors-meta");
/** @type {boolean} */
let memoryVectorsRebuildInFlight = false;
/** @type {"context" | "atoms" | "vectors" | "graph"} */
let memoryActiveTab = "context";
/** @type {string | null} */
let selectedAtomId = null;
let memoryAtomDetailLoadGen = 0;
/** Last meal fingerprint for Context soft-refresh (avoid wiping open inspect). */
let memoryContextMealFp = null;
/** @type {Map<string, object>} atom_id → last fetched detail for Context inspect restore */
const memoryContextAtomCache = new Map();
const toolsList = $("#tools-list");
const skillsList = $("#skills-list");
const catalogInspector = $("#catalog-inspector");
const catalogInspectorTitle = $("#catalog-inspector-title");
const catalogInspectorBadges = $("#catalog-inspector-badges");
const catalogInspectorDesc = $("#catalog-inspector-desc");
const catalogInspectorMeta = $("#catalog-inspector-meta");
const catalogInspectorDoc = $("#catalog-inspector-doc");
const catalogInspectorSchemaFold = $("#catalog-inspector-schema-fold");
const catalogInspectorSchema = $("#catalog-inspector-schema");
const catalogInspectorRunnerFold = $("#catalog-inspector-runner-fold");
const catalogInspectorRunner = $("#catalog-inspector-runner");
const catalogInspectorVcsHint = $("#catalog-inspector-vcs-hint");
const catalogInspectorVersions = $("#catalog-inspector-versions");
const catalogInspectorVersionDoc = $("#catalog-inspector-version-doc");
/** @type {{ kind: "tool" | "skill", name: string } | null} */
let catalogSelection = null;
const identitySelf = $("#identity-self");
const identityUser = $("#identity-user");
const identitySelfLabel = $("#identity-self-label");
const identityUserLabel = $("#identity-user-label");
const identitySelfDraftBadge = $("#identity-self-draft-badge");
const identityUserDraftBadge = $("#identity-user-draft-badge");
const identitySelfDraftFold = $("#identity-self-draft-fold");
const identityUserDraftFold = $("#identity-user-draft-fold");
const identitySelfDraft = $("#identity-self-draft");
const identityUserDraft = $("#identity-user-draft");
const identitySelfVersions = $("#identity-self-versions");
const identityUserVersions = $("#identity-user-versions");
const identitySelfVersionBody = $("#identity-self-version-body");
const identityUserVersionBody = $("#identity-user-version-body");
const identityUserMeta = $("#identity-user-meta");
const identityUserChips = $("#identity-user-chips");
const identityMintGrantBtn = $("#identity-mint-grant-btn");
const identityPromoteSelfBtn = $("#identity-promote-self-btn");
const identityPromoteUserBtn = $("#identity-promote-user-btn");
const identityGrantToken = $("#identity-grant-token");
const brandNameEl = $("#brand-name");
const brandSubEl = $("#brand-sub");
const sessionUserSelect = $("#session-user-select");
const sessionNewGuestBtn = $("#session-new-guest-btn");
// Styled switch class is shared; exclude non-continuous controls so their
// change handlers stay on their own PATCH paths (BUG-status-02 / #77).
const continuousToggles = document.querySelectorAll(
  ".continuous-toggle:not(#usage-override-toggle):not(#dev-speed-toggle):not(#semantic-wait-toggle)"
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
const oauthLoginStack = $("#oauth-login-stack");
const oauthLegacyBanner = $("#oauth-legacy-banner");
const oauthCtaBanner = $("#oauth-cta-banner");
const oauthMeta = $("#oauth-meta");
const oauthLoginBtn = $("#oauth-login-btn");
const oauthLogoutBtn = $("#oauth-logout-btn");
const oauthCancelBtn = $("#oauth-cancel-btn");
const oauthActivateCheckbox = $("#oauth-activate-checkbox");
const oauthPendingPanel = $("#oauth-pending-panel");
const oauthPendingLabel = $("#oauth-pending-label");
const oauthUserCode = $("#oauth-user-code");
const oauthCopyCodeBtn = $("#oauth-copy-code-btn");
const oauthVerifyLink = $("#oauth-verify-link");
const oauthCopyUriBtn = $("#oauth-copy-uri-btn");
const usageBadge = $("#usage-badge");
const usageWeekPct = $("#usage-week-pct");
const usageDayPct = $("#usage-day-pct");
const usageHourPct = $("#usage-hour-pct");
const usageSgPct = $("#usage-sg-pct");
const usageWeekBar = $("#usage-week-bar");
const usageDayBar = $("#usage-day-bar");
const usageHourBar = $("#usage-hour-bar");
const usageSgBar = $("#usage-sg-bar");
const railUsageWeekPct = $("#rail-usage-week-pct");
const railUsageWeekBar = $("#rail-usage-week-bar");
const railUsageSgPct = $("#rail-usage-sg-pct");
const railUsageSgBar = $("#rail-usage-sg-bar");
const railContextPct = $("#rail-context-pct");
const railContextBar = $("#rail-context-bar");
const railContextMealMark = $("#rail-context-meal-mark");
const railContextMeta = $("#rail-context-meta");
const contextWindowPct = $("#context-window-pct");
const contextWindowBar = $("#context-window-bar");
const contextMealMark = $("#context-meal-mark");
const contextMealPct = $("#context-meal-pct");
const contextMealBar = $("#context-meal-bar");
const contextDetail = $("#context-detail");
const usagePaceBadge = $("#usage-pace-badge");
const usageBurst = $("#usage-burst");
const usageDetail = $("#usage-detail");
const usageProductUsage = $("#usage-product-usage");
const usageProductUsageBody = $("#usage-product-usage-body");
const usageOverrideToggle = $("#usage-override-toggle");
const usageOverrideMeta = $("#usage-override-meta");
const devSpeedToggle = $("#dev-speed-toggle");
const devSpeedMeta = $("#dev-speed-meta");
const devSpeedBadge = $("#dev-speed-badge");
const devSpeedDelay = $("#dev-speed-delay");
const semanticWaitToggle = $("#semantic-wait-toggle");
const semanticWaitMeta = $("#semantic-wait-meta");
const semanticWaitBadge = $("#semantic-wait-badge");
const semanticWaitMaxMs = $("#semantic-wait-max-ms");

/** Active glass session user (who is typing) — not orient USER on pure work. */
let sessionUserId =
  (typeof localStorage !== "undefined" &&
    localStorage.getItem("elyra.sessionUserId")) ||
  "operator";
/** Display labels: self + per-user goes_by. */
let labelCache = { self: "Elyra", users: {} };
/** Selected user id in identity panel (may differ from session for review). */
let identityPanelUserId = sessionUserId;
/** Last minted grant token shown once in the identity panel. */
let lastMintedGrantToken = null;
const REASON_BUFFER_FULL = "interjection_buffer_full";

function getSessionUserId() {
  return sessionUserId || "operator";
}

/** Self display name for glass chrome (fallback Elyra). */
function selfDisplayName() {
  const n = (labelCache.self || "").trim();
  return n || "Elyra";
}

/**
 * Rail brand: {NAME} above "Project Elyra"; document title "{NAME} - Project Elyra".
 * NAME is whatever this instance calls itself (identity display_name / goes_by).
 */
function updateBrandChrome() {
  const name = selfDisplayName();
  if (brandNameEl) brandNameEl.textContent = name;
  if (brandSubEl) brandSubEl.textContent = "Project Elyra";
  document.title = `${name} - Project Elyra`;
}

function actorLabel(message) {
  if (!message) return selfDisplayName();
  if (message.role === "user") {
    const uid = message.user_id || getSessionUserId();
    return labelCache.users[uid] || uid;
  }
  return selfDisplayName();
}

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
/** True while PATCH /api/semantic-wait is in flight. */
let semanticWaitInFlight = false;
/** Last known semantic_wait.enabled from status. */
let lastSemanticWaitEnabled = true;
/** Last known semantic_wait.max_ms from status. */
let lastSemanticWaitMaxMs = 15000;
/** True while POST /api/reset is in flight. */
let resetInFlight = false;
/** True while PATCH /api/provider is in flight. */
let providerPatchInFlight = false;
/** True while PUT/DELETE api-key is in flight. */
let apiKeyInFlight = false;
/** True while xAI device start/cancel/logout is in flight. */
let oauthActionInFlight = false;
/** True while a device-code login is pending (server-side poll). */
let oauthDevicePending = false;
/** Public fields from last device start/status (never tokens / device_code). */
let oauthPendingPublic = null;
/** Interval handle for GET /api/auth/xai/device/status while pending. */
let oauthPollTimer = null;
/** Last known oauth_configured from status. */
let lastOauthConfigured = false;
/** True while PATCH /api/usage (hard-stop override) is in flight. */
let usageOverrideInFlight = false;
/**
 * Desired override while a PATCH is in flight. If the operator toggles again
 * before the first request finishes, we apply this after the in-flight call
 * (BUG-status-03 / #78 — dropped OFF clicks left disk stuck ON).
 * @type {boolean|null}
 */
let pendingOverrideTarget = null;
/** Last known hard_stop_override / override_active from status (server truth). */
let lastOverrideActive = false;
/** Last known usage.hard_stop value (for transition notices). */
let lastHardStop = null;
/** False until first successful status paint (skip transition notices on boot). */
let statusPrimed = false;
/** Last known model / credential_source (for select change detection). */
let lastProviderModel = null;
let lastCredentialSource = null;
/** Last *server-confirmed* reasoning effort (never set by optimistic paint). */
let lastReasoningEffort = "high";
/** Active nav panel name (chat | goals | memory | tools | identity | secrets | status). */
let activePanel = "chat";
/** True while secrets PUT/DELETE is in flight. */
let secretsInFlight = false;
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
/**
 * Pending composer attachments (local File + preview, or pre-uploaded id from STT).
 * On send: POST /api/media for local files → attachment_ids on POST /api/messages.
 */
let pendingAttachments = [];

/** MediaRecorder session for composer mic → POST /api/stt (PR6). */
let micRecorder = null;
let micChunks = [];
let micStream = null;
let micBusy = false;
/** Soft client caps matching host (elyra/media/upload.py). */
const MAX_PENDING_ATTACHMENTS = 8;
const MAX_CLIENT_IMAGE_BYTES = 20 * 1024 * 1024;
const MAX_CLIENT_AUDIO_BYTES = 25 * 1024 * 1024;
const MAX_CLIENT_FILE_BYTES = 48 * 1024 * 1024;
/** Safe attachment id segment (matches host validate_att_id). */
const ATT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
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
  const attFp = Array.isArray(last.attachments)
    ? last.attachments.map((a) => a && a.id).filter(Boolean).join(",")
    : "";
  return `${messages.length}|${last.id || ""}|${(last.content || "").length}|${
    last.created_at || ""
  }|${(last.reasoning || "").length}|${attFp}`;
}

/**
 * Stable JSON fingerprint for Glass soft-refresh (BUG-glass-03).
 * Tick may fetch often; DOM replace only when this changes (unless force).
 */
function stableFingerprint(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** Set textContent only when the string actually changed (preserve selection). */
function setTextIfChanged(el, text) {
  if (!el) return;
  const next = text == null ? "" : String(text);
  if (el.textContent !== next) el.textContent = next;
}

// Soft-refresh fingerprints for catalog panels (BUG-glass-03 / #86).
let lastGoalsFp = null;
let lastMomentsListFp = null;
let lastAtomsListFp = null;
let lastAtomDetailFp = null;
let lastVectorsFp = null;
let lastGraphFp = null;
let lastToolsCatalogFp = null;
let lastCatalogDetailFp = null;
let lastIdentityFp = null;
let lastSecretsFp = null;

/**
 * Resolve markdown media/link targets for glass CSP.
 * attachment:<id> and /api/media/<id> → same-origin serve URL; else http(s)/data:image.
 * Rejects javascript:, path traversal, and non-image data:.
 */
function resolveMediaUrl(url) {
  const u = String(url || "").trim();
  if (!u) return null;
  if (/^javascript:/i.test(u) || /^vbscript:/i.test(u)) return null;
  const attScheme = u.match(/^attachment:([A-Za-z0-9][A-Za-z0-9._-]*)$/i);
  if (attScheme && ATT_ID_RE.test(attScheme[1])) {
    return `/api/media/${attScheme[1]}`;
  }
  const apiPath = u.match(/^\/api\/media\/([A-Za-z0-9][A-Za-z0-9._-]*)$/i);
  if (apiPath && ATT_ID_RE.test(apiPath[1]) && !u.includes("..")) {
    return `/api/media/${apiPath[1]}`;
  }
  if (/^https?:\/\//i.test(u)) return u;
  if (/^data:image\//i.test(u)) return u;
  return null;
}

function mediaUrlForAttachment(att) {
  if (!att || !att.id || !ATT_ID_RE.test(String(att.id))) return null;
  return `/api/media/${att.id}`;
}

function visibleAttachments(list) {
  if (!Array.isArray(list)) return [];
  return list.filter((a) => a && a.kind !== "tts_cache");
}

function detectAttachmentKind(file) {
  const mime = String((file && file.type) || "").toLowerCase();
  const name = String((file && file.name) || "").toLowerCase();
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("audio/")) return "audio";
  if (mime.startsWith("video/")) return "video";
  if (/\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name)) return "image";
  if (/\.(mp3|wav|ogg|opus|m4a|aac|flac)$/i.test(name)) return "audio";
  if (/\.(mp4|mov|mkv|avi|webm)$/i.test(name)) return "video";
  return "file";
}

function clientMaxBytesForKind(kind) {
  if (kind === "image") return MAX_CLIENT_IMAGE_BYTES;
  if (kind === "audio") return MAX_CLIENT_AUDIO_BYTES;
  return MAX_CLIENT_FILE_BYTES;
}

function kindIcon(kind) {
  if (kind === "image") return "🖼";
  if (kind === "audio") return "🔊";
  if (kind === "video") return "🎬";
  return "📄";
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
    // images ![alt](url) — http(s), data:image, attachment:<id>, /api/media/<id>
    t = t.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, url) => {
      const resolved = resolveMediaUrl(url);
      if (!resolved) return escape(`![${alt}](${url})`);
      // Only render as <img> for image-like targets (not raw non-image data:)
      if (/^data:/i.test(resolved) && !/^data:image\//i.test(resolved)) {
        return escape(`![${alt}](${url})`);
      }
      return `<img class="md-img" src="${escape(resolved)}" alt="${escape(
        alt
      )}" loading="lazy" />`;
    });
    // links [text](url) — https?, attachment:<id>, /api/media/<id>
    t = t.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
      const resolved = resolveMediaUrl(url);
      if (!resolved || /^data:/i.test(resolved)) {
        // data: links not allowed as anchors; bare https only without resolve miss
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
      // same-origin media: open in new tab / download
      return `<a class="md-att-link" href="${escape(
        resolved
      )}" target="_blank" rel="noopener noreferrer">${escape(label)}</a>`;
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

/**
 * Attachments footer inventory (always when non-tts attachments present).
 * Body embeds are views; this section is the durable inventory.
 */
function renderAttachmentsFooter(attachments) {
  const atts = visibleAttachments(attachments);
  if (!atts.length) return null;
  const foot = document.createElement("div");
  foot.className = "msg-attachments";
  const heading = document.createElement("div");
  heading.className = "msg-attachments-label";
  heading.textContent = atts.length === 1 ? "Attachment" : "Attachments";
  foot.appendChild(heading);
  const list = document.createElement("div");
  list.className = "msg-attachments-list";
  for (const att of atts) {
    list.appendChild(renderAttachmentItem(att));
  }
  foot.appendChild(list);
  return foot;
}

function renderAttachmentItem(att) {
  const kind = String(att.kind || "file");
  const name = String(att.filename || att.name || att.id || "file");
  const size = Number(att.byte_size != null ? att.byte_size : att.size) || 0;
  const href = mediaUrlForAttachment(att);
  const item = document.createElement("div");
  item.className = `msg-att msg-att-${kind}`;
  item.dataset.attId = String(att.id || "");

  if (kind === "image" && href) {
    const a = document.createElement("a");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.className = "msg-att-thumb-link";
    a.title = name;
    const img = document.createElement("img");
    img.className = "msg-att-thumb";
    img.src = href;
    img.alt = name;
    img.loading = "lazy";
    a.appendChild(img);
    item.appendChild(a);
  } else if (kind === "audio" && href) {
    const audio = document.createElement("audio");
    audio.className = "msg-att-player";
    audio.controls = true;
    audio.preload = "metadata";
    audio.src = href;
    item.appendChild(audio);
  } else if (kind === "video" && href) {
    const video = document.createElement("video");
    video.className = "msg-att-player msg-att-video";
    video.controls = true;
    video.preload = "metadata";
    video.src = href;
    item.appendChild(video);
  } else {
    const icon = document.createElement("span");
    icon.className = "msg-att-icon";
    icon.textContent = kindIcon(kind);
    icon.setAttribute("aria-hidden", "true");
    item.appendChild(icon);
  }

  const meta = document.createElement("div");
  meta.className = "msg-att-meta";
  const title = document.createElement("div");
  title.className = "msg-att-name";
  title.textContent = name;
  title.title = name;
  meta.appendChild(title);
  const sub = document.createElement("div");
  sub.className = "msg-att-sub";
  const bits = [kind];
  if (size) bits.push(formatBytes(size));
  if (att.mime) bits.push(String(att.mime));
  sub.textContent = bits.join(" · ");
  meta.appendChild(sub);
  item.appendChild(meta);

  if (href) {
    const dl = document.createElement("a");
    dl.className = "msg-att-download";
    dl.href = href;
    dl.download = name;
    dl.target = "_blank";
    dl.rel = "noopener noreferrer";
    dl.textContent = "↓";
    dl.title = `Download ${name}`;
    dl.setAttribute("aria-label", `Download ${name}`);
    item.appendChild(dl);
  }
  return item;
}

/** Active TTS Audio element (stop previous play on new click). */
let _ttsAudio = null;
/** message_id → object URL for cached play (browser-side second click). */
const _ttsBlobUrls = new Map();

/**
 * Play TTS for a glass message (PR7 / KD3).
 * Host loads saved text only; disk cache on second host hit; never re-LLM.
 * Hide play when content is empty (caller responsibility).
 */
async function playMessageTts(messageId, btn) {
  if (!messageId) return;
  // Stop any in-flight playback.
  if (_ttsAudio) {
    try {
      _ttsAudio.pause();
    } catch {
      /* ignore */
    }
    _ttsAudio = null;
  }
  const prevLabel = btn ? btn.textContent : "";
  if (btn) {
    btn.disabled = true;
    btn.classList.add("is-loading");
    btn.textContent = "…";
  }
  try {
    let url = _ttsBlobUrls.get(messageId);
    if (!url) {
      const res = await fetch(
        `/api/messages/${encodeURIComponent(messageId)}/tts?voice=eve&language=en`,
        { method: "GET" }
      );
      if (!res.ok) {
        let reason = `tts_${res.status}`;
        try {
          const j = await res.json();
          if (j && j.reason) reason = j.reason;
          else if (j && j.error) reason = String(j.error);
        } catch {
          /* non-json */
        }
        showNotice(`TTS failed: ${reason}`);
        return;
      }
      const blob = await res.blob();
      url = URL.createObjectURL(blob);
      _ttsBlobUrls.set(messageId, url);
    }
    const audio = new Audio(url);
    _ttsAudio = audio;
    audio.addEventListener("ended", () => {
      if (_ttsAudio === audio) _ttsAudio = null;
    });
    await audio.play();
  } catch (err) {
    showNotice(`TTS play error: ${err && err.message ? err.message : err}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("is-loading");
      btn.textContent = prevLabel || "▶";
    }
  }
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
    const label = actorLabel(m);
    meta.innerHTML = `<span class="role-chip">${escapeHtml(
      label
    )}</span><span>${escapeHtml(formatMsgTime(m.created_at))}</span>`;
    // TTS play: only when content non-empty (media-only rows have no playable text).
    const content = m.content || "";
    if (content.trim() && m.id) {
      const playBtn = document.createElement("button");
      playBtn.type = "button";
      playBtn.className = "msg-tts-btn";
      playBtn.textContent = "▶";
      playBtn.title = "Play message";
      playBtn.setAttribute("aria-label", "Play message");
      playBtn.dataset.messageId = m.id;
      playBtn.addEventListener("click", (ev) => {
        ev.preventDefault();
        playMessageTts(m.id, playBtn);
      });
      meta.appendChild(playBtn);
    }
    div.appendChild(meta);
    const atts = visibleAttachments(m.attachments);
    // Media-only rows: skip empty markdown shell; footer carries inventory.
    if (content.trim()) {
      const body = document.createElement("div");
      body.className = "msg-body";
      body.innerHTML = renderMarkdown(content);
      div.appendChild(body);
      wireMessageBodyInteractions(body);
    } else if (!atts.length) {
      const body = document.createElement("div");
      body.className = "msg-body";
      body.innerHTML = renderMarkdown("");
      div.appendChild(body);
    }
    const foot = renderAttachmentsFooter(m.attachments);
    if (foot) div.appendChild(foot);
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
      body: JSON.stringify({ choice, user_id: getSessionUserId() }),
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

/** Compact token counts for context rail (e.g. 12.3k / 500k). */
function formatTokenCount(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  const v = Math.max(0, Math.round(Number(n)));
  if (v >= 1_000_000) {
    const m = v / 1_000_000;
    return `${m >= 10 ? Math.round(m) : m.toFixed(1).replace(/\.0$/, "")}M`;
  }
  if (v >= 10_000) return `${Math.round(v / 1000)}k`;
  if (v >= 1000) {
    const k = v / 1000;
    return `${k.toFixed(1).replace(/\.0$/, "")}k`;
  }
  return String(v);
}

function setMealBudgetMark(markEl, mealBudget, modelWindow) {
  if (!markEl) return;
  const budget = Number(mealBudget);
  const window = Number(modelWindow);
  if (!window || window <= 0 || Number.isNaN(budget) || budget <= 0) {
    markEl.style.display = "none";
    return;
  }
  const pct = Math.max(0, Math.min(100, (budget / window) * 100));
  markEl.style.display = "";
  markEl.style.left = `${pct}%`;
  markEl.title = `Product meal budget ${formatTokenCount(budget)} (${pct.toFixed(1)}% of model window)`;
}

function renderContextMeters(s) {
  const ctx = (s && s.context) || null;
  const used = ctx ? ctx.meal_used_tokens : null;
  const mealBudget = ctx ? ctx.meal_budget_tokens : 50000;
  const modelWindow = ctx ? ctx.model_window_tokens : 500000;
  const windowFrac = ctx ? ctx.window_used_fraction : null;
  const mealFrac = ctx ? ctx.meal_used_fraction : null;

  const label =
    used != null
      ? `${formatTokenCount(used)} / ${formatTokenCount(modelWindow)}`
      : `— / ${formatTokenCount(modelWindow)}`;

  if (railContextPct) railContextPct.textContent = label;
  if (contextWindowPct) contextWindowPct.textContent = label;
  if (contextMealPct) {
    contextMealPct.textContent =
      used != null
        ? `${formatTokenCount(used)} / ${formatTokenCount(mealBudget)}`
        : `— / ${formatTokenCount(mealBudget)}`;
  }

  // Model bar: usedMode so small fills stay visible without "crit" at empty.
  setUsageBar(railContextBar, windowFrac, { usedMode: true });
  setUsageBar(contextWindowBar, windowFrac, { usedMode: true });
  setUsageBar(contextMealBar, mealFrac, { usedMode: true });
  setMealBudgetMark(railContextMealMark, mealBudget, modelWindow);
  setMealBudgetMark(contextMealMark, mealBudget, modelWindow);

  if (railContextMeta) {
    railContextMeta.textContent = `meal ≤${formatTokenCount(mealBudget)} · model ${formatTokenCount(modelWindow)}`;
  }
  if (contextDetail) {
    const hop = ctx && ctx.hop != null ? ` · hop ${ctx.hop}` : "";
    contextDetail.textContent =
      `Last meal ${formatTokenCount(used)} of meal budget ${formatTokenCount(mealBudget)}` +
      ` (${formatTokenCount(modelWindow)} model window)${hop}. ` +
      `Gold mark = meal budget on model bar. Heuristic tokens (len/4); for memory source-split planning.`;
  }
}

function setUsageBar(barEl, frac, { usedMode = false, unavailable = false } = {}) {
  if (!barEl) return;
  barEl.classList.remove("usage-bar-warn", "usage-bar-crit", "usage-bar-na");
  if (unavailable || frac == null || Number.isNaN(Number(frac))) {
    barEl.style.width = "0%";
    barEl.classList.add("usage-bar-na");
    return;
  }
  const raw = Math.max(0, Math.min(1, Number(frac) || 0));
  // fill width = raw (remaining for Elyra bars; used fraction for SuperGrok).
  // usedMode only flips warn/crit thresholds onto remaining = 1 - used.
  const fill = raw;
  const remaining = usedMode ? 1 - raw : raw;
  barEl.style.width = `${Math.round(fill * 100)}%`;
  if (remaining <= 0.05) barEl.classList.add("usage-bar-crit");
  else if (remaining <= 0.2) barEl.classList.add("usage-bar-warn");
}

/**
 * SuperGrok pool meter view — shared by Status usage card and rail mini meter.
 * Returns { available, usedFrac, label } with Status-parity availability/labels.
 * KD11: never invent a simplified “ok” bar when stale / poll error.
 */
function supergrokMeterView(usage) {
  const sg = (usage && usage.supergrok) || null;
  const sgPct =
    usage && usage.credit_usage_percent != null
      ? usage.credit_usage_percent
      : sg && sg.credit_usage_percent != null
        ? sg.credit_usage_percent
        : null;
  const sgStatus = (sg && sg.status) || (usage && usage.credits_status) || null;
  const sgStale = Boolean(sg && sg.stale);
  const available =
    sgPct != null &&
    Number.isFinite(Number(sgPct)) &&
    !sgStale &&
    (sgStatus == null || sgStatus === "ok");
  if (available) {
    return {
      available: true,
      usedFrac: Number(sgPct) / 100,
      label: `${Math.round(Number(sgPct))}% used`,
    };
  }
  if (sgStale) {
    return { available: false, usedFrac: null, label: "— · stale" };
  }
  if (sgStatus && sgStatus !== "ok") {
    return { available: false, usedFrac: null, label: `— · ${sgStatus}` };
  }
  return { available: false, usedFrac: null, label: "— · poll …" };
}

/**
 * Pure usage card badge label from status usage block.
 * Stop text only from hard_stop — soft day/hour flags never invent a stop badge.
 */
function usageBadgeLabel(usage) {
  if (!usage) return "n/a";
  if (!usage.enabled) return "off";
  const hardStop = usage.hard_stop || null;
  const overrideActive = Boolean(usage.override_active);
  if (hardStop && !overrideActive) return `stop · ${hardStop}`;
  if (hardStop && overrideActive) return "override";
  // Soft day/hour exhaustion is detail-only (pace shown separately).
  return "ok";
}

/**
 * Provider-aware rail pill (#pill-provider). Matrix (design §6.3 / KD14):
 * xai auth / limit / ovrd / busy (chat_busy only) / ready; stub llm; local off.
 * Never "local ready".
 */
function renderProviderPill(s) {
  if (!pillProvider) return;
  const provider = (s && s.provider) || "xai";
  const usage = (s && s.usage) || {};
  const hardStop = usage.hard_stop || null;
  const overrideActive = Boolean(usage.override_active);
  const credentialOk = s && s.credential_ok !== false;
  const chatError = (s && s.chat_error) || null;
  const chatBusy = Boolean(s && s.chat_busy);

  // Stub wins for any provider.
  if (chatError === "stub_llm") {
    setPill(pillProvider, "stub llm", "pill-off");
    return;
  }

  // Local: never show ready this pass.
  if (provider === "local") {
    setPill(pillProvider, "local off", "pill-off");
    return;
  }

  // xai (and any non-local remote)
  if (!credentialOk) {
    setPill(pillProvider, `${provider} auth`, "pill-off");
    return;
  }
  if (hardStop && !overrideActive) {
    setPill(pillProvider, `${provider} limit`, "pill-off");
    return;
  }
  if (hardStop && overrideActive) {
    setPill(pillProvider, `${provider} ovrd`, "pill-busy");
    return;
  }
  // Busy only when chat_busy is true (gate); not worker/phase.
  if (chatBusy) {
    setPill(pillProvider, `${provider} busy`, "pill-busy");
    return;
  }
  setPill(pillProvider, `${provider} ready`, "pill-on");
}

/**
 * Status-safe credential_detail → operator CTA (mirrors design CTA table;
 * never includes tokens). Used by hard-stop banner + OAuth panel.
 */
const OAUTH_REAUTH_DETAILS = new Set([
  "missing_oauth_tokens",
  "invalid_oauth_tokens",
  "oauth_token_expired",
  "oauth_refresh_failed",
  "oauth_reauth_required",
  "oauth_denied",
  "oauth_device_expired",
  "oauth_ineligible",
  "oauth_pending",
]);

function credentialDetailCta(detail) {
  if (!detail) return null;
  const map = {
    missing_oauth_tokens: "Log in with xAI in Status (or elyra auth login).",
    invalid_oauth_tokens: "Log in again; if it persists, log out then log in.",
    oauth_token_expired: "Re-login with xAI if refresh also failed.",
    oauth_refresh_failed: "Wait / retry; check network; re-login if persistent.",
    oauth_reauth_required: "Log in with xAI again.",
    oauth_denied: "Retry login and approve on the consent screen.",
    oauth_device_expired: "Start login again — the device code timed out.",
    oauth_ineligible:
      "Account not eligible for this client/scopes — try API key or contact xAI.",
    oauth_pending: "Complete the verification URL + user code in your browser.",
    missing_auth_json:
      "Missing Grok Build auth.json — use Elyra xAI login (recommended) or grok login.",
    invalid_auth_json: "Invalid auth.json — re-run grok login or switch to Elyra xAI login.",
    missing_token: "auth.json has no access token — re-run grok login or use Elyra xAI login.",
    token_expired:
      "Grok Build token expired — use Elyra xAI login (recommended) or grok login.",
    missing_api_key: "Missing API key — paste key in Status or set XAI_API_KEY.",
    empty_api_key: "Empty API key rejected.",
    unknown_source: "Unknown credential source.",
    client_build_failed: "Failed to build chat client.",
    credential_unavailable: "Credentials unavailable.",
  };
  return map[detail] || detail;
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
    const cta = credentialDetailCta(detail);
    hardStopBanner.textContent = cta
      ? `Auth paused — ${detail}. ${cta}`
      : `Auth paused — ${detail}. Model moments will not open until credentials resolve.`;
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

/** Visual only — does NOT touch lastReasoningEffort. Shared by Status + rail. */
function paintEffortUI(effort) {
  const e = ["low", "medium", "high"].includes(effort) ? effort : "high";
  document.querySelectorAll(".effort-btn[data-effort]").forEach((btn) => {
    const val = btn.getAttribute("data-effort");
    if (val === "auto") return; // stays disabled stub
    const on = val === e;
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.classList.toggle("effort-btn-active", on);
  });
}

/** Server sync: paint + commit last* (only path that assigns lastReasoningEffort). */
function commitEffortFromStatus(effort) {
  const e = ["low", "medium", "high"].includes(effort) ? effort : "high";
  lastReasoningEffort = e;
  paintEffortUI(e);
}

/** Active (non-Auto) effort buttons across Status + rail. */
function effortActiveButtons() {
  return document.querySelectorAll(
    '.effort-btn[data-effort]:not([data-effort="auto"])'
  );
}

function setEffortButtonsDisabled(disabled) {
  effortActiveButtons().forEach((btn) => {
    btn.disabled = Boolean(disabled);
  });
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
      // Ensure option exists if server returns a known source we ship.
      if (
        s.credential_source &&
        !Array.from(providerCredentialSelect.options).some(
          (o) => o.value === s.credential_source
        )
      ) {
        const opt = document.createElement("option");
        opt.value = s.credential_source;
        opt.textContent = s.credential_source;
        providerCredentialSelect.appendChild(opt);
      }
      providerCredentialSelect.value = s.credential_source;
      lastCredentialSource = s.credential_source;
    }
    commitEffortFromStatus((s && s.reasoning_effort) || "high");
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
  renderOauthLoginPanel(s);
}

/**
 * Paint xAI OAuth login panel from status (+ local pending public fields).
 * Never displays access_token / refresh_token / device_code.
 */
function renderOauthLoginPanel(s) {
  const source = (s && s.credential_source) || lastCredentialSource || "";
  const detail = (s && s.credential_detail) || "";
  const oauthConfigured = Boolean(s && s.oauth_configured);
  lastOauthConfigured = oauthConfigured;

  const reauthCta =
    OAUTH_REAUTH_DETAILS.has(detail) ||
    (source === "xai_oauth" && s && s.credential_ok === false);
  const prominent = source === "xai_oauth" || reauthCta || oauthDevicePending;

  if (oauthLoginStack) {
    oauthLoginStack.classList.toggle("oauth-login-prominent", Boolean(prominent));
  }

  if (oauthLegacyBanner) {
    oauthLegacyBanner.hidden = source !== "grok_build";
  }

  if (oauthCtaBanner) {
    if (reauthCta && !oauthDevicePending) {
      const cta = credentialDetailCta(detail) || "Log in with xAI.";
      oauthCtaBanner.hidden = false;
      oauthCtaBanner.textContent = detail ? `${detail} — ${cta}` : cta;
    } else {
      oauthCtaBanner.hidden = true;
      oauthCtaBanner.textContent = "";
    }
  }

  if (oauthMeta) {
    if (oauthDevicePending) {
      oauthMeta.textContent = "Login in progress — complete the code below.";
    } else if (oauthConfigured) {
      const parts = ["xAI login configured"];
      const email = (s && s.credential_email) || "";
      // Only show email when active source is oauth (avoids stale email on other sources).
      if (email && source === "xai_oauth") parts.push(email);
      if (s && s.credential_expires_at && source === "xai_oauth") {
        parts.push(`exp ${s.credential_expires_at}`);
      }
      parts.push("(tokens never shown)");
      oauthMeta.textContent = parts.join(" · ");
    } else {
      oauthMeta.textContent = "not configured — Log in with xAI to store tokens in this instance";
    }
  }

  if (oauthLogoutBtn) {
    oauthLogoutBtn.hidden = !oauthConfigured || oauthDevicePending;
    oauthLogoutBtn.disabled = oauthActionInFlight;
  }

  if (oauthCancelBtn) {
    oauthCancelBtn.hidden = !oauthDevicePending;
    oauthCancelBtn.disabled = oauthActionInFlight;
  }

  if (oauthLoginBtn) {
    // Debounce / disable while pending or action in flight.
    oauthLoginBtn.disabled = oauthActionInFlight || oauthDevicePending;
    oauthLoginBtn.textContent = oauthDevicePending
      ? "Login pending…"
      : oauthConfigured
        ? "Re-login with xAI"
        : "Log in with xAI";
  }

  if (oauthActivateCheckbox) {
    oauthActivateCheckbox.disabled = oauthActionInFlight || oauthDevicePending;
  }

  paintOauthPendingPanel(oauthPendingPublic);
}

function paintOauthPendingPanel(publicFields) {
  if (!oauthPendingPanel) return;
  if (!oauthDevicePending || !publicFields) {
    oauthPendingPanel.hidden = true;
    return;
  }
  oauthPendingPanel.hidden = false;

  const userCode = publicFields.user_code || "";
  const uri =
    publicFields.verification_uri_complete ||
    publicFields.verification_uri ||
    "";
  const plainUri = publicFields.verification_uri || uri;

  if (oauthUserCode) {
    oauthUserCode.textContent = userCode || "—";
  }
  if (oauthVerifyLink) {
    if (uri) {
      oauthVerifyLink.href = uri;
      oauthVerifyLink.textContent = publicFields.verification_uri_complete
        ? "Open verification page (pre-filled code)"
        : plainUri || "Open verification page";
      oauthVerifyLink.hidden = false;
    } else {
      oauthVerifyLink.removeAttribute("href");
      oauthVerifyLink.textContent = "Verification URL unavailable — start login again";
    }
  }
  if (oauthPendingLabel) {
    oauthPendingLabel.textContent = userCode
      ? "Waiting for authorization on auth.x.ai…"
      : "Waiting for authorization…";
  }
  if (oauthCopyCodeBtn) oauthCopyCodeBtn.disabled = !userCode;
  if (oauthCopyUriBtn) oauthCopyUriBtn.disabled = !uri;
}

function stopOauthDevicePoll() {
  if (oauthPollTimer != null) {
    clearInterval(oauthPollTimer);
    oauthPollTimer = null;
  }
}

function startOauthDevicePoll() {
  stopOauthDevicePoll();
  oauthPollTimer = setInterval(() => {
    pollOauthDeviceStatus().catch(() => {
      /* transient; next tick retries */
    });
  }, 1500);
  // Immediate first poll after a short beat so start response paints first.
  setTimeout(() => {
    pollOauthDeviceStatus().catch(() => {});
  }, 400);
}

/**
 * Strip any accidental secret keys from client-held OAuth public state.
 * Defense-in-depth: API already never returns these.
 */
function publicOauthFieldsOnly(data) {
  if (!data || typeof data !== "object") return null;
  const out = {};
  for (const key of [
    "user_code",
    "verification_uri",
    "verification_uri_complete",
    "expires_in",
    "interval",
    "state",
    "detail",
    "email",
    "expires_at",
    "pending",
    "ok",
  ]) {
    if (data[key] !== undefined && data[key] !== null) out[key] = data[key];
  }
  // Explicitly never retain these even if server misbehaves.
  delete out.access_token;
  delete out.refresh_token;
  delete out.device_code;
  delete out.id_token;
  return out;
}

async function startXaiDeviceLogin() {
  if (oauthActionInFlight || oauthDevicePending) return;
  oauthActionInFlight = true;
  if (oauthLoginBtn) oauthLoginBtn.disabled = true;
  try {
    const activate = oauthActivateCheckbox
      ? Boolean(oauthActivateCheckbox.checked)
      : true;
    const data = await fetchJson("/api/auth/xai/device/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ activate }),
    });
    const pub = publicOauthFieldsOnly(data);
    if (!data || data.ok === false) {
      const detail = (data && data.detail) || (data && data.error) || "device_start_failed";
      oauthDevicePending = false;
      oauthPendingPublic = null;
      stopOauthDevicePoll();
      showNotice(`xAI login failed to start — ${detail}`);
      return;
    }
    oauthDevicePending = true;
    oauthPendingPublic = pub;
    paintOauthPendingPanel(pub);
    startOauthDevicePoll();
    showNotice("xAI login started — open the link and enter the code.");
  } catch (err) {
    oauthDevicePending = false;
    oauthPendingPublic = null;
    stopOauthDevicePoll();
    showNotice(String(err.message || err));
  } finally {
    oauthActionInFlight = false;
    // Re-paint so disabled state reflects pending vs idle.
    renderOauthLoginPanel({
      credential_source: lastCredentialSource,
      oauth_configured: lastOauthConfigured,
    });
  }
}

async function pollOauthDeviceStatus() {
  if (!oauthDevicePending) return;
  let data;
  try {
    data = await fetchJson("/api/auth/xai/device/status");
  } catch (err) {
    // 503 provider unavailable mid-flow: surface once, keep panel.
    if (err && err.status === 503) {
      if (oauthPendingLabel) {
        oauthPendingLabel.textContent =
          "Provider unavailable — retry shortly or start login again.";
      }
    }
    return;
  }
  const state = (data && data.state) || "idle";
  const pub = publicOauthFieldsOnly(data);

  if (state === "pending") {
    // Merge public fields so user_code survives status payloads that omit them
    // only when we already have them; status should include them while pending.
    oauthPendingPublic = {
      ...(oauthPendingPublic || {}),
      ...(pub || {}),
    };
    paintOauthPendingPanel(oauthPendingPublic);
    return;
  }

  // Terminal or idle (process restart mid-flow).
  stopOauthDevicePoll();
  oauthDevicePending = false;

  if (state === "success") {
    oauthPendingPublic = null;
    paintOauthPendingPanel(null);
    const email = (data && data.email) || "";
    showNotice(
      email
        ? `xAI login complete (${email}). Tokens stored in this instance.`
        : "xAI login complete. Tokens stored in this instance."
    );
    await refreshStatus();
    return;
  }

  if (state === "cancelled") {
    oauthPendingPublic = null;
    paintOauthPendingPanel(null);
    showNotice("xAI login cancelled.");
    await refreshStatus();
    return;
  }

  if (state === "error") {
    oauthPendingPublic = null;
    paintOauthPendingPanel(null);
    const detail = (data && data.detail) || "oauth_device_error";
    const cta = credentialDetailCta(detail);
    showNotice(cta ? `xAI login failed — ${detail}. ${cta}` : `xAI login failed — ${detail}`);
    await refreshStatus();
    return;
  }

  // idle: process likely restarted mid-flow (in-memory session gone).
  oauthPendingPublic = null;
  paintOauthPendingPanel(null);
  showNotice("Login session lost (server restarted?) — start login again.");
  await refreshStatus();
}

async function cancelXaiDeviceLogin() {
  if (oauthActionInFlight) return;
  oauthActionInFlight = true;
  if (oauthCancelBtn) oauthCancelBtn.disabled = true;
  try {
    await fetchJson("/api/auth/xai/device/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    stopOauthDevicePoll();
    oauthDevicePending = false;
    oauthPendingPublic = null;
    paintOauthPendingPanel(null);
    showNotice("xAI login cancelled.");
    await refreshStatus();
  } catch (err) {
    showNotice(String(err.message || err));
  } finally {
    oauthActionInFlight = false;
    renderOauthLoginPanel({
      credential_source: lastCredentialSource,
      oauth_configured: lastOauthConfigured,
    });
  }
}

async function logoutXaiOauth() {
  if (oauthActionInFlight || oauthDevicePending) return;
  oauthActionInFlight = true;
  if (oauthLogoutBtn) oauthLogoutBtn.disabled = true;
  try {
    // Canonical logout path (PR3): POST /api/auth/xai/logout
    await fetchJson("/api/auth/xai/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    showNotice("xAI login cleared for this instance.");
    await refreshStatus();
  } catch (err) {
    showNotice(String(err.message || err));
  } finally {
    oauthActionInFlight = false;
    renderOauthLoginPanel({
      credential_source: lastCredentialSource,
      oauth_configured: lastOauthConfigured,
    });
  }
}

async function copyOauthText(text, btn, label) {
  if (!text) {
    showNotice(`Nothing to copy (${label}).`);
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    if (btn) {
      const prev = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(() => {
        btn.textContent = prev || label;
      }, 1200);
    }
  } catch {
    showNotice(`Copy failed — select the ${label} manually.`);
  }
}

/**
 * If the browser reloaded mid device-flow, the server still has the pending
 * session — resume polling and re-show user_code / verification URI.
 */
async function maybeResumeOauthDeviceSession() {
  if (oauthDevicePending || oauthActionInFlight) return;
  try {
    const data = await fetchJson("/api/auth/xai/device/status");
    if (!data || data.state !== "pending") return;
    oauthDevicePending = true;
    oauthPendingPublic = publicOauthFieldsOnly(data);
    paintOauthPendingPanel(oauthPendingPublic);
    startOauthDevicePoll();
    renderOauthLoginPanel({
      credential_source: lastCredentialSource,
      oauth_configured: lastOauthConfigured,
      credential_detail: (data && data.detail) || "oauth_pending",
      credential_ok: false,
    });
  } catch {
    /* provider offline / 503 — ignore on boot */
  }
}

function renderUsageCard(s) {
  const usage = (s && s.usage) || null;
  const enabled = Boolean(usage && usage.enabled);
  const overrideActive = Boolean(usage && usage.override_active);
  // True hard stop only (account|week|day|hour). Soft day/hour alone never
  // sets hard_stop when day/hour hard flags are off — do not invent stop badge.
  const hardStop = (usage && usage.hard_stop) || null;
  const badgeLabel = usageBadgeLabel(usage);

  if (usageBadge) {
    usageBadge.textContent = badgeLabel;
    if (badgeLabel === "n/a" || badgeLabel === "off") {
      usageBadge.classList.remove("badge-open", "badge-bad");
    } else if (badgeLabel.startsWith("stop ·")) {
      usageBadge.classList.remove("badge-open");
      usageBadge.classList.add("badge-bad");
    } else {
      // "ok" or "override" — not a blocking stop
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
  // Rail compact: Elyra week only (same source + formatPctRemaining as Status).
  if (railUsageWeekPct) railUsageWeekPct.textContent = formatPctRemaining(week);
  setUsageBar(railUsageWeekBar, week);

  // SuperGrok pool: shared helper so Status + rail stay in lockstep (KD11).
  const sgView = supergrokMeterView(usage);
  if (usageSgPct) usageSgPct.textContent = sgView.label;
  if (railUsageSgPct) railUsageSgPct.textContent = sgView.label;
  if (sgView.available) {
    setUsageBar(usageSgBar, sgView.usedFrac, { usedMode: true });
    setUsageBar(railUsageSgBar, sgView.usedFrac, { usedMode: true });
  } else {
    setUsageBar(usageSgBar, null, { unavailable: true });
    setUsageBar(railUsageSgBar, null, { unavailable: true });
  }

  // Pace badge + burst remaining (derived max(0, BurstMax − over)).
  const paceBand = (usage && usage.pace_band) || "green";
  if (usagePaceBadge) {
    usagePaceBadge.textContent = enabled ? paceBand : "—";
    usagePaceBadge.dataset.band = enabled ? paceBand : "green";
  }
  if (usageBurst) {
    if (!usage || !enabled) {
      usageBurst.textContent = "—";
    } else {
      const rem = usage.burst_remaining_tokens;
      const max = usage.burst_max_tokens;
      usageBurst.textContent =
        rem != null && max != null ? `burst ${rem}/${max}` : "burst —";
    }
  }

  if (usageDetail) {
    if (!usage) {
      usageDetail.textContent = "Usage meter not bound.";
    } else if (!enabled) {
      usageDetail.textContent = "Usage meter disabled.";
    } else {
      const parts = [];
      if (usage.week_used_tokens != null) {
        parts.push(
          `Elyra week ${usage.week_used_tokens}/${usage.week_limit_tokens ?? usage.elyra_week_budget_tokens ?? "—"}`
        );
      }
      if (usage.pace_ratio != null) {
        parts.push(`pace ${Number(usage.pace_ratio).toFixed(2)}`);
      }
      if (usage.day_soft_exhausted) {
        parts.push("day pace high (soft)");
      }
      if (usage.hour_soft_exhausted) {
        parts.push("hour pace high (soft)");
      }
      if (usage.day_used_tokens != null && usage.day_hard_stop_enabled) {
        parts.push(
          `day ${usage.day_used_tokens}/${usage.day_limit_tokens ?? "—"}`
        );
      }
      if (usage.hour_used_tokens != null && usage.hour_hard_stop_enabled) {
        parts.push(
          `hour ${usage.hour_used_tokens}/${usage.hour_limit_tokens ?? "—"}`
        );
      }
      const sttCalls = usage.week_stt_calls;
      const ttsCalls = usage.week_tts_calls;
      if (sttCalls != null || ttsCalls != null) {
        parts.push(
          `stt ${sttCalls ?? 0} · tts ${ttsCalls ?? 0}`
        );
      }
      if (usage.last_record_at) parts.push(`last ${usage.last_record_at}`);
      usageDetail.textContent = parts.length ? parts.join(" · ") : "no usage yet";
    }
  }

  // product_usage collapsed under details (diagnostic only; Status card only).
  const productUsage = usage && usage.supergrok && usage.supergrok.product_usage;
  if (usageProductUsage && usageProductUsageBody) {
    if (
      productUsage &&
      typeof productUsage === "object" &&
      Object.keys(productUsage).length
    ) {
      usageProductUsage.hidden = false;
      usageProductUsageBody.textContent = Object.entries(productUsage)
        .map(([k, v]) => `${k}: ${v}`)
        .join("\n");
    } else {
      usageProductUsage.hidden = true;
      usageProductUsageBody.textContent = "";
    }
  }

  if (usageOverrideToggle && !usageOverrideInFlight) {
    usageOverrideToggle.checked = overrideActive;
    usageOverrideToggle.disabled = !enabled;
  }
  // Never clobber lastOverrideActive mid-PATCH with a concurrent status poll
  // (stale ON would undo a successful OFF on error rollback).
  if (!usageOverrideInFlight) {
    lastOverrideActive = overrideActive;
  }
  if (usageOverrideMeta) {
    usageOverrideMeta.textContent = overrideActive
      ? "override ON"
      : "default off";
  }
}

async function patchProvider(body) {
  if (providerPatchInFlight) return;
  // Never send Auto (UI-only stub); reject before flight.
  if (body && body.reasoning_effort === "auto") {
    return;
  }
  providerPatchInFlight = true;
  if (providerModelSelect) providerModelSelect.disabled = true;
  if (providerCredentialSelect) providerCredentialSelect.disabled = true;
  setEffortButtonsDisabled(true);
  try {
    await fetchJson("/api/provider", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    // Clear in-flight before status paint so commitEffortFromStatus runs
    // (success path: refreshStatus → renderProviderCard → commit last*).
    providerPatchInFlight = false;
    await refreshStatus();
  } catch (err) {
    if (providerModelSelect && lastProviderModel != null) {
      providerModelSelect.value = lastProviderModel;
    }
    if (providerCredentialSelect && lastCredentialSource != null) {
      providerCredentialSelect.value = lastCredentialSource;
    }
    // Revert optimistic effort paint; lastReasoningEffort is still pre-click.
    paintEffortUI(lastReasoningEffort);
    showNotice(String(err.message || err));
  } finally {
    providerPatchInFlight = false;
    if (providerModelSelect) providerModelSelect.disabled = false;
    if (providerCredentialSelect) providerCredentialSelect.disabled = false;
    setEffortButtonsDisabled(false);
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
  const want = Boolean(active);
  // Coalesce toggles while a PATCH is in flight so OFF is never dropped.
  if (usageOverrideInFlight) {
    pendingOverrideTarget = want;
    if (usageOverrideToggle) usageOverrideToggle.checked = want;
    return;
  }
  usageOverrideInFlight = true;
  pendingOverrideTarget = null;
  if (usageOverrideToggle) {
    usageOverrideToggle.disabled = true;
    usageOverrideToggle.checked = want;
  }
  try {
    const body = await fetchJson("/api/usage", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hard_stop_override: want }),
    });
    const serverActive = Boolean(
      body && body.usage && body.usage.override_active
    );
    lastOverrideActive = serverActive;
    if (usageOverrideToggle) usageOverrideToggle.checked = serverActive;
    if (usageOverrideMeta) {
      usageOverrideMeta.textContent = serverActive
        ? "override ON"
        : "default off";
    }
    if (serverActive !== want) {
      showNotice(
        "Hard-stop override did not stick on server — check Status after refresh."
      );
    } else if (want) {
      showNotice("Hard-stop override ON — model calls continue past budget.");
    } else {
      showNotice("Hard-stop override OFF — budget hard-stop enforced again.");
    }
    await refreshStatus();
  } catch (err) {
    if (usageOverrideToggle) usageOverrideToggle.checked = lastOverrideActive;
    showNotice(String(err.message || err));
  } finally {
    usageOverrideInFlight = false;
    if (usageOverrideToggle) {
      usageOverrideToggle.disabled = false;
    }
    // Apply last click during flight (e.g. ON then OFF before first finished).
    if (pendingOverrideTarget !== null) {
      const next = pendingOverrideTarget;
      pendingOverrideTarget = null;
      if (next !== lastOverrideActive) {
        void setHardStopOverride(next);
      } else if (usageOverrideToggle) {
        usageOverrideToggle.checked = lastOverrideActive;
      }
    }
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

function renderSemanticWait(s) {
  const d = (s && s.semantic_wait) || {};
  const enabled = d.enabled !== undefined ? Boolean(d.enabled) : true;
  const maxMs =
    typeof d.max_ms === "number" && !Number.isNaN(d.max_ms)
      ? d.max_ms
      : 15000;
  // Prefer live effective/snappy from status (settings.semantic_select_max_ms).
  const effective =
    typeof d.effective_select_max_ms === "number" &&
    !Number.isNaN(d.effective_select_max_ms)
      ? d.effective_select_max_ms
      : enabled
        ? maxMs
        : typeof d.snappy_select_max_ms === "number" &&
            !Number.isNaN(d.snappy_select_max_ms)
          ? d.snappy_select_max_ms
          : 50;
  lastSemanticWaitEnabled = enabled;
  lastSemanticWaitMaxMs = maxMs;

  if (!semanticWaitInFlight) {
    if (semanticWaitToggle) semanticWaitToggle.checked = enabled;
    if (semanticWaitMaxMs && document.activeElement !== semanticWaitMaxMs) {
      semanticWaitMaxMs.value = String(Math.round(maxMs));
    }
  }
  if (semanticWaitBadge) {
    semanticWaitBadge.textContent = enabled ? "on" : "off";
    semanticWaitBadge.classList.toggle("badge-open", enabled);
  }
  if (semanticWaitMeta) {
    semanticWaitMeta.textContent = enabled
      ? `up to ${Math.round(maxMs / 1000)}s for encode+search`
      : `off — snappy omit (${Math.round(effective)}ms)`;
  }
}

async function patchSemanticWait(body) {
  if (semanticWaitInFlight) return;
  semanticWaitInFlight = true;
  if (semanticWaitToggle) semanticWaitToggle.disabled = true;
  if (semanticWaitMaxMs) semanticWaitMaxMs.disabled = true;
  try {
    await fetchJson("/api/semantic-wait", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await refreshStatus();
  } catch (err) {
    if (semanticWaitToggle) semanticWaitToggle.checked = lastSemanticWaitEnabled;
    if (semanticWaitMaxMs) semanticWaitMaxMs.value = String(lastSemanticWaitMaxMs);
    showNotice(String(err.message || err));
  } finally {
    semanticWaitInFlight = false;
    if (semanticWaitToggle) semanticWaitToggle.disabled = false;
    if (semanticWaitMaxMs) semanticWaitMaxMs.disabled = false;
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
  renderContextMeters(s);
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
  renderSemanticWait(s);
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

async function refreshGoals(opts = {}) {
  const force = Boolean(opts.force);
  const data = await fetchJson("/api/goals");
  const goals = data.goals || [];
  const fp = stableFingerprint(goals);
  if (
    !force &&
    fp === lastGoalsFp &&
    goalsList &&
    goalsList.childElementCount > 0
  ) {
    return;
  }
  lastGoalsFp = fp;
  renderGoals(goals);
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

function formatBeatTs(ts) {
  if (!ts) return "";
  const s = String(ts);
  // Compact ISO for operators: 2026-07-30T12:34:56Z → 07-30 12:34:56Z
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
  if (m) return `${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}Z`;
  return s.length > 24 ? s.slice(0, 24) : s;
}

function beatKindClass(type) {
  const t = String(type || "beat").toLowerCase();
  if (t === "speak" || t === "assistant" || t === "model") return "beat-kind-speak";
  if (t === "tool" || t === "tool_call" || t === "tool_result") return "beat-kind-tool";
  if (t === "obs" || t === "observation" || t === "host") return "beat-kind-obs";
  if (t === "stop" || t === "error") return "beat-kind-stop";
  if (t === "user" || t === "social") return "beat-kind-user";
  return "beat-kind-other";
}

function appendBeatRawFold(row, label, text) {
  if (text == null || text === "") return;
  const details = document.createElement("details");
  details.className = "reason-fold beat-raw-fold";
  const summary = document.createElement("summary");
  summary.textContent = label;
  details.appendChild(summary);
  const pre = document.createElement("pre");
  pre.className = "beat-body";
  pre.textContent = String(text);
  details.appendChild(pre);
  row.appendChild(details);
}

/**
 * Best-effort pretty-print for tool (and other) beat bodies.
 * Host stores tool content as compact json.dumps (no indent); Moments should
 * show the same delimited structure as "raw fields" (BUG-glass-01 residual).
 * @returns {string|null} pretty JSON, or null if not parseable object/array JSON
 */
function tryPrettyJsonContent(raw) {
  if (raw == null) return null;
  const s = String(raw).trim();
  if (!s) return null;
  // Compact dumps always start with { or [; skip playbooks / prose.
  if (s[0] !== "{" && s[0] !== "[") return null;
  try {
    const parsed = JSON.parse(s);
    if (parsed === null || typeof parsed !== "object") return null;
    return JSON.stringify(parsed, null, 2);
  } catch {
    // Truncated tool_result_max_chars may break JSON — fall back to plain pre.
    return null;
  }
}

function appendBeatContentBody(row, content, { preferJson } = {}) {
  const text = String(content);
  const pretty = preferJson || text.trimStart().startsWith("{") || text.trimStart().startsWith("[")
    ? tryPrettyJsonContent(text)
    : null;
  if (pretty != null) {
    const pre = document.createElement("pre");
    pre.className = "beat-body beat-json-body";
    pre.textContent = pretty;
    row.appendChild(pre);
    return;
  }
  if (preferJson) {
    // Tool/playbook path: never markdown-collapse; preserve newlines.
    const pre = document.createElement("pre");
    pre.className = "beat-body";
    pre.textContent = text;
    row.appendChild(pre);
    return;
  }
  const prose = document.createElement("div");
  prose.className = "beat-prose";
  prose.innerHTML = renderMarkdown(text);
  row.appendChild(prose);
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
    const type = b.type || "beat";
    const isTool =
      type === "tool" || type === "tool_call" || type === "tool_result";
    row.className = `beat ${beatKindClass(type)}`;
    const head = document.createElement("div");
    head.className = "beat-head";
    const okBadge =
      isTool && typeof b.ok === "boolean"
        ? `<span class="badge ${b.ok ? "badge-open" : "badge-bad"}">${
            b.ok ? "ok" : "fail"
          }</span>`
        : "";
    head.innerHTML = `<span class="badge beat-kind-chip">${escapeHtml(
      type
    )}</span>${okBadge}
      <span class="meta beat-ts">${escapeHtml(formatBeatTs(b.ts))}</span>`;
    row.appendChild(head);

    // Tool name + host ok before body so the JSON isn't the only chrome.
    const toolName = b.name || b.tool;
    if (toolName) {
      const toolLine = document.createElement("div");
      toolLine.className = "beat-tool-line meta";
      toolLine.textContent = `tool · ${toolName}`;
      row.appendChild(toolLine);
    }
    if (b.error_reason) {
      const err = document.createElement("div");
      err.className = "beat-error";
      err.textContent = `error · ${b.error_reason}`;
      row.appendChild(err);
    }
    if (b.stop_reason) {
      const stop = document.createElement("div");
      stop.className = "meta";
      stop.textContent = `stop · ${b.stop_reason}`;
      row.appendChild(stop);
    }

    // Tool content: compact JSON from do-loop → pretty-print like raw fields.
    // Speak/model: markdown. Obs with JSON: pretty if parseable.
    if (b.content) {
      appendBeatContentBody(row, b.content, {
        preferJson: isTool || type === "obs",
      });
    }

    // Reasoning collapsed.
    if (b.reasoning) {
      appendBeatRawFold(row, "reasoning", b.reasoning);
    }

    // Payload / residual keys as collapsible raw (not inline dump).
    if (b.payload != null && typeof b.payload === "object") {
      try {
        appendBeatRawFold(
          row,
          "raw payload",
          JSON.stringify(b.payload, null, 2).slice(0, 4000)
        );
      } catch {
        /* ignore */
      }
    }
    const skip = new Set([
      "type",
      "ts",
      "reasoning",
      "content",
      "name",
      "tool",
      "error_reason",
      "stop_reason",
      "payload",
      "ok", // already shown as badge for tools
    ]);
    const rest = {};
    for (const [k, v] of Object.entries(b)) {
      if (!skip.has(k) && v != null && v !== "") rest[k] = v;
    }
    if (Object.keys(rest).length) {
      try {
        appendBeatRawFold(
          row,
          "raw fields",
          JSON.stringify(rest, null, 2).slice(0, 4000)
        );
      } catch {
        /* ignore */
      }
    }

    wrap.appendChild(row);
  }
  return wrap;
}

function setMomentDetailOpen(on) {
  // Moments is a Memory tab (BUG-glass-02); class lives on the Memory panel.
  const panel = document.getElementById("panel-memory");
  if (panel) panel.classList.toggle("moment-detail-open", !!on);
  const tab = document.getElementById("memory-tab-moments");
  if (tab) tab.classList.toggle("moment-detail-open", !!on);
}

function closeMomentDetail() {
  momentDetailLoadGen += 1;
  selectedMomentId = null;
  selectedMomentSnapshot = null;
  momentDetail.hidden = true;
  momentDetail.innerHTML = "";
  setMomentDetailOpen(false);
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
  setMomentDetailOpen(true);
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

async function refreshMoments(opts = {}) {
  const force = Boolean(opts.force);
  const data = await fetchJson("/api/moments?limit=40");
  const moments = data.moments || [];
  // List fingerprint: identity + hop/end meta (not full beat bodies).
  const listFp = stableFingerprint(
    moments.map((m) => ({
      id: m.id,
      hop_count: m.hop_count,
      ended_at: m.ended_at,
      stop_reason: m.stop_reason,
      why_now: m.why_now,
      started_at: m.started_at,
    }))
  );
  if (
    force ||
    listFp !== lastMomentsListFp ||
    !momentsList ||
    !momentsList.childElementCount
  ) {
    lastMomentsListFp = listFp;
    renderMoments(moments);
  }
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

// ── Memory panel (PR9) ────────────────────────────────────────────────

function setMemoryTab(name) {
  memoryActiveTab = name || "context";
  document.querySelectorAll(".memory-tab").forEach((btn) => {
    const on = btn.dataset.memoryTab === memoryActiveTab;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  document.querySelectorAll(".memory-tab-panel").forEach((panel) => {
    const id = panel.id || "";
    const key = id.replace(/^memory-tab-/, "");
    const on = key === memoryActiveTab;
    panel.classList.toggle("active", on);
    if (on) panel.removeAttribute("hidden");
    else panel.setAttribute("hidden", "");
  });
}

/** Last flags fingerprint so soft Context tick does not wipe the flags strip. */
let lastMemoryFlagsFp = null;

function renderMemoryFlags(mem, opts = {}) {
  if (!memoryContextFlags) return;
  const force = Boolean(opts.force);
  const m = mem || {};
  const rows = [
    ["enabled", m.enabled === true ? "true" : "false", m.enabled === true],
    ["write_atoms", m.write_atoms === true ? "true" : "false", m.write_atoms === true],
    ["backend", m.backend || "—", null],
    ["store", m.ok ? "ok" : m.error || "down", m.ok === true],
    ["atoms", m.atom_count != null ? String(m.atom_count) : "—", null],
    ["open moment", m.active_moment_id || "—", null],
  ];
  const fp = stableFingerprint(rows);
  if (
    !force &&
    fp === lastMemoryFlagsFp &&
    memoryContextFlags.childElementCount > 0
  ) {
    return;
  }
  lastMemoryFlagsFp = fp;
  memoryContextFlags.innerHTML = "";
  for (const [label, value, good] of rows) {
    const row = document.createElement("div");
    row.className = "status-row";
    const lab = document.createElement("span");
    lab.className = "status-label";
    lab.textContent = label;
    const val = document.createElement("span");
    val.className = "status-value";
    if (good === true) val.classList.add("status-ok");
    if (good === false) val.classList.add("status-bad");
    val.textContent = value;
    row.appendChild(lab);
    row.appendChild(val);
    memoryContextFlags.appendChild(row);
  }
}

/**
 * Stable fingerprint of the meal package (not flag/status churn).
 * Used to skip full Context rebuild on the 1.5s poll (inspect flash).
 *
 * Intentionally omits meal.recorded_at: on-demand compose (no last-hop
 * snapshot) re-stamps utc_now every GET even when channel content is
 * identical — including it forced a full DOM wipe every tick and killed
 * text selection / open inspect folds.
 */
function fingerprintMemoryMeal(data) {
  const meal = (data && data.meal) || {};
  const items = Array.isArray(meal.items) ? meal.items : [];
  const fixed = meal.fixed || {};
  const parts = [
    // Prefer meal.source over envelope source (both stable when content is).
    meal.source || (data && data.source),
    meal.open_moment_id,
    meal.total_tokens,
    meal.budget_tokens,
    meal.slid_off_count,
    meal.semantic_omitted_reason,
    meal.directed_keep_omitted_reason,
    // sort_keys via stableFingerprint would be ideal; meta is small and
    // server key order is stable enough for same process.
    JSON.stringify(meal.semantic_select_meta || null),
    JSON.stringify(meal.directed_keep_meta || null),
    fixed.system && fixed.system.content_chars,
    fixed.orient && fixed.orient.content_chars,
    fixed.system && fixed.system.snippet,
    fixed.orient && fixed.orient.snippet,
  ];
  for (const it of items) {
    parts.push(
      it.channel,
      it.atom_id,
      it.label,
      it.token_estimate,
      it.content_chars,
      it.snippet,
      it.t_start,
      it.meta && it.meta.scale,
      it.meta && it.meta.atom_count,
      it.meta && Array.isArray(it.meta.atom_ids)
        ? it.meta.atom_ids.join(",")
        : ""
    );
  }
  return parts.map((x) => (x == null ? "" : String(x))).join("|");
}

/** Capture open Context inspect folds before destructive re-render. */
function captureMemoryContextUi() {
  if (!memoryContextBody) return { openAtomIds: [], openChannels: [], scrollTop: 0 };
  const openAtomIds = [];
  memoryContextBody
    .querySelectorAll("details.memory-atom-inspect-fold[data-inspect-atom-id][open]")
    .forEach((el) => {
      const id = el.getAttribute("data-inspect-atom-id");
      if (id) openAtomIds.push(id);
    });
  const openChannels = [];
  memoryContextBody
    .querySelectorAll(
      "details.memory-atom-inspect-fold[data-inspect-channel][open]"
    )
    .forEach((el) => {
      const ch = el.getAttribute("data-inspect-channel");
      if (ch) openChannels.push(ch);
    });
  const openMembers = [];
  memoryContextBody
    .querySelectorAll(
      "details.memory-atom-inspect-fold[data-inspect-members][open]"
    )
    .forEach((el) => {
      const key = el.getAttribute("data-inspect-members");
      if (key) openMembers.push(key);
    });
  return {
    openAtomIds,
    openChannels,
    openMembers,
    scrollTop: memoryContextBody.scrollTop || 0,
  };
}

/** Escape a value for use inside a double-quoted CSS attribute selector. */
function cssAttrValue(s) {
  return String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

/** Re-open inspect folds after rebuild; use cache to avoid loading flash. */
function restoreMemoryContextUi(saved) {
  if (!memoryContextBody || !saved) return;
  for (const id of saved.openAtomIds || []) {
    const details = memoryContextBody.querySelector(
      `details.memory-atom-inspect-fold[data-inspect-atom-id="${cssAttrValue(id)}"]`
    );
    if (!details) continue;
    details.open = true;
    const panel = details.querySelector(".memory-atom-inspect-panel");
    if (panel && memoryContextAtomCache.has(id)) {
      fillAtomInspectInto(panel, memoryContextAtomCache.get(id), {
        showClose: false,
      });
      panel.dataset.inspectLoaded = "1";
    }
  }
  for (const ch of saved.openChannels || []) {
    const details = memoryContextBody.querySelector(
      `details.memory-atom-inspect-fold[data-inspect-channel="${cssAttrValue(ch)}"]`
    );
    if (details) details.open = true;
  }
  for (const key of saved.openMembers || []) {
    const details = memoryContextBody.querySelector(
      `details.memory-atom-inspect-fold[data-inspect-members="${cssAttrValue(key)}"]`
    );
    if (details) details.open = true;
  }
  if (typeof saved.scrollTop === "number") {
    memoryContextBody.scrollTop = saved.scrollTop;
  }
}

function renderMemoryContext(data) {
  if (!memoryContextBody) return;
  const savedUi = captureMemoryContextUi();
  memoryContextBody.innerHTML = "";
  const mem = data.memory || {};
  // Full context rebuild: always repaint flags strip with this payload.
  renderMemoryFlags(mem, { force: true });

  if (!data.ok && !data.meal) {
    const p = document.createElement("p");
    p.className = "muted memory-empty";
    p.textContent = data.error
      ? `Context meal unavailable: ${data.error}`
      : "No meal snapshot yet. Chat once with memory.enabled, or wait for a compose.";
    memoryContextBody.appendChild(p);
    return;
  }

  const meal = data.meal || {};
  const head = document.createElement("div");
  head.className = "card memory-channel-card";
  const headTitle = document.createElement("div");
  headTitle.className = "card-head";
  headTitle.innerHTML = `<strong>Meal package</strong><span class="badge">${escapeHtml(
    String(data.source || meal.source || "—")
  )}</span>`;
  head.appendChild(headTitle);
  const meta = document.createElement("div");
  meta.className = "meta";
  const bits = [
    meal.open_moment_id ? `moment=${meal.open_moment_id}` : "moment=—",
    meal.total_tokens != null ? `memory≈${meal.total_tokens} tok` : null,
    meal.fixed_tokens != null ? `fixed≈${meal.fixed_tokens} tok` : null,
    meal.budget_tokens != null ? `budget=${meal.budget_tokens}` : null,
    meal.slid_off_count != null ? `slid_off=${meal.slid_off_count}` : null,
    meal.recorded_at ? `at ${meal.recorded_at}` : null,
  ].filter(Boolean);
  meta.textContent = bits.join(" · ");
  head.appendChild(meta);
  memoryContextBody.appendChild(head);

  // Fixed system/orient if present (same card chrome as meal channels).
  const fixed = meal.fixed || {};
  for (const key of ["system", "orient"]) {
    const block = fixed[key];
    if (!block) continue;
    memoryContextBody.appendChild(
      renderMemoryChannelCard({
        label: block.label || key,
        channel: key,
        token_estimate: block.token_estimate,
        snippet: block.snippet,
        content_chars: block.content_chars,
      })
    );
  }

  const items = Array.isArray(meal.items) ? meal.items : [];
  if (!items.length && !Object.keys(fixed).length) {
    const p = document.createElement("p");
    p.className = "muted memory-empty";
    p.textContent =
      "Meal has no labeled channels yet (empty store or meal not composed).";
    memoryContextBody.appendChild(p);
    // Still show channel status notes below if select ran.
  }

  // Group variable meal items by channel for scan hierarchy (BUG-mem-ui-01).
  const byChannel = new Map();
  for (const item of items) {
    const ch = item.channel || item.label || "other";
    if (!byChannel.has(ch)) byChannel.set(ch, []);
    byChannel.get(ch).push(item);
  }
  const channelOrder = [
    "temporal",
    "episodic",
    "semantic",
    "directed_keep",
    "summary",
  ];
  const orderedKeys = [
    ...channelOrder.filter((k) => byChannel.has(k)),
    ...[...byChannel.keys()].filter((k) => !channelOrder.includes(k)),
  ];

  const semNote = renderSemanticChannelNote(meal);
  const dkCard = renderDirectedKeepStatusCard(meal);

  // Ensure status-only channels still appear in order when they have no items.
  const ensureStatusChannel = (ch) => {
    if (!orderedKeys.includes(ch)) orderedKeys.push(ch);
  };
  if (semNote && !byChannel.has("semantic")) ensureStatusChannel("semantic");
  if (dkCard && !byChannel.has("directed_keep")) ensureStatusChannel("directed_keep");

  // Re-sort keys with fixed channel order after possible status-only inserts.
  const orderIndex = (ch) => {
    const i = channelOrder.indexOf(ch);
    return i === -1 ? 1000 : i;
  };
  orderedKeys.sort((a, b) => {
    const d = orderIndex(a) - orderIndex(b);
    if (d !== 0) return d;
    return String(a).localeCompare(String(b));
  });

  for (const ch of orderedKeys) {
    const group = byChannel.get(ch) || [];
    const statusOnly =
      !group.length &&
      ((ch === "semantic" && semNote) || (ch === "directed_keep" && dkCard));
    if (!group.length && !statusOnly) continue;

    const section = document.createElement("div");
    section.className = "memory-channel-section";
    section.dataset.channel = ch;
    const h = document.createElement("div");
    h.className = "memory-channel-section-head";
    const badge =
      group.length > 0
        ? String(group.length)
        : ch === "semantic" || ch === "directed_keep"
          ? "status"
          : "0";
    h.innerHTML = `<strong>${escapeHtml(ch)}</strong><span class="badge">${badge}</span>`;
    section.appendChild(h);

    if (ch === "semantic" && semNote) section.appendChild(semNote);
    if (ch === "directed_keep" && dkCard) section.appendChild(dkCard);

    for (const item of group) {
      section.appendChild(renderMemoryChannelCard(item));
    }
    memoryContextBody.appendChild(section);
  }

  restoreMemoryContextUi(savedUi);
}

/**
 * Human-readable semantic select status for Memory → Context.
 * PR-R2 / KD-R6: include dedupe counts so operators see retrieval ran
 * even when no semantic items were packed.
 */
function formatSemanticSelectLine(meal) {
  if (!meal) return "";
  const reason = meal.semantic_omitted_reason || null;
  const sm = meal.semantic_select_meta || null;
  if (!reason && !sm) return "";
  const parts = [];
  if (reason) {
    parts.push(`omitted (${reason})`);
  } else if (sm && sm.packed != null) {
    parts.push(`packed=${sm.packed}`);
  }
  if (sm && sm.channel) {
    const chBit = sm.channel_reason
      ? `channel=${sm.channel} (${sm.channel_reason})`
      : `channel=${sm.channel}`;
    parts.push(chBit);
  }
  // Dedupe: primary path may show raw_hits=0 (excludes in search) while probe
  // counted matches already in temporal/episodic — surface the count.
  if (reason === "deduped") {
    const n =
      sm && sm.deduped != null
        ? Number(sm.deduped)
        : sm && sm.raw_hits != null
          ? Number(sm.raw_hits)
          : null;
    if (n != null && !Number.isNaN(n) && n > 0) {
      parts.push(
        `${n} match${n === 1 ? "" : "es"} already in temporal/episodic (not re-listed as semantic)`
      );
    } else {
      parts.push(
        "matches already in temporal/episodic (not re-listed as semantic)"
      );
    }
  } else if (reason === "no_hits" && sm && sm.channel) {
    parts.push(`no candidates on ${sm.channel}`);
  } else if (reason === "timeout") {
    parts.push("encode/search exceeded meal wall-clock");
  } else if (reason === "empty_seed") {
    parts.push("no observation/speak/model text on open moment");
  } else if (reason === "encoder") {
    parts.push("embedder not warm or encode failed");
  } else if (reason === "no_index") {
    parts.push("no vector index");
  }
  if (sm && sm.deduped != null && reason !== "deduped" && Number(sm.deduped) > 0) {
    parts.push(`also_deduped=${sm.deduped}`);
  }
  if (sm && sm.elapsed_ms != null) {
    const ms = Number(sm.elapsed_ms);
    if (!Number.isNaN(ms)) {
      parts.push(ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`);
    }
  }
  if (sm && sm.wait === true) parts.push("wait=on");
  if (sm && sm.wait === false) parts.push("wait=off");
  if (!parts.length) return "";
  return parts.join(" · ");
}

/** Small Context panel card so semantic omit (esp. dedupe) is easy to see. */
function renderSemanticChannelNote(meal) {
  const line = formatSemanticSelectLine(meal);
  if (!line) return null;
  const reason = meal.semantic_omitted_reason || null;
  const sm = meal.semantic_select_meta || null;
  const card = document.createElement("div");
  card.className = "card memory-channel-card memory-semantic-note";
  if (reason === "deduped") {
    card.classList.add("memory-semantic-note-deduped");
  } else if (reason) {
    card.classList.add("memory-semantic-note-omit");
  } else {
    card.classList.add("memory-semantic-note-ok");
  }
  const head = document.createElement("div");
  head.className = "card-head";
  const title = document.createElement("strong");
  title.textContent = "Semantic";
  const badge = document.createElement("span");
  badge.className = "badge";
  if (reason === "deduped") {
    const n = sm && sm.deduped != null ? Number(sm.deduped) : null;
    badge.textContent =
      n != null && !Number.isNaN(n) ? `deduped · ${n}` : "deduped";
  } else if (reason) {
    badge.textContent = `omitted · ${reason}`;
  } else if (sm && sm.packed != null) {
    badge.textContent = `packed · ${sm.packed}`;
  } else {
    badge.textContent = "select";
  }
  head.appendChild(title);
  head.appendChild(badge);
  card.appendChild(head);
  const body = document.createElement("p");
  body.className = "memory-semantic-meta";
  body.textContent = line;
  card.appendChild(body);
  return card;
}

/**
 * Human-readable directed_keep select status (parity with formatSemanticSelectLine).
 * No leading "directed_keep" token — the card title carries the channel name.
 */
function formatDirectedKeepLine(meal) {
  if (!meal) return "";
  const reason = meal.directed_keep_omitted_reason || null;
  const dm = meal.directed_keep_meta || null;
  if (!reason && !dm) return "";
  const parts = [];
  if (reason) {
    parts.push(`omitted (${reason})`);
  } else if (dm && dm.packed != null) {
    parts.push(`packed=${dm.packed}`);
  }
  if (reason === "deduped") {
    parts.push(
      "keeps already in temporal/episodic/semantic (not re-listed as directed_keep)"
    );
  } else if (reason === "disabled") {
    parts.push("directed keep is off for this run");
  } else if (reason === "empty") {
    parts.push("no confirmed keep-set from traverse");
  } else if (reason === "budget") {
    parts.push("meal budget too small to pack keeps");
  } else if (reason) {
    parts.push("channel not packed this compose");
  }
  if (dm && dm.keep_ids_in != null) {
    const n = Number(dm.keep_ids_in);
    if (!Number.isNaN(n)) {
      parts.push(
        n === 1 ? "1 keep id in session" : `${n} keep ids in session`
      );
    }
  }
  if (dm && dm.packed != null && !reason) {
    const n = Number(dm.packed);
    if (!Number.isNaN(n) && n > 0) {
      parts.push(
        n === 1 ? "1 atom in meal" : `${n} atoms in meal`
      );
    }
  }
  return parts.join(" · ");
}

/**
 * Status card for directed_keep — same bubble chrome as Semantic note.
 */
function renderDirectedKeepStatusCard(meal) {
  const line = formatDirectedKeepLine(meal);
  if (!line) return null;
  const reason = meal.directed_keep_omitted_reason || null;
  const dm = meal.directed_keep_meta || null;
  const card = document.createElement("div");
  // Reuse semantic note visual language (title + state badge + meta body).
  card.className = "card memory-channel-card memory-semantic-note memory-channel-status-directed";
  if (reason === "deduped") {
    card.classList.add("memory-semantic-note-deduped");
  } else if (reason) {
    card.classList.add("memory-semantic-note-omit");
  } else {
    card.classList.add("memory-semantic-note-ok");
  }
  const head = document.createElement("div");
  head.className = "card-head";
  const title = document.createElement("strong");
  title.textContent = "Directed keep";
  const badge = document.createElement("span");
  badge.className = "badge";
  if (reason === "deduped") {
    badge.textContent = "deduped";
  } else if (reason) {
    badge.textContent = `omitted · ${reason}`;
  } else if (dm && dm.packed != null) {
    badge.textContent = `packed · ${dm.packed}`;
  } else {
    badge.textContent = "select";
  }
  head.appendChild(title);
  head.appendChild(badge);
  card.appendChild(head);
  const body = document.createElement("p");
  body.className = "memory-semantic-meta";
  body.textContent = line;
  card.appendChild(body);
  return card;
}

/**
 * Shared atom inspect chrome (Atoms tab detail + Context expand).
 * @param {HTMLElement} container
 * @param {object} a atom detail from GET /api/memory/atoms/:id
 * @param {{ showClose?: boolean, onClose?: () => void }} opts
 */
function fillAtomInspectInto(container, a, opts = {}) {
  container.innerHTML = "";
  const head = document.createElement("div");
  head.className = "card-head";
  const strong = document.createElement("strong");
  strong.textContent = a.kind || "atom";
  head.appendChild(strong);
  if (opts.showClose) {
    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "link-btn";
    closeBtn.id = "close-atom-detail";
    closeBtn.textContent = "close";
    if (typeof opts.onClose === "function") {
      closeBtn.addEventListener("click", opts.onClose);
    }
    head.appendChild(closeBtn);
  }
  container.appendChild(head);
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = [
    a.atom_id,
    a.moment_id ? `moment=${a.moment_id}` : null,
    a.t_start ? formatBeatTs(a.t_start) || a.t_start : null,
    a.scale ? `scale=${a.scale}` : null,
    a.embedding_status ? `embed=${a.embedding_status}` : null,
    a.prev_atom_id ? `prev=${a.prev_atom_id}` : null,
    a.next_atom_id ? `next=${a.next_atom_id}` : null,
    a.content_truncated ? "text truncated" : null,
  ]
    .filter(Boolean)
    .join(" · ");
  container.appendChild(meta);
  const text = a.content_text != null ? String(a.content_text) : "";
  const pretty = tryPrettyJsonContent(text);
  if (pretty != null) {
    const pre = document.createElement("pre");
    pre.className = "memory-snippet beat-json-body";
    pre.textContent = pretty;
    container.appendChild(pre);
  } else {
    const pre = document.createElement("pre");
    pre.className = "memory-snippet memory-snippet-prose";
    pre.style.whiteSpace = "pre-wrap";
    pre.textContent = text || "(empty)";
    container.appendChild(pre);
  }
}

/**
 * Lazy-load atom into a Context inspect fold (does not switch tabs).
 * @param {HTMLElement} panel
 * @param {string} atomId
 */
function bindContextAtomInspect(panel, atomId) {
  if (!panel || !atomId) return;
  let gen = 0;
  const run = async () => {
    if (panel.dataset.inspectLoaded === "1" && panel.childElementCount) return;
    // Prefer cache after soft restore / prior expand.
    if (memoryContextAtomCache.has(atomId)) {
      fillAtomInspectInto(panel, memoryContextAtomCache.get(atomId), {
        showClose: false,
      });
      panel.dataset.inspectLoaded = "1";
      return;
    }
    const my = ++gen;
    panel.innerHTML = `<p class="muted">loading…</p>`;
    try {
      const data = await fetchJson(
        `/api/memory/atoms/${encodeURIComponent(atomId)}`
      );
      if (my !== gen) return;
      if (!data.ok || !data.atom) {
        panel.innerHTML = `<p class="muted">${escapeHtml(
          data.error || "not found"
        )}</p>`;
        return;
      }
      memoryContextAtomCache.set(atomId, data.atom);
      // Cap cache size (simple LRU-ish drop of oldest keys).
      if (memoryContextAtomCache.size > 40) {
        const first = memoryContextAtomCache.keys().next().value;
        if (first != null) memoryContextAtomCache.delete(first);
      }
      fillAtomInspectInto(panel, data.atom, { showClose: false });
      panel.dataset.inspectLoaded = "1";
    } catch (err) {
      if (my !== gen) return;
      panel.innerHTML = `<p class="muted">${escapeHtml(
        String(err.message || err)
      )}</p>`;
    }
  };
  const details = panel.closest("details");
  if (details) {
    details.addEventListener("toggle", () => {
      if (details.open) void run();
    });
    if (details.open) void run();
  } else {
    void run();
  }
}

function renderMemoryChannelCard(item) {
  const card = document.createElement("div");
  const ch = item.channel || "other";
  card.className = `card memory-channel-card memory-ch-${String(ch).replace(
    /[^a-z0-9_-]/gi,
    "_"
  )}`;
  const head = document.createElement("div");
  head.className = "card-head";
  const title = document.createElement("strong");
  title.textContent = item.label || ch || "channel";
  const badge = document.createElement("span");
  badge.className = "badge";
  const tok =
    item.token_estimate != null ? `≈${item.token_estimate} tok` : "—";
  badge.textContent = `${ch || "—"} · ${tok}`;
  head.appendChild(title);
  head.appendChild(badge);
  card.appendChild(head);

  const snippet = item.snippet != null ? String(item.snippet) : "";
  const snipLen = snippet.length;
  const fullChars =
    item.content_chars != null && !Number.isNaN(Number(item.content_chars))
      ? Number(item.content_chars)
      : null;
  const truncated = fullChars != null && fullChars > snipLen;
  const metaObj = item.meta && typeof item.meta === "object" ? item.meta : {};
  const memberIds = Array.isArray(metaObj.atom_ids)
    ? metaObj.atom_ids.filter((x) => typeof x === "string" && x.trim())
    : [];

  const meta = document.createElement("div");
  meta.className = "meta";
  const mbits = [];
  if (item.atom_id) mbits.push(item.atom_id);
  if (item.t_start) mbits.push(formatBeatTs(item.t_start) || item.t_start);
  if (metaObj.scale) mbits.push(`scale=${metaObj.scale}`);
  if (metaObj.moment_id) mbits.push(`moment=${metaObj.moment_id}`);
  if (metaObj.atom_count != null) {
    mbits.push(`${metaObj.atom_count} atoms`);
  }
  if (fullChars != null) {
    mbits.push(
      truncated
        ? `snippet ${snipLen}/${fullChars} chars (inspect truncates; not empty memory)`
        : `${fullChars} chars`
    );
  } else if (snipLen) {
    mbits.push(`${snipLen} chars shown`);
  }
  meta.textContent = mbits.join(" · ") || "—";
  card.appendChild(meta);

  // Prose-friendly body for summaries / speak-like channels.
  const proseCh = new Set([
    "temporal",
    "episodic",
    "semantic",
    "summary",
    "system",
    "orient",
    "directed_keep",
  ]);
  const body = document.createElement(proseCh.has(ch) ? "div" : "pre");
  body.className = proseCh.has(ch)
    ? "memory-snippet memory-snippet-prose"
    : "memory-snippet";
  if (proseCh.has(ch) && snippet) {
    body.innerHTML = renderMarkdown(snippet);
  } else {
    body.textContent = snippet || "(empty)";
  }
  card.appendChild(body);

  if (truncated) {
    const note = document.createElement("p");
    note.className = "muted memory-trunc-note";
    note.textContent =
      "Showing Glass inspect snippet only — expand inspect or open atom for full body.";
    card.appendChild(note);
  }

  const actions = document.createElement("div");
  actions.className = "memory-channel-actions";
  let hasAction = false;

  if (item.atom_id) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "link-btn";
    btn.textContent = "open atom";
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      setMemoryTab("atoms");
      void loadAtomDetail(item.atom_id);
    });
    actions.appendChild(btn);
    hasAction = true;
  }

  if (hasAction) {
    card.appendChild(actions);
  }

  if (item.atom_id) {
    // In-place inspect fold (unified with Atoms detail chrome).
    const details = document.createElement("details");
    details.className = "memory-atom-inspect-fold";
    details.setAttribute("data-inspect-atom-id", item.atom_id);
    const summary = document.createElement("summary");
    summary.textContent = "inspect atom";
    details.appendChild(summary);
    const panel = document.createElement("div");
    panel.className = "memory-atom-inspect-panel";
    details.appendChild(panel);
    bindContextAtomInspect(panel, item.atom_id);
    card.appendChild(details);
  } else if (ch === "system" || ch === "orient") {
    // Fixed blocks: expand shows meal snippet text (no store atom).
    const details = document.createElement("details");
    details.className = "memory-atom-inspect-fold";
    details.setAttribute("data-inspect-channel", ch);
    const summary = document.createElement("summary");
    summary.textContent = "inspect";
    details.appendChild(summary);
    const panel = document.createElement("div");
    panel.className = "memory-atom-inspect-panel";
    const pre = document.createElement("pre");
    pre.className = "memory-snippet memory-snippet-prose";
    pre.style.whiteSpace = "pre-wrap";
    pre.textContent = snippet || "(empty)";
    panel.appendChild(pre);
    const note = document.createElement("p");
    note.className = "muted memory-trunc-note";
    note.textContent =
      "Fixed meal channel (not a store atom). Body is the inspect snippet.";
    panel.appendChild(note);
    details.appendChild(panel);
    card.appendChild(details);
  }

  // Multi-atom summaries: member list with per-id open + inspect.
  if (memberIds.length && !item.atom_id) {
    const details = document.createElement("details");
    details.className = "memory-atom-inspect-fold";
    const membersKey = `${ch}:${item.label || ""}:${memberIds[0] || ""}`;
    details.setAttribute("data-inspect-members", membersKey);
    const summary = document.createElement("summary");
    summary.textContent = `inspect members (${memberIds.length}${
      metaObj.atom_count != null && metaObj.atom_count > memberIds.length
        ? ` of ${metaObj.atom_count}`
        : ""
    })`;
    details.appendChild(summary);
    const list = document.createElement("div");
    list.className = "memory-atom-member-list";
    for (const mid of memberIds) {
      const row = document.createElement("div");
      row.className = "memory-atom-member-row";
      const idSpan = document.createElement("code");
      idSpan.textContent = mid;
      row.appendChild(idSpan);
      const openBtn = document.createElement("button");
      openBtn.type = "button";
      openBtn.className = "link-btn";
      openBtn.textContent = "open";
      openBtn.addEventListener("click", (ev) => {
        ev.preventDefault();
        setMemoryTab("atoms");
        void loadAtomDetail(mid);
      });
      row.appendChild(openBtn);
      const sub = document.createElement("details");
      sub.className = "memory-atom-inspect-fold memory-atom-inspect-nested";
      sub.setAttribute("data-inspect-atom-id", mid);
      const subSum = document.createElement("summary");
      subSum.textContent = "inspect";
      sub.appendChild(subSum);
      const subPanel = document.createElement("div");
      subPanel.className = "memory-atom-inspect-panel";
      sub.appendChild(subPanel);
      bindContextAtomInspect(subPanel, mid);
      row.appendChild(sub);
      list.appendChild(row);
    }
    details.appendChild(list);
    card.appendChild(details);
  }

  return card;
}

async function refreshMemoryContext(opts = {}) {
  const force = Boolean(opts.force);
  const data = await fetchJson("/api/memory/context");
  const fp = fingerprintMemoryMeal(data);
  // Soft path: meal content unchanged — keep body/inspect DOM + selection.
  if (
    !force &&
    fp &&
    fp === memoryContextMealFp &&
    memoryContextBody &&
    memoryContextBody.querySelector(".memory-channel-section, .memory-channel-card")
  ) {
    renderMemoryFlags(data.memory || {}, { force: false });
    return;
  }
  memoryContextMealFp = fp;
  renderMemoryContext(data);
}

function setAtomDetailOpen(on) {
  const panel = document.getElementById("panel-memory");
  if (panel) panel.classList.toggle("atom-detail-open", !!on);
}

function closeAtomDetail() {
  memoryAtomDetailLoadGen += 1;
  selectedAtomId = null;
  lastAtomDetailFp = null;
  if (memoryAtomDetail) {
    memoryAtomDetail.hidden = true;
    memoryAtomDetail.innerHTML = "";
    delete memoryAtomDetail.dataset.atomId;
  }
  setAtomDetailOpen(false);
  if (memoryAtomsList) {
    memoryAtomsList
      .querySelectorAll(".card-selected")
      .forEach((el) => el.classList.remove("card-selected"));
  }
}

function renderAtomsList(atoms) {
  if (!memoryAtomsList) return;
  memoryAtomsList.innerHTML = "";
  if (!atoms.length) {
    memoryAtomsList.innerHTML = `<p class="muted empty memory-empty">No atoms match.</p>`;
    return;
  }
  for (const a of atoms) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "card card-btn";
    card.dataset.atomId = a.atom_id || "";
    if (selectedAtomId && a.atom_id === selectedAtomId) {
      card.classList.add("card-selected");
    }
    const head = document.createElement("div");
    head.className = "card-head";
    const strong = document.createElement("strong");
    strong.textContent = a.kind || "atom";
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = a.t_start || "—";
    head.appendChild(strong);
    head.appendChild(badge);
    card.appendChild(head);
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = [
      a.atom_id || "—",
      a.moment_id ? `moment=${a.moment_id}` : null,
      a.scale ? `scale=${a.scale}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    card.appendChild(meta);
    const snip = document.createElement("div");
    snip.className = "muted";
    snip.style.fontSize = "0.85rem";
    snip.style.marginTop = "0.35rem";
    snip.textContent = a.text || "(empty)";
    card.appendChild(snip);
    card.addEventListener("click", () => {
      loadAtomDetail(a.atom_id);
    });
    memoryAtomsList.appendChild(card);
  }
}

async function loadAtomDetail(id, opts = {}) {
  if (!id || !memoryAtomDetail) return;
  const soft = Boolean(opts.soft);
  const gen = ++memoryAtomDetailLoadGen;
  selectedAtomId = id;
  memoryAtomDetail.hidden = false;
  setAtomDetailOpen(true);
  if (!soft) {
    memoryAtomDetail.innerHTML = `<div class="card-head"><strong>${escapeHtml(
      id
    )}</strong><button type="button" class="link-btn" id="close-atom-detail">close</button></div><p class="muted">loading…</p>`;
    const closeBtn = $("#close-atom-detail");
    if (closeBtn) closeBtn.addEventListener("click", closeAtomDetail);
  }
  try {
    const data = await fetchJson(`/api/memory/atoms/${encodeURIComponent(id)}`);
    if (selectedAtomId !== id || gen !== memoryAtomDetailLoadGen) return;
    if (!data.ok || !data.atom) {
      memoryAtomDetail.innerHTML = `<div class="card-head"><strong>Atom</strong><button type="button" class="link-btn" id="close-atom-detail">close</button></div><p class="muted">${escapeHtml(
        data.error || "not found"
      )}</p>`;
      const c = $("#close-atom-detail");
      if (c) c.addEventListener("click", closeAtomDetail);
      lastAtomDetailFp = null;
      return;
    }
    const a = data.atom;
    const detailFp = stableFingerprint({
      atom_id: a.atom_id,
      kind: a.kind,
      t_start: a.t_start,
      content_text: a.content_text,
      content_truncated: a.content_truncated,
      embedding_status: a.embedding_status,
      moment_id: a.moment_id,
    });
    // Soft poll: keep painted detail when body unchanged (BUG-glass-03 / #74).
    if (
      soft &&
      detailFp === lastAtomDetailFp &&
      memoryAtomDetail.dataset.atomId === id &&
      memoryAtomDetail.childElementCount > 0
    ) {
      return;
    }
    lastAtomDetailFp = detailFp;
    memoryAtomDetail.dataset.atomId = id;
    fillAtomInspectInto(memoryAtomDetail, a, {
      showClose: true,
      onClose: closeAtomDetail,
    });
    const c2 = $("#close-atom-detail");
    if (c2) c2.addEventListener("click", closeAtomDetail);
    if (memoryAtomsList) {
      memoryAtomsList.querySelectorAll(".card-btn").forEach((el) => {
        el.classList.toggle("card-selected", el.dataset.atomId === id);
      });
    }
  } catch (err) {
    if (selectedAtomId !== id || gen !== memoryAtomDetailLoadGen) return;
    if (err && err.status === 404) {
      closeAtomDetail();
      return;
    }
    memoryAtomDetail.innerHTML = `<div class="card-head"><strong>Atom</strong><button type="button" class="link-btn" id="close-atom-detail">close</button></div><p class="muted">${escapeHtml(
      String(err.message || err)
    )}</p>`;
    const c3 = $("#close-atom-detail");
    if (c3) c3.addEventListener("click", closeAtomDetail);
  }
}

async function refreshMemoryAtoms(opts = {}) {
  const force = Boolean(opts.force);
  const params = new URLSearchParams();
  params.set("limit", "60");
  const kind = memoryAtomKind ? memoryAtomKind.value.trim() : "";
  const moment = memoryAtomMoment ? memoryAtomMoment.value.trim() : "";
  if (kind) params.set("kind", kind);
  if (moment) params.set("moment_id", moment);
  const data = await fetchJson(`/api/memory/atoms?${params.toString()}`);
  if (!data.ok && !(data.atoms || []).length) {
    if (memoryAtomsList) {
      memoryAtomsList.innerHTML = `<p class="muted empty memory-empty">${escapeHtml(
        data.error || "store unavailable"
      )}</p>`;
    }
    lastAtomsListFp = null;
    return;
  }
  const atoms = data.atoms || [];
  const listFp = stableFingerprint({ kind, moment, atoms });
  if (
    force ||
    listFp !== lastAtomsListFp ||
    !memoryAtomsList ||
    !memoryAtomsList.childElementCount
  ) {
    lastAtomsListFp = listFp;
    renderAtomsList(atoms);
  } else if (selectedAtomId && memoryAtomsList) {
    memoryAtomsList.querySelectorAll(".card-btn").forEach((el) => {
      el.classList.toggle("card-selected", el.dataset.atomId === selectedAtomId);
    });
  }
  if (selectedAtomId) {
    await loadAtomDetail(selectedAtomId, { soft: !force });
  }
}

/**
 * Format vectors_by_channel map for glass health (omit zero channels except joint/text).
 * @param {Record<string, number> | null | undefined} counts
 */
function formatVectorsByChannel(counts) {
  if (!counts || typeof counts !== "object") return "—";
  const order = ["joint", "text", "image", "audio", "video"];
  const parts = [];
  for (const ch of order) {
    const n = counts[ch];
    if (n == null) continue;
    const num = Number(n) || 0;
    if (num > 0 || ch === "joint" || ch === "text") {
      parts.push(`${ch}=${num}`);
    }
  }
  return parts.length ? parts.join(" · ") : "—";
}

/**
 * Honest ANN / search-mode copy: small corpus without IVF is not "search broken".
 * @param {Record<string, any>} idx
 */
function formatAnnHonesty(idx) {
  const ready = Number(idx.vectors_ready) || 0;
  const built = idx.ann_index_built === true;
  const mode = idx.search_mode ? String(idx.search_mode) : null;
  const repair = Number(idx.joint_repair_remaining) || 0;
  const bits = [];
  if (built) {
    bits.push("ann=built");
  } else if (ready === 0) {
    bits.push("ann=off (no vectors yet)");
  } else {
    // Small-N / not yet optimized: full scan still works.
    bits.push("ann=off — corpus small or IVF not built; full scan still used");
  }
  if (mode) bits.push(`mode=${mode}`);
  if (repair > 0) bits.push(`joint_repair_remaining=${repair}`);
  return bits.join(" · ");
}

function renderVectorsHealth(data) {
  if (!memoryVectorsHealth) return;
  memoryVectorsHealth.innerHTML = "";
  const enc = data.encoder || {};
  const idx = data.index || {};
  const mem = data.memory || {};
  const notes = Array.isArray(idx.last_optimize_notes)
    ? idx.last_optimize_notes
    : [];
  const deviceBits = [
    enc.device ? `eff=${enc.device}` : null,
    enc.device_pref ? `req=${enc.device_pref}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const rows = [
    [
      "encoder",
      enc.ok
        ? `${enc.backend || "—"} · ${deviceBits || enc.device || enc.device_pref || "—"}`
        : enc.error || "down",
      enc.ok === true,
    ],
    ["model", enc.model_id || "—", null],
    [
      "queue",
      `${enc.queue_depth != null ? enc.queue_depth : 0}/${
        enc.queue_max != null ? enc.queue_max : "—"
      }${enc.queue_dropped ? ` · dropped=${enc.queue_dropped}` : ""}`,
      null,
    ],
    [
      "embed flags",
      `embed=${enc.embed_enabled ? "on" : "off"} · semantic=${
        enc.semantic_enabled ? "on" : "off"
      }`,
      null,
    ],
    [
      "index",
      idx.ok
        ? `${idx.backend || "—"} · ready=${
            idx.vectors_ready != null ? idx.vectors_ready : 0
          }`
        : idx.error || "down",
      idx.ok === true,
    ],
    [
      "channels",
      formatVectorsByChannel(idx.vectors_by_channel),
      null,
    ],
    [
      "repair",
      (() => {
        const rem = Number(idx.joint_repair_remaining) || 0;
        const batch = Number(idx.joint_repair_last_batch) || 0;
        if (rem > 0) {
          return `pending=${rem}${batch ? ` · last_batch=${batch}` : ""} (auto prefers text)`;
        }
        return rem === 0 && batch > 0
          ? `complete (last_batch=${batch})`
          : "none pending";
      })(),
      // Repair pending is not search-broken — warn tone only.
      Number(idx.joint_repair_remaining) > 0 ? false : null,
    ],
    [
      "freshness",
      [
        idx.index_stale ? "stale" : "fresh",
        formatAnnHonesty(idx),
        idx.recent_buffer != null ? `buf=${idx.recent_buffer}` : null,
        idx.last_optimize ? `opt=${idx.last_optimize}` : null,
      ]
        .filter(Boolean)
        .join(" · ") || "—",
      // Stale is a warning; ann=off on small corpus must NOT look like status-bad.
      idx.index_stale === true ? false : null,
    ],
    [
      "optimize notes",
      notes.length
        ? notes.map((n) => String(n)).join("; ")
        : idx.last_optimize
          ? "(no notes)"
          : "—",
      null,
    ],
    ["store", mem.ok ? mem.backend || "ok" : mem.error || "down", mem.ok === true],
  ];
  for (const [label, value, good] of rows) {
    const row = document.createElement("div");
    row.className = "status-row";
    const lab = document.createElement("span");
    lab.className = "status-label";
    lab.textContent = label;
    const val = document.createElement("span");
    val.className = "status-value";
    if (good === true) val.classList.add("status-ok");
    if (good === false) {
      // repair / freshness warn — not hard error (search still works).
      val.classList.add(
        label === "freshness" || label === "repair"
          ? "memory-vector-stale"
          : "status-bad"
      );
    }
    val.textContent = value;
    row.appendChild(lab);
    row.appendChild(val);
    memoryVectorsHealth.appendChild(row);
  }
}

function renderVectorsAtomsList(atoms) {
  if (!memoryVectorsList) return;
  memoryVectorsList.innerHTML = "";
  if (!atoms.length) {
    memoryVectorsList.innerHTML = `<p class="muted empty memory-empty">No atoms match this embedding status.</p>`;
    return;
  }
  for (const a of atoms) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "card card-btn";
    card.dataset.atomId = a.atom_id || "";
    const head = document.createElement("div");
    head.className = "card-head";
    const strong = document.createElement("strong");
    strong.textContent = a.embedding_status || a.kind || "atom";
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = a.kind || "—";
    head.appendChild(strong);
    head.appendChild(badge);
    card.appendChild(head);
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = [
      a.atom_id || "—",
      a.t_start || null,
      a.embed_error ? `err=${a.embed_error}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    card.appendChild(meta);
    // embed_channels chips — joint vs text visible (PR-R5 honesty).
    if (Array.isArray(a.channels) && a.channels.length) {
      const chips = document.createElement("div");
      chips.className = "memory-channel-chips";
      for (const ch of a.channels) {
        const chip = document.createElement("span");
        chip.className = "badge memory-channel-chip";
        chip.textContent = String(ch);
        chips.appendChild(chip);
      }
      card.appendChild(chips);
    }
    const snip = document.createElement("div");
    snip.className = "muted";
    snip.style.fontSize = "0.85rem";
    snip.style.marginTop = "0.35rem";
    snip.textContent = a.text || "(empty)";
    card.appendChild(snip);
    card.addEventListener("click", () => {
      if (memoryNeighborAtom && a.atom_id) {
        memoryNeighborAtom.value = a.atom_id;
      }
      if (memoryNeighborQ) memoryNeighborQ.value = "";
      runNeighborSearch().catch((e) => panelLoadError("Memory neighbors", e));
    });
    memoryVectorsList.appendChild(card);
  }
}

/**
 * Neighbor empty-state / meta line: never blank without explanation when query ran.
 * @param {Record<string, any>} data
 */
function renderNeighborsMeta(data) {
  if (!memoryNeighborsMeta) return;
  const q = data.query || {};
  const req = q.channel || "auto";
  const resolved = q.resolved_channel || req;
  const reason = q.channel_reason || null;
  const parts = [
    `channel ${req}${resolved && resolved !== req ? ` → ${resolved}` : resolved ? ` (${resolved})` : ""}`,
    reason ? `reason=${reason}` : null,
  ].filter(Boolean);
  const idx = data.index || {};
  if (idx.search_mode) parts.push(`mode=${idx.search_mode}`);
  if (idx.ann_index_built === false) {
    parts.push("IVF not built — full scan still used");
  }
  if (Number(idx.joint_repair_remaining) > 0) {
    parts.push(`repair_pending=${idx.joint_repair_remaining}`);
  }
  memoryNeighborsMeta.hidden = false;
  memoryNeighborsMeta.textContent = parts.join(" · ");
}

function renderNeighborsList(data) {
  if (!memoryNeighborsList) return;
  memoryNeighborsList.innerHTML = "";
  renderNeighborsMeta(data);
  const neighbors = data.neighbors || [];
  if (!neighbors.length) {
    const omit = data.omitted_reason || data.error || "no_hits";
    const q = data.query || {};
    const resolved = q.resolved_channel || q.channel || "—";
    const reason = q.channel_reason || "—";
    const lines = [
      `No neighbors (${omit}).`,
      `Searched channel ${resolved} (${reason}).`,
    ];
    // Distinguish “search broken” vs empty channel / small corpus.
    if (omit === "no_hits" || omit === "no_vector") {
      lines.push(
        "Empty result is not necessarily broken search — try another channel or wait for encode/repair."
      );
    } else if (omit === "no_index" || omit === "encoder" || omit === "search_failed") {
      lines.push("Search path unavailable (index/encoder) — not an IVF small-corpus skip.");
    }
    memoryNeighborsList.innerHTML = `<p class="muted empty memory-empty">${escapeHtml(
      lines.join(" ")
    )}</p>`;
    return;
  }
  for (const n of neighbors) {
    const card = document.createElement("div");
    card.className = "card memory-channel-card";
    const head = document.createElement("div");
    head.className = "card-head";
    const title = document.createElement("strong");
    title.textContent = n.kind || "atom";
    const badge = document.createElement("span");
    badge.className = "badge";
    const score =
      n.score != null && Number.isFinite(Number(n.score))
        ? Number(n.score).toFixed(4)
        : "—";
    // Cosine similarity badge (higher = closer).
    const kind = n.score_kind === "cosine" || !n.score_kind ? "cosine" : n.score_kind;
    badge.textContent = `${kind}=${score}`;
    badge.title = "Cosine similarity (1 = identical direction)";
    head.appendChild(title);
    head.appendChild(badge);
    card.appendChild(head);
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = [
      n.atom_id || "—",
      n.moment_id ? `moment=${n.moment_id}` : null,
      n.channel ? `ch=${n.channel}` : null,
      n.embedding_status || null,
    ]
      .filter(Boolean)
      .join(" · ");
    card.appendChild(meta);
    const pre = document.createElement("pre");
    pre.className = "memory-snippet";
    pre.textContent = n.snippet || "(empty)";
    card.appendChild(pre);
    memoryNeighborsList.appendChild(card);
  }
}

async function refreshMemoryVectors(opts = {}) {
  const force = Boolean(opts.force);
  const health = await fetchJson("/api/memory/vectors");
  const params = new URLSearchParams();
  params.set("limit", "50");
  const status = memoryVectorStatus ? memoryVectorStatus.value.trim() : "";
  if (status) params.set("status", status);
  const data = await fetchJson(
    `/api/memory/vectors/atoms?${params.toString()}`
  );
  const atoms = data.atoms || [];
  const fp = stableFingerprint({ health, status, ok: data.ok, atoms });
  if (
    !force &&
    fp === lastVectorsFp &&
    memoryVectorsList &&
    memoryVectorsList.childElementCount > 0
  ) {
    return;
  }
  lastVectorsFp = fp;
  renderVectorsHealth(health);
  if (!data.ok && !atoms.length) {
    if (memoryVectorsList) {
      memoryVectorsList.innerHTML = `<p class="muted empty memory-empty">${escapeHtml(
        data.error || "store unavailable"
      )}</p>`;
    }
    return;
  }
  renderVectorsAtomsList(atoms);
}

async function runNeighborSearch() {
  const params = new URLSearchParams();
  const atomId = memoryNeighborAtom ? memoryNeighborAtom.value.trim() : "";
  const q = memoryNeighborQ ? memoryNeighborQ.value.trim() : "";
  let k = 8;
  if (memoryNeighborK) {
    const raw = parseInt(memoryNeighborK.value, 10);
    if (Number.isFinite(raw)) k = raw;
  }
  params.set("k", String(k));
  // Default auto when select missing; always send explicit channel from UI.
  const channel =
    memoryNeighborChannel && memoryNeighborChannel.value
      ? memoryNeighborChannel.value.trim() || "auto"
      : "auto";
  params.set("channel", channel);
  if (atomId) params.set("atom_id", atomId);
  else if (q) params.set("q", q);
  else {
    if (memoryNeighborsMeta) {
      memoryNeighborsMeta.hidden = true;
      memoryNeighborsMeta.textContent = "";
    }
    if (memoryNeighborsList) {
      memoryNeighborsList.innerHTML = `<p class="muted empty memory-empty">Pick an atom id or free-text query.</p>`;
    }
    return;
  }
  if (memoryNeighborsList) {
    memoryNeighborsList.innerHTML = `<p class="muted">searching…</p>`;
  }
  try {
    const data = await fetchJson(
      `/api/memory/vectors/neighbors?${params.toString()}`
    );
    renderNeighborsList(data);
  } catch (err) {
    if (memoryNeighborsMeta) {
      memoryNeighborsMeta.hidden = true;
    }
    if (memoryNeighborsList) {
      memoryNeighborsList.innerHTML = `<p class="muted empty memory-empty">${escapeHtml(
        String(err.message || err)
      )}</p>`;
    }
  }
}

/**
 * Format idle age for Graph session card (seconds → short human).
 * @param {number | null | undefined} ageS
 */
function formatIdleAge(ageS) {
  if (ageS == null || !Number.isFinite(Number(ageS))) return "—";
  const s = Math.max(0, Math.floor(Number(ageS)));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

/**
 * Graph overview flags + honesty note.
 * @param {Record<string, any>} data
 */
function renderGraphOverview(data) {
  if (!memoryGraphOverview) return;
  memoryGraphOverview.innerHTML = "";
  const trav = data.traversal || {};
  const mem = data.memory || {};
  const flagOn = trav.directed_traversal_enabled === true;
  const keepOn = trav.directed_keep_enabled === true;
  const rows = [
    [
      "traversal",
      flagOn ? "on" : "off (default)",
      flagOn === true,
    ],
    [
      "directed keep",
      keepOn ? "on" : "off",
      keepOn === true ? true : null,
    ],
    [
      "sessions",
      [
        data.has_active ? "active walk" : null,
        data.has_last_session ? "last finished" : null,
        !data.has_active && !data.has_last_session ? "none" : null,
      ]
        .filter(Boolean)
        .join(" · "),
      data.has_active === true ? true : null,
    ],
    [
      "meal keep",
      data.meal_keep_count != null ? String(data.meal_keep_count) : "0",
      null,
    ],
    [
      "budgets",
      [
        trav.traverse_max_steps != null ? `steps≤${trav.traverse_max_steps}` : null,
        trav.traverse_max_nodes != null ? `nodes≤${trav.traverse_max_nodes}` : null,
        trav.traverse_max_depth != null ? `depth≤${trav.traverse_max_depth}` : null,
        trav.traverse_expand_max_ms != null
          ? `expand_ms≤${trav.traverse_expand_max_ms}`
          : null,
        trav.traverse_session_ttl_s != null
          ? `idle_ttl=${trav.traverse_session_ttl_s}s`
          : null,
      ]
        .filter(Boolean)
        .join(" · ") || "—",
      null,
    ],
    [
      "store",
      mem.ok ? mem.backend || "ok" : mem.error || "down",
      mem.ok === true,
    ],
  ];
  for (const [label, value, good] of rows) {
    const row = document.createElement("div");
    row.className = "status-row";
    const lab = document.createElement("span");
    lab.className = "status-label";
    lab.textContent = label;
    const val = document.createElement("span");
    val.className = "status-value";
    if (good === true) val.classList.add("status-ok");
    if (good === false) val.classList.add("status-bad");
    // Flag off is expected default — muted, not status-bad.
    if (label === "traversal" && !flagOn) {
      val.classList.remove("status-bad");
      val.classList.add("memory-vector-stale");
    }
    val.textContent = value;
    row.appendChild(lab);
    row.appendChild(val);
    memoryGraphOverview.appendChild(row);
  }
  // Edge-kind legend as a compact line.
  const legend = Array.isArray(data.edge_kind_legend) ? data.edge_kind_legend : [];
  if (legend.length) {
    const row = document.createElement("div");
    row.className = "status-row";
    const lab = document.createElement("span");
    lab.className = "status-label";
    lab.textContent = "edge kinds";
    const val = document.createElement("span");
    val.className = "status-value";
    val.textContent = legend
      .map((e) => `${e.kind}=${e.base_weight != null ? e.base_weight : "?"}`)
      .join(" · ");
    row.appendChild(lab);
    row.appendChild(val);
    memoryGraphOverview.appendChild(row);
  }

  if (memoryGraphHonesty) {
    const honesty = data.honesty || {};
    const note = honesty.note || null;
    if (note) {
      memoryGraphHonesty.hidden = false;
      memoryGraphHonesty.textContent = String(note);
    } else {
      memoryGraphHonesty.hidden = true;
      memoryGraphHonesty.textContent = "";
    }
  }
}

/**
 * Session card: status, goal, budgets (steps/nodes/depth/expand_ms/idle) — no wall-clock.
 * @param {Record<string, any>} data session API payload
 */
function renderGraphSession(data) {
  const sess = data.session || null;
  const which = data.which || "none";
  const dual =
    data.has_active && data.has_last_session
      ? which === "active"
        ? "walking… · last finished retained"
        : "last finished · active also present"
      : null;

  if (memoryGraphSessionBadge) {
    if (!sess) {
      memoryGraphSessionBadge.textContent = which === "meal" ? "meal-thin" : "none";
    } else {
      const st = sess.status || which;
      memoryGraphSessionBadge.textContent = dual
        ? `${st} · dual`
        : String(st);
    }
  }

  if (!memoryGraphSessionBody) return;
  memoryGraphSessionBody.innerHTML = "";

  if (!sess) {
    const p = document.createElement("p");
    p.className = "muted empty memory-empty";
    if (data.honesty && data.honesty.note) {
      p.textContent = String(data.honesty.note);
    } else if (which === "meal") {
      const n = data.meal_keep_count != null ? data.meal_keep_count : 0;
      p.textContent = `Meal-thin keep only (${n} ids) — full walk is on active/last session.`;
    } else {
      p.textContent = "No walk yet. Start via traverse tools (flag on) or wait for a session.";
    }
    memoryGraphSessionBody.appendChild(p);
    return;
  }

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = [
    sess.session_id || "—",
    sess.status || null,
    which ? `view=${which}` : null,
    dual || null,
    sess.moment_id ? `moment=${sess.moment_id}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  memoryGraphSessionBody.appendChild(meta);

  const goal = document.createElement("div");
  goal.style.marginTop = "0.35rem";
  const goalLabel = document.createElement("span");
  goalLabel.className = "muted";
  goalLabel.textContent = "goal: ";
  goal.appendChild(goalLabel);
  goal.appendChild(document.createTextNode(sess.goal || "—"));
  memoryGraphSessionBody.appendChild(goal);

  const budgets = sess.budgets || {};
  const budgetRow = document.createElement("div");
  budgetRow.className = "memory-graph-budgets";
  // KD-A18: steps/nodes/depth + expand_ms + idle age — NOT multi-hop wall-clock.
  const idle =
    sess.idle_age_s != null
      ? sess.idle_age_s
      : data.session && data.session.idle_age_s;
  const bits = [
    `steps ${budgets.steps_spent != null ? budgets.steps_spent : 0}/${
      budgets.max_steps != null ? budgets.max_steps : "—"
    } (rem ${budgets.steps_remaining != null ? budgets.steps_remaining : "—"})`,
    `nodes ${budgets.nodes_spent != null ? budgets.nodes_spent : 0}/${
      budgets.max_nodes != null ? budgets.max_nodes : "—"
    }`,
    `depth ${budgets.depth_spent != null ? budgets.depth_spent : 0}/${
      budgets.max_depth != null ? budgets.max_depth : "—"
    }`,
    `expand_ms last=${
      budgets.expand_ms_spent_last != null ? budgets.expand_ms_spent_last : 0
    }/budget=${budgets.expand_ms_budget != null ? budgets.expand_ms_budget : "—"}`,
    budgets.expand_truncated ? "expand_truncated" : null,
    `idle ${formatIdleAge(idle)}`,
  ].filter(Boolean);
  budgetRow.textContent = bits.join(" · ");
  memoryGraphSessionBody.appendChild(budgetRow);

  const summary = document.createElement("pre");
  summary.className = "memory-graph-summary";
  summary.textContent =
    sess.walk_summary_nl && String(sess.walk_summary_nl).trim()
      ? String(sess.walk_summary_nl)
      : "no walk summary yet";
  memoryGraphSessionBody.appendChild(summary);

  if (data.meal_keep_count != null && data.has_last_session) {
    const meal = document.createElement("div");
    meal.className = "muted";
    meal.style.fontSize = "0.8rem";
    meal.style.marginTop = "0.35rem";
    meal.textContent = `meal keep ids: ${data.meal_keep_count} (thin; full considered is above)`;
    memoryGraphSessionBody.appendChild(meal);
  }
}

/**
 * @param {HTMLElement | null} el
 * @param {Array<Record<string, any>>} nodes
 * @param {"considered" | "kept" | "frontier"} mode
 */
function renderGraphNodeList(el, nodes, mode) {
  if (!el) return;
  el.innerHTML = "";
  if (!nodes.length) {
    const empty = mode === "frontier"
      ? "No frontier (active walks only; frozen on finished)."
      : mode === "kept"
        ? "No keeps yet."
        : "No considered nodes.";
    el.innerHTML = `<p class="muted empty memory-empty">${escapeHtml(empty)}</p>`;
    return;
  }
  for (const n of nodes) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "card card-btn memory-graph-node";
    if (mode === "kept" || n.kept) {
      card.classList.add("memory-graph-node-kept");
    } else if (mode === "considered") {
      card.classList.add("memory-graph-node-considered");
    }
    const aid = n.atom_id || "";
    card.dataset.atomId = aid;

    const head = document.createElement("div");
    head.className = "card-head";
    const strong = document.createElement("strong");
    strong.textContent = n.kind || n.edge_kind || mode;
    const badge = document.createElement("span");
    badge.className = "badge";
    if (mode === "kept" || n.kept) {
      badge.classList.add("memory-graph-kept-badge");
      badge.textContent = "kept";
    } else if (n.via_edge_kind || n.edge_kind) {
      badge.textContent = String(n.via_edge_kind || n.edge_kind);
    } else {
      badge.textContent = n.depth != null ? `d=${n.depth}` : mode;
    }
    head.appendChild(strong);
    head.appendChild(badge);
    card.appendChild(head);

    const meta = document.createElement("div");
    meta.className = "meta";
    const weight =
      n.weight != null && Number.isFinite(Number(n.weight))
        ? Number(n.weight).toFixed(3)
        : null;
    meta.textContent = [
      aid || "—",
      n.depth != null ? `depth=${n.depth}` : null,
      weight != null ? `w=${weight}` : null,
      n.via_edge_kind || n.edge_kind || null,
    ]
      .filter(Boolean)
      .join(" · ");
    card.appendChild(meta);

    const snip = document.createElement("div");
    snip.className = "muted";
    snip.style.fontSize = "0.85rem";
    snip.style.marginTop = "0.25rem";
    snip.textContent = n.snippet || n.label || n.preview || n.reason || "(empty)";
    card.appendChild(snip);

    card.addEventListener("click", () => {
      if (memoryGraphNeighborAtom && aid) {
        memoryGraphNeighborAtom.value = aid;
      }
      runGraphNeighborSearch().catch((e) =>
        panelLoadError("Memory graph neighbors", e)
      );
    });
    el.appendChild(card);
  }
}

/**
 * @param {Record<string, any>} data session payload
 */
function renderGraphLists(data) {
  const sess = data.session || null;
  const considered = sess && Array.isArray(sess.considered) ? sess.considered : [];
  const keepIds = new Set(
    sess && Array.isArray(sess.keep_ids) ? sess.keep_ids.map(String) : []
  );
  // Kept list: prefer order of keep_ids, fall back to considered.kept flags.
  const byId = new Map(considered.map((n) => [String(n.atom_id || ""), n]));
  const kept = [];
  if (sess && Array.isArray(sess.keep_ids)) {
    for (const id of sess.keep_ids) {
      const row = byId.get(String(id));
      if (row) kept.push({ ...row, kept: true });
      else kept.push({ atom_id: id, kept: true, label: id });
    }
  } else {
    for (const n of considered) {
      if (n.kept || keepIds.has(String(n.atom_id || ""))) {
        kept.push({ ...n, kept: true });
      }
    }
  }
  const frontier =
    sess && Array.isArray(sess.frontier) && data.which === "active"
      ? sess.frontier
      : [];
  renderGraphNodeList(memoryGraphConsidered, considered, "considered");
  renderGraphNodeList(memoryGraphKept, kept, "kept");
  renderGraphNodeList(memoryGraphFrontier, frontier, "frontier");
}

/**
 * Graph neighbor probe results (multi-kind edges).
 * @param {Record<string, any>} data
 */
function renderGraphNeighbors(data) {
  if (!memoryGraphNeighborsList) return;
  memoryGraphNeighborsList.innerHTML = "";
  if (memoryGraphNeighborsMeta) {
    const q = data.query || {};
    const em = data.expand_meta || {};
    const parts = [
      q.atom_id ? `atom=${q.atom_id}` : null,
      q.k != null ? `k=${q.k}` : null,
      q.allow_semantic === false ? "semantic=off" : "semantic=on",
      em.elapsed_ms != null ? `elapsed_ms=${em.elapsed_ms}` : null,
      em.expand_truncated ? "truncated" : null,
      em.semantic_reason ? `sem=${em.semantic_reason}` : null,
    ].filter(Boolean);
    memoryGraphNeighborsMeta.hidden = parts.length === 0;
    memoryGraphNeighborsMeta.textContent = parts.join(" · ");
  }
  const neighbors = data.neighbors || [];
  if (!neighbors.length) {
    const omit = data.omitted_reason || data.error || "no_hits";
    const lines = [
      `No neighbors (${omit}).`,
      "Structural edges need prev/next/parent links; semantic hops need index + warm encoder.",
    ];
    memoryGraphNeighborsList.innerHTML = `<p class="muted empty memory-empty">${escapeHtml(
      lines.join(" ")
    )}</p>`;
    return;
  }
  for (const n of neighbors) {
    const card = document.createElement("div");
    card.className = "card memory-channel-card memory-graph-node";
    const head = document.createElement("div");
    head.className = "card-head";
    const title = document.createElement("strong");
    title.textContent = n.kind || n.edge_kind || "atom";
    const badge = document.createElement("span");
    badge.className = "badge";
    const w =
      n.weight != null && Number.isFinite(Number(n.weight))
        ? Number(n.weight).toFixed(3)
        : "—";
    badge.textContent = `${n.edge_kind || "edge"} w=${w}`;
    badge.title = n.reason || "edge weight (v1 model)";
    head.appendChild(title);
    head.appendChild(badge);
    card.appendChild(head);
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = [
      n.atom_id || "—",
      n.moment_id ? `moment=${n.moment_id}` : null,
      n.reason || null,
    ]
      .filter(Boolean)
      .join(" · ");
    card.appendChild(meta);
    const pre = document.createElement("pre");
    pre.className = "memory-snippet";
    pre.textContent = n.snippet || n.label || "(empty)";
    card.appendChild(pre);
    card.style.cursor = "pointer";
    card.addEventListener("click", () => {
      if (memoryGraphNeighborAtom && n.atom_id) {
        memoryGraphNeighborAtom.value = n.atom_id;
      }
    });
    memoryGraphNeighborsList.appendChild(card);
  }
}

async function runGraphNeighborSearch() {
  const params = new URLSearchParams();
  const atomId = memoryGraphNeighborAtom
    ? memoryGraphNeighborAtom.value.trim()
    : "";
  let k = 12;
  if (memoryGraphNeighborK) {
    const raw = parseInt(memoryGraphNeighborK.value, 10);
    if (Number.isFinite(raw)) k = raw;
  }
  params.set("k", String(k));
  const allowSem =
    !memoryGraphNeighborSem || memoryGraphNeighborSem.checked !== false;
  params.set("allow_semantic", allowSem ? "1" : "0");
  if (!atomId) {
    if (memoryGraphNeighborsMeta) {
      memoryGraphNeighborsMeta.hidden = true;
      memoryGraphNeighborsMeta.textContent = "";
    }
    if (memoryGraphNeighborsList) {
      memoryGraphNeighborsList.innerHTML = `<p class="muted empty memory-empty">Pick an atom id to expand 1-hop.</p>`;
    }
    return;
  }
  params.set("atom_id", atomId);
  if (memoryGraphNeighborsList) {
    memoryGraphNeighborsList.innerHTML = `<p class="muted">expanding…</p>`;
  }
  try {
    const data = await fetchJson(
      `/api/memory/graph/neighbors?${params.toString()}`
    );
    renderGraphNeighbors(data);
  } catch (err) {
    if (memoryGraphNeighborsMeta) memoryGraphNeighborsMeta.hidden = true;
    if (memoryGraphNeighborsList) {
      memoryGraphNeighborsList.innerHTML = `<p class="muted empty memory-empty">${escapeHtml(
        String(err.message || err)
      )}</p>`;
    }
  }
}

async function refreshMemoryGraph(opts = {}) {
  const force = Boolean(opts.force);
  const overview = await fetchJson("/api/memory/graph");
  const session = await fetchJson("/api/memory/graph/session");
  // Merge honesty from overview when session has none.
  if (!session.honesty && overview.honesty) {
    session.honesty = overview.honesty;
  }
  // Prefer dual-badge presence from overview if session omitted.
  if (session.has_active == null) session.has_active = overview.has_active;
  if (session.has_last_session == null) {
    session.has_last_session = overview.has_last_session;
  }
  const fp = stableFingerprint({ overview, session });
  if (!force && fp === lastGraphFp) {
    return;
  }
  lastGraphFp = fp;
  renderGraphOverview(overview);
  renderGraphSession(session);
  renderGraphLists(session);
}

async function refreshMemory(opts = {}) {
  const force = Boolean(opts.force);
  if (memoryActiveTab === "moments") {
    await refreshMoments({ force });
    return;
  }
  if (memoryActiveTab === "atoms") {
    await refreshMemoryAtoms({ force });
    return;
  }
  if (memoryActiveTab === "vectors") {
    await refreshMemoryVectors({ force });
    return;
  }
  if (memoryActiveTab === "graph") {
    await refreshMemoryGraph({ force });
    return;
  }
  await refreshMemoryContext({ force });
}

if (memoryRefreshBtn) {
  memoryRefreshBtn.addEventListener("click", () => {
    refreshMemory({ force: true }).catch((e) => panelLoadError("Memory", e));
  });
}
document.querySelectorAll(".memory-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    setMemoryTab(btn.dataset.memoryTab || "context");
    refreshMemory({ force: true }).catch((e) => panelLoadError("Memory", e));
  });
});
if (memoryAtomsApply) {
  memoryAtomsApply.addEventListener("click", () => {
    refreshMemoryAtoms({ force: true }).catch((e) =>
      panelLoadError("Memory atoms", e)
    );
  });
}
if (memoryAtomKind) {
  memoryAtomKind.addEventListener("change", () => {
    if (memoryActiveTab === "atoms") {
      refreshMemoryAtoms({ force: true }).catch((e) =>
        panelLoadError("Memory atoms", e)
      );
    }
  });
}
if (memoryVectorsApply) {
  memoryVectorsApply.addEventListener("click", () => {
    refreshMemoryVectors({ force: true }).catch((e) =>
      panelLoadError("Memory vectors", e)
    );
  });
}
if (memoryVectorsRebuild) {
  memoryVectorsRebuild.addEventListener("click", () => {
    rebuildVectorIndex().catch((e) => panelLoadError("Memory vectors rebuild", e));
  });
}
if (memoryVectorStatus) {
  memoryVectorStatus.addEventListener("change", () => {
    if (memoryActiveTab === "vectors") {
      refreshMemoryVectors({ force: true }).catch((e) =>
        panelLoadError("Memory vectors", e)
      );
    }
  });
}
if (memoryNeighborsRun) {
  memoryNeighborsRun.addEventListener("click", () => {
    runNeighborSearch().catch((e) => panelLoadError("Memory neighbors", e));
  });
}
if (memoryGraphNeighborsRun) {
  memoryGraphNeighborsRun.addEventListener("click", () => {
    runGraphNeighborSearch().catch((e) =>
      panelLoadError("Memory graph neighbors", e)
    );
  });
}

/**
 * Rebuild approximate nearest-neighbor index over stored embeddings.
 * Does not re-run Nemotron / re-encode atoms.
 * Honesty: notes[] explain skips (no vectors / below IVF min) vs failures.
 */
async function rebuildVectorIndex() {
  if (memoryVectorsRebuildInFlight) return;
  memoryVectorsRebuildInFlight = true;
  if (memoryVectorsRebuild) {
    memoryVectorsRebuild.disabled = true;
    memoryVectorsRebuild.textContent = "Rebuilding…";
  }
  try {
    const data = await fetchJson("/api/memory/vectors/rebuild", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const notes = Array.isArray(data && data.notes)
      ? data.notes.map((n) => String(n))
      : [];
    const joined = notes.length
      ? notes.join("; ")
      : data && (data.note || data.error)
        ? String(data.note || data.error)
        : data && data.optimized === false
          ? "optimize finished without a durable ANN (full scan still works)"
          : "index rebuild requested";
    // Map common skip notes into operator-facing honesty.
    const lower = joined.toLowerCase();
    let notice = joined;
    if (
      data &&
      data.optimized === false &&
      (lower.includes("below_ivf_min") ||
        lower.includes("no_vectors") ||
        lower.includes("null"))
    ) {
      notice = `${joined} — IVF not built is normal on small/empty corpora; full scan still used (not search broken).`;
    }
    if (typeof showNotice === "function") {
      showNotice(
        data && data.ok !== false
          ? `Vector index: ${notice}`
          : `Vector index rebuild: ${notice}`
      );
    }
    await refreshMemoryVectors({ force: true });
  } finally {
    memoryVectorsRebuildInFlight = false;
    if (memoryVectorsRebuild) {
      memoryVectorsRebuild.disabled = false;
      memoryVectorsRebuild.textContent = "Rebuild ANN index";
    }
  }
}

function renderCatalog(el, items, emptyLabel, kind) {
  el.innerHTML = "";
  if (!items.length) {
    el.innerHTML = `<p class="muted empty">${emptyLabel}</p>`;
    return;
  }
  for (const t of items) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "card card-btn catalog-card";
    card.dataset.catalogKind = kind;
    card.dataset.catalogName = t.name || "";
    const selected =
      catalogSelection &&
      catalogSelection.kind === kind &&
      catalogSelection.name === t.name;
    if (selected) card.classList.add("card-selected");
    const source = t.source || t.kind || "";
    const toolKind = t.kind && t.source ? t.kind : "";
    card.innerHTML = `
      <div class="card-head">
        <strong>${escapeHtml(t.name)}</strong>
        <span class="badge">${escapeHtml(source)}</span>
      </div>
      ${
        toolKind
          ? `<div class="meta">${escapeHtml(toolKind)}</div>`
          : ""
      }
      <p class="muted">${escapeHtml(t.description || "")}</p>`;
    card.addEventListener("click", () => {
      selectCatalogItem(kind, t.name).catch((e) =>
        panelLoadError("Catalog", e)
      );
    });
    el.appendChild(card);
  }
}

function clearCatalogSelectionHighlight() {
  document
    .querySelectorAll("#tools-list .card-btn, #skills-list .card-btn")
    .forEach((el) => el.classList.remove("card-selected"));
}

function markCatalogSelectionHighlight() {
  clearCatalogSelectionHighlight();
  if (!catalogSelection) return;
  const list = catalogSelection.kind === "tool" ? toolsList : skillsList;
  if (!list) return;
  const btn = [...list.querySelectorAll(".card-btn")].find(
    (el) => el.dataset.catalogName === catalogSelection.name
  );
  if (btn) btn.classList.add("card-selected");
}

function setCatalogInspecting(on) {
  const panel = document.getElementById("panel-tools");
  if (panel) panel.classList.toggle("catalog-inspecting", !!on);
}

function hideCatalogInspector() {
  setCatalogInspecting(false);
  catalogSelection = null;
  clearCatalogSelectionHighlight();
  if (catalogInspector) catalogInspector.hidden = true;
  if (catalogInspectorVersionDoc) {
    catalogInspectorVersionDoc.hidden = true;
    catalogInspectorVersionDoc.textContent = "";
  }
  if (catalogInspectorSchemaFold) catalogInspectorSchemaFold.hidden = true;
  if (catalogInspectorRunnerFold) catalogInspectorRunnerFold.hidden = true;
}

function packageDocFromDetail(kind, detail) {
  const pkg = detail.package || {};
  if (kind === "skill") {
    return (
      detail.skill_md ||
      pkg.skill_md_preview ||
      detail.skill_md_preview ||
      "(no SKILL.md)"
    );
  }
  return pkg.tool_md_preview || detail.tool_md_preview || "(no TOOL.md)";
}

function renderCatalogInspector(kind, detail) {
  if (!catalogInspector) return;
  catalogInspector.hidden = false;
  const name = detail.name || "—";
  const source =
    detail.source || detail.catalog_source || detail.package?.source || "—";
  const which = detail.which || "current";
  if (catalogInspectorTitle) catalogInspectorTitle.textContent = name;
  if (catalogInspectorBadges) {
    const bits = [kind, source, which].filter(Boolean);
    catalogInspectorBadges.innerHTML = bits
      .map((b) => `<span class="badge">${escapeHtml(String(b))}</span>`)
      .join("");
  }
  if (catalogInspectorDesc) {
    catalogInspectorDesc.textContent =
      detail.description || detail.package?.description || "—";
  }
  const pkg = detail.package || {};
  const files = pkg.files_present
    ? Object.entries(pkg.files_present)
        .map(([k, v]) => `${k}${v ? "✓" : "✗"}`)
        .join(" · ")
    : "";
  const top = Array.isArray(pkg.top_level) ? pkg.top_level.join(", ") : "";
  const metaParts = [];
  if (detail.tool_kind) metaParts.push(`kind ${detail.tool_kind}`);
  if (pkg.complete === true) metaParts.push("package complete");
  if (pkg.complete === false) metaParts.push("package incomplete");
  if (files) metaParts.push(files);
  if (top) metaParts.push(`files: ${top}`);
  if (detail.version_id) metaParts.push(`viewing ${detail.version_id}`);
  if (catalogInspectorMeta) {
    catalogInspectorMeta.textContent = metaParts.length
      ? metaParts.join(" · ")
      : "—";
  }
  if (catalogInspectorDoc) {
    catalogInspectorDoc.textContent = packageDocFromDetail(kind, detail);
  }
  if (catalogInspectorSchemaFold && catalogInspectorSchema) {
    if (detail.schema_preview) {
      catalogInspectorSchemaFold.hidden = false;
      catalogInspectorSchema.textContent = detail.schema_preview;
    } else {
      catalogInspectorSchemaFold.hidden = true;
      catalogInspectorSchema.textContent = "—";
    }
  }
  if (catalogInspectorRunnerFold && catalogInspectorRunner) {
    if (detail.runner && typeof detail.runner === "object") {
      catalogInspectorRunnerFold.hidden = false;
      catalogInspectorRunner.textContent = JSON.stringify(detail.runner, null, 2);
    } else {
      catalogInspectorRunnerFold.hidden = true;
      catalogInspectorRunner.textContent = "—";
    }
  }
  const versions = Array.isArray(detail.versions) ? detail.versions : [];
  if (catalogInspectorVcsHint) {
    if (source === "bundled" || (source !== "local" && !versions.length)) {
      catalogInspectorVcsHint.textContent =
        "Bundled packages are immutable — no package-VCS archives. Local re-promotes archive the prior tree.";
    } else if (!versions.length) {
      catalogInspectorVcsHint.textContent =
        "No archives yet. Re-promoting a local package will archive the previous tree here.";
    } else {
      catalogInspectorVcsHint.textContent = `${versions.length} archive(s). Click to preview that tree’s docs (read-only). Revert stays model/tool path.`;
    }
  }
  if (catalogInspectorVersionDoc) {
    catalogInspectorVersionDoc.hidden = true;
    catalogInspectorVersionDoc.textContent = "";
  }
  renderVersionList(catalogInspectorVersions, versions, (vid) => {
    loadCatalogVersion(kind, name, vid).catch((e) =>
      panelLoadError("Package VCS", e)
    );
  });
  // Prefer archived_at in version rows (package VCS uses archived_at, not promoted_at)
  if (catalogInspectorVersions && versions.length) {
    const rows = catalogInspectorVersions.querySelectorAll(".version-row");
    versions.forEach((v, i) => {
      const row = rows[i];
      if (!row) return;
      const vid = v.version_id || "";
      const when = v.archived_at || v.promoted_at || "";
      const reason = v.reason ? ` · ${v.reason}` : "";
      row.textContent = when ? `${vid} · ${when}${reason}` : `${vid}${reason}`;
    });
  }
}

async function loadCatalogVersion(kind, name, versionId) {
  const base = kind === "tool" ? "/api/tools/" : "/api/skills/";
  const q = new URLSearchParams({
    which: "version",
    version_id: versionId,
    list_versions: "0",
  });
  const detail = await fetchJson(`${base}${encodeURIComponent(name)}?${q}`);
  if (!detail || detail.ok === false) {
    throw new Error(detail?.error || "version not found");
  }
  if (catalogInspectorVersionDoc) {
    catalogInspectorVersionDoc.hidden = false;
    catalogInspectorVersionDoc.textContent =
      `// version ${versionId}\n\n` + packageDocFromDetail(kind, detail);
  }
  if (catalogInspectorMeta) {
    catalogInspectorMeta.textContent = `viewing archive ${versionId} (read-only)`;
  }
}

async function selectCatalogItem(kind, name, opts = {}) {
  if (!name) return;
  const soft = Boolean(opts.soft);
  catalogSelection = { kind, name };
  markCatalogSelectionHighlight();
  const base = kind === "tool" ? "/api/tools/" : "/api/skills/";
  const q = new URLSearchParams({ which: "current", list_versions: "1" });
  const detail = await fetchJson(`${base}${encodeURIComponent(name)}?${q}`);
  if (!detail || detail.ok === false) {
    hideCatalogInspector();
    catalogSelection = null;
    clearCatalogSelectionHighlight();
    lastCatalogDetailFp = null;
    throw new Error(detail?.error || `${kind} not found`);
  }
  const detailFp = stableFingerprint({ kind, name, detail });
  if (soft && detailFp === lastCatalogDetailFp) {
    return;
  }
  lastCatalogDetailFp = detailFp;
  setCatalogInspecting(true);
  renderCatalogInspector(kind, detail);
}

async function refreshTools(opts = {}) {
  const force = Boolean(opts.force);
  const [tools, skills] = await Promise.all([
    fetchJson("/api/tools"),
    fetchJson("/api/skills"),
  ]);
  const toolItems = tools.tools || [];
  const skillItems = skills.skills || [];
  const listFp = stableFingerprint({ toolItems, skillItems });
  if (
    force ||
    listFp !== lastToolsCatalogFp ||
    !toolsList ||
    !toolsList.childElementCount
  ) {
    lastToolsCatalogFp = listFp;
    renderCatalog(toolsList, toolItems, "No tools.", "tool");
    renderCatalog(skillsList, skillItems, "No skills.", "skill");
  }
  markCatalogSelectionHighlight();
  if (toolsCountEl) setTextIfChanged(toolsCountEl, String(toolItems.length));
  if (skillsCountEl) setTextIfChanged(skillsCountEl, String(skillItems.length));
  if (catalogMeta) {
    const localTools = toolItems.filter((t) => t.source === "local").length;
    const localSkills = skillItems.filter((s) => s.source === "local").length;
    setTextIfChanged(
      catalogMeta,
      `${toolItems.length} tools (${localTools} local) · ${skillItems.length} skills (${localSkills} local) · select a package to inspect · rescanned from disk`
    );
  }
  // Soft-refresh open inspector if still selected (skip re-render when detail unchanged).
  if (catalogSelection) {
    const stillThere =
      catalogSelection.kind === "tool"
        ? toolItems.some((t) => t.name === catalogSelection.name)
        : skillItems.some((s) => s.name === catalogSelection.name);
    if (stillThere) {
      selectCatalogItem(catalogSelection.kind, catalogSelection.name, {
        soft: !force,
      }).catch(() => hideCatalogInspector());
    } else {
      catalogSelection = null;
      lastCatalogDetailFp = null;
      hideCatalogInspector();
    }
  }
}

function renderVersionList(el, versions, onPick) {
  if (!el) return;
  el.innerHTML = "";
  const list = Array.isArray(versions) ? versions : [];
  if (!list.length) {
    el.textContent = "No archived versions yet.";
    return;
  }
  for (const v of list) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "version-row";
    const vid = v.version_id || "";
    const when = v.promoted_at || "";
    row.textContent = when ? `${vid} · ${when}` : vid;
    row.title = vid;
    row.addEventListener("click", () => onPick && onPick(vid));
    el.appendChild(row);
  }
}

function renderUserChips(users, selectedId) {
  if (!identityUserChips) return;
  identityUserChips.innerHTML = "";
  const list = Array.isArray(users) ? users : [];
  for (const u of list) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className =
      "user-chip" + (u.user_id === selectedId ? " user-chip-active" : "");
    const label = u.goes_by || u.user_id;
    btn.textContent = u.provisional ? `${label} · provisional` : label;
    btn.title = u.user_id;
    btn.addEventListener("click", () => {
      identityPanelUserId = u.user_id;
      lastIdentityFp = null;
      refreshIdentity({ force: true }).catch((e) =>
        panelLoadError("Identity", e)
      );
    });
    identityUserChips.appendChild(btn);
  }
}

async function refreshLabelCache() {
  try {
    const [session, users] = await Promise.all([
      fetchJson("/api/session"),
      fetchJson("/api/users"),
    ]);
    if (session && session.self_display_name) {
      labelCache.self = session.self_display_name;
    }
    updateBrandChrome();
    if (session && session.user_id) {
      // Server is source of truth when available; keep localStorage in sync.
      if (session.user_id !== sessionUserId) {
        // Prefer localStorage if operator just switched (race); only adopt
        // server when local is default or matches.
        const local = localStorage.getItem("elyra.sessionUserId");
        if (!local || local === session.user_id) {
          sessionUserId = session.user_id;
        }
      }
      labelCache.users[session.user_id] = session.goes_by || session.user_id;
    }
    const rows = (users && users.users) || [];
    for (const u of rows) {
      if (u && u.user_id) {
        labelCache.users[u.user_id] = u.goes_by || u.user_id;
      }
    }
    populateSessionSelect(rows);
  } catch {
    /* offline */
  }
}

function populateSessionSelect(users) {
  if (!sessionUserSelect) return;
  const list = Array.isArray(users) ? users : [];
  const prev = getSessionUserId();
  sessionUserSelect.innerHTML = "";
  let hasPrev = false;
  for (const u of list) {
    const opt = document.createElement("option");
    opt.value = u.user_id;
    const label = u.goes_by || u.user_id;
    opt.textContent = u.provisional ? `${label} (${u.user_id} · provisional)` : `${label} (${u.user_id})`;
    if (u.user_id === prev) {
      opt.selected = true;
      hasPrev = true;
    }
    sessionUserSelect.appendChild(opt);
  }
  if (!hasPrev && prev) {
    const opt = document.createElement("option");
    opt.value = prev;
    opt.textContent = prev;
    opt.selected = true;
    sessionUserSelect.appendChild(opt);
  }
}

async function switchSessionUser(userId) {
  if (!userId) return;
  const data = await fetchJson("/api/session", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  sessionUserId = data.user_id || userId;
  try {
    localStorage.setItem("elyra.sessionUserId", sessionUserId);
  } catch {
    /* private mode */
  }
  if (data.goes_by) {
    labelCache.users[sessionUserId] = data.goes_by;
  }
  if (data.self_display_name) {
    labelCache.self = data.self_display_name;
    updateBrandChrome();
  }
  identityPanelUserId = sessionUserId;
  showNotice(`Session user: ${labelCache.users[sessionUserId] || sessionUserId}`);
  await Promise.all([
    refreshLabelCache(),
    refreshMessages({ force: true }),
    refreshIdentity({ force: true }).catch(() => {}),
  ]);
}

async function createProvisionalUser() {
  const goesBy = window.prompt("Goes by (display name for the new guest)?");
  if (!goesBy || !goesBy.trim()) return;
  try {
    const data = await fetchJson("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goes_by: goesBy.trim() }),
    });
    if (!data.ok && data.error) {
      showNotice(`Create user failed: ${data.error}`);
      return;
    }
    const uid = data.user_id;
    showNotice(`Created provisional user ${data.goes_by || uid} (${uid})`);
    await switchSessionUser(uid);
  } catch (err) {
    showNotice(String(err.message || err));
  }
}

async function mintSelfGrant() {
  try {
    const data = await fetchJson("/api/identity/grants", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: "glass mint" }),
    });
    if (!data.ok) {
      showNotice(`Mint grant failed: ${data.error || "unknown"}`);
      return;
    }
    lastMintedGrantToken = data.token || null;
    if (identityGrantToken && lastMintedGrantToken) {
      identityGrantToken.hidden = false;
      identityGrantToken.textContent = `Grant token (copy once): ${lastMintedGrantToken}`;
    }
    showNotice("Self-promote grant minted (one-time). Click Promote self to adopt draft.");
  } catch (err) {
    showNotice(String(err.message || err));
  }
}

async function promoteSelfDraft() {
  const reason =
    window.prompt(
      "Promote self draft — reason (min 8 chars)?",
      "operator adopt via glass"
    ) || "";
  if (!reason.trim()) return;
  try {
    const body = { reason: reason.trim() };
    if (lastMintedGrantToken) {
      body.grant_token = lastMintedGrantToken;
    }
    const data = await fetchJson("/api/identity/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!data.ok) {
      showNotice(`Promote self failed: ${data.error || "denied"}`);
      return;
    }
    lastMintedGrantToken = null;
    if (identityGrantToken) {
      identityGrantToken.hidden = true;
      identityGrantToken.textContent = "";
    }
    showNotice("Self identity promoted.");
    await refreshIdentity({ force: true });
    await refreshLabelCache();
  } catch (err) {
    showNotice(String(err.message || err));
  }
}

async function promoteUserDraft() {
  const uid = identityPanelUserId || getSessionUserId();
  const reason =
    window.prompt(
      `Promote user draft for ${uid} — reason (min 4 chars)?`,
      "operator glass promote"
    ) || "";
  if (!reason.trim()) return;
  try {
    const data = await fetchJson(`/api/users/${encodeURIComponent(uid)}/promote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason.trim() }),
    });
    if (!data.ok) {
      showNotice(`Promote user failed: ${data.error || "denied"}`);
      return;
    }
    showNotice(`User ${uid} identity promoted.`);
    await refreshIdentity({ force: true });
    await refreshLabelCache();
  } catch (err) {
    showNotice(String(err.message || err));
  }
}

/** Promote CTA: primary+enabled only when a draft exists; keep btn-sm always. */
function setPromoteBtnState(btn, hasDraft, titles) {
  if (!btn) return;
  btn.disabled = !hasDraft;
  btn.classList.toggle("btn-primary", hasDraft);
  btn.classList.toggle("btn-secondary", !hasDraft);
  btn.title = hasDraft ? titles.enabled : titles.disabled;
}

function disablePromoteButtons() {
  setPromoteBtnState(identityPromoteSelfBtn, false, {
    enabled: "Promote draft to live self identity",
    disabled: "No draft to promote",
  });
  setPromoteBtnState(identityPromoteUserBtn, false, {
    enabled: "Promote draft to live user identity",
    disabled: "No draft to promote",
  });
}

async function refreshIdentity(opts = {}) {
  const force = Boolean(opts.force);
  try {
    const uid = identityPanelUserId || getSessionUserId();
    const [self, user, usersList] = await Promise.all([
      fetchJson("/api/identity?include_draft=1"),
      fetchJson(`/api/users/${encodeURIComponent(uid)}`),
      fetchJson("/api/users"),
    ]);
    const s = (self && self.self) || {};
    const users = (usersList && usersList.users) || [];
    const fp = stableFingerprint({ uid, self, user, users });
    if (!force && fp === lastIdentityFp) {
      return;
    }
    lastIdentityFp = fp;

    setTextIfChanged(
      identitySelf,
      s.body || s.digest || "(empty self digest)"
    );
    const selfName =
      s.display_name ||
      (s.meta && (s.meta.display_name || s.meta.goes_by)) ||
      "Elyra";
    setTextIfChanged(identitySelfLabel, selfName);
    labelCache.self = selfName;
    updateBrandChrome();
    const hasSelfDraft = Boolean(s.has_draft);
    if (identitySelfDraftBadge) identitySelfDraftBadge.hidden = !hasSelfDraft;
    if (identitySelfDraftFold) {
      identitySelfDraftFold.hidden = !hasSelfDraft;
      // KD20: leave collapsed on has_draft; force closed when draft gone
      if (!hasSelfDraft) identitySelfDraftFold.open = false;
      if (identitySelfDraft) {
        setTextIfChanged(identitySelfDraft, s.draft_body || "(empty draft)");
      }
    }
    setPromoteBtnState(identityPromoteSelfBtn, hasSelfDraft, {
      enabled: "Promote draft to live self identity",
      disabled: "No draft to promote",
    });
    renderVersionList(identitySelfVersions, s.versions || [], async (vid) => {
      // Versions are listed via meta index; body reload via get_identity would need
      // a version query — for v1 show id in the version body area from list only.
      if (identitySelfVersionBody) {
        identitySelfVersionBody.hidden = false;
        setTextIfChanged(
          identitySelfVersionBody,
          `version ${vid} (body via model get_identity / review-identity)`
        );
      }
    });

    renderUserChips(users, uid);

    setTextIfChanged(
      identityUser,
      user.body || user.profile || "(empty profile)"
    );
    setTextIfChanged(
      identityUserLabel,
      user.goes_by || (user.meta && user.meta.goes_by) || uid
    );
    if (identityUserMeta && user.meta) {
      const m = user.meta;
      const bits = [
        `id ${uid}`,
        m.goes_by ? `goes_by ${m.goes_by}` : null,
        m.full_name ? `full_name ${m.full_name}` : null,
        `provisional ${Boolean(m.provisional)}`,
        `real_name_known ${Boolean(m.real_name_known)}`,
      ].filter(Boolean);
      setTextIfChanged(identityUserMeta, bits.join(" · "));
    }
    const hasUserDraft = Boolean(user.has_draft);
    if (identityUserDraftBadge) identityUserDraftBadge.hidden = !hasUserDraft;
    if (identityUserDraftFold) {
      identityUserDraftFold.hidden = !hasUserDraft;
      if (!hasUserDraft) identityUserDraftFold.open = false;
      if (identityUserDraft) {
        setTextIfChanged(identityUserDraft, user.draft_body || "(empty draft)");
      }
    }
    setPromoteBtnState(identityPromoteUserBtn, hasUserDraft, {
      enabled: "Promote draft to live user identity",
      disabled: "No draft to promote",
    });
    renderVersionList(identityUserVersions, user.versions || [], (vid) => {
      if (identityUserVersionBody) {
        identityUserVersionBody.hidden = false;
        setTextIfChanged(
          identityUserVersionBody,
          `version ${vid} (body via model get_identity / review-identity)`
        );
      }
    });
    if (user.goes_by) labelCache.users[uid] = user.goes_by;
  } catch (err) {
    // Hard failure: do not leave promote enabled against stale draft UI
    disablePromoteButtons();
    lastIdentityFp = null;
    throw err;
  }
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

if (semanticWaitToggle) {
  semanticWaitToggle.addEventListener("change", () => {
    patchSemanticWait({ enabled: Boolean(semanticWaitToggle.checked) });
  });
}
if (semanticWaitMaxMs) {
  semanticWaitMaxMs.addEventListener("change", () => {
    const n = Number(semanticWaitMaxMs.value);
    if (!Number.isFinite(n)) return;
    patchSemanticWait({ max_ms: n });
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
// Effort segmented control (Status + rail share .effort-btn).
document.querySelectorAll(".effort-btn[data-effort]").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled || btn.getAttribute("data-effort") === "auto") return;
    if (providerPatchInFlight) return;
    const effort = btn.getAttribute("data-effort");
    if (!["low", "medium", "high"].includes(effort)) return;
    // Compare to server last, not painted state — no spurious PATCH.
    if (effort === lastReasoningEffort) return;
    // Optimistic visual only — do NOT assign lastReasoningEffort.
    paintEffortUI(effort);
    patchProvider({ reasoning_effort: effort });
  });
});
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

// ── xAI OAuth device login (PR4) — never display tokens ─────────────────
if (oauthLoginBtn) {
  oauthLoginBtn.addEventListener("click", () => {
    startXaiDeviceLogin();
  });
}
if (oauthLogoutBtn) {
  oauthLogoutBtn.addEventListener("click", () => {
    logoutXaiOauth();
  });
}
if (oauthCancelBtn) {
  oauthCancelBtn.addEventListener("click", () => {
    cancelXaiDeviceLogin();
  });
}
if (oauthCopyCodeBtn) {
  oauthCopyCodeBtn.addEventListener("click", () => {
    const code =
      (oauthPendingPublic && oauthPendingPublic.user_code) ||
      (oauthUserCode && oauthUserCode.textContent) ||
      "";
    copyOauthText(code.trim() === "—" ? "" : code.trim(), oauthCopyCodeBtn, "Copy code");
  });
}
if (oauthCopyUriBtn) {
  oauthCopyUriBtn.addEventListener("click", () => {
    const uri =
      (oauthPendingPublic &&
        (oauthPendingPublic.verification_uri_complete ||
          oauthPendingPublic.verification_uri)) ||
      (oauthVerifyLink && oauthVerifyLink.href) ||
      "";
    const safe =
      uri && uri !== "#" && !uri.endsWith("#") ? uri : "";
    copyOauthText(safe, oauthCopyUriBtn, "Copy link");
  });
}

// ── Secrets panel (PR5) — write-only values, never re-display ───────────
const secretsListEl = $("#secrets-list");
const secretsCountBadge = $("#secrets-count-badge");
const secretsNameInput = $("#secrets-name-input");
const secretsValueInput = $("#secrets-value-input");
const secretsGrantsInput = $("#secrets-grants-input");
const secretsSaveBtn = $("#secrets-save-btn");
const secretsFormMeta = $("#secrets-form-meta");

function parseGrantsCsv(raw) {
  if (!raw || typeof raw !== "string") return [];
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

async function refreshSecrets(opts = {}) {
  if (!secretsListEl) return;
  const force = Boolean(opts.force);
  const data = await fetchJson("/api/secrets");
  const secrets = (data && data.secrets) || [];
  if (secretsCountBadge) {
    setTextIfChanged(secretsCountBadge, String(secrets.length));
  }
  const fp = stableFingerprint(secrets);
  if (
    !force &&
    fp === lastSecretsFp &&
    secretsListEl.childElementCount > 0
  ) {
    return;
  }
  lastSecretsFp = fp;
  if (!secrets.length) {
    secretsListEl.textContent = "No named secrets yet.";
    return;
  }
  secretsListEl.innerHTML = "";
  for (const s of secrets) {
    const row = document.createElement("div");
    row.className = "secrets-row";
    const main = document.createElement("div");
    main.className = "secrets-row-main";
    const nameEl = document.createElement("div");
    nameEl.className = "secrets-row-name";
    nameEl.textContent = s.name || "—";
    const meta = document.createElement("div");
    meta.className = "secrets-row-meta";
    const grants = Array.isArray(s.grants) ? s.grants : [];
    meta.textContent = [
      s.managed_by ? `managed_by=${s.managed_by}` : null,
      grants.length ? `grants: ${grants.join(", ")}` : "grants: (none)",
      s.updated_at ? `updated ${s.updated_at}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    main.appendChild(nameEl);
    main.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "secrets-row-actions";
    const grantBtn = document.createElement("button");
    grantBtn.type = "button";
    grantBtn.className = "btn-secondary btn-sm";
    grantBtn.textContent = "Edit grants";
    grantBtn.addEventListener("click", () => {
      const edit = row.querySelector(".secrets-grants-edit");
      if (edit) {
        edit.hidden = !edit.hidden;
        return;
      }
      const wrap = document.createElement("div");
      wrap.className = "secrets-grants-edit";
      const inp = document.createElement("input");
      inp.type = "text";
      inp.className = "status-input";
      inp.value = grants.join(", ");
      inp.placeholder = "tool_a, tool_b";
      inp.setAttribute("aria-label", `Grants for ${s.name}`);
      const saveG = document.createElement("button");
      saveG.type = "button";
      saveG.className = "btn-secondary btn-sm";
      saveG.textContent = "Save grants";
      saveG.addEventListener("click", async () => {
        if (secretsInFlight) return;
        secretsInFlight = true;
        try {
          await fetchJson(`/api/secrets/${encodeURIComponent(s.name)}/grants`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ grants: parseGrantsCsv(inp.value) }),
          });
          showNotice(`Grants updated for ${s.name}.`);
          await refreshSecrets({ force: true });
        } catch (err) {
          showNotice(`Grants failed: ${err && err.message ? err.message : err}`);
        } finally {
          secretsInFlight = false;
        }
      });
      wrap.appendChild(inp);
      wrap.appendChild(saveG);
      row.appendChild(wrap);
    });
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn-secondary btn-sm";
    delBtn.textContent = "Delete";
    delBtn.addEventListener("click", async () => {
      if (secretsInFlight) return;
      if (!window.confirm(`Delete secret “${s.name}”? This cannot be undone.`)) return;
      secretsInFlight = true;
      try {
        await fetchJson(`/api/secrets/${encodeURIComponent(s.name)}`, {
          method: "DELETE",
        });
        showNotice(`Deleted secret ${s.name}.`);
        await refreshSecrets({ force: true });
      } catch (err) {
        showNotice(`Delete failed: ${err && err.message ? err.message : err}`);
      } finally {
        secretsInFlight = false;
      }
    });
    actions.appendChild(grantBtn);
    actions.appendChild(delBtn);
    row.appendChild(main);
    row.appendChild(actions);
    secretsListEl.appendChild(row);
  }
}

async function saveSecret() {
  if (secretsInFlight || !secretsNameInput || !secretsValueInput) return;
  const name = secretsNameInput.value.trim();
  const value = secretsValueInput.value;
  if (!name) {
    showNotice("Secret name required.");
    return;
  }
  if (!value || !value.trim()) {
    showNotice("Secret value required.");
    return;
  }
  const grants = parseGrantsCsv(secretsGrantsInput ? secretsGrantsInput.value : "");
  secretsInFlight = true;
  if (secretsSaveBtn) secretsSaveBtn.disabled = true;
  try {
    const body = { name, value };
    if (grants.length) body.grants = grants;
    const data = await fetchJson("/api/secrets", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    // Write-only: clear the value field; never paint it back from the response.
    secretsValueInput.value = "";
    if (data && data.secret && data.secret.value) {
      // Defense: if server ever echoed value, do not keep it in UI state.
      delete data.secret.value;
    }
    if (secretsFormMeta) secretsFormMeta.textContent = `Saved ${name} (value not stored in UI).`;
    showNotice(`Secret ${name} saved.`);
    await refreshSecrets({ force: true });
  } catch (err) {
    showNotice(`Save secret failed: ${err && err.message ? err.message : err}`);
  } finally {
    secretsInFlight = false;
    if (secretsSaveBtn) secretsSaveBtn.disabled = false;
  }
}

if (secretsSaveBtn) {
  secretsSaveBtn.addEventListener("click", () => {
    saveSecret();
  });
}
if (secretsValueInput) {
  secretsValueInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      saveSecret();
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
    refreshMessages({ force: true }),
    refreshGoals({ force: true }).catch(() => {}),
    refreshMoments({ force: true }).catch(() => {}),
    refreshTools({ force: true }).catch(() => {}),
    refreshIdentity({ force: true }).catch(() => {}),
    refreshSecrets({ force: true }).catch(() => {}),
    refreshMemory({ force: true }).catch(() => {}),
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
      icon.textContent = kindIcon(att.kind);
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
    if (pendingAttachments.length >= MAX_PENDING_ATTACHMENTS) {
      showNotice(
        `Attachment limit: ${MAX_PENDING_ATTACHMENTS} files per message.`
      );
      break;
    }
    const kind = detectAttachmentKind(file);
    const maxBytes = clientMaxBytesForKind(kind);
    if (file.size > maxBytes) {
      showNotice(
        `${file.name} is too large (${formatBytes(file.size)}; max ${formatBytes(
          maxBytes
        )} for ${kind}).`
      );
      continue;
    }
    const att = {
      name: file.name,
      size: file.size,
      type: file.type || "application/octet-stream",
      kind,
      previewUrl: kind === "image" ? URL.createObjectURL(file) : null,
      file, // File/Blob for POST /api/media on send
    };
    pendingAttachments.push(att);
  }
  renderAttachTray();
}

/**
 * Upload pending tray files via multipart POST /api/media.
 * Pre-uploaded ids (e.g. STT keep_audio) are returned as-is.
 * Returns attachment id list (durable store). Does not clear tray.
 */
async function uploadPendingAttachments() {
  if (!pendingAttachments.length) return [];
  const already = [];
  const needUpload = [];
  for (const att of pendingAttachments) {
    if (att.id) {
      already.push(att.id);
    } else {
      needUpload.push(att);
    }
  }
  if (!needUpload.length) return already;

  // Group by origin so recordings keep user_recording / stt_source.
  const byOrigin = new Map();
  for (const att of needUpload) {
    const origin = att.origin || "user_upload";
    if (!byOrigin.has(origin)) byOrigin.set(origin, []);
    byOrigin.get(origin).push(att);
  }
  const uploadedIds = [];
  for (const [origin, group] of byOrigin.entries()) {
    const formData = new FormData();
    formData.append("user_id", getSessionUserId());
    formData.append("origin", origin);
    for (const att of group) {
      const blob = att.file;
      if (!blob) {
        throw new Error(`Missing file bytes for ${att.name || "attachment"}`);
      }
      formData.append("files", blob, att.name || "file");
    }
    const res = await fetch("/api/media", { method: "POST", body: formData });
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
    const uploaded = Array.isArray(data.attachments) ? data.attachments : [];
    if (!uploaded.length) {
      throw new Error("Upload returned no attachments");
    }
    for (const a of uploaded) {
      if (a && a.id) uploadedIds.push(a.id);
    }
  }
  return already.concat(uploadedIds);
}

function setMicUi({ recording = false, transcribing = false } = {}) {
  if (!micBtn) return;
  micBtn.classList.toggle("recording", !!recording);
  micBtn.classList.toggle("transcribing", !!transcribing);
  micBtn.setAttribute("aria-pressed", recording ? "true" : "false");
  micBtn.disabled = !!transcribing;
  micBtn.title = recording
    ? "Stop recording"
    : transcribing
      ? "Transcribing…"
      : "Record voice (speech-to-text)";
}

function stopMicStream() {
  if (micStream) {
    try {
      micStream.getTracks().forEach((t) => t.stop());
    } catch {
      /* ignore */
    }
    micStream = null;
  }
}

function pickRecorderMime() {
  if (typeof MediaRecorder === "undefined") return "";
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];
  for (const t of candidates) {
    if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) {
      return t;
    }
  }
  return "";
}

/**
 * POST audio blob to host STT proxy; fill composer with transcript.
 * keep_audio=1 stores recording as durable attachment and chips it in tray.
 */
async function transcribeRecordingBlob(blob, { keepAudio = true } = {}) {
  const mime = blob.type || "audio/webm";
  const ext = mime.includes("ogg")
    ? "ogg"
    : mime.includes("mp4") || mime.includes("m4a")
      ? "m4a"
      : mime.includes("wav")
        ? "wav"
        : "webm";
  const filename = `recording.${ext}`;
  const formData = new FormData();
  formData.append("user_id", getSessionUserId());
  formData.append("keep_audio", keepAudio ? "1" : "0");
  formData.append("origin", "user_recording");
  formData.append("file", blob, filename);

  const res = await fetch("/api/stt", { method: "POST", body: formData });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const reason = (data && (data.reason || data.error)) || text || res.statusText;
    const err = new Error(`${res.status}: ${reason}`);
    err.status = res.status;
    err.body = data;
    throw err;
  }
  return data;
}

function insertTranscriptIntoComposer(transcript) {
  if (!input) return;
  const t = String(transcript || "").trim();
  if (!t) return;
  const cur = input.value || "";
  if (!cur.trim()) {
    input.value = t;
  } else if (cur.endsWith(" ") || cur.endsWith("\n")) {
    input.value = cur + t;
  } else {
    input.value = `${cur} ${t}`;
  }
  autosizeComposer();
  input.focus();
}

async function finishMicRecording() {
  const chunks = micChunks.slice();
  micChunks = [];
  micRecorder = null;
  stopMicStream();
  setMicUi({ recording: false, transcribing: true });
  micBusy = true;
  try {
    if (!chunks.length) {
      showNotice("No audio captured.");
      return;
    }
    const blob = new Blob(chunks, {
      type: (chunks[0] && chunks[0].type) || "audio/webm",
    });
    if (!blob.size) {
      showNotice("Empty recording.");
      return;
    }
    const clientMax = clientMaxBytesForKind("audio");
    if (blob.size > clientMax) {
      showNotice(
        `Recording too large (${formatBytes(blob.size)}; max ${formatBytes(
          clientMax
        )}).`
      );
      return;
    }
    showNotice("Transcribing…");
    const data = await transcribeRecordingBlob(blob, { keepAudio: true });
    const transcript = (data && data.text) || "";
    if (!transcript.trim()) {
      showNotice("Empty transcript from speech-to-text.");
      return;
    }
    insertTranscriptIntoComposer(transcript);
    if (data.attachment_id) {
      if (pendingAttachments.length >= MAX_PENDING_ATTACHMENTS) {
        showNotice(
          `Transcript ready; attachment tray full (max ${MAX_PENDING_ATTACHMENTS}).`
        );
      } else {
        const meta = data.attachment || {};
        pendingAttachments.push({
          name: meta.filename || "recording.webm",
          size: meta.byte_size || blob.size,
          type: meta.mime || blob.type || "audio/webm",
          kind: "audio",
          previewUrl: null,
          id: data.attachment_id,
          origin: meta.origin || "user_recording",
        });
        renderAttachTray();
      }
    }
    showNotice("Transcript ready — edit and send when ready.");
  } catch (err) {
    const body = err && err.body;
    const reason = body && body.reason;
    if (reason === "provider_unsupported") {
      showNotice("Speech-to-text requires the xAI provider.");
    } else if (reason === "credential_unavailable") {
      showNotice("Speech-to-text: credentials unavailable (host).");
    } else if (reason === "stt_disabled") {
      showNotice("Speech-to-text is disabled.");
    } else {
      showNotice(String(err.message || err));
    }
  } finally {
    micBusy = false;
    setMicUi({ recording: false, transcribing: false });
  }
}

async function toggleMicRecording() {
  if (!micBtn || micBusy) return;
  if (micRecorder && micRecorder.state === "recording") {
    try {
      micRecorder.stop();
    } catch (err) {
      showNotice(String(err.message || err));
      stopMicStream();
      micRecorder = null;
      setMicUi({ recording: false, transcribing: false });
    }
    return;
  }
  if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices) {
    showNotice("Microphone recording is not supported in this browser.");
    return;
  }
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    showNotice(
      `Microphone permission denied or unavailable (${err && err.message ? err.message : err}).`
    );
    return;
  }
  micChunks = [];
  const mime = pickRecorderMime();
  try {
    micRecorder = mime
      ? new MediaRecorder(micStream, { mimeType: mime })
      : new MediaRecorder(micStream);
  } catch (err) {
    stopMicStream();
    showNotice(`Could not start recorder: ${err.message || err}`);
    return;
  }
  micRecorder.addEventListener("dataavailable", (ev) => {
    if (ev.data && ev.data.size) micChunks.push(ev.data);
  });
  micRecorder.addEventListener("stop", () => {
    finishMicRecording();
  });
  micRecorder.addEventListener("error", (ev) => {
    showNotice(`Recorder error: ${(ev.error && ev.error.message) || "unknown"}`);
    stopMicStream();
    micRecorder = null;
    setMicUi({ recording: false, transcribing: false });
  });
  try {
    micRecorder.start();
    setMicUi({ recording: true, transcribing: false });
  } catch (err) {
    stopMicStream();
    micRecorder = null;
    showNotice(`Could not start recording: ${err.message || err}`);
  }
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
  const hasPending = pendingAttachments.length > 0;
  // Media-only send allowed: empty text + attachments (R1b / glass empty-content).
  if (!text && !hasPending) return;
  sendBtn.disabled = true;
  try {
    let attachmentIds = [];
    if (hasPending) {
      attachmentIds = await uploadPendingAttachments();
    }
    const payload = {
      content: text, // user text only — no inventory prose (PR4)
      user_id: getSessionUserId(),
    };
    if (attachmentIds.length) {
      payload.attachment_ids = attachmentIds;
    }
    const data = await fetchJson("/api/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
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

if (micBtn) {
  micBtn.addEventListener("click", () => {
    toggleMicRecording();
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

const catalogInspectorClose = $("#catalog-inspector-close");
if (catalogInspectorClose) {
  catalogInspectorClose.addEventListener("click", () => {
    hideCatalogInspector();
  });
}

if (catalogRefreshBtn) {
  catalogRefreshBtn.addEventListener("click", () => {
    refreshTools({ force: true })
      .then(() => showNotice("Tools & skills rescanned from disk."))
      .catch((e) => panelLoadError("Tools", e));
  });
}

if (sessionUserSelect) {
  sessionUserSelect.addEventListener("change", () => {
    const uid = sessionUserSelect.value;
    if (!uid || uid === sessionUserId) return;
    switchSessionUser(uid).catch((e) => showNotice(String(e.message || e)));
  });
}
if (sessionNewGuestBtn) {
  sessionNewGuestBtn.addEventListener("click", () => {
    createProvisionalUser();
  });
}
if (identityMintGrantBtn) {
  identityMintGrantBtn.addEventListener("click", () => mintSelfGrant());
}
if (identityPromoteSelfBtn) {
  identityPromoteSelfBtn.addEventListener("click", () => promoteSelfDraft());
}
if (identityPromoteUserBtn) {
  identityPromoteUserBtn.addEventListener("click", () => promoteUserDraft());
}

autosizeComposer();
updateBrandChrome();
// Sync session + labels before first paint of messages.
refreshLabelCache()
  .then(() => {
    updateBrandChrome();
    // Align server session with localStorage preference on boot.
    if (sessionUserId) {
      return fetchJson("/api/session", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: sessionUserId }),
      }).catch(() => null);
    }
    return null;
  })
  .catch(() => {});

function panelLoadError(panelName, err) {
  showNotice(`${panelName}: ${err && err.message ? err.message : err}`);
}

function refreshActivePanel(opts = {}) {
  const force = Boolean(opts.force);
  const name = activePanel;
  // Tick uses soft-refresh (force=false); nav click / buttons pass force=true.
  // Moments is a Memory tab — polled via refreshMemory when activePanel === "memory".
  if (name === "goals") return refreshGoals({ force });
  if (name === "memory") return refreshMemory({ force });
  if (name === "tools") return refreshTools({ force });
  if (name === "identity") return refreshIdentity({ force });
  if (name === "secrets") return refreshSecrets({ force });
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
    // Force refresh on nav so opening a panel always shows current disk state.
    if (name === "goals")
      refreshGoals({ force: true }).catch((e) => panelLoadError("Goals", e));
    if (name === "memory")
      refreshMemory({ force: true }).catch((e) => panelLoadError("Memory", e));
    if (name === "tools")
      refreshTools({ force: true }).catch((e) => panelLoadError("Tools", e));
    if (name === "identity")
      refreshIdentity({ force: true }).catch((e) =>
        panelLoadError("Identity", e)
      );
    if (name === "secrets")
      refreshSecrets({ force: true }).catch((e) =>
        panelLoadError("Secrets", e)
      );
  });
});

async function tick() {
  // Single-flight: skip if previous tick still running (avoids list/detail races).
  if (tickInFlight) return;
  tickInFlight = true;
  try {
    const tasks = [refreshStatus(), refreshMessages()];
    // Also poll the active catalog panel so creates appear without nav re-click.
    // Moments is under Memory (active tab) — no separate activePanel.
    if (
      activePanel === "goals" ||
      activePanel === "memory" ||
      activePanel === "tools" ||
      activePanel === "identity" ||
      activePanel === "secrets"
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

tick().then(() => maybeResumeOauthDeviceSession().catch(() => {}));
setInterval(tick, 1500);
