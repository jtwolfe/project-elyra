# Stage 2 — per-request `thinking_budget_tokens` log

| Field | Value |
|-------|-------|
| **Stage** | 2 — `reasoning_budget_tokens` → wire `thinking_budget_tokens` |
| **Date** | 2026-07-22 |
| **Branch** | `execute-plan/cfae1e5f-pr-5-llm-per-request-thinking-budget-tokens-reasoning-bud` |
| **Model** | Gemma-4-12B-OBLITERATED-Q4_K_M via llama-server (`--jinja --reasoning on --reasoning-format auto`) |
| **Ship knobs** | temp **0.6**, top_p **0.95**, top_k **64**, budget **2048** |
| **Eval caps** | `max_tool_hops=12`, `moment_wall_clock_minutes=10`, poll T≈620s |
| **llama `-c`** | 16384 (`LIVE_EVAL_CTX=16384`; sliding meal still ~24k product setting) |
| **Path** | Product path: unique `ELYRA_HOME` per attempt → POST `/api/messages` → poll moment close |
| **Flood scorer** | `elyra.llm.reasoning_hygiene` only |
| **Scorecards** | `scripts/live_eval/logs/scorecard-stage-2_*.md` |
| **Raw exports** | `scripts/live_eval/logs/runs/` (gitignored bulk) |

## Protocol followed

Cost-bounded OFAT (design § Stage 2; user prioritize S-social hop2 flood):

1. **Freeze Stage 1 sampling** `temp=0.6`, `top_p=0.95`, `top_k=64` on every cell.
2. **Phase 1 OFAT** on **S-social** budgets **None / 2048 / 4096** × **3 tries** each (9 moments) — hop2 pure flood canary.
3. **Phase 2 confirm** winner candidates on **S-tools** × 3 each for **2048** and **4096** (6 moments).
4. Ship single product `default_reasoning_budget_tokens` on `LlamaServerConfig` only (KD13). Do-loop does not hardcode budget.

Server probe: `thinking_budget_tokens` accepted (HTTP 200) on this llama.cpp build.

## Phase 1 — S-social OFAT (budget cells)

| Attempt | budget | status | hops | stop | tools | flood | speak | free_text | latency_s | reasoning_len | markers r |
|---------|--------|--------|------|------|-------|-------|-------|-----------|-----------|---------------|-----------|
| bNone try1 | None | ok | 2 | wait | speak, wait_user | **N** | Y | N | **10.6** | 204 | 0 |
| bNone try2 | None | ok | 2 | no_tools | speak | **Y** | Y | N | 278.3 | 19653 | 1078 |
| bNone try3 | None | ok | 2 | no_tools | speak | **Y** | Y | N | 279.3 | 18359 | 999 |
| b2048 try1 | 2048 | ok | 3 | no_tools | speak | **Y** | Y | N | 286.3 | 23701 | 1214 |
| b2048 try2 | 2048 | ok | 2 | no_tools | (none) | **Y** | N | **Y** | 284.3 | 24603 | 1261 |
| b2048 try3 | 2048 | ok | 2 | wait | speak, wait_user | **N** | Y | N | **6.6** | 209 | 0 |
| b4096 try1 | 4096 | ok | 2 | no_tools | speak | **Y** | Y | N | 277.3 | 12821 | 696 |
| b4096 try2 | 4096 | ok | 2 | no_tools | (none) | **N** | N | **Y** | 291.3 | 3078 | 0 |
| b4096 try3 | 4096 | ok | 2 | no_tools | speak | **Y** | Y | N | 276.3 | 20747 | 1142 |

### Dimension pass rates (S-social)

| Budget | (A) flood | (B) tools | (B) speak | notes |
|--------|-----------|-----------|-----------|-------|
| None | **1/3** | 3/3 partial | **3/3** | try1 clean wait path; 2/3 hop2 RC flood ~280s |
| **2048** | **1/3** | 2/3 | **2/3** | flood cases still ~280s / ~1k markers; one free-text fail |
| 4096 | **1/3** | 2/3 | **2/3** | same flood shape; try2 long free-text monologue without markers |

