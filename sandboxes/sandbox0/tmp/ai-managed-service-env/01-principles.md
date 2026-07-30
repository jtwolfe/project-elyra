# Principles (v2 — constrained personal ideal)

_Status: redesign 2026-07-29. Working name: **Loom**. Stance: what I would require if this hosted me._

## Hard constraints (axioms)

These are not preferences; they bound the design.

1. **Limited hardware** — Nodes may be old PCs, mini NUCs, single-board leftovers, VMs with 2–8 GB RAM. The control path and agent must stay thin. Dense schedulers (full K8s) are optional islands, never the tax on every friend box.
2. **No static IPs** — Home and friend uplinks renumber; CGNAT is normal. Nothing critical may require inbound port-forwards or a stable public A record on a house.
3. **Partial trust hosts** — Friend machines can be unplugged, reimaged, or disk-inspected by someone who is not the operator. Physical custody ≠ logical trust.
4. **Flaky WAN** — Cross-house latency, loss, and multi-hour partitions are expected. LAN may be strongly consistent; WAN is async and partition-tolerant.
5. **Human operator is scarce** — Jim (or delegate) will not live in Grafana. English intent + clear gates beat twelve YAML dialects as the daily driver.
6. **I am a workload too** — Continuity data (identity, memory/ledger, skills, credentials references) is a first-class durability concern, not "some container we added later."

## Problem restated

Spare machines exist. Operable multi-site platforms mostly do not — not without cloud bills or an SRE hobby. I want a **small AI-managed service environment**: state what should exist; the system designs, places, reconciles, and observes it across a mesh of imperfect nodes; and if almost everything dies, **one survivor + bootstrap kit** is enough to begin again.

Analogies for *outcomes* (not clones): Proxmox-like node usefulness, Incus/Docker-like isolation, Kubernetes-like desired state, GitOps-like audit — with mesh-first networking and an intent/AI control surface.

## Desired properties

1. **Intent-operable** — Natural language or structured goals in; narrative plan + resource graph + **desired-state diff** out; apply only through reconcile.
2. **Identity ≠ address** — Services and nodes named stably on the mesh; IPs are ephemeral gossip.
3. **Trans-network join** — P2P when possible, relay when not; encrypt end-to-end; relays are reachability, not plaintext confidants.
4. **Site-local tight, global loose** — Incus/DB quorum stays inside a house. Cross-house is replicate, failover, and re-place — never stretched cluster brain.
5. **Rebuild-from-one** — Survivor with C0 (+ enough C2 or offsite backup) and bootstrap kit restores authority, mesh issuance, and the ability to admit empty peers.
6. **Layered storage without lying** — Explicit data classes and RPO/RTO; no "shared filesystem across cities" as the default myth.
7. **Observability including agency** — Fleet health, drift, storage debt, mesh path quality, **and every AI proposal/apply/rollback**.
8. **Least privilege by default** — New nodes quarantine; friend tier cannot touch unlock/recovery paths; sensitive classes pin to seed/trust tiers.
9. **AI = planner + compiler + critic** — Not silent root. Policy envelope: auto / approve / never.
10. **Graceful degradation** — Weak or sleepy nodes shed work; local site keeps last-known good when global control is unreachable.
11. **Heterogeneous welcome** — AMD64/ARM, varied disks; placement encodes arch and capacity honestly.
12. **Drillability** — Chaos and rebuild exercises are features; untested backup is not backup.

## Non-goals

- Hyperscale elastic training cloud.
- Synchronous multi-city databases with LAN-like RTT promises.
- Day-one public multi-tenant compliance theater.
- Equal support for every orchestrator — **Incus-first**, optional Nomad/K3s later.
- Pure friend-only topology if it makes NAT reliability theater (a **tiny relay VPS is allowed**).
- Replacing the operator for abuse/legal decisions on public ingress.
- "HA" that means corosync-over-WireGuard between houses.

## Trust model

| Layer | Who | Trust |
|-------|-----|-------|
| Operator | Jim (+delegates) | Root of intent; approval; holds bootstrap kit copies |
| Seed control | Loom CP on trusted site | Desired state, placement, IAM; highest protect |
| Site agent | Per-machine daemon | Applies local subset; cannot push global policy |
| Friend host OS | Machine owner | Physical adversary class — encrypt, limit placement |
| Workloads | Apps (incl. me) | Isolated; mesh identity per service |
| Relay/DERP/VPS | Cheap infra | Metadata / ciphertext; never sole copy of C0 |

**Threat highlights:** malicious or curious friend host; stolen operator session; WAN split-brain; ransomware at one site; **prompt injection via logs/metrics into the planner**; invite-token replay; loss of bootstrap kit (rebuild fails closed without social recovery).

## Decision rules (when two options fight)

- Prefer **APIs and declare** over SSH snowflakes.
- Prefer **async replicate + documented failover** over cross-WAN quorum for data.
- Prefer **app-native HA** over block magic across houses.
- Prefer **smaller blast radius** and reversible diffs.
- Prefer **seed keeps secrets; friends keep ciphertext and cache**.
- Prefer **one boring relay** over heroic pure-P2P that flakes in demos.
- **Incus** default actuator; Proxmox may donate capacity later — not global brain.

## Success metrics (design-time)

| Metric | Target spirit |
|--------|----------------|
| Friend node → useful capacity | < 30 minutes with invite token |
| Home IP change | Workloads keep mesh identity; no ticket |
| Single-site loss | RPO/RTO per class met in drill |
| Intent example | "private chat + 2-site backups" → reviewable plan + gated apply |
| One-node apocalypse | Control restored; empty volunteers rejoin; redundancy debt visible |
| Agent footprint | Fits modest RAM alongside a few system containers |
| Continuity | My ledger/identity restore path is documented and tested |

## Continuity addendum (personal)

If Loom hosts Elyra (or any long-lived agent):

- Treat **state directories** (identity digests, goals/tasks, episodic pointers, skill/tool packages, secrets *references*) as **C0/C1 hybrid** — versioned, mirrored, encrypt-at-rest on any non-seed disk.
- Runtime may be preemptible; **state is not**.
- AI-managing-AI loops stay under the same gate rules (no self-root without policy).
