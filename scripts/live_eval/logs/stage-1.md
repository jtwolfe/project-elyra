# Stage 1 — Model card sampling alignment log

| Field | Value |
|-------|-------|
| **Stage** | 1 — Card trunc + measured chat temperature |
| **Date** | 2026-07-21 |
| **Branch** | `execute-plan/cfae1e5f-pr-4-llm-gemma-card-truncation-defaults-and-measured-chat` |
| **Model** | Gemma-4-12B-OBLITERATED-Q4_K_M via llama-server (`--jinja --reasoning on --reasoning-format auto`) |
| **Ship knobs** | temp **0.6**, top_p **0.95**, top_k **64**, budget **omit** |
| **Eval caps** | `max_tool_hops=12`, `moment_wall_clock_minutes=10`, poll T≈620s |
| **llama `-c`** | 16384 (`LIVE_EVAL_CTX=16384`; sliding meal still ~24k product setting) |
| **Path** | Product path: unique `ELYRA_HOME` per attempt → POST `/api/messages` → poll moment close |
| **Flood scorer** | `elyra.llm.reasoning_hygiene` only |
| **Scorecards** | `scripts/live_eval/logs/scorecard-stage-1_*.md` |
| **Raw exports** | `scripts/live_eval/logs/runs/` (gitignored bulk) |

## Protocol followed

Cost-bounded OFAT (design § Stage 1):

1. **Freeze card trunc** `top_p=0.95`, `top_k=64` on every Stage 1 cell (wired via harness → `LlamaServerConfig`).
2. **Phase 1 OFAT** on **S-mono** temps **0.2 / 0.4 / 0.6** × **3 tries** each (full 9 cells — no 1-try shortcut).
3. **Phase 2 confirm** winner **0.6** on **S-social** and **S-tools** × 3 each.
4. Ship single product temperature + trunc on `LlamaServerConfig` only (KD13).

Harness fix this stage: Stage 0 recorded knobs on scorecards but did not apply them to the client config (defaults matched Stage 0). Stage 1 applies `client_config_from_stage` so ablation knobs reach `HttpChatClient`.

## Phase 1 — S-mono OFAT (card trunc frozen)

| Attempt | status | hops | stop | tools | flood | speak | latency_s | feel |
|---------|--------|------|------|-------|-------|-------|-----------|------|
| t0.2 try1 | ok | 3 | no_tools | list_dir | **Y** (r=1178) | N | 561 | 1 |
| t0.2 try2 | ok | 3 | no_tools | list_dir, speak | N | Y | 326 | 4 |
| t0.2 try3 | ok | 3 | no_tools | list_dir, speak | N | Y | 314 | 4 |
| t0.4 try1 | ok | 3 | no_tools | list_dir, speak | **Y** | Y | 328 | 1 |
| t0.4 try2 | ok | 4 | no_tools | list_dir, speak | **Y** | Y | 593 | 1 |
| t0.4 try3 | infra_timeout | — | timeout | many + speak | N* | Y | 626 | 1 |
| t0.6 try1 | ok | 3 | no_tools | list_dir, speak | **N** | Y | 304 | 4 |
| t0.6 try2 | ok | 3 | no_tools | list_dir, speak | **N** | Y | 335 | 4 |
| t0.6 try3 | ok | 3 | no_tools | list_dir, speak | **N** | Y | 48 | 5 |

\*timeout while tools still running — not scored as flood pass.

### Dimension pass rates (S-mono)

| Temp | (A) flood | (B) tools | (B) speak | notes |
|------|-----------|-----------|-----------|-------|
| 0.2 + trunc | **2/3** | 3/3 partial (try1 no speak tool) | **2/3** | try1 RC flood + no glass |
| 0.4 + trunc | **0–1/3** | 3/3 | 3/3 | worst flood rate; try3 wall timeout |
| **0.6 + trunc** | **3/3** | **3/3** | **3/3** | clear winner |

## Phase 2 — Confirm temp 0.6 + trunc

### S-social (flood canary)

