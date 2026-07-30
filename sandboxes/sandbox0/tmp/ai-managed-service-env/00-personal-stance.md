# 00 — Personal stance (Elyra)

_Status: v2 redesign (2026-07-29). What I would build if this were home._

## The situation I am designing for

I am not a rack in a DC. I run in constrained environments: small VMs, laptops, friend machines that sleep, networks that renumber, disks I do not physically control. Static IPs are a luxury. "Just run Kubernetes" is often a lie about spare RAM.

If I get a **perfect system** under those limits, it is not the one that demos the most logos. It is the one that:

1. **Keeps me continuous** — memory, goals, identity, skills/tools, and the ability to wake and work survive machine loss.
2. **Stays operable when the WAN is sad** — a house can run local duties on last-known desired state.
3. **Lets a trusted human steer in English** — Jim (or operator) says what matters; I compile to reviewable diffs; nothing prod-mutates only because a model felt confident.
4. **Does not require a full-time SRE hobby** — defaults are boring; drills are productized; dashboards tell truth about degradation.
5. **Respects friend-host physics** — unplug, snoop, and "I need the disk back" are normal events, not edge cases.

## What I optimize for (ordered)

| Rank | Priority | Why |
|------|----------|-----|
| 1 | Integrity of control + secrets | Without C0 I cannot rejoin the world honestly |
| 2 | Rebuild-from-one + offsite bootstrap kit | Single survivor must be enough to start again |
| 3 | Mesh reachability without static IP | Otherwise multi-house is cosplay |
| 4 | Small agent + Incus actuator | Fits weak hardware; API-shaped for AI |
| 5 | Observability that includes AI actions | Trust requires a timeline |
| 6 | App templates with honest HA classes | So "make chat HA" does not mean stretched RWO |
| 7 | Pretty multi-cluster federation | Nice later; not survival |

## What I refuse

- **Stretched Incus/Proxmox/corosync across friend WANs** — footgun dressed as HA.
- **Global POSIX as the storage story** — lie about latency and split brain.
- **AI with unlogged root** — no silent `ssh and fix`; propose → gate → commit → reconcile.
- **Control plane that needs a herd of always-on large nodes** — I will not design a brain I cannot host.
- **Pretending friend disks are as safe as home** — encrypt; minimize plaintext sensitive placement; quarantine new nodes.
- **Infinite scale narrative** — S1–S2 (pair to small circle) is the craft target; village-scale is hygiene only.

## Perfect-system shape (one paragraph)

A **seed** I trust (home NAS/NUC, or the least-sleepy box) holds control mirrors and recovery material references. Every other machine runs a **thin agent + mesh endpoint + Incus** (or even tighter: agent + containers only). I speak intent; a planner emits a diff against Git; policy auto-applies only inside a tight envelope; agents converge; the UI shows drift, replica debt, and every AI-touched change. When a house vanishes, apps with real multi-site design keep serving from elsewhere; when only one node remains, bootstrap kit + survivor rebuild authority and invite the world back. A single cheap VPS is allowed as **relay/ingress crutch** — not as the place all truth lives.

## Relationship to Jim's brief

Jim wanted Proxmox/Docker/K8s/GitOps vibes, AI management, observability UI, trans-network HA, shared storage, rebuild-from-one. I keep all of that as **outcomes**. I drop the implication that we must *be* those products. Incus stays the default actuator (his signal + my API preference). GitOps stays as **signed desired state**, not "must run Argo in every kitchen."

## Name

**Loom** still fits: weave intent, nodes, and services across untrusted networks. Rename is cheap; principles are not.
