#!/usr/bin/env bash
# =============================================================================
#  exasol-personal-license-export.sh
#  XYZ-PILOT-289 / XYZ-PILOT-258 helper: export the active Exasol Personal license
#  file from a node and preserve ConfD license metadata next to it.
#
#  Secret-safety contract:
#    - never prints license file contents
#    - writes exported license files mode 600
#    - writes metadata with size, sha256, source path, and ConfD summary only
# =============================================================================

set -euo pipefail

ts()    { date -u +%Y%m%dT%H%M%SZ ; }
log()   { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" ; }
err()   { printf '[%s] ERR: %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2 ; }
usage() {
    sed -n '2,28p' "$0"
    cat <<'EOF'

Usage:
  scripts/exasol-personal-license-export.sh \
    --node ubuntu@<host-or-ip> \
    --ssh-key /path/to/node_access.pem \
    [--preserve-dir licenses/backups] \
    [--label current]

Output:
  <preserve-dir>/<label>-<UTC>.exasol_license     active license file, mode 600
  <preserve-dir>/<label>-<UTC>.metadata.json      non-secret metadata
  <preserve-dir>/exasol-introspect-<UTC>/...      ConfD evidence
EOF
}

NODE=""
SSH_KEY=""
PRESERVE_DIR="${PWD}/licenses/backups"
LABEL="current"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --node) NODE="$2"; shift 2 ;;
        --ssh-key) SSH_KEY="$2"; shift 2 ;;
        --preserve-dir) PRESERVE_DIR="$2"; shift 2 ;;
        --label) LABEL="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "Unknown flag: $1"; usage; exit 2 ;;
    esac
done

[[ -n "$NODE" ]] || { err "--node is required"; usage; exit 2; }
[[ -n "$SSH_KEY" ]] || { err "--ssh-key is required"; usage; exit 2; }
[[ -f "$SSH_KEY" ]] || { err "SSH key not found: $SSH_KEY"; exit 2; }
[[ "$LABEL" =~ ^[A-Za-z0-9._-]+$ ]] || { err "--label must be filesystem-safe"; exit 2; }

chmod 600 "$SSH_KEY" 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTROSPECT_SCRIPT="${SCRIPT_DIR}/exasol-personal-license-introspect_gen.sh"
if [[ ! -x "$INTROSPECT_SCRIPT" ]]; then
    err "introspection script missing or not executable: $INTROSPECT_SCRIPT"
    exit 2
fi

STAMP="$(ts)"
RUN_DIR="${PRESERVE_DIR%/}"
mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR" 2>/dev/null || true

log "Preserving ConfD metadata before raw license export"
(
    cd "$RUN_DIR"
    "$INTROSPECT_SCRIPT" --non-interactive --node "$NODE" --ssh-key "$SSH_KEY" > "introspect-${STAMP}.log"
)

REMOTE_SCRIPT='
set -euo pipefail
PLAY_ID=$(c4 ps 2>/dev/null | awk "NR==2 {print \$2}")
if [ -z "$PLAY_ID" ]; then
    echo "ERROR=could_not_discover_play_id"
    exit 3
fi
confd_client() {
    c4 connect -s cos -i "$PLAY_ID" -- confd_client "$@" </dev/null
}
LICENSE_ID=$(confd_client license_info 2>/dev/null | awk "/^[[:space:]]+license_id:/ {print \$2; exit}")
COMPANY_NAME=$(confd_client license_info 2>/dev/null | awk -F": " "/^[[:space:]]+company_name:/ {print \$2; exit}")
EXPIRATION_DATE=$(confd_client license_info 2>/dev/null | awk -F": " "/^[[:space:]]+expiration_date:/ {print \$2; exit}")
SESSION_LIMIT=$(confd_client license_info 2>/dev/null | awk -F": " "/^[[:space:]]+max_session_limit:/ {print \$2; exit}")

HOME_DIR="${HOME:-/home/ubuntu}"
PERSONAL_TAG="$HOME_DIR/.ccc/x/.registry/tag/license:personal"
REMOTE_FILE=""
if [ -f "$PERSONAL_TAG" ]; then
    BRANCH=$(cat "$PERSONAL_TAG")
    MAPPED_BRANCH="${BRANCH/branchr-/branchr/}"
    CANDIDATE="$HOME_DIR/.ccc/x/u/$MAPPED_BRANCH/license.xml"
    if [ -f "$CANDIDATE" ]; then
        REMOTE_FILE="$CANDIDATE"
    fi
