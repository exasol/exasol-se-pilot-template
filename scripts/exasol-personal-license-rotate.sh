#!/usr/bin/env bash
# =============================================================================
#  exasol-personal-license-rotate.sh
#  XYZ-PILOT-289 helper: export the currently active Exasol Personal license,
#  stage a replacement license file, and optionally apply it through ConfD's
#  license_upload command after an explicit operator confirmation flag.
#
#  Secret-safety contract:
#    - never prints license file contents
#    - writes exported active licenses mode 600
#    - records only bytes, sha256, paths, and ConfD summary metadata
#    - defaults to non-mutating stage-only mode
# =============================================================================

set -euo pipefail

ts()    { date -u +%Y%m%dT%H%M%SZ ; }
log()   { printf '[%s] %s\n' "$(date -u +%H:%M:%SZ)" "$*" ; }
warn()  { printf '[%s] WARN: %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2 ; }
err()   { printf '[%s] ERR: %s\n' "$(date -u +%H:%M:%SZ)" "$*" >&2 ; }
usage() {
    sed -n '2,34p' "$0"
    cat <<'EOF'

Usage:
  scripts/exasol-personal-license-rotate.sh \
    --node ubuntu@<host-or-ip> \
    --ssh-key /path/to/node_access.pem \
    --license-file /secure/local/path/license.xml \
    [--preserve-dir /secure/local/evidence] \
    [--apply --confirm-apply]

Modes:
  default                 Export current state and stage replacement only.
  --apply --confirm-apply Export, stage, run confd_client license_upload,
                          then run license_run_check and license_info.

Notes:
  - Keep license files out of shared logs and tickets.
  - Backups are written under <preserve-dir>/exasol-license-rotation-<UTC>/.
  - After a successful apply the script writes non-secret operator evidence to
    operator-dashboard-license-evidence.json and, by default, publishes it to
    s3://xyz-pilot-status-<AWS_ACCOUNT_ID>/license/evidence.json. Override with
    XYZ_LICENSE_EVIDENCE_BUCKET / XYZ_LICENSE_EVIDENCE_KEY or disable with
    XYZ_LICENSE_EVIDENCE_PUBLISH=0.
  - The current deployed license can be exported first with
    scripts/exasol-personal-license-export.sh and then passed back here for a
    non-destructive "re-import current license" validation.
EOF
}

NODE=""
SSH_KEY=""
LICENSE_FILE=""
PRESERVE_DIR="${PWD}/license-rotation-evidence"
APPLY=0
CONFIRM_APPLY=0
LICENSE_EVIDENCE_BUCKET="${XYZ_LICENSE_EVIDENCE_BUCKET:-xyz-pilot-status-${XYZ_AWS_ACCOUNT_ID:-<AWS_ACCOUNT_ID>}}"
LICENSE_EVIDENCE_KEY="${XYZ_LICENSE_EVIDENCE_KEY:-license/evidence.json}"
LICENSE_EVIDENCE_PUBLISH="${XYZ_LICENSE_EVIDENCE_PUBLISH:-1}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --node) NODE="$2"; shift 2 ;;
        --ssh-key) SSH_KEY="$2"; shift 2 ;;
        --license-file) LICENSE_FILE="$2"; shift 2 ;;
        --preserve-dir) PRESERVE_DIR="$2"; shift 2 ;;
        --apply) APPLY=1; shift ;;
        --confirm-apply) CONFIRM_APPLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) err "Unknown flag: $1"; usage; exit 2 ;;
    esac
done

[[ -n "$NODE" ]] || { err "--node is required"; usage; exit 2; }
[[ -n "$SSH_KEY" ]] || { err "--ssh-key is required"; usage; exit 2; }
[[ -n "$LICENSE_FILE" ]] || { err "--license-file is required"; usage; exit 2; }
[[ -f "$SSH_KEY" ]] || { err "SSH key not found: $SSH_KEY"; exit 2; }
[[ -f "$LICENSE_FILE" ]] || { err "license file not found: $LICENSE_FILE"; exit 2; }
if [[ "$APPLY" -eq 1 && "$CONFIRM_APPLY" -ne 1 ]]; then
    err "--apply requires --confirm-apply"
    exit 2
fi

base_name="$(basename "$LICENSE_FILE")"
[[ "$base_name" =~ ^[A-Za-z0-9._-]+$ ]] || {
    err "license file basename must be filesystem-safe: $base_name"
    exit 2
}

