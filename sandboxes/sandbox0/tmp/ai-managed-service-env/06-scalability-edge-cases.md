# Scalability and edge cases (v2)

_Status: redesign 2026-07-29. Craft target: S1–S2 brilliantly, not village cosplay._

## Scale tiers I care about

| Tier | Shape | Must work | Explicitly defer |
|------|-------|-----------|------------------|
| **S0 Lab** | 1–2 nodes, one LAN | Intent→agent→Incus; local ZFS; UI drift; continuity bundle local | WAN, friends, relays |
| **S1 Pair** | Home + one friend NAT | Mesh+DERP; async object or DB replica; failover drill; IP churn survival | Clever placement ML |
| **S2 Circle** | ~5–15 nodes / 3–8 sites | Quarantine tiers, multi-relay, under-rep alarms, bandwidth caps | Global strong multi-writer CP |
| **S3 Village** | 50+ | Hierarchical agents, sharded inventory | Still not public mega-cloud |

**Bottlenecks:** Git thrash on noisy reconcile, image fanout, object rebalance over relay, human approval queue, AI context on huge fleets, sleepy-node flapping.

**Stance:** If it does not shine at **S1 pair on limited hardware**, it is not ready for friends.

## Edge cases (worklist)

### Network
- CGNAT + symmetric NAT → permanent relay cost.
- Captive portal / hotel Wi-Fi.
- MTU black holes; broken IPv6 half-path.
- Clock skew breaking mTLS.
- Seed sleeps while friends stay up (CP availability policy).

### Trust / people
- Friend revokes machine that held a unique replica.
- Roommate unplugs the noisy box mid-recv.
- Curious friend with physical disk access.
- Operator laptop compromised (daily keys vs bootstrap kit split).
- Bootstrap kit lost — need social recovery or accept fail-closed.
- Legal pressure on public edge.

### Data
- Split-brain two primaries after partition.
- EC below reconstruct threshold forever.
- "Backup green" but restore path bitrotten.
- Secrets left in old Git history.
- Continuity bundle newer than desired-state or vice versa.

### Placement / capacity
- All strong nodes sleepy at once.
- Thundering herd when a site returns.
- Disk full mid-zfs-recv.
- ARM vs AMD64 image matrix on mixed friends.
- GPU only on flaky site.

### Control / AI
- AI proposes bind Postgres to 0.0.0.0/0.
- Concurrent conflicting intents.
- Half-applied desired state (agent crash).
- Prompt injection via malicious app logs.
- Invite token replay.
- AI-managing-continuity loops trying to skip gates.

### Philosophy refusals
- Corosync/Incus stretch across WAN: **out of scope**.
- HA means **redundancy + documented failover + drills**, not invisible stretched magic.

## Open questions for Jim

1. One small always-on VPS for DERP/ingress — accept as default crutch?
2. May encrypted backups / continuity ciphertext live on friend disks?
3. First three app templates to drive the catalog?
4. Incus-only workers first, or Proxmox donor agents early?
5. Name "Loom" OK?
6. How personal is continuity-on-Loom (host Elyra-class runtime) vs general homelab only?
