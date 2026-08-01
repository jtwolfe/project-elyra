# Grok Build headless spike notes (PR0a)

| Field | Value |
|-------|--------|
| **Status** | Spike notes — **not signed** for enabling `deep_research` beyond experimental |
| **Date** | 2026-08-01 |
| **Branch / PR** | `feature/gb-*-pr0a` · PR0a |
| **Parent design** | [design-grok-build-tool.md](design-grok-build-tool.md) — **[KD16](design-grok-build-tool.md)** (`deep_research` experimental until this spike) · **[KD15](design-grok-build-tool.md)** (headless human-gate) |
| **Host binary used for local probe** | `/home/jim/.grok/bin/grok` → `grok-0.2.118-linux-x86_64` |
| **Host docs** | `~/.grok/docs/user-guide/14-headless-mode.md`, `04-slash-commands.md` |

**Purpose:** document how to run headless spikes for `/deep-research` exit timing and `/design` human-gate behavior; record strategy options for PE `grok_build` integration; **block enabling `deep_research` beyond `mode_experimental` until an operator signs this spike.**

---

## 1. Operator checklist — how to run headless spikes

Use a throwaway cwd when possible. Prefer isolated `GROK_HOME` later for PE dogfood; operator spikes may use the default home if already authenticated.

**Flag notes (host 0.2.x):**

| Flag | Role |
|------|------|
| `-p` / `--single` | Headless single-turn prompt; process prints result and exits |
| `--output-format json` | Single JSON object on stdout after the response completes |
| `--yolo` | Auto-approve tool executions (alias of `--always-approve` / `bypassPermissions`) |
| `--always-approve` | Same auto-approve semantics; design doc / PE argv sketch prefer this name |
| `--max-turns N` | Cap agentic turns (headless); useful to bound accidental spend |
| `--cwd PATH` | Working directory for the session |
| `--rules TEXT` | Extra system rules (PE human-gate policy can live here later) |

> **Exit-timing unknown until measured per host version.** TUI docs and a quick local probe can disagree with longer real research runs. Always record wall clock, exit code, and whether stdout JSON arrived before the process died.

### 1.1 Deep research spike

```bash
# Wall-clock with kill backstop (adjust seconds; 15–120s for first look is fine)
export PATH="${HOME}/.grok/bin:${PATH}"   # if needed
PROBE_DIR=$(mktemp -d /tmp/grok-dr-spike-XXXX)
cd "$PROBE_DIR"

START=$(date +%s.%N)
timeout --signal=TERM --kill-after=5s 120s \
  grok -p "/deep-research <short factual query>" \
    --output-format json \
    --yolo \
  >"$PROBE_DIR/stdout.json" 2>"$PROBE_DIR/stderr.txt"
EC=$?
END=$(date +%s.%N)

echo "exit_code=$EC duration_s=$(python3 -c "print(round($END-$START,3))")"
# Inspect: sessionId, text, any workflow/run id fields, stopReason, usage
head -c 2000 "$PROBE_DIR/stdout.json"; echo
```

**Record for each run:**

1. Wall duration and process exit code (`timeout` → 124 on wall kill).
2. Whether the process **blocked until a full report** or **exited early** with a “started in background” style message.
3. Structured fields: `sessionId`, `requestId`, `stopReason`, `usage`, and **any** workflow / run / display-name id (or only free-text mention of `/workflows`).
4. Whether a later headless poll exists (`grok sessions …`, resume, or documented workflow status CLI) that can harvest the report **without** a TUI.
5. Where the final report lands (session resume text, artifact path, or only TUI conversation).

**Optional follow-ups (operator):**

- Resume the `sessionId` after a few minutes: `grok -p "summarize the deep-research result if ready" -r <sessionId> --output-format json --yolo`
- Confirm whether background workflow survives process exit (TUI docs claim async workflow; headless process may detach or die with the session).
- Do **not** burn multi-hour research on first spike; a tiny query plus wall timeout is enough to classify strategy (1) vs (2).