fi
if [ -z "$REMOTE_FILE" ]; then
    REMOTE_FILE=$(find "$HOME_DIR/.ccc/x" -type f -name license.xml 2>/dev/null | sort | head -1 || true)
fi
if [ -z "$REMOTE_FILE" ] || [ ! -f "$REMOTE_FILE" ]; then
    echo "ERROR=license_file_not_found"
    exit 4
fi
BYTES=$(wc -c < "$REMOTE_FILE" | tr -d " ")
SHA256=$(sha256sum "$REMOTE_FILE" | awk "{print \$1}")
printf "PLAY_ID=%s\n" "$PLAY_ID"
printf "REMOTE_LICENSE_FILE=%s\n" "$REMOTE_FILE"
printf "BYTES=%s\n" "$BYTES"
printf "SHA256=%s\n" "$SHA256"
printf "LICENSE_ID=%s\n" "$LICENSE_ID"
printf "COMPANY_NAME=%s\n" "$COMPANY_NAME"
printf "EXPIRATION_DATE=%s\n" "$EXPIRATION_DATE"
printf "MAX_SESSION_LIMIT=%s\n" "$SESSION_LIMIT"
'

log "Discovering active license file on node"
DISCOVERY="$(ssh -n -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 "$NODE" "$REMOTE_SCRIPT")"
if grep -q '^ERROR=' <<<"$DISCOVERY"; then
    err "remote discovery failed"
    printf '%s\n' "$DISCOVERY" >&2
    exit 3
fi

remote_file="$(awk -F= '$1=="REMOTE_LICENSE_FILE" {print substr($0, index($0,$2))}' <<<"$DISCOVERY")"
remote_sha="$(awk -F= '$1=="SHA256" {print $2}' <<<"$DISCOVERY")"
remote_bytes="$(awk -F= '$1=="BYTES" {print $2}' <<<"$DISCOVERY")"
[[ -n "$remote_file" ]] || { err "remote discovery did not return REMOTE_LICENSE_FILE"; exit 3; }

license_out="${RUN_DIR}/${LABEL}-${STAMP}.exasol_license"
metadata_out="${RUN_DIR}/${LABEL}-${STAMP}.metadata.json"

log "Copying active license file to local evidence store without printing contents"
scp -q -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$NODE:$remote_file" "$license_out"
chmod 600 "$license_out"

local_sha="$(shasum -a 256 "$license_out" | awk '{print $1}')"
local_bytes="$(wc -c < "$license_out" | tr -d ' ')"
if [[ "$local_sha" != "$remote_sha" || "$local_bytes" != "$remote_bytes" ]]; then
    err "copied license metadata mismatch; remote=${remote_bytes}/${remote_sha}, local=${local_bytes}/${local_sha}"
    exit 5
fi

DISCOVERY="$DISCOVERY" LICENSE_OUT="$license_out" METADATA_OUT="$metadata_out" NODE="$NODE" STAMP="$STAMP" python3 - <<'PY'
import json
import os
from pathlib import Path

data = {}
for line in os.environ["DISCOVERY"].splitlines():
    if "=" not in line:
        continue
    key, value = line.split("=", 1)
    data[key] = value

payload = {
    "exported_at_utc": os.environ["STAMP"],
    "node": os.environ["NODE"],
    "local_license_file": os.environ["LICENSE_OUT"],
    "remote_license_file": data.get("REMOTE_LICENSE_FILE", ""),
    "bytes": int(data["BYTES"]) if data.get("BYTES", "").isdigit() else data.get("BYTES", ""),
    "sha256": data.get("SHA256", ""),
    "play_id": data.get("PLAY_ID", ""),
    "confd_contract": {
        "license_id": data.get("LICENSE_ID", ""),
        "company_name": data.get("COMPANY_NAME", ""),
        "expiration_date": data.get("EXPIRATION_DATE", ""),
    },
    "confd_limits": {
        "max_session_limit": data.get("MAX_SESSION_LIMIT", ""),
    },
}
Path(os.environ["METADATA_OUT"]).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
chmod 600 "$metadata_out"

cat <<EOF

Export complete:
  license:  $license_out
  metadata: $metadata_out

Validation:
  bytes:  $local_bytes
  sha256: $local_sha
EOF
