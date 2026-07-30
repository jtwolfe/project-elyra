# Networking (v2) — no static IPs

_Status: redesign 2026-07-29. Axiom: house public IPv4 is not a resource we own._

## Requirements

1. **Join from anywhere** — residential NAT, CGNAT, flaky Wi-Fi; no mandatory port-forward.
2. **P2P first, relay fallback** — E2E encrypt; relays see metadata/ciphertext only.
3. **Stable identity** — nodes/services addressed by mesh name / SPIFFE-like ID, never "the IP we had Tuesday."
4. **Split planes** — admin/control mesh ≠ public ingress path.
5. **ACL default-deny** across trust tiers; friend ≠ recovery endpoints.
6. **Partition survival** — site uses last-known desired state; no global thrash.
7. **Bootstrap** — short-lived invite tokens; keys in rebuild kit.
8. **Sleepy nodes** — laptops that close lids rejoin without renumber drama.

## What I will not design around

- Static residential IP or dynamic DNS as the backbone.
- "Everyone opens 41641/udp forever" as onboarding.
- Raw WireGuard mesh with hand-maintained peer files as the product.
- Stretching Incus cluster / corosync heartbeat across the WAN.

## Fabric choice

| Criterion | Headscale + clients | Nebula | Raw WG |
|-----------|---------------------|--------|--------|
| NAT traversal | Excellent (DERP) | Good (lighthouses) | DIY pain |
| Ops on limited HW | Small Go CP + optional DERP | Lighthouses + certs | Peer explosion |
| ACL as code | Strong | Good | Weak |
| AI-operable | Documented API/ACL | YAML-friendly | Snowflake |

**Default I would run:** self-hosted **Headscale** on seed (or tiny VPS if seed sleeps) + **at least one DERP** on a cheap always-on VPS.

That VPS is a **crutch for reachability**, not the system of record. C0 does not live only there.

**Alt:** Nebula if we want stricter cert graphs and to avoid Tailscale client ecosystem — revisit after S1 pair works.

## Underlay vs overlay

**Inside a house**
- Normal LAN bridge for Incus NICs.
- Incus management/cluster traffic: **LAN only**.

**Across houses**
- Only mesh overlay + explicit service ports.
- No assumption of multicast or LAN MTU.

**Public ingress** (separate)
- Edge role with better uplink **or** tunnel adapter (Cloudflare Tunnel / similar) so houses still need no static IP.
- Reverse proxy (Caddy/Traefik) publishes **catalog-approved** routes only.
- Admin UI stays on mesh + SSO; not the same listener as random toys when avoidable.

## Identity and discovery

1. Mesh node names / MagicDNS for machines.
2. **Loom DNS** (CoreDNS or equivalent) fed by catalog: `vault.svc.loom` → healthy backends.
3. Health-aware records; optional policy: prefer non-relay paths for chonky replica traffic.
4. Apps use retrying HTTP/gRPC; no cross-site LAN myths.

## Membership lifecycle

```
invite (TTL, single-use) → install agent + mesh
  → hardware/owner attest tags
  → inventory QUARANTINE (metrics only)
  → operator/AI policy promote
  → placement tiers unlocked
```

Demote/revoke is a first-class intent ("friend needs disk back").

## Failure modes

| Failure | Effect | Mitigation |
|---------|--------|------------|
| IP churn / CGNAT | Brief blip | Mesh renegotiate; identity stable |
| DERP down | P2P may live; else partition | ≥2 relays; site autonomy |
| Both sides symmetric NAT | Permanent relay | Budget relay bandwidth; place bulk storage accordingly |
| Split brain CP | Conflicting placement | Single-writer seed v1; leases later |
| Hostile mesh peer | Lateral movement | ACL, per-svc identity, disk crypto |
| MTU blackhole | Weird stalls | Clamp overlay MTU |
| Clock skew | mTLS pain | Chrony; reject forever-skew |
| Captive portal Wi-Fi | Never joins | Detect + surface; do not fake healthy |

## IPv6

Prefer IPv6 path when end-to-end works (less NAT theater). Dual-stack clients; do not *require* global IPv6 on friends.

## Open decisions

- Headscale vs Nebula after S1 metrics.
- Userspace vs system mesh on locked-down friends.
- Whether seed may sleep if VPS holds Headscale (I lean: CP can colocate with DERP only if C0 still mirrors home).
