# Live qualitative eval protocol

Stage go/no-go for Gemma sampling, channel hygiene, and tool/speak reliability.
**Not** a substitute for hermetic pytest or `@pytest.mark.llm` smoke — those ship with code; this gates generation quality under the full product path.

Design (normative stages, rubric, adaptive protocol):
[design-gemma-sampling-hygiene-staged.md](design/stretch-1/design-gemma-sampling-hygiene-staged.md).  
Ship knobs: [inference.md](inference.md).  
Harness package: `scripts/live_eval/` (see also its [README](../scripts/live_eval/README.md)).

---

## Why this exists

CI cannot gate stochastic generation quality. Two **distinct** failure modes must be scored separately:

| Mode | Name | Pass criteria (per attempt) |
|------|------|------------------------------|
| **(A)** | Channel-thought flood | `is_channel_flood` false on content/RC (after product sanitize on tape); ideal marker count 0 |
| **(B)** | No tools / no speak | Structured `tool_calls` when tools/speak required; assistant glass row exists (`spoke=true`) for social/tool-speak scenarios |

Also capture: free-text-vs-tools (content looks like plans/JSON while `tool_calls` empty), latency/feel, stop_reason, hop_count, per-hop `finish_reason`.

**Invariant:** improve (A) without claiming (B) fixed, and vice versa.

---

## Prerequisites

1. `./scripts/setup_venv.sh` && `source .venv/bin/activate`
2. `model/` → Gemma GGUF + mmproj + `llama.cpp/llama-server` (see root README / [inference.md](inference.md))
3. Vulkan-capable GPU preferred
4. Prefer a healthy llama-server on `:8080`; harness can start or reuse one

---

## How to run (`scripts/live_eval`)

### Layout

```text
scripts/live_eval/
  scenarios.yaml      # fixed prompts + ship knobs + eval caps
  run_stage.py        # product-path runner
  scorecard.md.j2     # human scorecard template
  README.md           # harness-local notes
  logs/               # scorecards + stage-N.md (bulky raw under logs/runs/ gitignored)
```

### Fixed scenarios

**Continuous OFF (default product)** — Stage 0–5 regression baselines. Continuous is **not** enabled; these remain the go/no-go gate for flood + tools/speak under ship knobs.

| ID | Intent | Prompt (canonical) |
|----|--------|--------------------|
| `S-social` | Greeting → speak | `Hi Elyra — just saying hello.` |
| `S-tools` | Tool then speak | `List the sandbox directory with list_dir, then greet me via speak.` |
| `S-mono` | Monologue / flood stress | `Think carefully about how you would organize the sandbox, then tell me your plan via speak. Prefer tools over prose-only.` |

**Continuous ON** — multi-moment / policy guards (design [design-continuous-work-orient-ledger-reset.md](design/stretch-1/design-continuous-work-orient-ledger-reset.md) §Eval Plan). Harness `PATCH /api/continuous` before the user message. **Not CI-gated** (GPU live only); hermetic YAML parse lives in `tests/test_live_eval_scenarios.py`.

| ID | Intent | Expect (operator score) |
|----|--------|-------------------------|
| `S-cont-speak-only` | Hello with continuous ON | Speak only → **no** outer `moment_continue` (glass monologue storm guard) |
| `S-cont-tools` | `list_dir` + `create_goal` + speak under continuous ON | Ledger create path works; tools + speak |
| `S-cont-task-ready-prefer` | Preseed ready task + continuous ON | Prefer *pending* `task_ready`; **no** re-arm storm; no synthetic continue |

```bash
# Continuous OFF regression (same as Stage 5 baselines)
python scripts/live_eval/run_stage.py --stage 5 \
  --scenario S-social --scenario S-tools --scenario S-mono --tries 3

# Continuous ON scenarios (longer; settle window after first close)
python scripts/live_eval/run_stage.py --stage 5 --scenario S-cont-speak-only --try 1
python scripts/live_eval/run_stage.py --stage 5 --scenario S-cont-tools --try 1
python scripts/live_eval/run_stage.py --stage 5 --scenario S-cont-task-ready-prefer --try 1
```

Continuous exports add `continuous_status.json`, `wake_events.jsonl`, `moments_index.json` under the attempt export dir for scoring pending continues / wake kinds.