chmod 600 "$SSH_KEY" 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPORT_SCRIPT="${SCRIPT_DIR}/exasol-personal-license-export.sh"
[[ -x "$EXPORT_SCRIPT" ]] || { err "export script missing or not executable: $EXPORT_SCRIPT"; exit 2; }

STAMP="$(ts)"
RUN_DIR="${PRESERVE_DIR%/}/exasol-license-rotation-${STAMP}"
mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR" 2>/dev/null || true

local_sha="$(shasum -a 256 "$LICENSE_FILE" | awk '{print $1}')"
local_bytes="$(wc -c < "$LICENSE_FILE" | tr -d ' ')"

log "Exporting current deployed license before staging replacement"
"$EXPORT_SCRIPT" \
    --node "$NODE" \
    --ssh-key "$SSH_KEY" \
    --preserve-dir "$RUN_DIR/current-license" \
    --label current-before-rotation > "$RUN_DIR/export-current.log"

REMOTE_DIR="/tmp/exasol-license-rotation-${STAMP}"
REMOTE_FILE="${REMOTE_DIR}/${base_name}"
remote_dir_q="$(printf '%q' "$REMOTE_DIR")"
remote_file_q="$(printf '%q' "$REMOTE_FILE")"

log "Staging replacement license file on node without printing contents"
ssh -n -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$NODE" \
    "install -d -m 700 $remote_dir_q"
scp -q -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$LICENSE_FILE" "$NODE:$REMOTE_FILE"
REMOTE_META="$(
    ssh -n -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$NODE" \
        "chmod 600 $remote_file_q && BYTES=\$(wc -c < $remote_file_q | tr -d ' ') && SHA256=\$(sha256sum $remote_file_q | awk '{print \$1}') && printf 'BYTES=%s\nSHA256=%s\n' \"\$BYTES\" \"\$SHA256\""
)"
remote_sha="$(awk -F= '$1=="SHA256" {print $2}' <<<"$REMOTE_META")"
remote_bytes="$(awk -F= '$1=="BYTES" {print $2}' <<<"$REMOTE_META")"
if [[ "$remote_sha" != "$local_sha" || "$remote_bytes" != "$local_bytes" ]]; then
    err "staged license metadata mismatch; local=${local_bytes}/${local_sha}, remote=${remote_bytes}/${remote_sha}"
    exit 5
fi

PLAN_JSON="$RUN_DIR/rotation-plan-${STAMP}.json"
STAMP="$STAMP" NODE="$NODE" LICENSE_FILE="$LICENSE_FILE" REMOTE_FILE="$REMOTE_FILE" \
LOCAL_BYTES="$local_bytes" LOCAL_SHA="$local_sha" APPLY="$APPLY" PLAN_JSON="$PLAN_JSON" python3 - <<'PY'
import json
import os
from pathlib import Path

payload = {
    "created_at_utc": os.environ["STAMP"],
    "node": os.environ["NODE"],
    "local_license_file": os.environ["LICENSE_FILE"],
    "remote_staged_file": os.environ["REMOTE_FILE"],
    "bytes": int(os.environ["LOCAL_BYTES"]),
    "sha256": os.environ["LOCAL_SHA"],
    "apply_requested": os.environ["APPLY"] == "1",
    "current_license_export_log": "export-current.log",
}
Path(os.environ["PLAN_JSON"]).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
chmod 600 "$PLAN_JSON"

if [[ "$APPLY" -ne 1 ]]; then
    cat <<EOF

Rotation plan prepared; no license mutation was performed.

Evidence directory:
  $RUN_DIR

Replacement license staged on node:
  $REMOTE_FILE

Staged file metadata:
  bytes:  $local_bytes
  sha256: $local_sha

To apply after explicit operator approval, rerun with:
  scripts/exasol-personal-license-rotate.sh ... --apply --confirm-apply

Post-rotation validation:
  1. Refresh Grafana Exasol Monitor -> License Export / Import Controls.
  2. Export /license/export?refresh=true and compare Contract/Limits fields.
  3. Re-run XYZ_STRESS for XYZ-PILOT-258 if the goal is lifting the 20-session cap.
EOF
    exit 0
fi

