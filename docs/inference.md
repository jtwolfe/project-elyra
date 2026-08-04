# Inference — llama.cpp (Vulkan) + Gemma 4

> **Historical freeze — do not follow for product setup.** The shipped product path is **xAI Grok** with a week ledger + SuperGrok pool pacing. Operator notes + dogfood checklist: [state/usage-and-pacing.md](state/usage-and-pacing.md). Full design: [design-usage-tracking-supergrok-pacing.md](design/usage/design-usage-tracking-supergrok-pacing.md).

Port from **project-elyra2**. Do not invent a new stack.

## Model files (leave in elyra2)

| File | Role |
|------|------|
| `Gemma-4-12B-OBLITERATED-Q4_K_M.gguf` | Weights |
| `mmproj-BF16.gguf` | Multimodal projector |
| `llama.cpp/llama-server` | Server binary (+ `libggml-vulkan.so`) |

Setup when implementing (weights stay put):

```bash
# from project-elyra root
ln -sfn ../aurimago/project-elyra2/model model
```

`./scripts/setup_venv.sh` links this automatically when the candidate path exists.

## Ceiling vs sliding fill (Stretch 1 law)

| Knob | Default | Meaning |
|------|---------|---------|
| llama-server **`-c`** | **86000** (`CONTEXT_WINDOW_TOKENS`) | Server **KV ceiling** (allocated window) |
| Client / meal **`sliding_input_tokens`** | **250000** (`DEFAULT_SLIDING_INPUT_TOKENS`) | Settings/unit-test fallback meal size (was 50k / 24k) |
| Runtime **`meal_budget_fraction`** | **0.5** (slider max **0.75** default) | Product SSOT: fraction of model window → effective meal tokens (default **250k** of **500k**). `data/runtime/meal_budget.json` + `PATCH /api/meal-budget`. Raise slider ceiling with **`elyra start --max-meal-override PCT`** (1–100; e.g. `100` = full window). |
| Generation headroom | **~8192** (`generation_max_tokens`) | Tool-loop max_tokens; do not starve multi-hop |
| Model window (glass) | **500000** (`MODEL_CONTEXT_WINDOW_TOKENS`) | Grok-class context; product meal = fraction × this window |

**Important:** starting with `-c 86000` does **not** mean every prompt is 86k tokens.  
Crashes were mostly large prefills / VRAM, not “full context successfully used.”  
Always assemble **sliding** meals well under the model window; never pack the full KV by default.

```text
                    ┌── model window (e.g. Grok 500k) / legacy -c 86000 ──┐
                    │                                                     │
  [ sliding meal ~250k @ 50% ]  [ generation headroom ]  [ unused …  ]
```

Code constants: `elyra/llm/constants.py`. Runtime fraction: `elyra/runtime/meal_budget.py`. Settings fallbacks: `Settings.loop.sliding_input_tokens` / `in_turn_max_tokens` (see `elyra/settings.py`). Product paths apply effective tokens to **both** sliding and in-turn (policy A).

### What elyra2 used (historical)

| Setting | Value | Meaning |
|---------|-------|---------|
| `-c` | **86000** | Server KV ceiling |
| Input assembly max | **~69616** | `86000 − 16384` — hard cap, not always filled |
| Linear memory default | **~16000 SAFE** | Large linear prefills crashed 16 GiB AMD |
| Many step `max_tokens` | **4096** | Not 16k every step |
| `-np` | **1** | One parallel slot |
| `-ngl` | **99** | Offload (Vulkan backend) |
| `--jinja` / `--reasoning on` | yes | Chat template + reasoning |

## Stretch 1 defaults

```text
llama-server
  -m …/Gemma-4-12B-OBLITERATED-Q4_K_M.gguf
  --mmproj …/mmproj-BF16.gguf
  --embedding --jinja --pooling mean
  -c <ceiling>          # 86000 max; lower if unstable (e.g. 32k–48k)
  -b 2048 -ub 2048
  -ngl 99 -np 1
  --cache-ram 0
  --host 127.0.0.1 --port 8080
  --no-webui --threads 4
  --reasoning on --reasoning-format auto
  # no global --reasoning-budget unless needed
```

| Policy | Preference |
|--------|------------|
| Input | Sliding window — **~24k default**, always **well under** `-c` |
| In-turn chain | Same budget; truncate tool payloads; drop oldest pairs if needed |
| Generation | **Generous** when stable (do not starve tool loops) |
| Stability | One HTTP call at a time (chat or embed); drop `-c` before inventing new stacks |

