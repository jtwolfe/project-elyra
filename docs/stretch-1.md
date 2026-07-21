# Stretch 1 — runtime contract

**Status:** Build freeze. Implementers start here.  
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

- **One persistent sandbox** across moments.  
- Simple storage (jsonl/sqlite): moments, beats, goals, users, wakes — **migratable to Lance later**.  
- No Stretch 2 graph schema in Stretch 1.

---

## 8. Inference

Details: [inference.md](inference.md).

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

**Tools:** `read_file`, `list_dir`, `grep`, `search_replace`, `run` (sandbox) · `update_task`, `update_goal` · `speak`, `schedule_wake`, wait/questions · `load_skill` · `verify_tool`, `promote_tool` · later `search_tools` / `use_tool`.

**Skills:** `talk`, `plan-work`, `do-work`, `review-work`, `rest`, `create-skill`, `create-tool`.

Formats: [tools-and-skills.md](tools-and-skills.md).

---

## 11. Non-goals

- Sleep / dream / hypergraph / strain product  
- Subagents / multi-worker  
- Organs / monologue ceremony stages  
- Free-text chat bypassing `speak`  
- Fused self+user  
- Draft tools callable or auto-promote without verify  
- Filling the full KV window every call  

---

## Done when (Stretch 1)

- [ ] Presence + wake queue + single worker do-loops  
- [ ] Moments/beats persist; restart-safe  
- [ ] Base tools + sandbox; speak with transport feedback  
- [ ] Wait + multi-choice + timeout path  
- [ ] Skills loadable mid-loop; base skills present  
- [ ] Goals/tasks + review-before-close bias  
- [ ] create-tool / create-skill fail-closed  
- [ ] llama.cpp Gemma path works; context policy documented  
- [ ] Interjections mid-moment  

Not required: Stretch 2 memory, Lance graph, multi-sandbox, subagents.
