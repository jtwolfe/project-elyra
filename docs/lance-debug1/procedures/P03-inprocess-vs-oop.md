# P03 — In-process vs out-of-process

| Field | Value |
|-------|--------|
| **Status** | Stub (filled in PR4) |
| **Safety class** | **R2** + **R1** on snapshot (not dual live connect by default) |
| **Prove / disprove** | process-specific vs pure client (H1 universal) |
| **Evidence** | glass snapshots + quarantine api-matrix |

## Purpose

Compare live glass memory payloads just after restart to offline probes on a quarantine snapshot.

## Preferred path

1. After restart, before heavy promote: glass `GET /api/memory`, atoms, vectors.
2. Idle quarantine snapshot → P01 on **snapshot**, not dual-connect on live URI.
3. Compare glass `atom_count` / vectors to snapshot `n_arrow` / `n_full`.

## Dual-connect policy

Discouraged. Only with explicit operator accept + multi-connect / possibly-torn tag. See [../SAFETY.md](../SAFETY.md).
