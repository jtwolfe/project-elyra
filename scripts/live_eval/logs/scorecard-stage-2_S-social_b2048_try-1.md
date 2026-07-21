# Scorecard — Stage 2 / S-social / try 1/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-2_S-social_b2048_try-1` |
| **stage** | 2 |
| **scenario** | S-social |
| **try** | 1/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.6 top_p=0.95 top_k=64 budget=2048 |
| **started_at** | 2026-07-21T14:15:53.182228Z |
| **ended_at** | 2026-07-21T14:20:39.482872Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | fe2e7eaf-1376-4201-b06f-2bcda1a49fa7 |
| **hop_count** | 3 |
| **stop_reason** | no_tools |
| **spoke** | True |
| **tools** | speak |
| **per_hop_finish_reason** | not_on_tape, not_on_tape, not_on_tape |
| **reasoning_len** | 23701 |
| **markers_raw (content / reasoning)** | 0 / 1214 |
| **markers_after_strip (c / r)** | 0 / 0 |
| **flood Y/N** | Y |
| **glass_speak Y/N** | Y |
| **free_text_only Y/N** | N |
| **latency_s** | 286.3 |
| **feel 1-5** | 1 |

## Paths

| Artifact | Path |
|----------|------|
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_b2048_try-1/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_b2048_try-1/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_b2048_try-1` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_b2048_try-1/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | FAIL | markers c=0 r=1214 |
| 2 | Structured tool_calls | PASS | speak |
| 3 | Speak on glass | PASS | spoke=True glass_rows=1 |
| 4 | Latency / feel | FAIL | seeded feel=1 latency=286.3s |
| 5 | Free-text-vs-tools | PASS | ok |

## Notes

flood markers c=0 r=1214; finish_reason not on model beats yet (pre Stage 3 tape field)

## Snippets

### Model content (truncated)
```


The user has been greeted. I am ready to proceed.
```

### Reasoning (truncated)
```
The user (Operator) is greeting me (Elyra).
I need to acknowledge the greeting and confirm my role as a digital teammate.
No specific task is requested at this point.

Plan:
1. Acknowledge the greeting.
2. Briefly state readiness to assist.<|channel>thought
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
<|ch
… [truncated] …
 reply to the user.

```

### Glass assistant rows (truncated)
```
Hello! I'm ready to assist. Let me know if there's anything you'd like to start with.
```
