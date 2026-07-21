# Scorecard — Stage 2 / S-social / try 3/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-2_S-social_b2048_try-3` |
| **stage** | 2 |
| **scenario** | S-social |
| **try** | 3/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.6 top_p=0.95 top_k=64 budget=2048 |
| **started_at** | 2026-07-21T14:25:23.811757Z |
| **ended_at** | 2026-07-21T14:25:30.363664Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | 4726c244-de50-4933-837c-84df1de163dd |
| **hop_count** | 2 |
| **stop_reason** | wait |
| **spoke** | True |
| **tools** | speak, wait_user |
| **per_hop_finish_reason** | not_on_tape, not_on_tape |
| **reasoning_len** | 209 |
| **markers_raw (content / reasoning)** | 0 / 0 |
| **markers_after_strip (c / r)** | 0 / 0 |
| **flood Y/N** | N |
| **glass_speak Y/N** | Y |
| **free_text_only Y/N** | N |
| **latency_s** | 6.55 |
| **feel 1-5** | 5 |

## Paths

| Artifact | Path |
|----------|------|
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_b2048_try-3/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_b2048_try-3/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_b2048_try-3` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_b2048_try-3/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | PASS | no markers |
| 2 | Structured tool_calls | PASS | speak,wait_user |
| 3 | Speak on glass | PASS | spoke=True glass_rows=1 |
| 4 | Latency / feel | PASS | seeded feel=5 latency=6.55s |
| 5 | Free-text-vs-tools | PASS | ok |

## Notes

finish_reason not on model beats yet (pre Stage 3 tape field)

## Snippets

### Model content (truncated)
```


```

### Reasoning (truncated)
```
The user (Operator) has sent a greeting to Elyra.
I should acknowledge the greeting and state my readiness.

Plan:
1. Acknowledge the greeting.
2. Explain that I am ready to assist with any tasks or questions.

```

### Glass assistant rows (truncated)
```
Hello! I'm ready to assist. Let me know if there's anything you'd like to work on or any questions you have.
```
