# Scorecard — Stage 5 / S-social / try 1/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-5_S-social_ship-hop0pin_try-1` |
| **stage** | 5 |
| **scenario** | S-social |
| **try** | 1/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.6 top_p=0.95 top_k=64 budget=2048 |
| **started_at** | 2026-07-21T15:02:52.042630Z |
| **ended_at** | 2026-07-21T15:07:35.291703Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | 4596c42d-4ac2-4087-89be-132377662033 |
| **hop_count** | 2 |
| **stop_reason** | no_tools |
| **spoke** | True |
| **tools** | speak |
| **per_hop_finish_reason** | tool_calls, length |
| **reasoning_len** | 324 |
| **markers_raw (content / reasoning)** | 0 / 0 |
| **markers_after_strip (c / r)** | 0 / 0 |
| **flood Y/N** | N |
| **glass_speak Y/N** | Y |
| **free_text_only Y/N** | N |
| **latency_s** | 283.25 |
| **feel 1-5** | 4 |

## Paths

| Artifact | Path |
|----------|------|
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-hop0pin_try-1/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-hop0pin_try-1/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-hop0pin_try-1` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-hop0pin_try-1/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | PASS | no markers |
| 2 | Structured tool_calls | PASS | speak |
| 3 | Speak on glass | PASS | spoke=True glass_rows=1 |
| 4 | Latency / feel | PASS | seeded feel=4 latency=283.25s |
| 5 | Free-text-vs-tools | PASS | ok |

## Notes

—

## Snippets

### Model content (truncated)
```


```

### Reasoning (truncated)
```
The user (Operator) just sent a greeting to me (Elyra).
I should respond to the greeting.

Plan:
1. Acknowledge the greeting.
2. Since no specific task is provided, I will wait for the user to provide instructions or tasks.
The user (Operator) is greeting me. I have acknowledged the greeting. I am waiting for the next step.
```

### Glass assistant rows (truncated)
```
Hello! I'm ready to help. Let me know what we're working on.
```