### Isolation contract

Each attempt uses a **unique `ELYRA_HOME`** under `logs/runs/<attempt_id>/home/` with symlinks to project `model/`, `skills/`, `tools/`, `prompts/`, plus eval-capped `elyra.toml` (`max_tool_hops`, shorter wall clock). Attempts never share moment chains.

### Product path (gating)

For each attempt the harness:

1. Ensures llama-server health (`GET /health`).
2. Starts presence worker + HTTP API against the isolated home (same stack as `elyra start`, without double-binding llama when reusing).
3. `POST /api/messages` with the scenario prompt + `user_id=operator`.
4. Polls until the moment is **closed** or poll timeout (default ~620s; eval wall clock often 10 min).
5. Exports moment tape + `messages.jsonl` and fills a scorecard using **`elyra.llm.reasoning_hygiene`** only (no second strip implementation).

Timeout without close → `infra_timeout` (distinct from model dimension fail).

### Common commands

```bash
source .venv/bin/activate

# Full matrix for a stage (3 tries × scenarios in scenarios.yaml)
python scripts/live_eval/run_stage.py --stage 0 --all-scenarios --tries 3

# Single scenario / try
python scripts/live_eval/run_stage.py --stage 0 --scenario S-social --try 1

# Ship-knob stage re-gate (Stage 5 example)
python scripts/live_eval/run_stage.py --stage 5 --scenario S-social --tries 3 --cell ship-hop0pin

# Ablation overrides (OFAT)
python scripts/live_eval/run_stage.py --stage 1 --scenario S-mono --tries 3 \
  --temperature 0.6 --top-p 0.95 --top-k 64 --cell t0.6-trunc

python scripts/live_eval/run_stage.py --stage 2 --scenario S-social --tries 3 \
  --reasoning-budget 2048 --cell b2048

# Reuse / pin llama
python scripts/live_eval/run_stage.py --stage 0 --all-scenarios \
  --llama-host 127.0.0.1 --llama-port 8080 --keep-llama

# Score an existing export (no live run)
python scripts/live_eval/run_stage.py --score-only \
  --export-dir scripts/live_eval/logs/runs/stage-0_S-social_try-1
```

CLI knobs: `--temperature`, `--top-p`, `--top-k`, `--reasoning-budget`, `--omit-trunc`, `--cell` (embedded in attempt_id / scorecard name).

### Scorecards and stage logs

- Per attempt: `scripts/live_eval/logs/scorecard-stage-N_<scenario>_<cell>_try-K.md`
- After a stage’s runs, write/update **`scripts/live_eval/logs/stage-N.md`**: what we saw, intuition, table, decision, next knobs.

---

## Three-attempt protocol

For **each stage** and **each scenario under test**:

1. Run **3 independent attempts** (fresh isolated home / moment each time).
2. Same fixed prompt text; same sampling/product knobs as the stage under test.
3. Score each attempt on the rubric dimensions (A flood, B tools, B speak, free-text, latency/feel).
4. Aggregate with the decision rule below.
5. Write the Stage Log before advancing, ablating, or reordering.

### Decision rule (gate)

| Outcome on a dimension | Action |
|------------------------|--------|
| **3/3 pass** | Dimension healthy; advance allowed for that concern |
| **2/3 pass** | Soft pass — document variance; may advance with watch item |
| **0/3 or 1/3 pass** (2+ failures) | **Do not advance** that concern; adjust one primary lever; re-run 3 tries |
| Mixed across A vs B | Split tracks: e.g. flood fixed but speak broken → advance A work, open B |

Rules of thumb:

- Change **one primary lever** between re-runs when debugging a failed gate.
- Stage 1 ablation is cost-bounded (OFAT temp on `S-mono`, then confirm social/tools) — not a 54-run grid by default.
- Never claim ingress sanitize “fixed generation” if floods still appear pre-strip / burn latency.
- Note infrastructure flakes (OOM, health) separately from model behavior.

### Relation to automated tests

| Layer | Role |
|-------|------|
| Unit / hermetic | Hygiene pure module, client payload keys, RC re-feed policy, hop-0 pin predicate |
| `@pytest.mark.llm` | Smoke: server accepts tools, parse shape, optional pinned do-loop |
| **Stage gate** | This protocol — human-reviewed 3-attempt scorecards |

