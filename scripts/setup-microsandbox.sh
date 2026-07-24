#!/usr/bin/env bash
# Doctor + optional setup for Elyra warm microsandbox isolation (sandbox0).
#
# This does NOT install KVM for you. It checks host readiness, optional Python
# extra, host tree chmod policy, and (when microsandbox is installed) a smoke
# create/exec/readiness against a temporary sandbox name — never sandbox0.
#
# Usage (from project root):
#   ./scripts/setup-microsandbox.sh
#   ./scripts/setup-microsandbox.sh --doctor-only
#   ./scripts/setup-microsandbox.sh --install-extra
#   ./scripts/setup-microsandbox.sh --ensure-tree
#   ./scripts/setup-microsandbox.sh --smoke
#
# Product enablement (isolation on by default when ELYRA_SANDBOX is unset):
#   pip install -e '.[sandbox]'
#   elyra start
# Disable isolation: export ELYRA_SANDBOX=0
#
# Overlay re-bootstrap: wiping the guest overlay (or remove+recreate after
# fingerprint mismatch) requires re-installing curated guest packages if any
# were pip-installed into the image layer. Host seed under sandboxes/sandbox0
# is re-seeded via ensure_host_tree / this script's --ensure-tree.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ELYRA_HOME="${ELYRA_HOME:-${ROOT}}"
SANDBOX0="${ELYRA_HOME}/sandboxes/sandbox0"
SMOKE_NAME="elyra-msb-smoke-$$"

log() { printf '[setup-microsandbox] %s\n' "$*"; }
warn() { printf '[setup-microsandbox] WARN: %s\n' "$*" >&2; }
die() { printf '[setup-microsandbox] ERROR: %s\n' "$*" >&2; exit 1; }

doctor_kvm() {
  if [[ "$(uname -s)" != "Linux" ]]; then
    log "non-Linux host ($(uname -s)); microsandbox may use platform virt (Apple Silicon / WHP)"
    return 0
  fi
  if [[ -e /dev/kvm ]]; then
    if [[ -r /dev/kvm && -w /dev/kvm ]]; then
      log "KVM: /dev/kvm present and accessible"
      return 0
    fi
    warn "KVM: /dev/kvm exists but not RW for $(id -un) — add user to kvm group or fix udev"
    return 1
  fi
  warn "KVM: /dev/kvm missing — install CPU virtualization / kvm modules"
  return 1
}

doctor_python_extra() {
  if python3 -c 'import microsandbox' 2>/dev/null; then
    log "python: microsandbox import OK"
    return 0
  fi
  warn "python: microsandbox not importable"
  log "hint: pip install -e '.[sandbox]'   # or: pip install 'elyra[sandbox]'"
  return 1
}

ensure_tree() {
  mkdir -p "${SANDBOX0}"/{lib,general,fixtures,tmp,tools}
  # Prefer Python ensure (seed copy + chmod) when package is importable.
  if python3 -c 'from elyra.sandbox.paths import ensure_host_tree' 2>/dev/null; then
    ELYRA_HOME="${ELYRA_HOME}" python3 -c '
import os
from pathlib import Path
from elyra.config import resolve_paths
from elyra.sandbox.paths import ensure_host_tree
layout = resolve_paths(os.environ.get("ELYRA_HOME") or None)
print(ensure_host_tree(paths=layout))
' || true
  fi
  # Seed trees world-readable for guest default user (DESIGN chmod policy).
  if [[ -d "${SANDBOX0}/lib" ]]; then chmod -R a+rX "${SANDBOX0}/lib" 2>/dev/null || true; fi
  if [[ -d "${SANDBOX0}/general" ]]; then chmod -R a+rX "${SANDBOX0}/general" 2>/dev/null || true; fi
  if [[ -d "${SANDBOX0}/fixtures" ]]; then chmod -R a+rX "${SANDBOX0}/fixtures" 2>/dev/null || true; fi
  chmod 1777 "${SANDBOX0}/tmp" 2>/dev/null || true
  chmod 755 "${SANDBOX0}/tools" 2>/dev/null || true
  log "host tree ready: ${SANDBOX0}"
  log "  tmp mode=$(stat -c '%a' "${SANDBOX0}/tmp" 2>/dev/null || echo '?') tools mode=$(stat -c '%a' "${SANDBOX0}/tools" 2>/dev/null || echo '?')"
}

