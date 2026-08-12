#!/usr/bin/env bash
#
# <PILOT_NAME> Phase 02b app-stack re-host helper.
#
# Purpose:
#   Relay the already staged legacy app-stack archives from the legacy account
#   into the target archive bucket, then prepare the target EC2 host through SSM.
#
# Safety defaults:
#   - Plan-only unless --execute is passed.
#   - Does not start cloudflared tunnel services.
#   - Does not print or inspect secret values.
#   - Starts non-cloudflared app services only when --start-apps is also passed.
#
set -euo pipefail

SOURCE_PROFILE="${SOURCE_PROFILE:-<OPS_USER_1>Degen}"
SOURCE_REGION="${SOURCE_REGION:-<AWS_REGION>}"
SOURCE_BUCKET="${SOURCE_BUCKET:-xyz-pilot-stack-transfer-<SOURCE_AWS_ACCOUNT_ID>}"

TARGET_PROFILE="${TARGET_PROFILE:-default}"
TARGET_REGION="${TARGET_REGION:-<AWS_REGION>}"
TARGET_INSTANCE_ID="${TARGET_INSTANCE_ID:-<EC2_INSTANCE_ID>}"
TARGET_BUCKET="${TARGET_BUCKET:-exasol-ddbc4cd5-s3-archive-f14b725b-e3d0-94b6-2929-605024c767ca}"
TARGET_PREFIX="${TARGET_PREFIX:-transfer}"

TARGET_EXASOL_HOST="${TARGET_EXASOL_HOST:-host.docker.internal}"
TARGET_EXASOL_PORT="${TARGET_EXASOL_PORT:-8563}"
WORK_DIR="${WORK_DIR:-/tmp/xyz-pilot-phase02b}"

EXECUTE=0
START_APPS=0
WAIT=0

usage() {
  cat <<'USAGE'
<PILOT_NAME> Phase 02b app-stack re-host helper.

Purpose:
  Relay the already staged legacy app-stack archives from the legacy account
  into the target archive bucket, then prepare the target EC2 host through SSM.

Safety defaults:
  - Plan-only unless --execute is passed.
  - Does not start cloudflared tunnel services.
  - Does not print or inspect secret values.
  - Starts non-cloudflared app services only when --start-apps is also passed.

Usage:
  scripts/xyz_pilot_phase02b_rehost_app_stack.sh [--execute] [--start-apps] [--wait]

Environment overrides:
  SOURCE_PROFILE=<OPS_USER_1>Degen
  SOURCE_REGION=<AWS_REGION>
  SOURCE_BUCKET=xyz-pilot-stack-transfer-<SOURCE_AWS_ACCOUNT_ID>
  TARGET_PROFILE=default
  TARGET_REGION=<AWS_REGION>
  TARGET_INSTANCE_ID=<EC2_INSTANCE_ID>
  TARGET_BUCKET=exasol-ddbc4cd5-s3-archive-f14b725b-e3d0-94b6-2929-605024c767ca
  TARGET_PREFIX=transfer
  TARGET_EXASOL_HOST=host.docker.internal
  TARGET_EXASOL_PORT=8563

Recommended Friday-safe dry run:
  scripts/xyz_pilot_phase02b_rehost_app_stack.sh

Relay + extract + patch + compose-config validation:
  scripts/xyz_pilot_phase02b_rehost_app_stack.sh --execute --wait

Same, then start only non-cloudflared app services:
  scripts/xyz_pilot_phase02b_rehost_app_stack.sh --execute --start-apps --wait
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --execute) EXECUTE=1; shift ;;
    --start-apps) START_APPS=1; shift ;;
    --wait) WAIT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown flag: $1" >&2; usage >&2; exit 2 ;;
  esac
done

log() {
  printf '>> %s\n' "$*" >&2
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 2
  }
}

run_or_print() {
  if [ "$EXECUTE" = "1" ]; then
    "$@"
  else
    printf '+'
    printf ' %q' "$@"
    printf '\n'
  fi
}

