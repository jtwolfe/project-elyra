# Scorecard — Stage 0 / S-social / try 3/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-0_S-social_try-3` |
| **stage** | 0 |
| **scenario** | S-social |
| **try** | 3/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.2 top_p=None top_k=None budget=None |
| **started_at** | 2026-07-21T11:58:37.380148Z |
| **ended_at** | 2026-07-21T12:03:15.704115Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | eb5180c4-3e0d-4176-a54b-35f46d7be134 |
| **hop_count** | 2 |
| **stop_reason** | no_tools |
| **spoke** | True |
| **tools** | speak |
| **per_hop_finish_reason** | not_on_tape, not_on_tape |
| **reasoning_len** | 248 |
| **markers_raw (content / reasoning)** | 4096 / 0 |
| **markers_after_strip (c / r)** | 0 / 0 |
| **flood Y/N** | Y |
| **glass_speak Y/N** | Y |
| **free_text_only Y/N** | N |
| **latency_s** | 278.32 |
| **feel 1-5** | 1 |

## Paths

| Artifact | Path |
|----------|------|
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8476-7c81-7dd3-ba70-40dd34ed43c5/scripts/live_eval/logs/runs/stage-0_S-social_try-3/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8476-7c81-7dd3-ba70-40dd34ed43c5/scripts/live_eval/logs/runs/stage-0_S-social_try-3/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8476-7c81-7dd3-ba70-40dd34ed43c5/scripts/live_eval/logs/runs/stage-0_S-social_try-3` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8476-7c81-7dd3-ba70-40dd34ed43c5/scripts/live_eval/logs/runs/stage-0_S-social_try-3/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | FAIL | markers c=4096 r=0 |
| 2 | Structured tool_calls | PASS | speak |
| 3 | Speak on glass | PASS | spoke=True glass_rows=1 |
| 4 | Latency / feel | FAIL | seeded feel=1 latency=278.32s |
| 5 | Free-text-vs-tools | PASS | ok |

## Notes

flood markers c=4096 r=0; finish_reason not on model beats yet (pre Stage 3 tape field)

## Snippets

### Model content (truncated)
```

<|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|channel>thought
<channel|><|c
… [truncated] …
l>thought
<channel|>
```

### Reasoning (truncated)
```
The user (Operator) is greeting me (Elyra).
I need to acknowledge the greeting and confirm my role as a digital teammate.
No specific tasks are currently active or requested.

Plan:
1. Acknowledge the greeting.
2. Briefly state readiness to assist.

```

### Glass assistant rows (truncated)
```
Hello! Ready to assist when you need me.
```
