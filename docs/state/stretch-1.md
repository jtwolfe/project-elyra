# Stretch 1 — runtime contract

| Field | Value |
|-------|--------|
| **Class** | STATE |
| **Status** | **Shipped** (still law for behaviour) |
| **Path** | `docs/state/stretch-1.md` (basename kept; #121 PR4) |
| **Prefer** | Code on `working` if conflict |

**Status:** **Shipped.** Build freeze still law for behaviour; implementers use this as the runtime contract.  
**Supersedes** archive notes and older wording on hop caps, pre-stage skills, or fused wake/goals.

Continuously present **single worker**: wake queue → moments (do-loops) → tools/skills → speak/wait.  
No hypergraph, sleep product, or subagents.

---

## 1. Presence and moments

```text
presence (always)
  wake queue + goals/tasks
       │
       ▼
  open MOMENT (= one do-loop)
       model ↔ tools until stop / wait
       │
       ▼
  close moment · persist beats
       │
       ▼
  next wake item
```

| Moment is | Moment is not |
|-----------|----------------|
| Full Grok-style work arc until stop | A single tool hop |
| Social bout with tools + speak/wait | An empty idle tick |

**Beats** (tool, obs, speak, ledger patch) live *inside* the moment.  
Idle presence may hold timers **without** opening a moment.

**Tags (minimal):** id, start/end UTC, why-now, user?, goal/task ids, skills used, stop reason.

---

## 2. Do-loop

```text
context = thin system + orient + sliding recent history
loop:
  model (may load_skill mid-loop)
  if tools → execute → append → continue
  if stop / wait / no tools → exit
persist moment + beats
```

**Skills:** not a pre-stage. Short catalog in orient; full body on demand (`load_skill` or equivalent). Soft why-now bias OK (user message → `talk`, ready task → `do-work`).

**Stop:** no tools; wait for user; blocked; policy; time-continue declined.  
**Goal close:** prefer **review-work** first, then close.

---

## 3. Context and reasoning

**Context meal (each model call):**

1. Thin system (laws only)  
2. Orient near the end: `NOW`, `SELF`, optional `USER`, why-now, relevant goals/tasks  
3. Sliding recent beats + short history tail (token-budgeted)  
4. Depth via tools (`read_file`, ledger) — not full life dump  

**Reasoning** (provider private stream):

| | Rule |
|--|------|
| Store | Yes — moment tape / glass |
| Resend to model | **Default no** after the multi-tool chain ends |
| In-turn tool hops | Keep if the provider requires it for that continuous sample |
| User-visible | No |

---

## 4. Time-based continue (not hop-max)

No small hop cap as the main stop law.  
Host may inject after N minutes since last **speak** or **task change**:

```text
HOST: N minutes idle on this work — continue / speak / wait / stop / schedule?
```

Ops backstop (absolute wall-clock / cancel) still required. No thrash loops.

---

## 5. Speak, wait, interjections

**Speak** = product act (user + text).  
**Transport** delivers it (glass/chat). Tool result must report failure with reason.

**Wait** (default **~2 minutes**): multi-choice and/or free text.  
On timeout: resume with `wait_elapsed`; model decides independently (do not hang forever).

**Interjections:** user message while a moment is running → inject into **same** do-loop at next safe point.  
If idle → wake queue → new moment.

---

## 6. Wake queue vs goals (separate)

| Store | Purpose |
|-------|---------|
| **Goals / tasks** | Durable *what* (can sit for days) |
| **Wake queue** | *What starts the next do-loop* |

Link with pointers (`task_ready:T3`), do not merge into one “importance” list.  
Queue order (simple bands): user/interjection > wait timeout > timer > task ready > background.

---

## 7. Sandbox and persistence

- **One persistent primary sandbox** (`sandbox0`) across moments.
- **Host tree:** `{ELYRA_HOME}/sandboxes/sandbox0/` — product FS tools path-jail here (seed `lib/` / `general/` / `fixtures/`; RW `tmp/` / `tools/`).
- **Guest (isolation on, product default):** warm microsandbox mounts that tree at `/workspace`; `run` and `sandbox_*` runners use guest exec. Fail closed (`sandbox_unavailable:*`) when the guest is unusable — **no** silent host fallback. Host stub only when `ELYRA_SANDBOX=0` (tests/CI).
- **Drafts vs staged tools:** host `tools/drafts/` is growth-tool-only (not visible via sandbox FS). Sandbox `tools/` holds staged runtime copies for promoted/bundled packages and verify (`.verify/`); not the draft tree.
- Simple storage (jsonl/sqlite): moments, beats, goals, users, wakes — **migratable to Lance later**.
- No Stretch 2 graph schema in Stretch 1.
- Isolation design + install: [design/capability/harness-sandbox-fitness.md](../design/capability/harness-sandbox-fitness.md); operator doctor: `scripts/setup-microsandbox.sh`.

---

## 8. Inference

Details: [inference.md](../inference.md).

- Gemma 4 Q4 + llama.cpp **Vulkan** from elyra2 `model/` (symlink OK).  
- Server may use large `-c` (elyra2 used **86000**); that is **KV ceiling**, not every-prompt size.  
- Prefer **sliding input** well under ceiling; generous generation headroom when stable.  
- Lower `-c` if VRAM/crashes (document chosen value when implementing).

---

## 9. create-tool (required, safe)

In Stretch 1 **done** criteria. Fail-closed:

1. Write only `tools/drafts/<name>/` (not callable)  
2. No name clash with bundled/promoted tools  
3. Valid package (`TOOL.md`, `schema.json`, `runner.json`, tests)  
4. `verify_tool` in sandbox must pass  
5. `promote_tool` only after verify (optional human ack)  
6. Never overwrite bundled or existing promoted tools  

Broken drafts are fine. **Broken promoted tools are not.**  
Skill text must match these gates; runtime enforces them anyway.

Also ship **create-skill** (same dogfood idea for playbooks).

---

## 10. Base catalog (names)

**Tools:** `read_file`, `list_dir`, `grep`, `search_replace`, `run` (sandbox) · `create_goal`, `create_task`, `list_goals`, `get_goal`, `get_task`, `update_task`, `update_goal` · `speak`, `schedule_wake`, wait/questions · `load_skill` · `verify_tool`, `promote_tool` · later `search_tools` / `use_tool`.

**Skills:** `talk`, `plan-work`, `do-work`, `review-work`, `rest`, `create-skill`, `create-tool`.

Formats: [tools-and-skills.md](tools-and-skills.md).

---

## 11. Continuous work (opt-in, post-core)

**Product framing: Continue open work** — a **progress-gated chain on open ledger work**, not “always alive” / autopilot forever. Default **OFF**.

When enabled (Glass / `PATCH /api/continuous`), presence may inject a budgeted in-moment work-continue HOST and, after finalize with non-speak progress + open work, enqueue a gated `moment_continue` wake. Prefer *pending* `task_ready` only — never re-arm ready tasks. Distinct from time-idle continue (`continue_policy.py`).

**Tracker / design:** [#130](https://github.com/jtwolfe/project-elyra/issues/130) package; refine design [design-continuous-work-refine.md](../design/stretch-1/design-continuous-work-refine.md) (**Active** — Option A + Status move + HOST/skills). Shipped baseline: [design-continuous-work-orient-ledger-reset.md](../design/stretch-1/design-continuous-work-orient-ledger-reset.md).

### Outer continue — honest exits (skip / stop paths)

Outer gate: `should_enqueue_moment_continue` in `elyra/loop/continuous_policy.py`. Canonical `last_skip_reason` values:

| Skip / stop path | `last_skip_reason` / mechanism | Notes |
|------------------|-------------------------------|-------|
| Toggle OFF | `disabled` | No enqueue; cancels pending `moment_continue` only |
| Non-allowlist stop | `stop_reason` | Includes `wait` (`wait_user` arms wait → no outer continue) |
| Pending wait | `pending_wait` | Durable wait present |
| Dedupe | `dedupe` | Already one pending `moment_continue` |
| Streak exhausted | `streak` | Default max 8 consecutive continues |
| Cooldown | `cooldown` | Default 30s since last enqueue (or flood tick) |
| No non-speak progress | `no_progress` | No successful non-speak tool **and** no ledger mutation |
| **Honest exit (Option A)** | **`honest_exit`** | See below — **refine package; not yet on all tips until PR2 lands** |
| Pure social | `pure_social` | Social wake + no tools/ledger |
| Prefer pending task_ready | `pending_task_ready` | Never synthesize task_ready (K4/K16) |
| Empty ledger | `no_open_work` | K18 |
| Flood thrash | `flood` | Majority flood formula; starts cooldown |

Stop allowlist for outer continue: `{no_tools, time_continue_declined, max_hops}` only.

### Option A — audit then idle (normative for refine package)

**Invariant:** Keep model stop as `no_tools` for honest idle. Change only the **outer continue** decision.

When continuous is ON and the moment stops with `stop_reason=no_tools` **after a successful ledger audit** this moment (`list_goals` / `get_goal` / `get_task` with `ToolResult.ok`), **do not enqueue** `moment_continue` even if open goals remain and non-speak tools ran earlier. Skip reason: **`honest_exit`**.

- Audit set (single source: `LEDGER_AUDIT_TOOLS` in continuous_policy): `{list_goals, get_goal, get_task}`.
- **Does not** count: failed inspects, thrash skip-identical, orient goals slice, host `_has_open_work` re-read, free-text “I checked,” mutating ledger tools alone.
- Gate placement: after progress gate, before pure_social. Streak/cooldown/dedupe may still mask the observable reason (product outcome: no enqueue either way).
- `time_continue_declined` / `max_hops` do **not** auto-exit via Option A in v1.

**HOST contract (one-liner, after refine PR2):** under Continue open work ON — call tools if useful; to halt honestly, inspect ledger then stop with no tools; `wait_user` pauses the chain; bare stop after tools without a ledger check may re-wake.

Decision archaeology: [design-continuous-work-refine.md](../design/stretch-1/design-continuous-work-refine.md).

### Live-eval note (`S-cont-*`)

Live-eval: `S-cont-*` in `scripts/live_eval/scenarios.yaml`; OFF baselines `S-social` / `S-tools` / `S-mono` remain the regression gate. Continuous live stage remains operator-gated.

**Expected drift after Option A:** scenarios that allow outer continue when open work remains may see **fewer continues** if the model calls `list_goals` / other audit tools before stop (`honest_exit`). No scenario rewrite required in v1 unless a harness asserts enqueue; document frequency change only.

### Not continuous

Proactive / autotelic experiment mode is a separate **Draft** catalog entry — not wired, not a continuous policy extension: [design-proactive-autotelic-experiment-mode.md](../design/stretch-1/design-proactive-autotelic-experiment-mode.md). Parallel only to Phase 3 procedural [#117](https://github.com/jtwolfe/project-elyra/issues/117).

---

## 12. Non-goals

- Sleep / dream / hypergraph / strain product  
- Subagents / multi-worker  
- Organs / monologue ceremony stages  
- Free-text chat bypassing `speak`  
- Fused self+user  
- Draft tools callable or auto-promote without verify  
- Filling the full KV window every call  

---

## Done when (Stretch 1)

All items below are **done**. Regression mapping: `tests/test_stretch1_donewhen.py` and the README testing section.

- [x] Presence + wake queue + single worker do-loops  
- [x] Moments/beats persist; restart-safe  
- [x] Base tools + sandbox; speak with transport feedback  
- [x] Wait + multi-choice + timeout path  
- [x] Skills loadable mid-loop; base skills present  
- [x] Goals/tasks + review-before-close bias  
- [x] create-tool / create-skill fail-closed (**requires PR13 gates** — `tests/test_create_tool_gates.py`; not “hardening later”)  
- [x] llama.cpp Gemma path works; context policy documented (`-c` ceiling vs sliding ~24k)  
- [x] Interjections mid-moment  

Not required: Stretch 2 memory, Lance graph, multi-sandbox, subagents.
