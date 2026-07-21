# Overview

## What we are building

A **communal assistant** that stays present on a host machine: it can work with tools, speak to users, hold goals, and grow capabilities — without dual engines or ceremony every wake.

Shape borrowed from coding agents (Grok Build):

```text
while not done:
  model → tools → results → continue
else:
  stop / wait / speak
```

Wrapped in a **life shell**: multi-user identity walls, timers, voice as a tool, durable goals.

## Glossary

| Term | Meaning |
|------|---------|
| **Presence** | Always-on host process (not “off” between work) |
| **Wake queue** | What should start the next do-loop (messages, timers, …) |
| **Moment** | One **do-loop** from wake to stop |
| **Beat** | One step inside a moment (tool call, obs, speak, …) |
| **Skill** | Markdown playbook (`SKILL.md`) — how to work |
| **Tool** | Callable action with schema + runner |
| **Goal / task** | Durable *what* (ledger) |
| **Self** | Elyra’s identity files |
| **User model** | Per-person relationship files (never fused with self) |
| **Reasoning** | Provider private CoT stream — store for glass; usually omit from next model prompt |
| **Speak** | Tool that addresses a user; transport delivers it |
| **Interjection** | User message mid-moment, injected into the same do-loop |

## Architecture napkin

```text
presence
  wake queue ──► open moment ──► do-loop (model ↔ tools)
  goals/tasks         │              skill load on demand
  identity/users      │              speak / wait / sandbox
                      ▼
                 close moment (tape of beats)
                      │
                      ▼
                 next wake (continuous)
```

Stretch 2 (later): chain moments into days; **opaque sleep** sparsely weaves a graph. Not built in Stretch 1.

## Mental units (closed set)

If it is not one of these, it does not ship as a new subsystem:

| Unit | Role |
|------|------|
| Do-loop / moment | Work engine |
| Tools | Actions |
| Skills | Procedures |
| Goals / tasks | Commitments |
| Self / users | Who (separate) |
| Speak / wait | Social acts |
| Wake queue | Scheduling |
| Sandbox | Isolated work FS |

See [stretch-1.md](stretch-1.md) for how they run.