Client: `http://127.0.0.1:8080/v1/chat/completions` (tools + reasoning). Long read timeout (~600s).

---

## Ship knobs (as-shipped, Stages 1–5)

Product path after the Gemma sampling / hygiene / tool-speak initiative.
Design plan: [design-gemma-sampling-hygiene-staged.md](design/stretch-1/design-gemma-sampling-hygiene-staged.md).
Live gates: [live-eval.md](live-eval.md).

### Failure modes (must stay separate)

| Mode | Name | Primary symptom | Primary mitigations |
|------|------|-----------------|---------------------|
| **(A)** | Channel-thought flood | Pure / trailing `` `<|channel>thought` `` loops; often `finish_reason=length`; long RC | Sampling, thinking budget, **ingress sanitize**, **flood-aware RC re-feed** |
| **(B)** | No tools / no speak | Empty `tool_calls`, free-text plans; glass silent (only `speak` writes glass) | Sampling, social hop-0 speak pin, talk skill / system bias, one `NO_SPEAK_NUDGE` |

A stage may help both, but scorecards rate **(A)** and **(B)** independently. Do not close a flood stage because speak improved, or a speak stage because floods dropped on tape.

### Product chat sampling (Stages 1–2)

Defaults live on `LlamaServerConfig` (KD13). Do-loop does **not** hardcode sampling; `HttpChatClient` falls back when kwargs are `None`.

| Knob | Ship default | Constant / source |
|------|--------------|-------------------|
| **temperature** | **1.0** | `DEFAULT_CHAT_TEMPERATURE` — dogfood thrash experiment (was 0.6 after Stage 1 OFAT; re-measure before permanent ship) |
| **top_p** | **0.95** | `GEMMA_TOP_P` (Gemma card / elyra2 freeze) |
| **top_k** | **64** | `GEMMA_TOP_K` |
| **reasoning budget** | **2048** | `DEFAULT_REASONING_BUDGET_TOKENS` → wire **`thinking_budget_tokens`** (Stage 2 OFAT; relative to do-loop `generation_max_tokens=8192`) |

Wire rules:

- When `top_p` / `top_k` are explicitly `None` on config, the client **omits** them from the HTTP body (server default).
- When `default_reasoning_budget_tokens` is `None` and `reasoning=True`, the client **omits** `thinking_budget_tokens` (unbounded private channel).
- When `reasoning=False`, the client always sends a budget (value or `0`) so the private channel can be disabled.
- Python name is `reasoning_budget_tokens`; never emit that string as a chat-body key.

Code: `elyra/llm/constants.py`, `elyra/llm/config.py`, `elyra/llm/client.py`.

### Hygiene at completion ingress (Stage 3 / PR6)

After every do-loop `chat_completion` return, the loop calls `sanitize_completion` from `elyra.llm.reasoning_hygiene` **before** model beats and chain re-feed:

- Pure channel floods → empty string (fail-closed; threshold ≥5 markers).
- Prose prefix + flood trailer → prose retained; tags stripped.
- WARNING log on any markers; optional `hygiene` dict on the model beat (`c_markers`, `r_markers`, `flood`).
- Moment tape stores **cleaned** content/RC.

**Non-claim:** strip is **boundary defense**, not a generation cure. The model can still burn the private channel on the next hop (latency residual ~280s on some S-social hop-2 floods). Hygiene keeps poison off tape and chain.

### Flood-aware RC re-feed (Stage 4 / PR7)

`assistant_message_from_result` re-attaches `reasoning_content` only when:

| Case | Behavior |
|------|----------|
| RC empty (raw or after sanitize) | **Omit** key |
| RC is a channel flood (`is_channel_flood`) | **Omit** (even if non-empty) |
| RC cleaned non-empty non-flood, in-turn tool hop | **Re-feed cleaned only** |
| After chain ends / outer meal rebuild | **No** historical RC rehydration (Stretch 1 law) |

Bare “omit empty” is **not** a flood reinfection cut — pure floods are long non-empty marker strings. Product path uses both PR6 sanitize and flood-aware omit (defense in depth).

### Social hop-0 speak pin (Stage 5 / L4)

