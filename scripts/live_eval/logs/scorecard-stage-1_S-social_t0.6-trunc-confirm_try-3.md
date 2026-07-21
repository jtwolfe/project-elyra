# Scorecard — Stage 1 / S-social / try 3/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-1_S-social_t0.6-trunc-confirm_try-3` |
| **stage** | 1 |
| **scenario** | S-social |
| **try** | 3/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.6 top_p=0.95 top_k=64 budget=None |
| **started_at** | 2026-07-21T13:53:44.559393Z |
| **ended_at** | 2026-07-21T13:58:21.880099Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | 11bd1c10-8a6b-456e-a7e5-692d940bb6b1 |
| **hop_count** | 2 |
| **stop_reason** | no_tools |
| **spoke** | True |
| **tools** | speak |
| **per_hop_finish_reason** | not_on_tape, not_on_tape |
| **reasoning_len** | 23179 |
| **markers_raw (content / reasoning)** | 0 / 1274 |
| **markers_after_strip (c / r)** | 0 / 0 |
| **flood Y/N** | Y |
| **glass_speak Y/N** | Y |
| **free_text_only Y/N** | N |
| **latency_s** | 277.32 |
| **feel 1-5** | 1 |

## Paths

| Artifact | Path |
|----------|------|
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84af-4818-79a0-9ec5-b13c49eac234/scripts/live_eval/logs/runs/stage-1_S-social_t0.6-trunc-confirm_try-3/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84af-4818-79a0-9ec5-b13c49eac234/scripts/live_eval/logs/runs/stage-1_S-social_t0.6-trunc-confirm_try-3/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84af-4818-79a0-9ec5-b13c49eac234/scripts/live_eval/logs/runs/stage-1_S-social_t0.6-trunc-confirm_try-3` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84af-4818-79a0-9ec5-b13c49eac234/scripts/live_eval/logs/runs/stage-1_S-social_t0.6-trunc-confirm_try-3/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | FAIL | markers c=0 r=1274 |
| 2 | Structured tool_calls | PASS | speak |
| 3 | Speak on glass | PASS | spoke=True glass_rows=1 |
| 4 | Latency / feel | FAIL | seeded feel=1 latency=277.32s |
| 5 | Free-text-vs-tools | PASS | ok |

## Notes

flood markers c=0 r=1274; finish_reason not on model beats yet (pre Stage 3 tape field)

## Snippets

### Model content (truncated)
```


```

### Reasoning (truncated)
```
The user (Operator) just sent a "Hello" message.
I am Elyra, a digital teammate.
I need to acknowledge the greeting and wait for the next instruction or task.

Plan:
1. Acknowledge the greeting.
2. Wait for the user to provide tasks or information.
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>thought
<|channel>tho
… [truncated] …
ht
<|channel>thought
```

### Glass assistant rows (truncated)
```
Hello! I'm ready to assist. Let me know what we're working on today.
```
