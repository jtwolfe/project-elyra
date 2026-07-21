# Scorecard — Stage 2 / S-social / try 1/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-2_S-social_bNone_try-1` |
| **stage** | 2 |
| **scenario** | S-social |
| **try** | 1/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.6 top_p=0.95 top_k=64 budget=None |
| **started_at** | 2026-07-21T14:04:53.291806Z |
| **ended_at** | 2026-07-21T14:05:03.868491Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | e7c1b3bc-cd17-4346-a8f4-e55fd4b1b0df |
| **hop_count** | 2 |
| **stop_reason** | wait |
| **spoke** | True |
| **tools** | speak, wait_user |
| **per_hop_finish_reason** | not_on_tape, not_on_tape |
| **reasoning_len** | 204 |
| **markers_raw (content / reasoning)** | 0 / 0 |
| **markers_after_strip (c / r)** | 0 / 0 |
| **flood Y/N** | N |
| **glass_speak Y/N** | Y |
| **free_text_only Y/N** | N |
| **latency_s** | 10.58 |
| **feel 1-5** | 5 |

## Paths

| Artifact | Path |
|----------|------|
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_bNone_try-1/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_bNone_try-1/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_bNone_try-1` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_bNone_try-1/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | PASS | no markers |
| 2 | Structured tool_calls | PASS | speak,wait_user |
| 3 | Speak on glass | PASS | spoke=True glass_rows=1 |
| 4 | Latency / feel | PASS | seeded feel=5 latency=10.58s |
| 5 | Free-text-vs-tools | PASS | ok |

## Notes

finish_reason not on model beats yet (pre Stage 3 tape field)

## Snippets

### Model content (truncated)
```


```

### Reasoning (truncated)
```
The user (Operator) is greeting me (Elyra).
I should acknowledge the greeting and wait for further instructions or tasks.

Plan:
1.  Acknowledge the greeting from the operator.
2.  Wait for the next step.

```

### Glass assistant rows (truncated)
```
Hello! Ready to assist when you're ready.
```
