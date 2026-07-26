# Live qualitative eval harness (Stage gates)

**Fail-closed:** the local Gemma/llama-server product path is **removed**.
Operator `run_stage.py` always exits **2** with an instruction to use **xAI
dogfood** (`elyra start`) or a future OpenAI-compat eval harness. Scenario YAML
still loads for hermetic tests (`tests/test_live_eval_scenarios.py` — no GPU
in CI).

Historical protocol (3-attempt rule, A/B modes, P0 exit): **`docs/live-eval.md`**
(historical freeze — do not treat as active setup). Design stages + Adaptive
Execution Protocol: `docs/design-gemma-sampling-hygiene-staged.md` (historical).
Ship knobs inventory: `docs/inference.md` (historical freeze). Continuous design:
`docs/design-continuous-work-orient-ledger-reset.md`.

## Layout

```text
scripts/live_eval/
  scenarios.yaml      # fixed prompts + eval caps (hermetic loader still reads)
  run_stage.py        # fail-closed operator entry; Scenario/load_scenarios import-safe
  scorecard.md.j2     # human scorecard template (historical)
  README.md           # this file
  logs/               # historical scorecards + stage logs (bulky raw exports gitignored)
```

## Operator entry (always fails closed)

```bash
source .venv/bin/activate
python scripts/live_eval/run_stage.py --stage 0 --all-scenarios --tries 3
# → exit 2; stderr explains Gemma/llama path removed
```

`--help` still works. Flags beyond argparse help are not executed.

## Hermetic tests (keep green)

```bash
pytest tests/test_live_eval_scenarios.py -q
```

Loads `scenarios.yaml` only — no process spawn, no model weights, no port 8080.

## Continuous scenarios (`S-cont-*`)

Scenario IDs remain in YAML for hermetic parse coverage. Live continuous stage
runs require a future OpenAI-compat or xAI-backed harness (out of scope here).

| ID | continuous | Score focus (historical) |
|----|------------|--------------------------|
| `S-social` / `S-tools` / `S-mono` | **OFF** | Stage 5 regression cells |
| `S-cont-speak-only` | ON | Speak-only → no outer `moment_continue` |
| `S-cont-tools` | ON | Tools under continuous |
| `S-cont-task-ready-prefer` | ON | Prefer pending `task_ready` |

## Notes

- Flood scoring helpers still import `elyra.llm.reasoning_hygiene` when
  re-enabled — do not vendor a second strip regex.
- Continuous policy unit tests: `tests/test_continuous_policy.py` (hermetic).
- Scenario YAML hermetic: `tests/test_live_eval_scenarios.py` (no GPU).
