# MVP phases (v2)

_Status: redesign 2026-07-29. Path from one constrained box to a small circle._

## Phase 0 — Lab (single site, limited hardware)

**Goal:** Prove intent loop + thin agent + Incus on one modest machine.

- Incus standalone; agent applies declarative specs from Git.
- Minimal UI: inventory, drift, last reconcile error, **AI action log stub**.
- Planner stub: prompt → desired-state diff → human apply.
- Local ZFS (or best-effort snapshots).
- **Continuity bundle** local export/import smoke test.

**Exit:** Sample stack (proxy + static site + one DB) converges without hand SSH; bundle restore works on wipe-and-rejoin of the same box.

## Phase 1 — No-static-IP pair

**Goal:** Trans-network reality with one friend NAT.

- Headscale + DERP crutch (tiny VPS allowed).
- Second node joins quarantine → worker.
- One durable dataset cross-site (object or async DB/ZFS send).
- Kill home public IP (DHCP renew); mesh identities hold.
- Fail a node; restore per runbook (manual OK).

**Exit:** Documented RPO/RTO for one stateful app; IP churn drill green.

## Phase 2 — AI as primary admin surface

**Goal:** "Tell it what you want" is real.

- Chat/UI intent → planner design + diff + risks.
- Gates: auto / approve / never (expose & destroy always gated).
- Observability shows full intent timeline.
- Catalog: 3–5 templates Jim cares about + optional continuity template.
- Prompt-injection tests on log ingestion boundary.

**Exit:** Non-expert operator requests a service; gets running deployment with reviewable plan; rejection path works.

## Phase 3 — Circle resilience + rebuild drills

**Goal:** Productize honesty.

- ≥3 failure domains in placement.
- Under-replication alarms; bandwidth-capped rebalance.
- Chaos button: **rebuild-from-one** including continuity bundle priority.
- Edge ingress role or tunnel; admin SSO on mesh.
- Sleepy-node policies (drain, no C0 sole copy).

**Exit:** Lose any one site in drill; survivor restores control, admits replacement, clears redundancy debt on a timer.

## Phase 4+ (later)

- Optional K3s/Nomad islands for dense schedules only where paid for in RAM.
- Energy-aware placement; friend contribution stats.
- Hierarchical S3 village.
- Stronger multi-writer CP if seed HA becomes the real pain.

## Engineering spikes (next, in order I would take)

1. Desired-state schema (minimal YAML/Cue) + agent reconcile contract.
2. Incus API map used by agent (instances, profiles, projects, events).
3. Headscale reference deploy + ACL-as-code tied to trust tiers.
4. C0c continuity bundle format + restore hook.
5. C2 two-site experiment (MinIO vs Seaweed) with bandwidth caps.
6. Threat model one-pager (friend host + prompt injection).

## Explicit non-MVP

- Stretched clusters, global POSIX, fancy multi-cluster service mesh, GPU marketplace.
