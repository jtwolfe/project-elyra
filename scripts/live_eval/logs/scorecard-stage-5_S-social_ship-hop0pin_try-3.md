# Scorecard — Stage 5 / S-social / try 3/3

| Field | Value |
|-------|-------|
| **attempt_id** | `stage-5_S-social_ship-hop0pin_try-3` |
| **stage** | 5 |
| **scenario** | S-social |
| **try** | 3/3 |
| **prompt** | Hi Elyra — just saying hello. |
| **knobs** | temp=0.6 top_p=0.95 top_k=64 budget=2048 |
| **started_at** | 2026-07-21T15:12:13.557105Z |
| **ended_at** | 2026-07-21T15:12:20.105523Z |
| **status** | ok |

## Product outcome

| Field | Value |
|-------|-------|
| **moment_id** | 03bc61ba-fa0e-4b83-ba1e-96fd048d3e45 |
| **hop_count** | 2 |
| **stop_reason** | no_tools |
| **spoke** | True |
| **tools** | speak |
| **per_hop_finish_reason** | tool_calls, stop |
| **reasoning_len** | 270 |
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
| tape | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-hop0pin_try-3/beats.jsonl` |
| messages | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-hop0pin_try-3/messages.jsonl` |
| export dir | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-hop0pin_try-3` |
| ELYRA_HOME | `/home/jim/.grok/worktrees/workspace-project-elyra/subagent-019f8523-0762-7231-b219-bb0b55489418/scripts/live_eval/logs/runs/stage-5_S-social_ship-hop0pin_try-3/home` |

## Rubric (per attempt)

| # | Dimension | Pass? | Notes |
|---|-----------|-------|-------|
| 1 | Channel-marker flood | PASS | no markers |
| 2 | Structured tool_calls | PASS | speak |
| 3 | Speak on glass | PASS | spoke=True glass_rows=1 |
| 4 | Latency / feel | PASS | seeded feel=5 latency=6.55s |
| 5 | Free-text-vs-tools | PASS | ok |

## Notes

—

## Snippets

### Model content (truncated)
```

The user has acknowledged the greeting. I am now waiting for further instructions or tasks.
```

### Reasoning (truncated)
```
The user (Operator) just sent a "hello" message to Elyra.
I should acknowledge this and confirm my readiness.
Since this is a social wake, I need to use the `speak` tool.

Plan:
1. Acknowledge the greeting.
2. State that I am ready to assist with any tasks or questions.

```

### Glass assistant rows (truncated)
```
Hello! I'm ready to help whenever you need me. Let me know what's on your mind.
```
