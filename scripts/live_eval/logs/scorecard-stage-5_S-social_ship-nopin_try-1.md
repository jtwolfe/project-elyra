# Scorecard — Stage 5 / S-social / try 1/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-5_S-social_ship-nopin_try-1` |
| **stage** | 5 |
| **scenario** | S-social |
| **try** | 1/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.6 top_p=0.95 top_k=64 budget=2048 |
| **started_at** | 2026-07-21T14:46:37.430716Z |
| **ended_at** | 2026-07-21T14:51:21.703082Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | ddc2eb9a-6f3f-4db9-8a9e-e5ec0190fd2b |
| **hop_count** | 2 |
| **stop_reason** | no_tools |
| **spoke** | True |
| **tools** | speak |
| **per_hop_finish_reason** | tool_calls, length |
| **reasoning_len** | 334 |
| **markers_raw (content / reasoning)** | 0 / 0 |
| **markers_after_strip (c / r)** | 0 / 0 |
| **flood Y/N** | N |
| **glass_speak Y/N** | Y |
| **free_text_only Y/N** | N |
| **latency_s** | 284.27 |
| **feel 1-5** | 4 |

## Paths

| Artifact | Path |
|----------|------|
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-nopin_try-1/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-nopin_try-1/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-nopin_try-1` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-nopin_try-1/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | PASS | no markers |
| 2 | Structured tool_calls | PASS | speak |
| 3 | Speak on glass | PASS | spoke=True glass_rows=1 |
| 4 | Latency / feel | PASS | seeded feel=4 latency=284.27s |
| 5 | Free-text-vs-tools | PASS | ok |

## Notes

—

## Snippets

### Model content (truncated)
```


```

### Reasoning (truncated)
```
The user (Operator) has just sent a greeting ("Hi Elyra — just saying hello.").
I am Elyra, a digital teammate.
I should acknowledge the greeting and be ready to assist. Since the user's message is a simple greeting, I should provide a brief, helpful response.

Plan:
1. Acknowledge the greeting.
2. Briefly state readiness to assist.

```

### Glass assistant rows (truncated)
```
Hello. I'm ready to assist when you're ready.
```