install_extra() {
  log "installing optional extra: elyra[sandbox] (editable from ${ROOT})"
  python3 -m pip install -e "${ROOT}[sandbox]"
  doctor_python_extra || die "microsandbox still not importable after install"
}

cmd_smoke() {
  doctor_python_extra || die "install microsandbox first (--install-extra)"
  doctor_kvm || warn "continuing smoke without healthy KVM — may fail"
  ensure_tree
  log "smoke: create/exec/remove temporary sandbox ${SMOKE_NAME} (not sandbox0)"
  python3 - <<'PY' "${SMOKE_NAME}" "${SANDBOX0}"
import asyncio
import sys
from pathlib import Path

name = sys.argv[1]
host = Path(sys.argv[2])

async def main() -> None:
    import os
    from microsandbox import Network, Sandbox, Volume

    volumes = {
        "/workspace/lib": Volume.bind(str(host / "lib"), readonly=True),
        "/workspace/general": Volume.bind(str(host / "general"), readonly=True),
        "/workspace/fixtures": Volume.bind(str(host / "fixtures"), readonly=True),
        "/workspace/tmp": Volume.bind(str(host / "tmp"), readonly=False),
        "/workspace/tools": Volume.bind(str(host / "tools"), readonly=False),
    }
    # Match product default (ELYRA_SANDBOX_NETWORK / public_only egress).
    pol = (os.environ.get("ELYRA_SANDBOX_NETWORK") or "public_only").strip().lower()
    net = {
        "none": Network.none,
        "public_only": Network.public_only,
        "allow_all": Network.allow_all,
    }.get(pol, Network.public_only)()
    sb = await Sandbox.create(
        name,
        image="python",
        cpus=1,
        memory=512,
        security="restricted",
        workdir="/workspace",
        env={"ELYRA_SANDBOX_ROOT": "/workspace", "PYTHONDONTWRITEBYTECODE": "1"},
        pull_policy="if-missing",
        detached=True,
        network=net,
        volumes=volumes,
        replace=True,
    )
    try:
        out = await sb.exec("python3", ["-B", "-c", "print(40+2)"], cwd="/workspace", timeout=30.0)
        text = (getattr(out, "stdout_text", "") or "").strip()
        code = int(getattr(out, "exit_code", 1))
        if code != 0 or text != "42":
            raise SystemExit(f"smoke exec failed: exit={code} stdout={text!r}")
        print("smoke ok: python3 -B printed 42")
    finally:
        try:
            await sb.stop()
        except Exception:
            try:
                await sb.kill()
            except Exception:
                pass
        try:
            await Sandbox.remove(name)
        except Exception as exc:
            print(f"warn: remove {name}: {exc}", file=sys.stderr)

asyncio.run(main())
PY
  log "smoke complete"
}

cmd_doctor() {
  local rc=0
  doctor_kvm || rc=1
  doctor_python_extra || rc=1
  if [[ -d "${SANDBOX0}" ]]; then
    log "host tree exists: ${SANDBOX0}"
  else
    warn "host tree missing: ${SANDBOX0} (run --ensure-tree)"
    rc=1
  fi
  if [[ "${rc}" -eq 0 ]]; then
    log "doctor: OK"
  else
    log "doctor: issues found (see WARN lines)"
  fi
  return "${rc}"
}

usage() {
  cat <<EOF
Usage: $0 [--doctor-only|--install-extra|--ensure-tree|--smoke|--help]

  (default)       doctor + ensure-tree
  --doctor-only   KVM / pip / tree checks only
  --install-extra pip install -e '.[sandbox]'
  --ensure-tree   mkdir + chmod sandboxes/sandbox0
  --smoke         create/exec/remove temporary msb sandbox
EOF
}

main() {
  local mode="default"
  if [[ $# -gt 0 ]]; then
    case "$1" in
      --doctor-only) mode="doctor" ;;
      --install-extra) mode="install" ;;
      --ensure-tree) mode="tree" ;;
      --smoke) mode="smoke" ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown arg: $1 (try --help)" ;;
    esac
  fi
  case "${mode}" in
    doctor) cmd_doctor ;;
    install) install_extra ;;
    tree) ensure_tree ;;
    smoke) cmd_smoke ;;
    default)
      cmd_doctor || true
      ensure_tree
      log "next: --install-extra if needed, then --smoke, then elyra start (isolation on by default)"
      ;;
  esac
}

main "$@"
