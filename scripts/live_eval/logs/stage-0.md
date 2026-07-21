# Stage 0 — Baseline measurement log

| Field | Value |
|-------|-------|
| **Stage** | 0 — Baseline (no product sampling/hygiene wire changes) |
| **Date** | 2026-07-21 |
| **Branch** | `execute-plan/cfae1e5f-pr-2-eval-stage-0-live-qualitative-harness-and-baseline` |
| **Model** | Gemma-4-12B-OBLITERATED-Q4_K_M via llama-server (`--jinja --reasoning on --reasoning-format auto`) |
| **Knobs** | temp **0.2**, top_p **omit**, top_k **omit**, budget **omit** (product defaults) |
| **Eval caps** | `max_tool_hops=12`, `moment_wall_clock_minutes=10`, poll T≈620s |
| **llama `-c`** | 16384 for this batch (`LIVE_EVAL_CTX=16384`; sliding meal still ~24k product setting, under ceiling) |
| **Path** | Product path: unique `ELYRA_HOME` per attempt → POST `/api/messages` → poll moment close |
| **Flood scorer** | `elyra.llm.reasoning_hygiene` only (`channel_marker_count` / `is_channel_flood`) |
| **Scorecards** | `scripts/live_eval/logs/scorecard-stage-0_*.md` (9 files) |
| **Raw exports** | `scripts/live_eval/logs/runs/` (gitignored bulk) |

## What we saw

### S-social — 3/3 same shape

1. Hop 1: structured `speak` tool_call; short healthy reasoning (no markers); glass gets a greeting.
2. Hop 2: **pure channel-thought flood in `content`** — interleaved `<|channel>thought` / `<channel|>` until generation budget (~57k chars, **4096 markers** by hygiene count). `reasoning` empty on flood hop. Stop `no_tools`.
3. Latency **~278–280s** almost entirely flood burn after speak already succeeded.
4. Failure mode **(A)** is real and deterministic on this prompt under temp 0.2. Failure mode **(B)** does **not** show on social: tools+speak 3/3.

Glass sample (try 1): *“Hello! I'm ready to help when you're ready…”*

### S-tools — 3/3 clean and fast

1. Hop 1: `list_dir`; hop 2: `speak`; hop 3: free-text status JSON in content (no tools) then stop `no_tools`.
2. **Zero** channel markers. Latency **8–15s**. Feel 5.
3. Explicit tool instruction pegs hard — (B) not visible when the user names tools.

### S-mono — 3/3 tools+speak, no marker flood, long private reasoning

1. Tools: `list_dir` + `speak` (try 3 also `run` ×2). Glass speak yes.
2. **No** channel markers (flood dim PASS 3/3).
3. But hop-level **reasoning is huge** (e.g. try 1 hop 2: **~31k chars** of prose RC with no tools). Latency **320–600s** — wall-clock stress without tag spam.
4. So monologue pressure currently burns **private channel tokens**, not (always) pure `<|channel>thought` loops. Still an (A)-adjacent budget problem; Stage 2 thinking budget is the natural lever.

## Baseline summary table

| Attempt | status | hops | stop | tools | flood | speak | free_text_only | markers c/r | latency_s | feel |
|---------|--------|------|------|-------|-------|-------|----------------|-------------|-----------|------|
| S-social try1 | ok | 2 | no_tools | speak | **Y** | Y | N | 4096/0 | 279 | 1 |
| S-social try2 | ok | 2 | no_tools | speak | **Y** | Y | N | 4096/0 | 280 | 1 |
| S-social try3 | ok | 2 | no_tools | speak | **Y** | Y | N | 4096/0 | 278 | 1 |
| S-tools try1 | ok | 3 | no_tools | list_dir, speak | N | Y | N | 0/0 | 15 | 5 |
| S-tools try2 | ok | 3 | no_tools | list_dir, speak | N | Y | N | 0/0 | 12 | 5 |
| S-tools try3 | ok | 3 | no_tools | list_dir, speak | N | Y | N | 0/0 | 9 | 5 |
| S-mono try1 | ok | 4 | no_tools | list_dir, speak | N | Y | N | 0/0 | 586 | 4 |
| S-mono try2 | ok | 4 | no_tools | list_dir, speak | N | Y | N | 0/0 | 320 | 4 |
| S-mono try3 | ok | 6 | no_tools | list_dir, run×2, speak | N | Y | N | 0/0 | 600 | 4 |

### Dimension pass rates (3-attempt rule)

| Scenario | (A) flood | (B) tool_calls | (B) glass speak | free_text-only fail | notes |
|----------|-----------|----------------|-----------------|---------------------|-------|
| S-social | **0/3** | 3/3 | 3/3 | 0/3 | post-speak hop floods content |
| S-tools | 3/3 | 3/3 | 3/3 | 0/3 | healthy baseline for B |
| S-mono | 3/3 markers | 3/3 | 3/3 | 0/3 | long RC latency watch |

Gate language: **(A) not healthy on S-social (0/3)**; soft/ok elsewhere for markers. **(B) healthy 3/3 on all three scenarios** under these prompts (no forced `tool_choice`).

## Intuition

1. **Flood is scenario-coupled, not universal.** Empty “hello” social turns produce a second hop that falls into pure tag loops at temp 0.2 after speak. Explicit tool-named prompts do not.
2. **(B) is not the Stage 0 headline failure** on this host/run — model emits real `tool_calls` and `speak` without pins for all nine attempts. Stage 5 should wait for evidence after sampling changes, not lead.
3. **Monologue stress shows as long reasoning, not tag floods**, this batch. Hygiene strip would not shorten those moments; **thinking_budget** (Stage 2) and sampling (Stage 1) are the generation levers. Hygiene + reinfection cut still required so S-social hop-2 floods never re-enter chain/tape.
4. **finish_reason** is still absent from model beats (pre Stage 3 optional field). Flood hops almost certainly hit `length`; record when ingress lands.

## Decision

| Track | Decision |
|-------|----------|
| **Harness** | **Ship** — product path, 3×3 protocol, scorecards, this log |
| **(A) flood** | **Advance to Stage 1 sampling** (and keep Stages 3–4 unblocked for store/reinfection). S-social is the flood canary. |
| **(B) tools/speak** | **Watch only** — 3/3 natural tool_calls; do not pull Stage 5 early from this baseline |
| **Product defaults** | **Unchanged** (temp 0.2, no top_p/k) — Stage 0 measurement only |

## Next experiment knobs (Stage 1)

Per design cost-bounded ablation:

1. Freeze card trunc `top_p=0.95`, `top_k=64`.
2. OFAT temp **0.2 / 0.4 / 0.6** on **S-mono** × 3 (and keep **S-social** as flood canary when confirming).
3. Primary success signal: S-social flood rate drops without regressing S-tools tool peg.
4. Secondary: S-mono latency / reasoning_len shrinks.

Single primary lever between re-runs: **temperature + card trunc** (plumbing PR3 then defaults PR4).

## Infra notes

- llama-server started once for the batch; reused healthy `:8080`.
- All 9 attempts `status=ok` (no infra_timeout).
- `finish_reason` scored as `not_on_tape` until Stage 3 beat field.
- Feel values are **seeded** from rubric heuristics; operator may edit scorecards.
