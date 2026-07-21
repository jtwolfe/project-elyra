# Inference — llama.cpp (Vulkan) + Gemma 4

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
| Client / meal **`sliding_input_tokens`** | **24000** (`DEFAULT_SLIDING_INPUT_TOKENS`) | Product **input budget** per outer meal + in-turn chain |
| Generation headroom | **~8192** (`generation_max_tokens`) | Tool-loop max_tokens; do not starve multi-hop |

**Important:** starting with `-c 86000` does **not** mean every prompt is 86k tokens.  
Crashes were mostly large prefills / VRAM, not “full context successfully used.”  
Always assemble **sliding** meals well under the ceiling; never pack the full KV by default.

```text
                    ┌── KV ceiling (-c, e.g. 86000) ──────────────┐
                    │                                             │
  [ sliding meal ~24k ]  [ generation headroom ]  [ unused KV  …  ]
```

Code constants: `elyra/llm/constants.py`. Runtime settings: `Settings.loop.sliding_input_tokens` / `in_turn_max_tokens` (see `elyra/settings.py`). CLI `--context-tokens N` only changes **`-c`**, not the 24k meal budget (lower `-c` if VRAM crashes; keep meals smaller than the new ceiling).

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

### Product chat sampling (Stages 1–2)

Defaults live on `LlamaServerConfig` (KD13). Do-loop does not hardcode sampling; `HttpChatClient` falls back when kwargs are `None`.

| Knob | Default | Constant / source |
|------|---------|-------------------|
| **temperature** | **0.6** | `DEFAULT_CHAT_TEMPERATURE` — Stage 1 live OFAT (S-mono 0.2/0.4/0.6 × 3; 0.6 cleanest flood + tools) |
| **top_p** | **0.95** | `GEMMA_TOP_P` (Gemma card / elyra2 freeze) |
| **top_k** | **64** | `GEMMA_TOP_K` |
| **reasoning budget** | **2048** | `DEFAULT_REASONING_BUDGET_TOKENS` → wire `thinking_budget_tokens` (Stage 2 OFAT; relative to `generation_max_tokens=8192`) |

When `top_p` / `top_k` are explicitly `None` on config, the client **omits** them from the HTTP body (server default). When `default_reasoning_budget_tokens` is `None` and `reasoning=True`, the client **omits** `thinking_budget_tokens`. When `reasoning=False`, the client always sends a budget (value or `0`) so the private channel can be disabled.

**Known residual:** S-social post-speak hop-2 pure channel flood (~280s) is **not cured** by sampling or by 2048/4096 thinking budgets (flood still scores when the model burns the private channel on `<|channel>thought` loops). Stages 3–4 (hygiene ingress, flood-safe RC re-feed) remain the (A) boundary fix. S-tools stays 3/3 clean under budget 2048.

See `scripts/live_eval/logs/stage-1.md`, `scripts/live_eval/logs/stage-2.md`, and `docs/design-gemma-sampling-hygiene-staged.md`.

## Real model tests

```bash
# needs model/ + GPU; skips cleanly otherwise
pytest -m llm
```

See root README **Testing** for details. Markers: `@pytest.mark.llm` in `tests/test_doloop.py`, `tests/test_llm_client_tools.py`.

## Non-goals

New quants, multi-slot server, cloud default, elyra2 step-profile zoo.
