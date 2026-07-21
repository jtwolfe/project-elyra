# Scorecard — Stage 5 / S-social / try 2/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-5_S-social_ship-hop0pin_try-2` |
| **stage** | 5 |
| **scenario** | S-social |
| **try** | 2/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.6 top_p=0.95 top_k=64 budget=2048 |
| **started_at** | 2026-07-21T15:07:35.293332Z |
| **ended_at** | 2026-07-21T15:12:13.555695Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | 07f609d5-f3ae-4598-b8f3-7a42486f9386 |
| **hop_count** | 2 |
| **stop_reason** | no_tools |
| **spoke** | True |
| **tools** | speak |
| **per_hop_finish_reason** | tool_calls, length |
| **reasoning_len** | 330 |
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
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-hop0pin_try-2/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-hop0pin_try-2/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-hop0pin_try-2` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-hop0pin_try-2/home` |

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
The user (Operator) has sent a "Hello" message to Elyra.
I am a digital teammate.
I should acknowledge the greeting and wait for further instructions or tasks.

Plan:
1. Acknowledge the greeting.
2. State availability.
The user said hello and I have responded. I should now wait for the user to provide more information or a task.

```

### Glass assistant rows (truncated)
```
Hello! I'm ready to assist. Let me know what we're working on today.
```
