# Research notes

_Status: filled (first pass, 2026-07-29). Citations are starting points, not endorsements._

**Working name:** Loom
**Jim signal:** Incus looks like a strong starting compute substrate — treated as first-class below.

---

## Networking / mesh (trans-network + NAT)

| Option | Role | NAT / CGNAT | Control plane | Fit for Loom |
|--------|------|-------------|---------------|--------------|
| **WireGuard (raw)** | Data-plane crypto | Weak alone (needs keepalive + manual holepunch/portfwd) | None | Use as *transport*, not product |
| **Tailscale** | Mesh + UX | Strong (DERP relays when P2P fails) | SaaS coordination | Fast path for friend nodes; trust/dependency on vendor |
| **Headscale** | Self-hosted Tailscale coord | Same clients/DERP patterns | You run coordination | Better sovereignty than Tailscale SaaS |
| **Nebula** (Slack) | Cert-based overlay | Lighthouse-assisted UDP punch; good but less "magic" | DIY PKI + lighthouses | Strong if we want explicit trust graphs |
| **NetBird / Netmaker** | WG mesh products | Built-in traversal | Self-host or cloud | Alternatives if Headscale UX pain |
| **ZeroTier** | Overlay SDN | Mature traversal | Central controllers | Viable; different trust/control model |

