const $ = (sel) => document.querySelector(sel);

const messagesEl = $("#messages");
const form = $("#chat-form");
const input = $("#chat-input");
const sendBtn = $("#send-btn");
const statusJson = $("#status-json");
const pillLlama = $("#pill-llama");
const pillWorker = $("#pill-worker");
const pillPhase = $("#pill-phase");
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

const USER_ID = "operator";
const REASON_BUFFER_FULL = "interjection_buffer_full";

let lastPendingWaitId = null;
let noticeTimer = null;
/** True while a wait-choice POST is in flight (blocks double-submit). */
let waitReplyInFlight = false;

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

function renderMessages(messages) {
  messagesEl.innerHTML = "";
  for (const m of messages) {
    const div = document.createElement("div");
    div.className = `msg ${m.role === "user" ? "user" : "assistant"}`;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${m.role} · ${m.created_at || ""}`;
    div.appendChild(meta);
    const body = document.createElement("div");
    body.textContent = m.content || "";
    div.appendChild(body);
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
  messagesEl.scrollTop = messagesEl.scrollHeight;
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

async function refreshMessages() {
  const data = await fetchJson("/api/messages?limit=200");
  renderMessages(data.messages || []);
}

async function refreshStatus() {
  const s = await fetchJson("/api/status");
  statusJson.textContent = JSON.stringify(s, null, 2);

  if (s.llama_ready) {
    setPill(
      pillLlama,
      s.llama_busy ? "llama busy" : "llama ready",
      s.llama_busy ? "pill-busy" : "pill-on"
    );
  } else if (s.llama_error === "stub_llm") {
    setPill(pillLlama, "stub llm", "pill-off");
  } else {
    setPill(pillLlama, s.llama_error ? "llama error" : "llama off", "pill-off");
  }

  if (s.worker_busy) {
    setPill(pillWorker, "worker busy", "pill-busy");
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
  for (const b of beats) {
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

async function loadMomentDetail(id) {
  momentDetail.hidden = false;
  momentDetail.innerHTML = "loading…";
  try {
    const data = await fetchJson(`/api/moments/${encodeURIComponent(id)}`);
    const m = data.moment || {};
    momentDetail.innerHTML = "";
    const head = document.createElement("div");
    head.className = "card-head";
    head.innerHTML = `<strong>${escapeHtml(m.why_now || m.id)}</strong>
      <button type="button" class="link-btn" id="close-moment-detail">close</button>`;
    momentDetail.appendChild(head);
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${m.id} · stop=${m.stop_reason || "—"} · hops=${
      m.hop_count ?? 0
    }`;
    momentDetail.appendChild(meta);
    momentDetail.appendChild(renderBeats(data.beats || []));
    $("#close-moment-detail").addEventListener("click", () => {
      momentDetail.hidden = true;
      momentDetail.innerHTML = "";
    });
  } catch (err) {
    momentDetail.textContent = String(err.message || err);
  }
}

async function refreshMoments() {
  const data = await fetchJson("/api/moments?limit=40");
  renderMoments(data.moments || []);
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
    card.innerHTML = `
      <div class="card-head">
        <strong>${escapeHtml(t.name)}</strong>
        <span class="badge">${escapeHtml(t.kind || t.source || "")}</span>
      </div>
      <p class="muted">${escapeHtml(t.description || "")}</p>`;
    el.appendChild(card);
  }
}

async function refreshTools() {
  const [tools, skills] = await Promise.all([
    fetchJson("/api/tools"),
    fetchJson("/api/skills"),
  ]);
  renderCatalog(toolsList, tools.tools || [], "No tools.");
  renderCatalog(skillsList, skills.skills || [], "No skills.");
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

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const content = input.value.trim();
  if (!content) return;
  sendBtn.disabled = true;
  try {
    const data = await fetchJson("/api/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, user_id: USER_ID }),
    });
    input.value = "";
    if (data.ok === false && data.reason === REASON_BUFFER_FULL) {
      showNotice(
        "Interjection buffer full — message queued as a wake for after this moment."
      );
    }
    await refreshMessages();
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

function panelLoadError(panelName, err) {
  showNotice(`${panelName}: ${err && err.message ? err.message : err}`);
}

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    const panel = document.getElementById(`panel-${btn.dataset.panel}`);
    if (panel) panel.classList.add("active");
    // Refresh panel data when opened; surface failures (parity with chat).
    const name = btn.dataset.panel;
    if (name === "goals") refreshGoals().catch((e) => panelLoadError("Goals", e));
    if (name === "moments") refreshMoments().catch((e) => panelLoadError("Moments", e));
    if (name === "tools") refreshTools().catch((e) => panelLoadError("Tools", e));
    if (name === "identity") refreshIdentity().catch((e) => panelLoadError("Identity", e));
  });
});

async function tick() {
  try {
    await Promise.all([refreshStatus(), refreshMessages()]);
  } catch {
    /* offline */
  }
}

tick();
setInterval(tick, 1500);
