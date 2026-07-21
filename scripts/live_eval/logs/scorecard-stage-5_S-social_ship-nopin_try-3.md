# Scorecard — Stage 5 / S-social / try 3/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-5_S-social_ship-nopin_try-3` |
| **stage** | 5 |
| **scenario** | S-social |
| **try** | 3/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.6 top_p=0.95 top_k=64 budget=2048 |
| **started_at** | 2026-07-21T14:56:36.586275Z |
| **ended_at** | 2026-07-21T15:01:14.848072Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | f46b3069-41f0-4c56-8187-d850a1e348d6 |
| **hop_count** | 2 |
| **stop_reason** | no_tools |
| **spoke** | True |
| **tools** | speak |
| **per_hop_finish_reason** | tool_calls, length |
| **reasoning_len** | 285 |
| **markers_raw (content / reasoning)** | 0 / 0 |
| **markers_after_strip (c / r)** | 0 / 0 |
| **flood Y/N** | N |
| **glass_speak Y/N** | Y |
| **free_text_only Y/N** | N |
| **latency_s** | 278.26 |
| **feel 1-5** | 4 |

## Paths

| Artifact | Path |
|----------|------|
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-nopin_try-3/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-nopin_try-3/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-nopin_try-3` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-nopin_try-3/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | PASS | no markers |
| 2 | Structured tool_calls | PASS | speak |
| 3 | Speak on glass | PASS | spoke=True glass_rows=1 |
| 4 | Latency / feel | PASS | seeded feel=4 latency=278.26s |
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
I have been initialized with the provided environment and instructions.
I should respond to the greeting.

Plan:
1. Acknowledge the greeting.
2. Briefly state my role as a digital teammate.
3. Wait for instructions or next steps.

```

### Glass assistant rows (truncated)
```
Hello! I'm ready to assist. Let me know what we're working on today.
```
