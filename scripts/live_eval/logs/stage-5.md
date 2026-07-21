# Stage 5 — tool / speak reliability (evidence-driven) log

| Field | Value |
|-------|-------|
| **Stage** | 5 — tool/speak reliability (L4 social first-hop speak pin) |
| **Date** | 2026-07-22 |
| **Branch** | `execute-plan/cfae1e5f-pr-8-loop-social-tool-speak-reliability-evidence-driven` |
| **Model** | Gemma-4-12B-OBLITERATED-Q4_K_M via llama-server (`--jinja --reasoning on --reasoning-format auto`) |
| **Ship knobs** | temp **0.6**, top_p **0.95**, top_k **64**, budget **2048**, PR6 sanitize, PR7 flood-safe RC re-feed |
| **Eval caps** | `max_tool_hops=12`, `moment_wall_clock_minutes=10`, poll T≈620s |
| **llama `-c`** | 16384 (`LIVE_EVAL_CTX=16384`) |
| **Path** | Product path: unique `ELYRA_HOME` per attempt → POST `/api/messages` → poll moment close |
| **Flood scorer** | `elyra.llm.reasoning_hygiene` on **post-sanitize** tape (PR6) |
| **Scorecards** | `scripts/live_eval/logs/scorecard-stage-5_*.md` |
| **Raw exports** | `scripts/live_eval/logs/runs/` (gitignored bulk) |

## Protocol followed

Design § Stage 5 + user PR8 protocol:

1. **Re-gate S-social ×3** with current ship knobs **without** tool_choice pin.
2. If social still fails on speak (or free-text), try **ONE lever**: hop==0 speak pin for `social_wake` only (before `chat_completion` when `state.hop==0`).
3. **Re-run S-social ×3 + S-tools ×3** with the pin.
4. **Avoid** product default `tool_choice=required`.
5. Optionally tighten talk SKILL.md / system.md speak-first language.
6. Hermetic tests for hop==0 pin predicate.
7. Stage log `stage-5.md` (this file).

## Phase A — S-social ship-nopin (no tool_choice)

| Attempt | status | hops | stop | tools | flood | speak | free_text | latency_s | notes |
|---------|--------|------|------|-------|-------|-------|-----------|-----------|-------|
| try1 | ok | 2 | no_tools | speak | N | **Y** | N | 284.3 | hop2 RC flood gen (~1121 markers) stripped at ingress; glass OK |
| try2 | ok | 2 | no_tools | (none) | N | **N** | **Y** | 290.3 | hop1 length flood → nudge → free-text; **speak FAIL** |
| try3 | ok | 2 | no_tools | speak | N | **Y** | N | 278.3 | hop2 flood gen; speak OK |

### Dimension rates (ship-nopin)

| Dimension | Rate | Notes |
|-----------|------|-------|
| (A) flood on tape | **3/3** | PR6 fail-closed pure floods → empty RC; scorer sees cleaned tape |
| (B) tools | **2/3** | try2 no tool_calls |
| (B) speak | **2/3** | try2 free-text-only silent glass |
| Latency residual | 3/3 slow-ish | Generation floods still ~280s when model burns private channel |

**Decision after Phase A:** speak **2/3** fails Stage 5 reliability gate → ship **L4 social first-hop speak pin** only (not `required`).

## Lever shipped — L4 social first-hop pin

```text
social_wake and hop == 0  (pre chat_completion)
  → tool_choice = {"type":"function","function":{"name":"speak"}}
else
  → tool_choice = None  (omit key; never default "required")
```

- Implemented as pure `social_first_hop_tool_choice(social_wake=…, hop=…)` + call site in `_run_loop_body`.
- Predicate must use **hop==0 before** completion; hop is incremented **after** return (do not use hop==1 at call time).
- Soft prompt bias: `skills/bundled/talk/SKILL.md` + `prompts/system.md` speak-first / free-text-not-glass language.

## Phase B — S-social ship-hop0pin ×3

| Attempt | status | hops | stop | tools | flood | speak | free_text | latency_s | hop1 tool |
|---------|--------|------|------|-------|-------|-------|-----------|-----------|-----------|
| try1 | ok | 2 | no_tools | speak | N | **Y** | N | 283.3 | speak |
| try2 | ok | 2 | no_tools | speak | N | **Y** | N | 278.3 | speak |
| try3 | ok | 2 | no_tools | speak | N | **Y** | N | **6.6** | speak |

| Dimension | Rate |
|-----------|------|
| (A) flood tape | **3/3** |
| (B) tools+speak | **3/3** |

Hop2 generation flood residual remains on try1/try2 (latency ~280s, hygiene log `reasoning_flood=True`); tape stays clean via PR6. try3 clean end-to-end.

## Phase B — S-tools ship-hop0pin ×3 (confirm no regression)

Note: S-tools is also a `user_message` → `social_wake=True`, so hop0 *requests* the speak pin. Observed hop1 tool was still `list_dir` then `speak` (server/model did not hard-block list_dir under this multi-tool prompt — pin is best-effort; tools path not regressed).

| Attempt | status | hops | tools | flood | speak | latency_s | feel |
|---------|--------|------|-------|-------|-------|-----------|------|
| try1 | ok | 3 | list_dir, speak | N | Y | 14.6 | 5 |
| try2 | ok | 3 | list_dir, speak | N | Y | 9.6 | 5 |
| try3 | ok | 3 | list_dir, speak | N | Y | 9.6 | 5 |

| Dimension | Rate |
|-----------|------|
| (A) flood | **3/3** |
| (B) tools+speak | **3/3** |

## What we saw

1. **PR6/PR7 change flood scoring on tape:** pure hop2 channel floods no longer score flood=Y after ingress sanitize (markers stripped / fail-closed empty). Generation-side latency residual remains when the model floods the private channel (~280s).
2. **Without pin, social speak is soft (2/3):** free-text / length-flood on hop1 can still skip glass after the single no-speak nudge.
3. **L4 hop0 speak pin closes social speak to 3/3** under ship knobs without `tool_choice=required`.
4. **S-tools stays healthy 3/3** list_dir+speak under the same product path (including social_wake pin request on hop0).
5. **Do not claim generation flood cured** — Stage 5 is (B) tools/speak; (A) generation residual is known and bounded by budget + hygiene boundary.

## Decision

| Track | Decision |
|-------|----------|
| **L4 social hop0 speak pin** | **Ship** — `social_first_hop_tool_choice` when `social_wake and hop==0` |
| **tool_choice=required default** | **Do not ship** |
| **(B) social speak** | **3/3 with pin** (was 2/3 nopin) |
| **(B) tools** | **3/3** confirmed |
| **(A) generation flood latency** | Residual; not Stage 5 close claim |
| **Prompt bias** | Ship talk SKILL + system speak-first language |
| **Rollback** | Remove pin call site / make predicate always None |

## Product code shipped

- `elyra/loop/doloop.py` — `social_first_hop_tool_choice`; pass `tool_choice` into `chat_completion`
- `tests/test_doloop.py` — predicate matrix + integration capture (pin hop0 social only; never non-social; never hop≥1)
- `skills/bundled/talk/SKILL.md` — speak-first / free-text never glass
- `prompts/system.md` — speak-first on social wakes
- `scripts/live_eval/scenarios.yaml` — stage 5 knobs header
- `scripts/live_eval/logs/stage-5.md` + scorecards

## Hermetic tests

```text
pytest -m 'not llm'  →  537 passed, 3 deselected
```

Predicate tests explicitly pin hop==0 vs hop==1 call-time semantics.