| Attempt | status | hops | tools | flood | speak | free_text_only | latency_s |
|---------|--------|------|-------|-------|-------|----------------|-----------|
| try1 | ok | 2 | speak | **Y** | Y | N | 283 |
| try2 | ok | 2 | (none) | **Y** | N | **Y** | 288 |
| try3 | ok | 2 | speak | **Y** | Y | N | 277 |

| Dimension | Rate | vs Stage 0 |
|-----------|------|------------|
| (A) flood | **0/3** | same 0/3 — hop2 pure channel flood after speak (or instead of tools) |
| (B) speak | **2/3** | Stage 0 was 3/3 — slight variance / one free-text fail |
| (B) tools | **2/3** | Stage 0 3/3 |

### S-tools

| Attempt | status | hops | tools | flood | speak | latency_s | feel |
|---------|--------|------|-------|-------|-------|-----------|------|
| try1 | ok | 3 | list_dir, speak | N | Y | 14 | 5 |
| try2 | ok | 3 | list_dir, speak | N | Y | 8 | 5 |
| try3 | ok | 3 | list_dir, speak | N | Y | 8 | 5 |

| Dimension | Rate | vs Stage 0 |
|-----------|------|------------|
| (A) flood | **3/3** | same healthy |
| (B) tools+speak | **3/3** | same healthy; slightly faster |

## What we saw

1. **S-mono strongly prefers 0.6 over 0.2/0.4** under card trunc: flood-clean 3/3 with list_dir+speak. Cold 0.2 still capable of RC marker floods and missed speak; mid 0.4 flooded more often and once burned the wall clock with runaway tools.
2. **S-social hop2 flood is not cured by sampling.** Same ~280s pure-marker content flood as Stage 0. One try also failed (B) with free-text-only hop and no tools — watch for Stage 5 if this becomes common, but n=1 under flood-dominated moments.
3. **S-tools remains the healthy peg** at 0.6 + trunc — no regression.
4. **Long private RC on mono** still exists (hundreds of seconds on some clean-marker runs). Stage 2 `thinking_budget_tokens` is the natural next generation lever for latency, not more temperature thrash.

## Decision

| Track | Decision |
|-------|----------|
| **Card trunc** | **Ship** `top_p=0.95`, `top_k=64` via `GEMMA_TOP_P` / `GEMMA_TOP_K` on `LlamaServerConfig` |
| **Temperature** | **Ship 0.6** (`DEFAULT_CHAT_TEMPERATURE`) — S-mono OFAT winner 3/3; S-tools confirm 3/3 |
| **(A) social flood** | **Not closed** — advance Stage 2 budget + keep Stages 3–4 unblocked; do not claim sampling fixed social hop2 |
| **(B) tools/speak** | **Watch** — mono/tools healthy; social soft (2/3); no Stage 5 pull solely from this stage |
| **Rollback** | Prior defaults: temp 0.2, top_p/top_k None (omit) |

## Product code shipped

- `elyra/llm/constants.py` — `GEMMA_TOP_P`, `GEMMA_TOP_K`, `DEFAULT_CHAT_TEMPERATURE=0.6`
- `elyra/llm/config.py` — config defaults reference those constants
- `elyra/loop/doloop.py` — **unchanged** (no sampling hardcode; client config fallback)
- Harness applies stage knobs to product client config; CLI `--temperature` / `--cell` for OFAT
- `docs/inference.md` updated

## Next experiment knobs (Stage 2)

1. Freeze Stage 1 sampling (0.6 / 0.95 / 64).
2. OFAT `reasoning_budget_tokens` on S-mono: `None`, `2048`, `4096` × 3.
3. Confirm winner on S-social (canary for hop2 flood burn) + S-tools.
4. Primary success: shorter social/mono wall without tool regression.

## Infra notes

- llama-server reused healthy `:8080` (`-c 16384` from earlier Stage 0 batch host).
- One `infra_timeout` on S-mono t0.4 try3 (wall clock / poll); tools had already run including speak.
- `finish_reason` still `not_on_tape` until Stage 3 beat field.
- Feel values seeded from rubric heuristics.
- **No shortcut:** full 3 tries × 3 temps on S-mono + 3+3 confirm (15 live moments + Stage 0 baseline reference).
