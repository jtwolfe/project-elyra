const $ = (sel) => document.querySelector(sel);

const messagesEl = $("#messages");
const form = $("#chat-form");
const input = $("#chat-input");
const sendBtn = $("#send-btn");
const statusJson = $("#status-json");
const pillLlama = $("#pill-llama");
const pillWorker = $("#pill-worker");

function setPill(el, label, mode) {
  el.textContent = label;
  el.classList.remove("pill-on", "pill-off", "pill-busy");
  el.classList.add(mode);
}

async function fetchJson(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`${res.status}: ${t}`);
  }
  return res.json();
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
      const r = document.createElement("div");
      r.className = "reason";
      r.textContent = `reasoning: ${m.reasoning.slice(0, 800)}${m.reasoning.length > 800 ? "…" : ""}`;
      div.appendChild(r);
    }
    messagesEl.appendChild(div);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function refreshMessages() {
  const data = await fetchJson("/api/messages?limit=200");
  renderMessages(data.messages || []);
}

async function refreshStatus() {
  const s = await fetchJson("/api/status");
  statusJson.textContent = JSON.stringify(s, null, 2);

  if (s.llama_ready) {
    setPill(pillLlama, s.llama_busy ? "llama busy" : "llama ready", s.llama_busy ? "pill-busy" : "pill-on");
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
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const content = input.value.trim();
  if (!content) return;
  sendBtn.disabled = true;
  try {
    await fetchJson("/api/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, user_id: "operator" }),
    });
    input.value = "";
    await refreshMessages();
  } catch (err) {
    alert(String(err.message || err));
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

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    const panel = document.getElementById(`panel-${btn.dataset.panel}`);
    if (panel) panel.classList.add("active");
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
