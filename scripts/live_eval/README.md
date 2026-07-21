# Live qualitative eval harness (Stage gates)

Fixed scenarios + product-path orchestration for **full-stack real-LLM**
gates (Gemma via llama-server). Stage go/no-go is human-reviewed 3-attempt
scorecards — not green pytest alone.

Operator protocol (3-attempt rule, A/B modes, P0 exit): **`docs/live-eval.md`**.  
Design stages + Adaptive Execution Protocol:
`docs/design-gemma-sampling-hygiene-staged.md`. Ship knobs: `docs/inference.md`.

## Layout

```text
scripts/live_eval/
  scenarios.yaml      # fixed prompts + eval caps
  run_stage.py        # product-path runner
  scorecard.md.j2     # human scorecard template
  README.md           # this file
  logs/               # scorecards + stage logs (bulky raw exports gitignored)
```

## Prerequisites

1. `./scripts/setup_venv.sh` and `source .venv/bin/activate`
2. `model/` → Gemma GGUF + `llama.cpp/llama-server` (see root README)
3. Vulkan-capable GPU (or enough RAM for CPU — not recommended)
4. Prefer a healthy llama-server already on `:8080`; otherwise the harness
   starts one (or reuses product paths)

## Isolation contract

Each attempt uses a **unique `ELYRA_HOME`** under `logs/runs/<attempt_id>/home/`
with:

- `model` → symlink to project `model/`
- `skills` / `tools` / `prompts` → symlinks to project roots (bundled packages)
- `elyra.toml` with **eval caps** (`max_tool_hops`, shorter wall clock)

Attempts never share moment chains. Raw exports stay under
`logs/runs/` (gitignored).

## Product path

For each attempt the harness:

1. Ensures llama-server health (`GET /health` on configured host:port).
2. Starts presence worker + HTTP API against the isolated home
   (same stack as `elyra start`, without double-binding llama when reusing).
3. `POST /api/messages` with the scenario prompt + `user_id=operator`.
4. Polls `/api/status` and `/api/moments` until the moment is **closed**
   or poll timeout fires (default ~620s; product wall clock default 10 min
   for eval homes).
5. Exports moment tape + `messages.jsonl` and fills a scorecard via
   `elyra.llm.reasoning_hygiene` (flood dim only — same module as product).

**Stage 1 knobs** (scenarios.yaml): card trunc `top_p=0.95`, `top_k=64`;
temperature ablated via CLI. Product `LlamaServerConfig` ships the same trunc.

## Run Stage 0 baseline (3 tries × 3 scenarios)

```bash
source .venv/bin/activate
# optional: start llama once and leave it up
#   elyra start   # or start llama-server alone on :8080

python scripts/live_eval/run_stage.py \
  --stage 0 \
  --tries 3 \
  --all-scenarios

# single scenario / try
python scripts/live_eval/run_stage.py --stage 0 --scenario S-social --try 1

# reuse / force llama
python scripts/live_eval/run_stage.py --stage 0 --all-scenarios \
  --llama-host 127.0.0.1 --llama-port 8080

# score from an existing export dir (no live run)
python scripts/live_eval/run_stage.py --score-only \
  --export-dir scripts/live_eval/logs/runs/stage-0_S-social_try-1
```

## Stage 1 sampling ablation (cost-bounded OFAT)

```bash
# Phase 1 — freeze card trunc; OFAT temp on S-mono
for t in 0.2 0.4 0.6; do
  python scripts/live_eval/run_stage.py --stage 1 --scenario S-mono --tries 3 \
    --temperature "$t" --cell "t${t}-trunc" --keep-llama
done

# Phase 2 — confirm winner on S-social + S-tools
WIN=0.4   # replace after Phase 1
python scripts/live_eval/run_stage.py --stage 1 \
  --scenario S-social --scenario S-tools --tries 3 \
  --temperature "$WIN" --cell "t${WIN}-trunc-confirm" --keep-llama
```

CLI knob overrides: `--temperature`, `--top-p`, `--top-k`, `--omit-trunc`,
`--cell` (embedded in attempt_id / scorecard name).

## Scorecard fields

| Field | Source |
|-------|--------|
| hop_count / stop_reason / spoke | stop beat + moment meta |
| tools | tool beat names (order) |
| reasoning_len | sum of model-beat `reasoning` chars |
| finish_reason | model beat if present; else `not_on_tape` (Stage 3+) |
| markers / flood | `channel_marker_count` / `is_channel_flood` |
| glass_speak | assistant rows in messages.jsonl |
| free_text_only | model content non-empty while tool_calls empty on all hops |
| latency_s | wall time open → close (or timeout) |
| feel 1–5 | operator fill-in (auto-estimate seeded from latency/outcome) |

## Stage Log

After a stage’s 3×N runs, write/update:

`scripts/live_eval/logs/stage-N.md`

with: what we saw, intuition, baseline summary table, decision, next knobs.

## Decision rule (gate)

| Outcome on a dimension | Action |
|------------------------|--------|
| 3/3 pass | Healthy |
| 2/3 pass | Soft pass — document variance |
| 0–1/3 pass | Do not advance that concern |

Score **(A) flood** and **(B) tools/speak** independently.

## Notes

- Flood scoring uses **only** `elyra.llm.reasoning_hygiene` — do not vendor a
  second strip regex.
- Hygiene is **boundary defense scoring** here; Stage 0 does not change product
  sampling or wire sanitize at ingress.
- Timeout without close → status `infra_timeout` (distinct from model fail).
