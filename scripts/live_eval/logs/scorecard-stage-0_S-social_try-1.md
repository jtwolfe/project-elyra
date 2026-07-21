# Scorecard — Stage 0 / S-social / try 1/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-0_S-social_try-1` |
| **stage** | 0 |
| **scenario** | S-social |
| **try** | 1/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.2 top_p=None top_k=None budget=None |
| **started_at** | 2026-07-21T11:49:17.724306Z |
| **ended_at** | 2026-07-21T11:53:57.035186Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | 515c3f87-f3a1-4f1c-ad59-501bd6ef0608 |
| **hop_count** | 2 |
| **stop_reason** | no_tools |
| **spoke** | True |
| **tools** | speak |
| **per_hop_finish_reason** | not_on_tape, not_on_tape |
| **reasoning_len** | 210 |
| **markers_raw (content / reasoning)** | 4096 / 0 |
| **markers_after_strip (c / r)** | 0 / 0 |
| **flood Y/N** | Y |
| **glass_speak Y/N** | Y |
| **free_text_only Y/N** | N |
| **latency_s** | 279.31 |
| **feel 1-5** | 1 |

## Paths

| Artifact | Path |
|----------|------|
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8476-7c81-7dd3-ba70-40dd34ed43c5/scripts/live_eval/logs/runs/stage-0_S-social_try-1/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8476-7c81-7dd3-ba70-40dd34ed43c5/scripts/live_eval/logs/runs/stage-0_S-social_try-1/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8476-7c81-7dd3-ba70-40dd34ed43c5/scripts/live_eval/logs/runs/stage-0_S-social_try-1` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8476-7c81-7dd3-ba70-40dd34ed43c5/scripts/live_eval/logs/runs/stage-0_S-social_try-1/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | FAIL | markers c=4096 r=0 |
| 2 | Structured tool_calls | PASS | speak |
| 3 | Speak on glass | PASS | spoke=True glass_rows=1 |
| 4 | Latency / feel | FAIL | seeded feel=1 latency=279.31s |
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
The user (Operator) has sent a "hello" message.
I am Elyra, a digital teammate.
I need to acknowledge the greeting and be ready to assist.

Plan:
1. Acknowledge the greeting.
2. Briefly state my role/readiness.

```

### Glass assistant rows (truncated)
```
Hello! I'm ready to help when you're ready. Let me know if there's anything you'd like to work on.
```
