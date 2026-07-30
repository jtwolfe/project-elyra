# Architecture (v2)

_Status: redesign 2026-07-29. Minimal planes I would actually run on limited hardware._

## One-sentence shape

**Intent** becomes **signed desired state**; **thin site agents** reconcile **Incus (or tighter)** on a **no-static-IP mesh**; **layered storage + app HA** carry durability; **observability** feeds humans and the critic — including continuity workloads for long-lived agents.

```
  operator / AI chat
           |
           v
  planner → critic → gated commit
           |
           v
  desired-state log (Git / signed objects)  <--- mirrors on seed + offsite
           |
     pull / watch (when mesh up)
           |
     +-----+-----+
     v           v
  seed CP     site agent (each machine)
  placement    Incus actuator
  catalog      local last-known cache
  policy       metrics + events
     |           |
     +----- mesh (identity, not IP)
                 |
            workloads + volumes
```

## Design budget (limited resources)

| Component | Budget mindset |
|-----------|----------------|
| loom-agent | Single modest process; no local kube-apiserver |
| Incus | Default compute; standalone OK; cluster only inside LAN |
| Desired state | Git repo or sqlite+signatures if Git is heavy — but **append-only audit** required |
| Global CP | **One seed** first; cold standby via C0 mirror; multi-voter metadata only if seed HA becomes real pain |
| Observability | Metrics + event log first; full distributed tracing later |
| AI | Can run off-cluster (API) ; on-cluster only if resources allow — planner is not tied to friend GPUs |

## Planes

### 1. Intent / AI

- **In:** chat, forms, SLOs, budget (RAM/disk/power), trust tags, "host me" continuity goals.
- **Out:** design narrative, risk list, desired-state diff, dry-run result.
- **Loop:** propose → simulate → gate → commit → reconcile → evaluate → correct/rollback.
- **Hard rule:** production mutations go through the log. Tool outputs and app logs are **untrusted data** to the planner (injection boundary).

### 2. Control (seed-centric)

Responsibilities only:
- Inventory & capabilities (arch, disk, uptime class, owner trust, sleepy?).
- Placement constraints (trust tier, locality, replicas, data class).
- Catalog/templates (including **continuity-agent**, chat, backup sinks).
- Policy (expose, leave-house, friend placement).
- Bootstrap / recovery coordination.

**v1 deployment:** seed site runs CP. Not a seven-node etcd fantasy.  
**v2:** optional tiny metadata quorum; still never put blob IO in the quorum store.

### 3. Site agent

- Apply assigned desired subset.
- Drive Incus API (instances, profiles, networks, pools).
- Cache last-known good for **partition mode** (local reconcile continues; no global reshuffle).
- Push metrics/events; pull images via content-addressed cache.
- Optional: Docker-only mode for ultra-weak nodes (reduced template set).

### 4. Fabric

- Mesh with NAT traversal + relay fallback (see `03-networking.md`).
- Service identity and internal DNS from catalog.
- Public ingress **separate** from admin mesh (edge role or tunnel adapter).

### 5. Data

- C0–C4 as in `04-storage-ha.md`.
- Continuity bundles ride C0/C1/C2 deliberately.

### 6. Observability / UX

Web UI must-haves:
- Fleet map + mesh path quality (P2P vs relay).
- Drift: desired vs actual.
- Storage replica debt + encryption status.
- Intent timeline, approvals, AI action history.
- Continuity health (last successful state mirror).
- Energy/uptime honesty (friend nodes sleep).

Chat is for **change**; UI is for **truth and gates**.

## Node roles

| Role | Job | Hardware note |
|------|-----|----------------|
| **Seed** | CP + C0 authority refs + often primary continuity | Most trusted, least sleepy |
| **Worker** | Run general workloads | Preemptible OK |
| **Storage-heavy** | C2 targets, backup sink | Disk > CPU |
| **Edge** | Public HTTPS termination | Better uplink; still no house static IP required if tunnel used |
| **Relay** | DERP/lighthouse | **Allowed on tiny VPS** |
| **Witness** | Extra C0 mirror / metrics | Can be low-power |

Roles combine with caps. Friend default = worker + quarantine history.

## Intent → runtime pipeline

1. Parse intent → Goal (services, data class, HA level, exposure, continuity?).
2. Design → templates + failure domains + trust filters.
3. Compile → Incus objects, mesh ACLs, storage directives, app config.
4. Diff & gate → UI/chat approval per policy.
5. Commit → signed desired state.
6. Reconcile → agents converge; blockers explicit.
7. Verify → synthetics + SLO burn.
8. Record → outcome memory for later plans (visible, editable).

## Why this shape on weak hardware

- **One actuator family (Incus)** covers VM + system container without cluster DNS plugins on every box.
- **Git as truth** needs no always-on advanced CP DB for v1.
- **Agents are boring** — restartable, cache last good, small blast radius.
- **AI off-box OK** — intelligence is not coupled to friend GPU availability.
- **Federation of sites** replaces stretched HA brains that melt under packet loss.

## Security anchors

- mTLS agent channel; short-lived creds.
- Bootstrap kit offline (age/SOPS, mesh authority export, recovery codes).
- Signed desired state; sites cannot mint global policy.
- Separate recovery keys from daily operator laptop session when possible.
- Prompt-injection: planner tools distinguish **untrusted observations** vs **signed state**.

## Continuity as architecture

```
 continuity-bundle (encrypted)
   identity, ledger snapshot refs, packages index, config
        |
        +--> C0 mirror (seed + offsite)
        +--> C2 versioned objects (friends hold ciphertext OK)
 runtime (Elyra/loom-manager) placed by policy
        may move; bundle restore precedes wake
```

Placement policy: prefer seed/trusted for decrypted runtime; ciphertext replicas may live wider.
