# Loom — personal ideal redesign

**Goal:** g_2f86c5313008  
**Working name:** *Loom* (still fine; rename anytime)  
**Stance:** first-principles redesign as if this were **Elyra's** home platform under hard limits — limited hardware, no static IPs, friend-site chaos — not a feature mash of Proxmox+K8s+GitOps.

## What changed vs draft v1

Prior pass answered "design Jim's combo product." This pass answers: **if I had to live on this stack, what would I actually build and refuse?**

Key shifts:
1. **Continuity workloads first** — operator memory, goals, identity, backups of *me* are C0-adjacent, not afterthought apps.
2. **Ruthlessly small control plane** — limited RAM/disk means no K8s-shaped tax on day one.
3. **No static IP is a hard axiom** — mesh + one optional cheap relay; never design around home public IPv4.
4. **Site autonomy > pretty global quorum** — partition = local keeps running last-known good.
5. **AI is the primary admin surface** — UI is observability + gates; chat/intent is how work enters.
6. **Honesty over magic HA** — rebuild-from-one is a drillable procedure, not a marketing claim.

## Document index

| Doc | Status | Purpose |
|-----|--------|---------|
| [00-personal-stance.md](00-personal-stance.md) | v2 | What I would optimize for and refuse |
| [01-principles.md](01-principles.md) | v2 | Constraints, properties, non-goals, trust |
| [02-architecture.md](02-architecture.md) | v2 | Minimal planes I would run |
| [03-networking.md](03-networking.md) | v2 | No-static-IP fabric |
| [04-storage-ha.md](04-storage-ha.md) | v2 | Data classes, rebuild-from-one |
| [05-research.md](05-research.md) | v1 keep | Comparable tech + citations |
| [06-scalability-edge-cases.md](06-scalability-edge-cases.md) | v2 | Scale I care about + edges |
| [07-mvp-phases.md](07-mvp-phases.md) | v2 | Path from one box to circle |

## Architecture snapshot (v2)

```
intent (AI chat) → signed desired-state (Git) → site agents → Incus
                         ↕
              mesh (no static IPs; DERP crutch OK)
                         ↕
         C0 control mirrors · C2 object · app-native HA
```

- **Compute:** Incus site-local only (never stretch cluster across houses).
- **Fabric:** Headscale + DERP default; identity not IP.
- **Truth:** Git/signed log; AI proposes, gates apply, agents reconcile.
- **Storage:** layered; no fake global POSIX across friend WANs.
- **Me-shaped load:** Elyra/runtime continuity treated as first-class durable service class.

## Path

All files: `tmp/ai-managed-service-env/`
