#!/usr/bin/env bash
# quarantine_copy.sh — copy full memory root into a marked quarantine tree.
#
# Usage:
#   ./docs/lance-debug1/scripts/quarantine_copy.sh <SRC_MEMORY_ROOT> <QUARANTINE_ROOT>
# Example:
#   ./docs/lance-debug1/scripts/quarantine_copy.sh data/memory /tmp/lance-q-20260729
#
# Layout (canonical — KD15):
#   $QUARANTINE_ROOT/
#     .lance-debug1-quarantine          # ONLY marker path written by this script
#     data/memory/                      # full copy of SRC_MEMORY_ROOT
#       meta.json
#       lance/
#       atoms/   (if present)
#       ladder/  (if present, else empty dir)
#
# Safety:
#   - Does NOT open LanceMemoryStore, mutate live data, or run compact/optimize.
#   - Refuses if QUARANTINE_ROOT resolves under a live workspace data/ tree.
#   - Prefer idle/stopped writer before copy (concurrent merge_insert → torn risk).

set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage: quarantine_copy.sh <SRC_MEMORY_ROOT> <QUARANTINE_ROOT>

  SRC_MEMORY_ROOT   e.g. data/memory  (must contain lance/ and usually meta.json)
  QUARANTINE_ROOT   e.g. /tmp/lance-q-YYYYMMDD

Copies SRC → $QUARANTINE_ROOT/data/memory/ and writes marker ONLY at
  $QUARANTINE_ROOT/.lance-debug1-quarantine
EOF
  exit 2
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -lt 2 ]]; then
  usage
fi

SRC_MEMORY_ROOT="${1%/}"
QUARANTINE_ROOT="${2%/}"

if [[ ! -d "$SRC_MEMORY_ROOT" ]]; then
  echo "error: SRC_MEMORY_ROOT is not a directory: $SRC_MEMORY_ROOT" >&2
  exit 1
fi

if [[ ! -d "$SRC_MEMORY_ROOT/lance" ]]; then
  echo "error: expected lance/ under SRC_MEMORY_ROOT: $SRC_MEMORY_ROOT/lance" >&2
  exit 1
fi

# Resolve absolute paths where possible.
if command -v realpath >/dev/null 2>&1; then
  SRC_ABS="$(realpath "$SRC_MEMORY_ROOT")"
  # QUARANTINE may not exist yet
  if [[ -e "$QUARANTINE_ROOT" ]]; then
    QROOT_ABS="$(realpath "$QUARANTINE_ROOT")"
  else
    parent="$(dirname "$QUARANTINE_ROOT")"
    base="$(basename "$QUARANTINE_ROOT")"
    if [[ -d "$parent" ]]; then
      QROOT_ABS="$(realpath "$parent")/$base"
    else
      QROOT_ABS="$QUARANTINE_ROOT"
    fi
  fi
else
  SRC_ABS="$(cd "$SRC_MEMORY_ROOT" && pwd)"
  QROOT_ABS="$QUARANTINE_ROOT"
fi