On social wakes (`user_message` / `wait_reply` via `SOCIAL_WAKE_KINDS`) the **first** completion of the moment (`state.hop == 0` **before** `chat_completion`) pins:

```text
tool_choice = {"type": "function", "function": {"name": "speak"}}
```

Later hops and non-social wakes omit `tool_choice` (never product-default `required`). Predicate lives in `social_first_hop_tool_choice` (`elyra/loop/doloop.py`). Soft bias: `skills/bundled/talk/SKILL.md` + `prompts/system.md` speak-first language. Glass remains **speak-tool-only**.

### Known residual (generation-side)

S-social post-speak hop-2 pure channel flood can still **generate** for ~280s under ship knobs. After PR6 the **tape scores clean** (stripped / fail-closed empty); latency residual is not claimed cured. S-tools stays clean and fast under budget 2048. Stage logs: `scripts/live_eval/logs/stage-1.md` … `stage-5.md`.

---

## P0 exit criteria (summary)

P0 of the sampling/hygiene initiative is **complete** when all hold under **ship knobs** (live 3-attempt protocol; see [live-eval.md](live-eval.md)):

| # | Criterion | Ship status |
|---|-----------|-------------|
| 1 | **(A) generation flood dim** ≥2/3 pass on flood scoring for S-social, S-tools, S-mono | Met on tape after PR6 (generation residual documented) |
| 2 | **(A) chain reinfection closed** — flood-aware / cleaned-only re-feed | Shipped (PR7 + PR6) |
| 3 | **(A) tape hygiene** — ingress sanitize so tape stores cleaned RC | Shipped (PR6) |
| 4 | **(B) tools/speak** ≥2/3 on S-social + S-tools (Stage 5 re-gate after lever) | Met 3/3 with hop-0 speak pin |
| 5 | **Docs** — this file + live-eval protocol + README Testing links | This PR |
| 6 | **Hermetic** — hygiene, client payload fallback, RC re-feed (incl. non-empty flood omit), hop-0 pin predicate | CI `@pytest.mark` pack without GPU |

Incomplete if chain re-feed is bare omit-empty, if PR6 never lands while claiming P0, or if Stage 5 keeps thrashing levers without a stage-log stop/defer.

---

## Rollback (config-first where possible)

Prefer knobs over code reverts. Single source of truth: **`LlamaServerConfig`** at supervisor / client construction (no separate `[llm]` toml for P0).

| Lever | Rollback |
|-------|----------|
| Temperature | Set `LlamaServerConfig.temperature = 0.2` (pre-Stage-1 cold default) |
| Card trunc | `top_p=None`, `top_k=None` → client omits keys |
| Thinking budget | `default_reasoning_budget_tokens=None` → omit when `reasoning=True` |
| Ingress sanitize | Revert do-loop sanitize call site (PR6); tape stores raw again — avoid unless debugging |
| Flood-aware RC omit | Revert `assistant_message_from_result` policy (PR7); reinfection reopens |
| Social hop-0 speak pin | Make `social_first_hop_tool_choice` always return `None`, or remove call-site pin |

CLI / eval overrides: `scripts/live_eval/run_stage.py` `--temperature`, `--top-p`, `--top-k`, `--reasoning-budget` for ablations without changing product defaults.

---

## Real model tests

```bash
# needs model/ + GPU; skips cleanly otherwise
pytest -m llm
```

Hermetic pack (no GPU):

```bash
pytest -m 'not llm'
```

Live qualitative gates (full stack, 3 attempts): see [live-eval.md](live-eval.md) and `scripts/live_eval/`.  
Root README **Testing** links both. Markers: `@pytest.mark.llm` in `tests/test_doloop.py`, `tests/test_llm_client_tools.py`.

### Continuous work (opt-in)

Continuous work toggle is **default OFF** and does not change ship sampling knobs above. Live scenarios `S-cont-speak-only`, `S-cont-tools`, `S-cont-task-ready-prefer` exercise multi-moment policy under continuous ON (see [live-eval.md](live-eval.md)); re-run `S-social` / `S-tools` / `S-mono` with continuous OFF as the regression gate. Design: [design-continuous-work-orient-ledger-reset.md](design/stretch-1/design-continuous-work-orient-ledger-reset.md).

## Non-goals

New quants, multi-slot server, cloud default, elyra2 step-profile zoo, GBNF `channel_final` lead, free-text auto-glass, product-default `tool_choice=required`.