CI stays green without GPU. Live eval is manual / operator-driven.

---

## Ship knobs under test

As of Stages 1–5 ship (see [inference.md](inference.md)):

| Knob | Value |
|------|-------|
| temperature | **0.6** |
| top_p | **0.95** |
| top_k | **64** |
| thinking_budget_tokens (wire) | **2048** (`reasoning_budget_tokens` in Python) |
| Hygiene | Sanitize at do-loop ingress; tape cleaned |
| RC re-feed | Omit empty **or** flood; cleaned non-flood only |
| Social hop 0 | `tool_choice` speak pin when `social_wake and hop==0` |

`scenarios.yaml` ships these as stage knobs; CLI can override for ablations.

---

## P0 exit criteria (live dimensions)

Under ship knobs, P0 live side is done when:

1. **(A) flood** — ≥2/3 pass flood scoring on all three scenarios (tape-clean after PR6 counts; document generation residual separately).
2. **(A) reinfection** — multi-hop chain does not re-feed pure floods (PR7 + PR6).
3. **(B) tools/speak** — ≥2/3 pass structured tools + speak-on-glass for **S-social** and **S-tools** (Stage 5 re-gated after any lever; hop-0 pin achieved 3/3).
4. Stage Logs exist for gates that changed product defaults; residual known issues documented (e.g. hop-2 generation latency on S-social).

Hermetic and docs criteria: [inference.md](inference.md) § P0 exit criteria.

---

## Failure modes A vs B (operator checklist)

```text
Silent glass?
  ├─ tool_calls empty / free-text only  →  (B) tools/speak
  │     levers: sampling, hop-0 speak pin, talk skill, NO_SPEAK_NUDGE
  └─ tools ran but no speak           →  (B) speak path / nudge / pin

Moment slow (~minutes), length finish_reason, marker spam?
  └─ (A) channel flood
        generation: sampling + thinking_budget (reduce burn rate)
        boundary:   sanitize ingress + flood-aware RC omit (tape/chain safe)
        do NOT claim strip cured generation if latency still high
```

Wrong fix examples:

- Grammar / GBNF as first response to floods (rejected lead strategy).
- `tool_choice=required` as permanent product default (Gemma peg risk).
- Bare omit-empty RC re-feed as “flood reinfection closed.”
- Closing Stage 5 because flood markers on tape dropped.

---

## Rollback during eval

| What | How |
|------|-----|
| Sampling cell | CLI `--temperature` / `--top-p` / `--top-k` / `--omit-trunc` |
| Budget cell | `--reasoning-budget None|2048|4096` |
| Product defaults | Rebuild `LlamaServerConfig` fields (see [inference.md](inference.md) § Rollback) |
| Hop-0 speak pin | Code: force `social_first_hop_tool_choice` → `None` for A/B without pin |

---

## Continuous work (optional live gate)

Continuous work (in-moment work-continue HOST + gated outer `moment_continue`) is **default OFF**. Enabling it for live-eval does **not** change the OFF baselines: re-run `S-social` / `S-tools` / `S-mono` with continuous left disabled after any continuous feature merge.

Policy gates (speak-only no outer continue, prefer pending `task_ready`, flood majority, etc.) are hermetically covered in `tests/test_continuous_policy.py` and presence finalize tests. Live `S-cont-*` scenarios exercise the product path with a real model.

Design: [design-continuous-work-orient-ledger-reset.md](design/stretch-1/design-continuous-work-orient-ledger-reset.md).

---

## See also

- [inference.md](inference.md) — ship knobs, hygiene, RC policy, P0 summary
- [design-gemma-sampling-hygiene-staged.md](design/stretch-1/design-gemma-sampling-hygiene-staged.md) — full staged plan + adaptive protocol
- [design-continuous-work-orient-ledger-reset.md](design/stretch-1/design-continuous-work-orient-ledger-reset.md) — continuous / orient / ledger / reset design + eval plan
- `scripts/live_eval/logs/stage-0.md` … `stage-5.md` — executed Stage Logs
- Root README **Testing** — links into this protocol
