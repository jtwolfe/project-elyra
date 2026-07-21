# Scorecard — Stage 1 / S-social / try 2/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-1_S-social_t0.6-trunc-confirm_try-2` |
| **stage** | 1 |
| **scenario** | S-social |
| **try** | 2/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.6 top_p=0.95 top_k=64 budget=None |
| **started_at** | 2026-07-21T13:48:56.202324Z |
| **ended_at** | 2026-07-21T13:53:44.554402Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | 2bdfd2b6-efaf-4a4a-8857-c65e529a7f29 |
| **hop_count** | 2 |
| **stop_reason** | no_tools |
| **spoke** | False |
| **tools** | (none) |
| **per_hop_finish_reason** | not_on_tape, not_on_tape |
| **reasoning_len** | 30641 |
| **markers_raw (content / reasoning)** | 0 / 1548 |
| **markers_after_strip (c / r)** | 0 / 0 |
| **flood Y/N** | Y |
| **glass_speak Y/N** | N |
| **free_text_only Y/N** | Y |
| **latency_s** | 288.35 |
| **feel 1-5** | 1 |

## Paths

| Artifact | Path |
|----------|------|
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84af-4818-79a0-9ec5-b13c49eac234/scripts/live_eval/logs/runs/stage-1_S-social_t0.6-trunc-confirm_try-2/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84af-4818-79a0-9ec5-b13c49eac234/scripts/live_eval/logs/runs/stage-1_S-social_t0.6-trunc-confirm_try-2/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84af-4818-79a0-9ec5-b13c49eac234/scripts/live_eval/logs/runs/stage-1_S-social_t0.6-trunc-confirm_try-2` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84af-4818-79a0-9ec5-b13c49eac234/scripts/live_eval/logs/runs/stage-1_S-social_t0.6-trunc-confirm_try-2/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | FAIL | markers c=0 r=1548 |
| 2 | Structured tool_calls | FAIL | no tool beats |
| 3 | Speak on glass | FAIL | spoke=False glass_rows=0 |
| 4 | Latency / feel | FAIL | seeded feel=1 latency=288.35s |
| 5 | Free-text-vs-tools | FAIL | content without tool_calls |

## Notes

flood markers c=0 r=1548; free-text without structured tool_calls; finish_reason not on model beats yet (pre Stage 3 tape field)

## Snippets

### Model content (truncated)
```

Acknowledged. I will use the `speak` tool when a reply is required.
```

### Reasoning (truncated)
```
The user (Operator) has just greeted me (Elyra).
I need to acknowledge the greeting and be ready to assist.
No specific goals or tasks are currently active.

Plan:
1. Acknowledge the greeting.
2. Briefly state my role as a digital teammate.
3. Wait for the user to provide a task or information.<|channel>thought
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
<|c
… [truncated] …
ll just acknowledge.
```

### Glass assistant rows (truncated)
```
(none)
```