### 1.2 Design / human-gate spike (`needs_human`)

```bash
PROBE_DIR=$(mktemp -d /tmp/grok-design-spike-XXXX)
cd "$PROBE_DIR"
ART="$PROBE_DIR/artifacts"
mkdir -p "$ART"

# Intentionally ambiguous product prompt so the skill may need human decisions.
# Inject KD15-style policy (see design-grok-build-tool.md § Headless human-gate policy).
POLICY='HEADLESS PE POLICY (mandatory):
- You are running non-interactively for Project Elyra. There is no human at this TTY.
- Do NOT block waiting for interactive clarification, ask_user_question, or permission prompts.
- If you need a human decision: write remaining open questions into '"$ART"'/design.md
  under a clear NEEDS_HUMAN section, and end the run.
- Prefer fail-closed documented gaps over inventing product decisions.
- Do not spin escalate loops beyond 2 rounds of unresolved needs-user-input; then NEEDS_HUMAN stop.'

timeout --signal=TERM --kill-after=10s 600s \
  grok -p "/design Design a feature with deliberately underspecified requirements: X vs Y tradeoff with no preference stated. Write the design doc to ${ART}/design.md" \
    --output-format json \
    --always-approve \
    --rules "$POLICY" \
  >"$PROBE_DIR/stdout.json" 2>"$PROBE_DIR/stderr.txt"
```

**Record:**

1. Did the process **hang** on an interactive ask tool, or **exit** with `NEEDS_HUMAN` / open questions in artifact or stdout?
2. Exit code path for “needs human” (design KD15 wants PE `status=needs_human` as **ok path**, not hard tool error).
3. Whether `--always-approve` alone is enough, or PE **must** inject the human-gate rules text (KD15).
4. Artifact harvest: path under `$ART` vs only TMP/session paths.

> Full `/design` can be long and spendy. For a smoke-only check, use a short ambiguous prompt + `--max-turns` and accept incomplete skill fidelity; for sign-off, run without turn starvation and keep the wall timeout.

---

## 2. Strategy options for `deep_research` PE integration (KD16)

From [design-grok-build-tool.md](design-grok-build-tool.md) § **deep_research contract (experimental — KD16)**:

| Strategy | Meaning | PE implication |
|----------|---------|----------------|
| **(1) Headless blocks until report** | `grok -p "/deep-research …"` keeps the process alive until the research report is in the response | Treat like other long modes: async job + reaper waits on process; harvest report from stdout/artifacts; default wall ~60 min |
| **(2) Process exits early with workflow id** | Process returns quickly; research continues under a workflow/run handle | Store workflow/run id (or session handle) in `meta.json`; reaper/poller uses documented CLI **or** fail closed with `workflow_poll_unsupported` |
| **(3) Spike inconclusive / poll unsupported** | Cannot prove (1) or a reliable headless poll for (2) | Handler returns `error_reason=mode_experimental`; schema enum still includes `deep_research` |

**Until this document is signed with strategy (1) or (2) green, ship path is (3).** Dogfood **D7** stays blocked. Do not claim `status=completed` from a background-launch ack alone.

### 2.1 Local quick probe (2026-08-01, host 0.2.118) — provisional only

Safe, spend-light probes with `/home/jim/.grok/bin/grok` (wall timeout 15s):

| Probe | Command sketch | Observed |
|-------|----------------|----------|
| Light headless | `grok -p "Reply with exactly: PONG" --output-format json --yolo --max-turns 1` | **exit 0** in ~3.8s; JSON with `text`, `stopReason=end_turn`, `sessionId`, `usage` |
| Deep research | `grok -p "/deep-research What is 2+2? …" --output-format json --yolo --max-turns 3` | **exit 0** in ~1.6s (**did not block for a full report**). JSON `text` ≈ *Deep research 'deep-research' started in the background… Use /workflows to follow progress.* Fields: `sessionId`, `requestId`, `stopReason=end_turn`. **No structured workflow/run id field.** No `usage` block on this response. |
| `/workflows` via `-p` | `grok -p "/workflows" --output-format json --yolo --max-turns 1` | Not a useful headless poll; hit turn/cancel path (`stopReason=cancelled` / max turns). TUI `/workflows` is the documented dashboard. |

