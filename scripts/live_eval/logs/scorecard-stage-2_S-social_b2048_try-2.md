# Scorecard — Stage 2 / S-social / try 2/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-2_S-social_b2048_try-2` |
| **stage** | 2 |
| **scenario** | S-social |
| **try** | 2/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.6 top_p=0.95 top_k=64 budget=2048 |
| **started_at** | 2026-07-21T14:20:39.487709Z |
| **ended_at** | 2026-07-21T14:25:23.807155Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | c3565d28-7bb7-47c1-8e14-ea4e89f0af64 |
| **hop_count** | 2 |
| **stop_reason** | no_tools |
| **spoke** | False |
| **tools** | (none) |
| **per_hop_finish_reason** | not_on_tape, not_on_tape |
| **reasoning_len** | 24603 |
| **markers_raw (content / reasoning)** | 0 / 1261 |
| **markers_after_strip (c / r)** | 0 / 0 |
| **flood Y/N** | Y |
| **glass_speak Y/N** | N |
| **free_text_only Y/N** | Y |
| **latency_s** | 284.32 |
| **feel 1-5** | 1 |

## Paths

| Artifact | Path |
|----------|------|
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_b2048_try-2/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_b2048_try-2/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_b2048_try-2` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84fb-1497-78c3-8f02-fa4ad8c330a0/scripts/live_eval/logs/runs/stage-2_S-social_b2048_try-2/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | FAIL | markers c=0 r=1261 |
| 2 | Structured tool_calls | FAIL | no tool beats |
| 3 | Speak on glass | FAIL | spoke=False glass_rows=0 |
| 4 | Latency / feel | FAIL | seeded feel=1 latency=284.32s |
| 5 | Free-text-vs-tools | FAIL | content without tool_calls |

## Notes

flood markers c=0 r=1261; free-text without structured tool_calls; finish_reason not on model beats yet (pre Stage 3 tape field)

## Snippets

### Model content (truncated)
```

The `speak` tool should only be used when a reply is required. Otherwise, I will remain silent.

I am ready to begin.
```

### Reasoning (truncated)
```
The user (Operator) is greeting me (Elyra).
I need to acknowledge the greeting and confirm my readiness to assist.
I should not perform any actions at this stage as no specific task has been provided.

Plan:
1. Acknowledge the greeting.
2. State readiness to assist.<|channel>thought
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
<|channel>thou
… [truncated] …
dge the "Host" note.
```

### Glass assistant rows (truncated)
```
(none)
```