make_remote_script() {
  local remote_script="$1"
  cat > "$remote_script" <<REMOTE
#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="/tmp/xyz-phase02b"
TARGET_BUCKET="${TARGET_BUCKET}"
TARGET_PREFIX="${TARGET_PREFIX}"
TARGET_REGION="${TARGET_REGION}"
TARGET_EXASOL_HOST="${TARGET_EXASOL_HOST}"
TARGET_EXASOL_PORT="${TARGET_EXASOL_PORT}"
START_APPS="${START_APPS}"

log() {
  printf 'phase02b-target: %s\\n' "\$*"
}

upsert_env() {
  local file="\$1"
  local key="\$2"
  local value="\$3"

  if [ ! -f "\$file" ]; then
    return 0
  fi

  sudo cp -n "\$file" "\${file}.pre-phase02b" || true
  if sudo grep -qE "^\${key}=" "\$file"; then
    sudo sed -i "s|^\${key}=.*|\${key}=\${value}|" "\$file"
  else
    printf '%s=%s\\n' "\$key" "\$value" | sudo tee -a "\$file" >/dev/null
  fi
}

log "creating workspace"
sudo mkdir -p "\$WORK_DIR"
sudo chown "\${USER:-ubuntu}:\${USER:-ubuntu}" "\$WORK_DIR" || true

log "downloading staged archives from target bucket"
aws s3 cp "s3://\${TARGET_BUCKET}/\${TARGET_PREFIX}/xyz-stack-transfer.tar.gz" "\$WORK_DIR/xyz-stack-transfer.tar.gz" --region "\$TARGET_REGION"
aws s3 cp "s3://\${TARGET_BUCKET}/\${TARGET_PREFIX}/xyz-cloudflared.tar.gz" "\$WORK_DIR/xyz-cloudflared.tar.gz" --region "\$TARGET_REGION"

log "extracting /opt/xyz-* app stack"
sudo tar xzf "\$WORK_DIR/xyz-stack-transfer.tar.gz" -C /

log "extracting cloudflared credentials but not starting tunnel services"
sudo tar xzf "\$WORK_DIR/xyz-cloudflared.tar.gz" -C /home/ubuntu
sudo chown -R ubuntu:ubuntu /home/ubuntu/.cloudflared || true
sudo chmod 700 /home/ubuntu/.cloudflared || true

log "patching target-local runtime env values"
for env_file in \
  /opt/xyz-chatbot/.env \
  /opt/xyz-chatbot/mcp-internal.env \
  /opt/xyz-mcp/.env \
  /opt/xyz-qbr/.env \
  /opt/xyz-grafana/.env
do
  upsert_env "\$env_file" EXASOL_HOST "\$TARGET_EXASOL_HOST"
  upsert_env "\$env_file" EXASOL_PORT "\$TARGET_EXASOL_PORT"
  upsert_env "\$env_file" AWS_REGION "\$TARGET_REGION"
  upsert_env "\$env_file" AWS_DEFAULT_REGION "\$TARGET_REGION"
done

log "validating compose files and host-gateway hints"
for compose_dir in /opt/xyz-chatbot /opt/xyz-mcp /opt/xyz-qbr /opt/xyz-grafana
do
  if [ ! -f "\$compose_dir/docker-compose.yml" ] && [ ! -f "\$compose_dir/compose.yml" ]; then
    log "skip missing compose dir: \$compose_dir"
    continue
  fi

  log "compose config: \$compose_dir"
  (
    cd "\$compose_dir"
    sudo docker compose config >/tmp/"\$(basename "\$compose_dir")-compose-config.yml"
    if ! sudo docker compose config | grep -q "host.docker.internal"; then
      log "WARN: \$compose_dir does not show host.docker.internal in docker compose config"
    fi

    if [ "\$START_APPS" = "1" ]; then
      services="\$(sudo docker compose config --services | awk '!/cloudflared|tunnel/' | xargs || true)"
      if [ -n "\$services" ]; then
        log "starting non-cloudflared services in \$compose_dir: \$services"
        sudo docker compose up -d --build \$services
        sudo docker compose ps
      else
        log "no non-cloudflared services found in \$compose_dir"
      fi
    else
      log "START_APPS=0, not starting services in \$compose_dir"
    fi
  )
done

log "private host-local smoke probes"
sudo ss -tlnp | grep -E ':(3000|8501|8563|9090)\\b' || true

log "Phase 02b target prep complete. Cloudflared tunnel services remain stopped unless started separately in Phase 03."
REMOTE
}