log "Applying staged license with guarded ConfD license_upload"
remote_arg="$(printf '%q' "$REMOTE_FILE")"
APPLY_OUTPUT="$(
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$NODE" "bash -s -- $remote_arg" <<'REMOTE_APPLY'
set -euo pipefail
REMOTE_FILE="$1"
if [ ! -f "$REMOTE_FILE" ]; then
    echo "ERROR=staged_license_not_found"
    exit 4
fi
PLAY_ID=$(c4 ps 2>/dev/null | awk "NR==2 {print \$2}")
if [ -z "$PLAY_ID" ]; then
    echo "ERROR=could_not_discover_play_id"
    exit 3
fi
confd_client() {
    c4 connect -s cos -i "$PLAY_ID" -- confd_client "$@" </dev/null
}
# Use Exasol's stdin-reader syntax as one YAML parameter argument. Passing
# `license:` and the placeholder as two arguments parses the value as a dict on
# this host; the single-argument form reaches license verification correctly and
# keeps the license body out of process arguments.
set +e
UPLOAD_RESULT=$(cat "$REMOTE_FILE" | timeout "${LICENSE_UPLOAD_TIMEOUT_SECONDS:-180}" c4 connect -s cos -i "$PLAY_ID" -- confd_client license_upload "license: '\"{< -}\"'" 2>&1)
UPLOAD_RC=$?
set -e
if printf '%s\n' "$UPLOAD_RESULT" | grep -Eq 'UsageError|LicenseVerificationError|JSONDecodeError|ConstructorError'; then
    printf "LICENSE_UPLOAD_RC=%s\n" "$UPLOAD_RC"
    printf "LICENSE_UPLOAD_RESULT=%s\n" "$UPLOAD_RESULT"
    echo "ERROR=license_upload_obvious_failure"
    exit 8
fi
sleep "${LICENSE_APPLY_WAIT_SECONDS:-60}"
RUN_CHECK_RAW=$(confd_client license_run_check 2>&1 || true)
RUN_CHECK=$(printf '%s\n' "$RUN_CHECK_RAW" | awk '/^OK$/ {print; found=1; exit} END {if (!found) print ""}')
INFO=$(confd_client license_info)
DB_STATE=$(confd_client db_state db_name: Exasol 2>/dev/null | awk -F": " '/^[[:space:]]*state:/ {print $2; exit} /^[[:space:]]*running[[:space:]]*$/ {print "running"; exit}' || true)
printf "PLAY_ID=%s\n" "$PLAY_ID"
printf "LICENSE_UPLOAD_RC=%s\n" "$UPLOAD_RC"
printf "LICENSE_UPLOAD_RESULT=%s\n" "$UPLOAD_RESULT"
printf "LICENSE_RUN_CHECK=%s\n" "${RUN_CHECK:-$RUN_CHECK_RAW}"
printf "DB_STATE=%s\n" "$DB_STATE"
printf "%s\n" "$INFO" | awk '
    /^[[:space:]]+(license_id|company_name|distributor|expiration_date|max_session_limit|max_db_mem_size_in_gb|max_db_raw_data_size_in_gb|max_nodes_per_cluster|max_num_clusters):/ {
        gsub(/^[[:space:]]+/, "");
        print "LICENSE_INFO_" $0
    }'
if [ "$RUN_CHECK" != "OK" ]; then
    echo "ERROR=license_run_check_not_ok"
    exit 7
fi
REMOTE_APPLY
)"
if grep -q '^ERROR=' <<<"$APPLY_OUTPUT"; then
    err "remote apply failed"
    printf '%s\n' "$APPLY_OUTPUT" >&2
    exit 6
fi
printf '%s\n' "$APPLY_OUTPUT" > "$RUN_DIR/apply-${STAMP}.log"
chmod 600 "$RUN_DIR/apply-${STAMP}.log"

EVIDENCE_JSON="$RUN_DIR/operator-dashboard-license-evidence.json"
APPLY_OUTPUT="$APPLY_OUTPUT" EVIDENCE_JSON="$EVIDENCE_JSON" NODE="$NODE" \
LICENSE_FILE="$LICENSE_FILE" LICENSE_BYTES="$local_bytes" LICENSE_SHA="$local_sha" \
RUN_DIR="$RUN_DIR" STAMP="$STAMP" GIT_COMMIT="$(git rev-parse --short=12 HEAD 2>/dev/null || true)" \
OPERATOR="${XYZ_OPERATOR_EMAIL:-${USER:-unknown}}" python3 - <<'PY'
import glob
import json
import os
from datetime import datetime, timezone
from pathlib import Path