# Refuse if quarantine root resolves under live workspace data/
# (heuristic: path contains /data/ and is not under /tmp).
case "$QROOT_ABS" in
  */data|*/data/*)
    case "$QROOT_ABS" in
      /tmp/*|/var/tmp/*)
        ;;
      *)
        echo "error: QUARANTINE_ROOT must not resolve under live workspace data/: $QROOT_ABS" >&2
        echo "  use e.g. /tmp/lance-q-YYYYMMDD" >&2
        exit 1
        ;;
    esac
    ;;
esac

# Best-effort writer detection (do not hard-fail).
# High-confidence cmdline patterns only — NOT bare substring "elyra" (that matches
# terminals/cwd paths like workspace-project-elyra and floods possibly_torn).
# Patterns: python -m elyra…, uvicorn …elyra…, elyra.runtime / elyra.presence modules.
WRITER_PID=""
WRITER_MATCH=""
POSSIBLY_TORN=false
SELF_PID=$$
PARENT_PID=${PPID:-}
if command -v pgrep >/dev/null 2>&1; then
  # pgrep -af → "PID full cmdline"
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    pid="${line%% *}"
    cmd="${line#* }"
    # Skip self / parent shell of this script.
    if [[ "$pid" == "$SELF_PID" || "$pid" == "$PARENT_PID" ]]; then
      continue
    fi
    # Require an actual interpreter/app token, not path-only noise.
    case "$cmd" in
      *python*-m*elyra*|*uvicorn*elyra*|*elyra.runtime*|*elyra.presence*|*elyra.memory*)
        WRITER_PID="$pid"
        WRITER_MATCH="$cmd"
        break
        ;;
    esac
  done < <(pgrep -af '(python[0-9.]*[[:space:]].*-m[[:space:]]+elyra|uvicorn[[:space:]].*elyra|elyra\.runtime|elyra\.presence|elyra\.memory)' 2>/dev/null || true)

  if [[ -n "$WRITER_PID" ]]; then
    POSSIBLY_TORN=true
    echo "warn: high-confidence Elyra/writer PID $WRITER_PID — prefer idle/stop before copy" >&2
    echo "warn: match: ${WRITER_MATCH:0:160}" >&2
    echo "warn: concurrent merge_insert can yield a torn snapshot (possibly_torn=true)" >&2
  fi
fi

DEST_MEMORY="$QROOT_ABS/data/memory"
MARKER="$QROOT_ABS/.lance-debug1-quarantine"

mkdir -p "$DEST_MEMORY"

echo "copying $SRC_ABS → $DEST_MEMORY ..."
# Prefer rsync if available (handles partial re-copy); else cp -a.
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete "$SRC_ABS/" "$DEST_MEMORY/"
else
  # Fresh dest subtree for cp -a semantics without leftover files.
  rm -rf "$DEST_MEMORY"
  mkdir -p "$(dirname "$DEST_MEMORY")"
  cp -a "$SRC_ABS" "$DEST_MEMORY"
fi

# Ensure ladder/ exists for layout parity even if source lacks it.
mkdir -p "$DEST_MEMORY/ladder"

UTC_NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u)"

# Marker ONLY at $QUARANTINE_ROOT/.lance-debug1-quarantine (JSON stamp).
# Do NOT write markers under data/ or data/memory/.
cat >"$MARKER" <<EOF
{
  "kind": "lance-debug1-quarantine",
  "source": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$SRC_ABS"),
  "quarantine_root": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$QROOT_ABS"),
  "copied_at_utc": "$UTC_NOW",
  "writer_pid": $(if [[ -n "$WRITER_PID" ]]; then echo "\"$WRITER_PID\""; else echo "null"; fi),
  "writer_match": $(if [[ -n "$WRITER_MATCH" ]]; then python3 -c 'import json,sys; print(json.dumps(sys.argv[1][:240]))' "$WRITER_MATCH"; else echo "null"; fi),
  "possibly_torn": $POSSIBLY_TORN,
  "writer_detection": "high-confidence cmdline only (python -m elyra / uvicorn elyra / elyra.runtime); advisory"
}
EOF

# Guard: refuse accidental alternate markers we might have left elsewhere.
for bad in \
  "$QROOT_ABS/data/.lance-debug1-quarantine" \
  "$QROOT_ABS/data/memory/.lance-debug1-quarantine"
do
  if [[ -e "$bad" ]]; then
    echo "warn: removing non-canonical marker $bad" >&2
    rm -f "$bad"
  fi
done

echo
echo "OK: quarantine ready"
echo "  LANCE_DEBUG_DATA_DIR=$QROOT_ABS/data"
echo "  LANCE_DEBUG_URI=$QROOT_ABS/data/memory/lance"
echo "  MARKER=$MARKER"
echo "  possibly_torn=$POSSIBLY_TORN"
echo
echo "Next (R1):"
echo "  export LANCE_DEBUG_DATA_DIR=$QROOT_ABS/data"
echo "  export LANCE_DEBUG_URI=$QROOT_ABS/data/memory/lance"
echo "  python docs/lance-debug1/scripts/api_matrix.py --uri \"\$LANCE_DEBUG_URI\" --out docs/lance-debug1/evidence/\$(date +%Y-%m-%d)-run-01/api-matrix.json"