make_ssm_parameters() {
  local remote_script="$1"
  local params_file="$2"
  python3 - "$remote_script" "$params_file" <<'PY'
import json
import pathlib
import sys

remote_script = pathlib.Path(sys.argv[1]).read_text()
params_file = pathlib.Path(sys.argv[2])
params_file.write_text(json.dumps({
    "commands": [
        "cat > /tmp/xyz-phase02b-target.sh <<'REMOTE_PHASE02B_SCRIPT'\n"
        + remote_script
        + "\nREMOTE_PHASE02B_SCRIPT",
        "chmod 700 /tmp/xyz-phase02b-target.sh",
        "bash /tmp/xyz-phase02b-target.sh",
    ]
}, indent=2))
PY
}

relay_archive() {
  local key="$1"
  local local_path="${WORK_DIR}/${key}"
  run_or_print mkdir -p "$WORK_DIR"
  run_or_print aws --profile "$SOURCE_PROFILE" s3 cp "s3://${SOURCE_BUCKET}/${key}" "$local_path" --region "$SOURCE_REGION"
  run_or_print aws --profile "$TARGET_PROFILE" s3 cp "$local_path" "s3://${TARGET_BUCKET}/${TARGET_PREFIX}/${key}" --region "$TARGET_REGION"
}

require_cmd aws
require_cmd python3

log "source: ${SOURCE_PROFILE} ${SOURCE_REGION} s3://${SOURCE_BUCKET}"
log "target: ${TARGET_PROFILE} ${TARGET_REGION} ${TARGET_INSTANCE_ID} s3://${TARGET_BUCKET}/${TARGET_PREFIX}"
log "target env patch: EXASOL_HOST=${TARGET_EXASOL_HOST}, EXASOL_PORT=${TARGET_EXASOL_PORT}, AWS_REGION=${TARGET_REGION}"

relay_archive "xyz-stack-transfer.tar.gz"
relay_archive "xyz-cloudflared.tar.gz"

REMOTE_SCRIPT="${WORK_DIR}/xyz-phase02b-target.sh"
PARAMS_FILE="${WORK_DIR}/xyz-phase02b-ssm-parameters.json"
if [ "$EXECUTE" = "1" ]; then
  run_or_print mkdir -p "$WORK_DIR"
else
  mkdir -p "$WORK_DIR"
fi
make_remote_script "$REMOTE_SCRIPT"
make_ssm_parameters "$REMOTE_SCRIPT" "$PARAMS_FILE"

if [ "$EXECUTE" = "1" ]; then
  log "sending SSM command to target"
  COMMAND_ID="$(aws --profile "$TARGET_PROFILE" ssm send-command \
    --region "$TARGET_REGION" \
    --instance-ids "$TARGET_INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --comment "<PILOT_NAME> Phase 02b app-stack rehost prep" \
    --parameters "file://${PARAMS_FILE}" \
    --query 'Command.CommandId' \
    --output text)"
  log "SSM command id: ${COMMAND_ID}"

  if [ "$WAIT" = "1" ]; then
    log "waiting for SSM command completion"
    aws --profile "$TARGET_PROFILE" ssm wait command-executed \
      --region "$TARGET_REGION" \
      --command-id "$COMMAND_ID" \
      --instance-id "$TARGET_INSTANCE_ID"
    aws --profile "$TARGET_PROFILE" ssm get-command-invocation \
      --region "$TARGET_REGION" \
      --command-id "$COMMAND_ID" \
      --instance-id "$TARGET_INSTANCE_ID" \
      --query '{Status:Status,ResponseCode:ResponseCode,StandardOutput:StandardOutputContent,StandardError:StandardErrorContent}' \
      --output json
  fi
else
  log "plan-only mode; SSM parameters written locally for inspection: ${PARAMS_FILE}"
  printf '+ aws --profile %q ssm send-command --region %q --instance-ids %q --document-name AWS-RunShellScript --parameters file://%q\n' \
    "$TARGET_PROFILE" "$TARGET_REGION" "$TARGET_INSTANCE_ID" "$PARAMS_FILE"
fi