apply = {}
license_info = {}
for raw in os.environ["APPLY_OUTPUT"].splitlines():
    if raw.startswith("LICENSE_INFO_") and ":" in raw:
        key, value = raw[len("LICENSE_INFO_"):].split(":", 1)
        license_info[key.strip()] = value.strip()
    elif "=" in raw:
        key, value = raw.split("=", 1)
        apply[key.strip()] = value.strip()

backup = {}
for metadata_path in sorted(glob.glob(str(Path(os.environ["RUN_DIR"]) / "current-license" / "current-before-rotation-*.metadata.json"))):
    try:
        backup = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    except Exception:
        backup = {}

payload = {
    "published_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "operation": "exasol-personal-license-rotation",
    "operator": os.environ.get("OPERATOR", "unknown"),
    "node": os.environ["NODE"],
    "play_id": apply.get("PLAY_ID", ""),
    "license_id": license_info.get("license_id", ""),
    "company_name": license_info.get("company_name", ""),
    "distributor": license_info.get("distributor", ""),
    "expiration_date": license_info.get("expiration_date", ""),
    "max_session_limit": license_info.get("max_session_limit", ""),
    "max_db_mem_size_in_gb": license_info.get("max_db_mem_size_in_gb", ""),
    "max_db_raw_data_size_in_gb": license_info.get("max_db_raw_data_size_in_gb", ""),
    "max_nodes_per_cluster": license_info.get("max_nodes_per_cluster", ""),
    "max_num_clusters": license_info.get("max_num_clusters", ""),
    "license_run_check": apply.get("LICENSE_RUN_CHECK", ""),
    "db_state": apply.get("DB_STATE", ""),
    "replacement_license_basename": Path(os.environ["LICENSE_FILE"]).name,
    "replacement_license_bytes": int(os.environ["LICENSE_BYTES"]),
    "replacement_license_sha256": os.environ["LICENSE_SHA"],
    "backup_license_basename": Path(str(backup.get("local_license_file", ""))).name,
    "backup_license_sha256": backup.get("sha256", ""),
    "git_commit": os.environ.get("GIT_COMMIT", ""),
    "notes": [
        "Non-secret evidence only; signed license bodies are not included.",
        "Dashboard activity log reads this object through xyz-pilot-user-mgmt license_evidence.",
    ],
}
Path(os.environ["EVIDENCE_JSON"]).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
chmod 600 "$EVIDENCE_JSON"

if [[ "$LICENSE_EVIDENCE_PUBLISH" != "0" && -n "$LICENSE_EVIDENCE_BUCKET" ]]; then
    if command -v aws >/dev/null 2>&1; then
        if AWS_PROFILE="${AWS_PROFILE:-default}" AWS_REGION="${AWS_REGION:-<AWS_REGION>}" aws s3 cp \
            "$EVIDENCE_JSON" "s3://${LICENSE_EVIDENCE_BUCKET}/${LICENSE_EVIDENCE_KEY}" \
            --content-type application/json \
            --cache-control "no-cache, no-store, must-revalidate" \
            --no-cli-pager >/dev/null; then
            log "Published non-secret Operator Dashboard license evidence to s3://${LICENSE_EVIDENCE_BUCKET}/${LICENSE_EVIDENCE_KEY}"
        else
            warn "Could not publish Operator Dashboard license evidence to s3://${LICENSE_EVIDENCE_BUCKET}/${LICENSE_EVIDENCE_KEY}; local evidence retained at $EVIDENCE_JSON"
        fi
    else
        warn "aws CLI not found; local Operator Dashboard license evidence retained at $EVIDENCE_JSON"
    fi
fi

cat <<EOF

License upload completed.

Evidence directory:
  $RUN_DIR

Operator Dashboard evidence:
  $EVIDENCE_JSON

Staged file metadata:
  bytes:  $local_bytes
  sha256: $local_sha

Apply summary:
$(printf '%s\n' "$APPLY_OUTPUT" | sed 's/^/  /')

Next validation:
  1. Refresh Grafana /license/export?refresh=true.
  2. Confirm Contract/Limits fields.
  3. Re-run XYZ_STRESS if validating session-limit relief.
EOF