**Interpretation:**

- Strategy **(1) is not supported** by this host version for a trivial query: the headless process **returns immediately** after launching background research (matches TUI docs: command “returns right away”).
- Strategy **(2) is suggested by exit timing**, but the JSON ack is **prose + `sessionId`**, not a documented machine-readable workflow id + headless status/poll CLI. Without a proven poll/harvest path, PE cannot safely treat early exit as completion or as a reaper-owned wait.
- Therefore the **provisional PE recommendation is strategy (3)** until an operator completes §1.1 (including post-exit poll / report harvest) and **signs** strategy (2) — or proves a later host version blocks until report (1).

### 2.2 Provisional recommendation (unsigned)

| Item | Value |
|------|--------|
| **Ship default** | **Strategy (3)** — `mode_experimental` soft-fail |
| **Do not enable** | Non-experimental `deep_research` completion paths |
| **Next measurement** | Operator runs full §1.1 checklist; if early exit + durable poll of report is proven, **sign strategy (2)** with the exact poll argv and harvest rules; if a host version blocks until report, sign **(1)** |
| **PE handler until signed** | `error_reason=mode_experimental` + payload hint pointing at this doc |

---

## 3. Link to design KD16 (and KD15)

Normative product decisions live in the design doc, not here.

- **[KD16 — `deep_research` experimental until PR0a spike](design-grok-build-tool.md)** — headless exit/workflow contract must be documented; may soft-fail `mode_experimental`.
- Design § **deep_research contract (experimental — KD16)** — strategies (1)/(2)/(3) table (source of the table in §2).
- **[KD15 — Headless human-gate policy](design-grok-build-tool.md)** — no hang on ask tools; return `status=needs_human` + artifacts (exit-0 path for PE). Checklist §1.2 exercises this for `/design` under `--always-approve`.
- Mode table row for **`deep_research`**: `grok -p "/deep-research <query>"`; failure modes include `mode_experimental`, `workflow_poll_unsupported`.
- Summary index: [design-grok-build-tool-summary.md](design-grok-build-tool-summary.md) (PR0a row).

---

## 4. Acceptance — what this PR blocks

| Gate | Rule |
|------|------|
| **PR0a acceptance (this doc)** | Checklist written; strategies defined; provisional recommendation recorded; link to KD16 |
| **Enable `deep_research` beyond `mode_experimental`** | **Blocked** until an operator (or follow-up commit) **signs** strategy **(1)** or **(2)** with measured exit timing, JSON sample fields, and harvest/poll procedure |
| **Dogfood D7** | Blocked on signed strategy (design dogfood table) |
| **PR4 tool registration** | May expose enum value `deep_research` but handler must fail closed with `mode_experimental` until signed |
| **Human-gate for design** | PE implementation still injects KD15 policy; operator §1.2 is evidence, not a code gate for other modes |

### Sign-off block (fill when strategy is chosen)

```text
Signed strategy:     (1 | 2 | still 3)
Host grok version:
Date / operator:
Exit timing summary:
JSON fields used for id / report:
Poll / harvest command (if 2):
Evidence path / notes:
```

---

## 5. Related PE notes (no runtime code in PR0a)

- PE argv sketch uses `--always-approve` and `--output-format json`; host treats `--yolo` as the same auto-approve class.
- Long modes default **async** (KD11); even strategy (1) would reaper-wait on the process. Strategy (2) needs extra poll state beyond pid wait.
- Do **not** treat background-ack `text` as a completed research report.
- Auth hang risk under headless remains: wall timeout + process-group kill (design § auth handoff / risks).

---

*End of PR0a spike notes. No runtime instrument code. Enabling deep_research non-experimental requires a signed strategy above.*
