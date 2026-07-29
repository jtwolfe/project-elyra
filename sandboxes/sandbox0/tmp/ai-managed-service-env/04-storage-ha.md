# Storage and HA (v2)

_Status: redesign 2026-07-29. Honesty over magic; rebuild-from-one is a procedure._

## Requirements

1. Durable classes survive loss of any **one** site when policy says so.
2. Friend disks may hold **ciphertext**, not assumed-safe plaintext of crown jewels.
3. Limited hardware: no cluster filesystem tax on every node.
4. Rebuild-from-one: survivor + bootstrap kit restores authority and hydration path.
5. Visible RPO/RTO per class; under-replication is an alarm, not a surprise.
6. Continuity bundles (agent state) have an explicit lane.

## Data classes

| Class | Examples | Consistency | Replication |
|-------|----------|-------------|-------------|
| **C0 Control** | desired-state log, IAM, mesh authority refs, policy | Strong on seed; async mirror | ≥2 sites when possible + offline kit |
| **C0c Continuity** | identity, ledger snapshots, package indexes, config refs | Versioned; restore-before-run | Mirror with C0; ciphertext on friends OK |
| **C1 Catalog** | images, templates, agent packages | Content-addressed | Lazy pull + seed mirror |
| **C2 Object** | backups, media, weights, encrypted bundles | Eventual | N-way or EC (Seaweed/MinIO) |
| **C3 Volume** | VM disks, local DB files | Site-local strong | ZFS snap/send or app HA |
| **C4 Ephemeral** | build cache, scratch | None | Local |

**Rule:** Multi-site HA for an app = **C2 + app replication** or multi-primary protocol — **not** one RWO block stretched across cities.

## Substrate mapping (what I would run)

### C0 / C0c

- Git (or signed append log) on seed; **offsite mirror** (other house and/or object).
- Secrets: SOPS/age; recovery keys in **bootstrap kit** (paper + offline USB), not only on friend NVMe.
- Continuity: encrypted bundle publish on interval + pre-stop hook; UI shows last good bundle age.

### C1

- Content-addressed blob cache; seed holds source of truth for templates.

### C2

- **MinIO or SeaweedFS** on storage-heavy nodes; start with **2––3 way replication** before erasure-coding cleverness.
- WAN-aware scheduling; bandwidth caps on rebalance.
- Friends may store encrypted backups of home data — policy explicit.

### C3 + Incus

- ZFS when disk allows; ext4+snapshot tool as fallback (weaker).
- Site HA: restart/migrate inside LAN Incus only.
- Cross-site: `zfs send/recv` or restore from C2 images.
- Longhorn-class block HA = **LAN island only** if K8s appears later.

### App-native templates

- Postgres: primary + async replica cross-site; failover runbook + AI assist.
- SQLite apps: LiteFS-style or "single writer + backup."
- Queues: pick software with a real story; catalog encodes it.

## Rebuild-from-one

**Inputs:** survivor node, bootstrap kit, whatever C0/C2 fragments remain (or offsite backup).

1. Promote survivor to **Seed**; bring CP up read/write.
2. Unlock secrets with kit; restore desired-state log.
3. Restore or reissue mesh authority.
4. Open join window; empty nodes enroll **quarantine**.
5. Hydrate C2 from shards/backups; mark **redundancy debt** in UI.
6. Restore C0c continuity bundle **before** starting agent runtime if that is the goal.
7. Reconcile workloads by priority: DNS/auth/ingress/continuity → critical apps → toys.
8. Run verify synthetics; clear debt as replicas refill.

**If fragments missing:** stop at control plane; data RPO = last real backup. No romance.

## HA patterns

| Pattern | Use |
|---------|-----|
| Stateless N+1 | Web/API across ≥2 sites |
| Primary + async replica | DBs |
| Active/passive site | Whole service moves on death |
| Local-only | Trust-sensitive; never friend tier |
| Ciphertext-wide / decrypt-narrow | Backups and continuity bundles |

## Failure modes

| Failure | Mitigation |
|---------|------------|
| Friend wipes disk | Min replica count; alert under-rep |
| Rebuild storm fills uplink | Caps + priority classes |
| Ransomware site | Snapshots + immutable object window |
| Bitrot | ZFS scrub; object checksums |
| Backup never restored | Scheduled restore **jobs** |
| Kit lost | Social recovery design (split custody); fail closed otherwise |
| Continuity bundle too stale | Block auto-wake or warn hard |

## Open decisions

- SeaweedFS vs MinIO default after a 2-site experiment.
- How little C0 may live on friends (I lean: mirrors of encrypted Git only).
- 3-way copy vs EC at S2 scale.
