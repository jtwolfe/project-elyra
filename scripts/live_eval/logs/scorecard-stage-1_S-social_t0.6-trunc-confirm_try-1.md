# Scorecard — Stage 1 / S-social / try 1/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-1_S-social_t0.6-trunc-confirm_try-1` |
| **stage** | 1 |
| **scenario** | S-social |
| **try** | 1/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.6 top_p=0.95 top_k=64 budget=None |
| **started_at** | 2026-07-21T13:44:12.861911Z |
| **ended_at** | 2026-07-21T13:48:56.198366Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | 278243c9-b634-4f22-81a6-71ea9892566a |
| **hop_count** | 2 |
| **stop_reason** | no_tools |
| **spoke** | True |
| **tools** | speak |
| **per_hop_finish_reason** | not_on_tape, not_on_tape |
| **reasoning_len** | 21691 |
| **markers_raw (content / reasoning)** | 0 / 1183 |
| **markers_after_strip (c / r)** | 0 / 0 |
| **flood Y/N** | Y |
| **glass_speak Y/N** | Y |
| **free_text_only Y/N** | N |
| **latency_s** | 283.34 |
| **feel 1-5** | 1 |

## Paths

| Artifact | Path |
|----------|------|
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84af-4818-79a0-9ec5-b13c49eac234/scripts/live_eval/logs/runs/stage-1_S-social_t0.6-trunc-confirm_try-1/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84af-4818-79a0-9ec5-b13c49eac234/scripts/live_eval/logs/runs/stage-1_S-social_t0.6-trunc-confirm_try-1/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84af-4818-79a0-9ec5-b13c49eac234/scripts/live_eval/logs/runs/stage-1_S-social_t0.6-trunc-confirm_try-1` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f84af-4818-79a0-9ec5-b13c49eac234/scripts/live_eval/logs/runs/stage-1_S-social_t0.6-trunc-confirm_try-1/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | FAIL | markers c=0 r=1183 |
| 2 | Structured tool_calls | PASS | speak |
| 3 | Speak on glass | PASS | spoke=True glass_rows=1 |
| 4 | Latency / feel | FAIL | seeded feel=1 latency=283.34s |
| 5 | Free-text-vs-tools | PASS | ok |

## Notes

flood markers c=0 r=1183; finish_reason not on model beats yet (pre Stage 3 tape field)

## Snippets

### Model content (truncated)
```


```

### Reasoning (truncated)
```
The user (Operator) has just sent a "Hello" message. I am Elyra, a digital teammate.
I need to acknowledge the greeting and confirm readiness to assist.

Plan:
1. Acknowledge the greeting.
2. Briefly state my role (optional but good for confirmation).
3. Wait for the next instruction or task.

Since the user just said "Hi Elyra", a simple "Hello" or "Hello, Operator" via `speak` is appropriate.
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
<|channe
… [truncated] …
t
<|channel>thought

```

### Glass assistant rows (truncated)
```
Hello! I'm ready to help when you need me.
```