## Phase 2 — Confirm on S-tools

### budget 2048

| Attempt | status | hops | tools | flood | speak | latency_s | feel |
|---------|--------|------|-------|-------|-------|-----------|------|
| try1 | ok | 3 | list_dir, speak | N | Y | 16.6 | 5 |
| try2 | ok | 3 | list_dir, speak | N | Y | 8.6 | 5 |
| try3 | ok | 3 | list_dir, speak | N | Y | 9.6 | 5 |

| Dimension | Rate |
|-----------|------|
| (A) flood | **3/3** |
| (B) tools+speak | **3/3** |

### budget 4096

| Attempt | status | hops | tools | flood | speak | latency_s | feel |
|---------|--------|------|-------|-------|-------|-----------|------|
| try1 | ok | 3 | list_dir, speak | N | Y | 8.6 | 5 |
| try2 | ok | 3 | list_dir, speak | N | Y | 7.6 | 5 |
| try3 | ok | 3 | list_dir, speak | N | Y | 9.6 | 5 |

| Dimension | Rate |
|-----------|------|
| (A) flood | **3/3** |
| (B) tools+speak | **3/3** |

## What we saw

1. **Wire adapter works.** Server accepts `thinking_budget_tokens`; hermetic tests pin Python name never on body; product path applies budget via `LlamaServerConfig.default_reasoning_budget_tokens` (do-loop does not hardcode).
2. **S-social hop2 flood is not closed by 2048 or 4096.** When the model enters pure `<|channel>thought` loops in `reasoning_content`, flood score stays Y and wall latency stays ~275–290s — same as unbounded. A single hop can still pack hundreds–thousands of markers inside a few thousand thinking tokens (and multi-hop accumulates on tape scores).
3. **Clean social moments are already fast (~7–11s)** under Stage 1 sampling; budget does not differentiate those cells.
4. **S-tools remains the healthy peg** at both 2048 and 4096 (3/3 list_dir+speak, no flood). Neither budget kills tools.
5. **Prefer 2048** relative to `generation_max_tokens=8192`: leaves more room for tool JSON / content after private channel, matches user guidance, and does not regress S-tools vs 4096.

## Decision

| Track | Decision |
|-------|----------|
| **Wire adapter** | **Ship** Python `reasoning_budget_tokens` → HTTP `thinking_budget_tokens` only; elyra2 `reasoning=False` → budget 0 completeness |
| **Product default** | **Ship 2048** (`DEFAULT_REASONING_BUDGET_TOKENS` / `LlamaServerConfig.default_reasoning_budget_tokens`) |
| **(A) social flood** | **Not closed** — advance Stages 3–4 (ingress hygiene + flood-safe RC re-feed); do not claim budget fixed hop2 flood |
| **(B) tools/speak** | **Healthy** on S-tools 3/3; social soft (2/3 under non-None budgets) — variance / free-text, not a Stage 5 pull solely from this stage |
| **Rollback** | Prior: omit budget (`None`); keep Stage 1 temp/trunc |

## Product code shipped

- `elyra/llm/client.py` — `reasoning_budget_tokens` on Protocol / Stub / Http / Gated; wire mapping
- `elyra/llm/config.py` — `default_reasoning_budget_tokens`
- `elyra/llm/constants.py` — `DEFAULT_REASONING_BUDGET_TOKENS = 2048`
- `tests/test_llm_client_tools.py` — hermetic payload tests
- `scripts/live_eval/run_stage.py` — `--reasoning-budget` / `--omit-budget`; config apply
- `docs/inference.md` — product knobs table

## Adaptive note

Stage 2 is a **generation-side cap** and **API completeness** win, not an (A) cure. Keep Stages 3–4 unblocked. Optional later: tighter budgets (e.g. 512/1024) only if measured flood-latency benefit appears without tool collapse — not justified by this OFAT.