**Takeaways**
- Pure WG is insufficient for "plug in at a friend's house." Need coordination + relay fallback (DERP-class) or lighthouses.
- Split **management plane access** from **public service ingress** (don't hairpin everything through one mesh policy).
- For rebuild-from-one-node: mesh membership + identity keys must be **recoverable from cluster state backup**, not only from a dead SaaS account.

**Sources**
- https://pinggy.io/blog/top_open_source_tailscale_alternatives/
- https://www.vpnsmith.com/en/blog/best-self-host-vpn-2026
- https://sumguy.com/mesh-vpn-showdown-2026/
- https://tailscale.com/compare/nebula
- https://lilting.ch/en/articles/tailscale-alternatives-wireguard-split-access

---

## Compute / orchestration substrate

### Incus (recommended exploration anchor)

- Successor community path from LXD: system containers (LXC) **and** VMs, REST API, clustering, storage drivers (ZFS/dir/LVM/Ceph… depending on setup).
- Homelab migrations **Proxmox → NixOS + Incus** argue it pairs well with **declarative hosts** and **AI-agent-friendly** APIs (imperative appliance UI vs API/Git-shaped control).
- Clustering distributes instances; not a full K8s scheduler — think **fleet of machines/VMs/sys containers** with a clean API, not pods/CRDs by default.
- Gaps vs Proxmox often cited: less batteries-included firewall/network UI; ops surface more DIY; HA semantics differ from Proxmox HA manager + corosync assumptions (LAN-ish clusters).
- Ecosystem signal: Cluster API provider for Incus/LXD exists; commercial stacks (e.g. FuturFusion materials) describe control planes talking to Incus clusters over mTLS REST — similar shape to what Loom's control plane would do.

**Sources**
- https://homelabstarter.com/proxmox-vs-incus-comparison/
- https://www.nijho.lt/post/proxmox-to-nixos/
- https://tadeubento.com/2024/replace-proxmox-with-incus-lxd/
- https://www.xda-developers.com/who-needs-proxmox-im-finally-trying-out-incus/
- https://spacelift.io/blog/kubernetes-alternatives
- https://futurfusion.io/images/FuturFusion-Dell_Collateral-Kit.pdf

### Proxmox VE

- Mature VM/LXC + web UI + HA manager; Corosync/pmxcfs oriented toward **low-latency cluster networks**.
- Multi-site WAN HA is a known footgun: quorum, fencing, and shared storage visibility break across flaky links.
- Great **node appliance** UX; weaker fit as the *global* control plane across friend NATs without an outer mesh + different consistency model.

**Sources**
- https://pve.proxmox.com/wiki/High_Availability_Cluster
- https://homelabstarter.com/proxmox-clustering/
- https://cr0x.net/en/proxmox-clustering-ha-design/

### K3s / Kubernetes

- Rich ecosystem (operators, GitOps, observability). Heavy for "friend PC joins mesh and runs two services."
- Multi-cluster GitOps (Flux per cluster vs Argo hub) is proven but operationally expensive at hobby WAN scale.
- Still the default if we need K8s-native storage (Longhorn) and portable workload defs.

**Sources**
- https://docs.k3s.io/quick-start
- https://computingforgeeks.com/flux-vs-argocd-multi-cluster/
- https://dev.to/devopsstart/argo-cd-vs-flux-a-guide-for-multi-cluster-gitops-1em5

### Nomad

- Lighter scheduler; **multi-region federation** is a first-class story; runs containers/binaries/QEMU more flexibly than "only k8s."
- Pairs with Consul (discover) + optional Vault; good middle ground between Docker Swarm and full K8s.

**Source**
- https://developer.hashicorp.com/nomad/docs/what-is-nomad

### Docker Swarm

- Simple multi-host overlay; lower mindshare/ecosystem than K8s; fine for tiny fleets, weaker long-term bet for AI-driven complex topologies.

**Source**
- https://docs.docker.com/engine/swarm/

### Portainer / cockpit-style UIs

- Fleet UI over Docker/K8s — useful **observability/management shell** pattern, not the distributed brain.

**Source**
- https://www.portainer.io/

**Substrate recommendation (research stance)**
1. **Incus cluster per site / per trusted LAN** as node agent target (VMs + system containers).
2. **Outer mesh** (Headscale or Nebula) for trans-network fabric.
3. Optional **Nomad or K3s inside** only when workload density needs a real scheduler; avoid forcing K8s on every friend node day one.
4. **Git-backed desired state** above all of it (see GitOps).

---

## Storage

| Option | Model | WAN / friend-node fit | Notes |
|--------|-------|----------------------|-------|
| **Longhorn** | Replicated block in K8s | Needs K8s; replica rebuild across WAN is painful; flaky nodes reported painful | Good LAN cluster disk; poor sole answer for multi-house |
| **SeaweedFS** | Distributed object + Filer + S3 | Horizontal; EC in enterprise story; O(1) volume access | Strong for **object/shared blobs** and backup targets |
| **MinIO** | S3 object | Erasure coding across nodes; careful topology | Familiar API; distributed mode wants thoughtful disk layout |
| **Ceph** | RBD/RGW/CephFS | Powerful, ops-heavy; WAN = separate skill tree | Only if dedicated storage crew (us-as-AI must absorb complexity) |
| **ZFS send/recv + snapshots** | Per-node + replication | Excellent for **asynchronous** multi-site; Incus loves ZFS | Core of rebuild-from-one if used as lineage store |
| **LiteFS / rqlite / Postgres logical** | App-level state | Prefer **stateful apps that replicate themselves** over magic shared POSIX across WAN | Critical design rule |

**Takeaways**
- "Shared storage" across houses must be layered: **(A)** cluster state/control repo, **(B)** object/blob volumes, **(C)** app-native replication. Do **not** promise single global POSIX RWX over NAT as the primary model.
- Rebuild-from-one-node ⇒ every critical datum has a **replica schedule** and a **bootstrap bundle** (identity, mesh keys, desired-state Git, storage recovery keys).

**Sources**
- https://longhorn.io/docs/latest/concepts/
- https://github.com/seaweedfs/seaweedfs
- https://seaweedfs.com/
- https://sumguy.com/minio-vs-seaweedfs-object-storage/
- https://oneuptime.com/blog/post/2025-11-27-choosing-kubernetes-storage-layers/view

---

## GitOps / desired state

- **Flux**: per-cluster controllers pull Git; good autonomous edge sites.
- **Argo CD**: central UI/app-of-apps; hub registers spokes.
- For Loom: desired state should be **intent + compiled manifests** in Git (or signed object store), reconciled by site agents — GitOps principles even if substrate is Incus API not K8s.

**Sources**
- https://computingforgeeks.com/flux-vs-argocd-multi-cluster/
- https://codelit.io/blog/gitops-deployment-guide
- https://wgall.com/blog/gitops-flux-argocd-2026.html

---

## AI ops / intent layer

- Academic/industry direction: **intent-based infrastructure** — natural language or high-level goals → policy + declarative config → closed-loop reconcile (not one-shot shell from an LLM).
- Practical pattern for Loom:
  1. User states intent in chat/UI.
  2. Planner produces **design doc + resource graph + risk notes**.
  3. Compiler emits **desired-state commit** (Iac for Incus/Nomad/K8s + mesh ACL + storage class).
  4. Reconciler applies; observability feeds **eval** back to AI (with human approval gates on destructive ops).
- Avoid equating "AI SSH's to prod" with management plane. AI should write **proposed state** and **runbooks**, execution is gated reconcile.

**Sources**
- https://ijaidsml.org/index.php/ijaidsml/article/view/182 (intent-based infrastructure framing)
- Adjacent: agent + API control planes (various); Incus REST is a clean actuator target (see Nijholt NixOS+Incus writeup — AI-agent-friendly infra).

---

## Comparable product shapes (category, not clones)

- **Homelab virt**: Proxmox, Incus
- **Edge K8s**: K3s + Flux
- **Mesh**: Tailscale/Headscale/Nebula
- **PaaS-like**: Nomad+Consul, Docker Swarm, Portainer
- **Chat-to-app** (different layer): Grok Build / similar — UI inspiration for intent UX, not multi-house HA

---

## Open research gaps (next passes)

- Incus cluster behavior over high-latency links (should site clusters be **local-only** with federated control?).
- Concrete DERP/Headscale self-host sizing for N friend nodes.
- Object storage WAN replication (SeaweedFS/MinIO) latency vs consistency knobs.
- Identity: SPIFFE/SPIRE vs mesh certs vs mTLS bootstrap tablets.
- Billing-free" contribution" fairness when friends donate electricity/disk (policy, not just tech).
