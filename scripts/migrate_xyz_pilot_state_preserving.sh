#!/usr/bin/env bash
# State-preserving cross-account migration for <PILOT_NAME>.
#
# This creates a target-account copy of the stopped source EC2 root and data
# volumes, then recreates the minimal EC2/network scaffolding around those
# copied volumes. It does not delete or disable the source environment.
set -euo pipefail

# <PILOT_NAME> state-preserving migration (XYZ-PILOT-225 account + XYZ-PILOT-226 region).
# Adapted from <GITHUB_OWNER>/<PROJECT_SLUG> migrate_e2e_workshop_state_preserving.sh.
# KEY DELTA vs the Codex original: this is CROSS-REGION (<AWS_REGION> -> <AWS_REGION>),
# so REGION is split into SOURCE_REGION/TARGET_REGION and snapshot copies cross regions.
#
# STATUS (2026-06-11): VALIDATED, run-ready for Phase 1-2 — NOT yet executed for real.
# Resumable + idempotent (records state.env, skips done steps), safe to run detached.
# Source is never modified by this script.
#   [done] SOURCE_SECURITY_GROUP_ID confirmed = <SECURITY_GROUP_ID> (<EXASOL_DEPLOYMENT_ID>-sg).
#   [done] Elastic-IP allocation wired (allocate_target_eip; ALLOCATE_EIP=1).
#   [done] Step-0 DRY_RUN preflight passed CLEAN (0 failures) 2026-06-11.
#   [Phase 9] Per-resource exa:DataClassification (XYZ-PILOT-247: personal-edition/networking/
#     Cognito = internal, sys-secret = restricted) is authoritatively enforced + verified via
#     scripts/aws_tag_inventory.sh; inline tags here are the create-time starting point
#     (snapshots/KMS stay confidential — migration artifacts holding DB state).
#
# Step 0 (validate only, creates nothing):
#   DRY_RUN=1 bash scripts/migrate_xyz_pilot_state_preserving.sh
# Real run (after operator go; detachable so a session/limit halt can't break it):
#   nohup bash scripts/migrate_xyz_pilot_state_preserving.sh > /tmp/xyz-pilot-migration.log 2>&1 &
SOURCE_PROFILE="${SOURCE_PROFILE:-<SOURCE_AWS_PROFILE>}"      # -> source account <SOURCE_AWS_ACCOUNT_ID>
TARGET_PROFILE="${TARGET_PROFILE:-default}"          # -> target account <AWS_ACCOUNT_ID>
SOURCE_REGION="${SOURCE_REGION:-<AWS_REGION>}"
TARGET_REGION="${TARGET_REGION:-<AWS_REGION>}"
SOURCE_ACCOUNT="${SOURCE_ACCOUNT:-<SOURCE_AWS_ACCOUNT_ID>}"
TARGET_ACCOUNT="${TARGET_ACCOUNT:-<AWS_ACCOUNT_ID>}"
DEPLOYMENT_ID="${DEPLOYMENT_ID:-<EXASOL_DEPLOYMENT_ID>}"     # source Exasol launcher deployment id (reused for target, per Codex)
SOURCE_INSTANCE_ID="${SOURCE_INSTANCE_ID:-<EC2_INSTANCE_ID>}"
SOURCE_ROOT_VOLUME_ID="${SOURCE_ROOT_VOLUME_ID:-<EBS_VOLUME_ID>}"   # /dev/sda1, unencrypted
SOURCE_DATA_VOLUME_ID="${SOURCE_DATA_VOLUME_ID:-<EBS_VOLUME_ID>}"   # /dev/sdf, encrypted, holds Exasol DB + external semantic layer
SOURCE_SECURITY_GROUP_ID="${SOURCE_SECURITY_GROUP_ID:-<SECURITY_GROUP_ID>}"  # <EXASOL_DEPLOYMENT_ID>-sg (confirmed step a)
TARGET_AZ="${TARGET_AZ:-<AWS_REGION>a}"
INSTANCE_TYPE="${INSTANCE_TYPE:-r6i.xlarge}"
VPC_CIDR="${VPC_CIDR:-10.40.0.0/16}"                  # collision-checked clean in target <AWS_REGION>
SUBNET_CIDR="${SUBNET_CIDR:-10.40.1.0/24}"
PRIVATE_IP="${PRIVATE_IP:-10.40.1.11}"
KEY_NAME="${KEY_NAME:-xyz-pilot-${TARGET_REGION}-key}"          # target key-pair name (import as)
SOURCE_KEY_NAME="${SOURCE_KEY_NAME:-<EXASOL_DEPLOYMENT_ID>-key}"      # source key-pair to export public material from
ALLOCATE_EIP="${ALLOCATE_EIP:-1}"                     # allocate a fresh Elastic IP in TARGET_REGION (decision #3)
DRY_RUN="${DRY_RUN:-0}"                                # 1 = Step-0 preflight only: validate, create/delete NOTHING
EXA_ENVIRONMENT="${EXA_ENVIRONMENT:-staging}"          # exa:Environment tag value; same-region DEV move overrides to 'development'

MIGRATION_RUN="${MIGRATION_RUN:-$(date -u +%Y%m%dT%H%M%SZ)}"
STATE_DIR="${STATE_DIR:-/tmp/xyz-pilot-migration-${MIGRATION_RUN}}"
STATE_FILE="${STATE_FILE:-${STATE_DIR}/state.env}"

mkdir -p "${STATE_DIR}"
chmod 700 "${STATE_DIR}"

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

die() {
  log "ERROR: $*"
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

aws_source() {
  aws --profile "${SOURCE_PROFILE}" --region "${SOURCE_REGION}" "$@" --no-cli-pager
}

aws_target() {
  aws --profile "${TARGET_PROFILE}" --region "${TARGET_REGION}" "$@" --no-cli-pager
}

aws_target_global() {
  aws --profile "${TARGET_PROFILE}" "$@" --no-cli-pager
}

save_var() {
  local key="$1"
  local value="$2"
  local tmp="${STATE_FILE}.tmp"
  touch "${STATE_FILE}"
  chmod 600 "${STATE_FILE}"
  grep -v "^${key}=" "${STATE_FILE}" >"${tmp}" || true
  printf '%s=%q\n' "${key}" "${value}" >>"${tmp}"
  mv "${tmp}" "${STATE_FILE}"
}

load_state() {
  if [ -f "${STATE_FILE}" ]; then
    # shellcheck disable=SC1090
    source "${STATE_FILE}"
  fi
}

ec2_tags() {
  local name="$1"
  local workload="$2"
  local classification="$3"
  shift 3
  printf '%s\0' \
    "Key=Name,Value=${name}" \
    "Key=Project,Value=exasol-personal" \
    "Key=Deployment,Value=${DEPLOYMENT_ID}" \
    "Key=ManagedBy,Value=aws-cli-state-migration" \
    "Key=CreatedAt,Value=${MIGRATION_RUN}" \
    "Key=MigrationRun,Value=${MIGRATION_RUN}" \
    "Key=MigratedFromAccount,Value=${SOURCE_ACCOUNT}" \
    "Key=Owner,Value=<AWS_ARN>${TARGET_ACCOUNT}:user/<OPERATOR_USER>" \
    "Key=exa:Department,Value=SE" \
    "Key=exa:Environment,Value=${EXA_ENVIRONMENT}" \
    "Key=exa:CostCenter,Value=20060" \
    "Key=exa:Owner,Value=<OPERATOR_USER>" \
    "Key=exa:Project,Value=XYZPILOT" \
    "Key=exa:customer,Value=XYZ" \
    "Key=exa:Workload,Value=${workload}" \
    "Key=exa:DataClassification,Value=${classification}" \
    "$@"
}

tag_ec2_target() {
  local resource_id="$1"
  shift
  local -a tags
  while IFS= read -r -d '' item; do
    tags+=("${item}")
  done < <(ec2_tags "$@")
  aws_target ec2 create-tags --resources "${resource_id}" --tags "${tags[@]}" >/dev/null
}

tag_ec2_source_snapshot() {
  local resource_id="$1"
  local name="$2"
  local role="$3"
  aws_source ec2 create-tags \
    --resources "${resource_id}" \
    --tags \
      "Key=Name,Value=${name}" \
      "Key=Project,Value=exasol-personal" \
      "Key=Deployment,Value=${DEPLOYMENT_ID}" \
      "Key=ManagedBy,Value=aws-cli-state-migration" \
      "Key=CreatedAt,Value=${MIGRATION_RUN}" \
      "Key=MigrationRun,Value=${MIGRATION_RUN}" \
      "Key=MigratedToAccount,Value=${TARGET_ACCOUNT}" \
      "Key=Role,Value=${role}" \
      "Key=exa:Department,Value=SE" \
      "Key=exa:Environment,Value=${EXA_ENVIRONMENT}" \
      "Key=exa:CostCenter,Value=20060" \
      "Key=exa:Owner,Value=<OPERATOR_USER>" \
      "Key=exa:Project,Value=XYZPILOT" \
      "Key=exa:customer,Value=XYZ" \
      "Key=exa:Workload,Value=personal-edition" \
      "Key=exa:DataClassification,Value=confidential" >/dev/null
}

tag_kms_source() {
  local key_id="$1"
  aws_source kms tag-resource \
    --key-id "${key_id}" \
    --tags \
      TagKey=Name,TagValue="${DEPLOYMENT_ID}-migration-snapshot-share-key" \
      TagKey=Project,TagValue=exasol-personal \
      TagKey=Deployment,TagValue="${DEPLOYMENT_ID}" \
      TagKey=ManagedBy,TagValue=aws-cli-state-migration \
      TagKey=MigrationRun,TagValue="${MIGRATION_RUN}" \
      TagKey=exa:Department,TagValue=SE \
      TagKey=exa:Environment,TagValue=${EXA_ENVIRONMENT} \
      TagKey=exa:CostCenter,TagValue=20060 \
      TagKey=exa:Owner,TagValue=<OPERATOR_USER> \
      TagKey=exa:Project,TagValue=XYZPILOT \
      TagKey=exa:customer,TagValue=XYZ \
      TagKey=exa:Workload,TagValue=personal-edition \
      TagKey=exa:DataClassification,TagValue=confidential >/dev/null
}

wait_snapshot() {
  local profile_label="$1"
  local snapshot_id="$2"
  log "Waiting for ${profile_label} snapshot ${snapshot_id} to complete"
  local last_progress=""
  local state=""
  local progress=""
  local message=""
  while true; do
    local status
    if [ "${profile_label}" = "source" ]; then
      status="$(aws_source ec2 describe-snapshots --snapshot-ids "${snapshot_id}" \
        --query 'Snapshots[0].[State,Progress,StateMessage]' \
        --output text)"
    else
      status="$(aws_target ec2 describe-snapshots --snapshot-ids "${snapshot_id}" \
        --query 'Snapshots[0].[State,Progress,StateMessage]' \
        --output text)"
    fi
    read -r state progress message <<<"${status}"
    if [ "${state}" = "completed" ]; then
      break
    fi
    if [ "${state}" = "error" ]; then
      die "Snapshot ${snapshot_id} failed: ${message:-no state message}"
    fi
    if [ "${progress}" != "${last_progress}" ]; then
      log "Snapshot ${snapshot_id} state=${state} progress=${progress}"
      last_progress="${progress}"
    fi
    sleep 60
  done
  if [ "${profile_label}" = "source" ]; then
    aws_source ec2 describe-snapshots --snapshot-ids "${snapshot_id}" \
      --query 'Snapshots[0].{SnapshotId:SnapshotId,State:State,Progress:Progress,StateMessage:StateMessage}' \
      --output json
  else
    aws_target ec2 describe-snapshots --snapshot-ids "${snapshot_id}" \
      --query 'Snapshots[0].{SnapshotId:SnapshotId,State:State,Progress:Progress,StateMessage:StateMessage}' \
      --output json
  fi
}

ensure_identity() {
  local source_id
  local target_id
  source_id="$(aws --profile "${SOURCE_PROFILE}" sts get-caller-identity --query Account --output text --no-cli-pager)"
  target_id="$(aws --profile "${TARGET_PROFILE}" sts get-caller-identity --query Account --output text --no-cli-pager)"
  [ "${source_id}" = "${SOURCE_ACCOUNT}" ] || die "Source profile ${SOURCE_PROFILE} resolves to ${source_id}, expected ${SOURCE_ACCOUNT}"
  [ "${target_id}" = "${TARGET_ACCOUNT}" ] || die "Target profile ${TARGET_PROFILE} resolves to ${target_id}, expected ${TARGET_ACCOUNT}"
  log "AWS identities verified: source=${source_id}, target=${target_id}, region=${SOURCE_REGION}->${TARGET_REGION}"
}

ensure_source_stopped() {
  local state
  state="$(aws_source ec2 describe-instances \
    --instance-ids "${SOURCE_INSTANCE_ID}" \
    --query 'Reservations[0].Instances[0].State.Name' \
    --output text)"
  [ "${state}" = "stopped" ] || die "Source instance ${SOURCE_INSTANCE_ID} is ${state}; stop it cleanly before volume snapshots"
  log "Source instance ${SOURCE_INSTANCE_ID} is stopped"
}

ensure_no_target_collision() {
  local collisions
  collisions="$(aws_target ec2 describe-instances \
    --filters "Name=tag:Deployment,Values=${DEPLOYMENT_ID}" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].InstanceId' \
    --output text)"
  [ -z "${collisions}" ] || die "Target already has active instances for ${DEPLOYMENT_ID}: ${collisions}"
  log "No active target EC2 instance collision for ${DEPLOYMENT_ID}"
}

ensure_source_share_key() {
  load_state
  if [ -n "${SOURCE_SHARE_KEY_ARN:-}" ]; then
    log "Using existing source migration KMS key ${SOURCE_SHARE_KEY_ARN}"
    return
  fi

  local alias_name="alias/${DEPLOYMENT_ID}-migration-${MIGRATION_RUN}"
  local policy_file="${STATE_DIR}/source-kms-policy.json"
  python3 - "${policy_file}" "${SOURCE_ACCOUNT}" "${TARGET_ACCOUNT}" <<'PY'
import json
import sys

path, source_account, target_account = sys.argv[1:4]
policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "EnableSourceAccountIamPermissions",
            "Effect": "Allow",
            "Principal": {"AWS": f"<AWS_ARN>{source_account}:root"},
            "Action": "kms:*",
            "Resource": "*",
        },
        {
            "Sid": "AllowTargetAccountUseForEbsSnapshotCopy",
            "Effect": "Allow",
            "Principal": {"AWS": f"<AWS_ARN>{target_account}:root"},
            "Action": [
                "kms:DescribeKey",
                "kms:Decrypt",
                "kms:ReEncrypt*",
                "kms:GenerateDataKey",
                "kms:GenerateDataKeyWithoutPlaintext",
            ],
            "Resource": "*",
        },
        {
            "Sid": "AllowTargetAccountGrantForAwsResources",
            "Effect": "Allow",
            "Principal": {"AWS": f"<AWS_ARN>{target_account}:root"},
            "Action": ["kms:CreateGrant", "kms:ListGrants", "kms:RevokeGrant"],
            "Resource": "*",
            "Condition": {"Bool": {"kms:GrantIsForAWSResource": True}},
        },
    ],
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(policy, handle, indent=2, sort_keys=True)
PY

  SOURCE_SHARE_KEY_ARN="$(aws_source kms create-key \
    --description "Temporary EBS snapshot sharing key for ${DEPLOYMENT_ID} migration ${MIGRATION_RUN}" \
    --policy "file://${policy_file}" \
    --query KeyMetadata.Arn \
    --output text)"
  save_var SOURCE_SHARE_KEY_ARN "${SOURCE_SHARE_KEY_ARN}"
  aws_source kms create-alias --alias-name "${alias_name}" --target-key-id "${SOURCE_SHARE_KEY_ARN}" >/dev/null
  tag_kms_source "${SOURCE_SHARE_KEY_ARN}"
  log "Created source migration KMS key ${SOURCE_SHARE_KEY_ARN}"
}

create_source_snapshots() {
  load_state
  if [ -z "${SOURCE_ROOT_SNAPSHOT_ID:-}" ]; then
    SOURCE_ROOT_SNAPSHOT_ID="$(aws_source ec2 create-snapshot \
      --volume-id "${SOURCE_ROOT_VOLUME_ID}" \
      --description "State-preserving migration root snapshot for ${DEPLOYMENT_ID} ${MIGRATION_RUN}" \
      --query SnapshotId \
      --output text)"
    save_var SOURCE_ROOT_SNAPSHOT_ID "${SOURCE_ROOT_SNAPSHOT_ID}"
    tag_ec2_source_snapshot "${SOURCE_ROOT_SNAPSHOT_ID}" "${DEPLOYMENT_ID}-root-migration-${MIGRATION_RUN}" root
    log "Created source root snapshot ${SOURCE_ROOT_SNAPSHOT_ID}"
  fi

  if [ -z "${SOURCE_DATA_ORIGINAL_SNAPSHOT_ID:-}" ]; then
    SOURCE_DATA_ORIGINAL_SNAPSHOT_ID="$(aws_source ec2 create-snapshot \
      --volume-id "${SOURCE_DATA_VOLUME_ID}" \
      --description "State-preserving migration data snapshot for ${DEPLOYMENT_ID} ${MIGRATION_RUN}" \
      --query SnapshotId \
      --output text)"
    save_var SOURCE_DATA_ORIGINAL_SNAPSHOT_ID "${SOURCE_DATA_ORIGINAL_SNAPSHOT_ID}"
    tag_ec2_source_snapshot "${SOURCE_DATA_ORIGINAL_SNAPSHOT_ID}" "${DEPLOYMENT_ID}-data-original-migration-${MIGRATION_RUN}" data-original
    log "Created source data snapshot ${SOURCE_DATA_ORIGINAL_SNAPSHOT_ID}"
  fi

  wait_snapshot source "${SOURCE_ROOT_SNAPSHOT_ID}"
  wait_snapshot source "${SOURCE_DATA_ORIGINAL_SNAPSHOT_ID}"
}

copy_source_data_to_shareable_key() {
  load_state
  if [ -n "${SOURCE_DATA_SHAREABLE_SNAPSHOT_ID:-}" ]; then
    log "Using existing source shareable data snapshot ${SOURCE_DATA_SHAREABLE_SNAPSHOT_ID}"
    return
  fi
  SOURCE_DATA_SHAREABLE_SNAPSHOT_ID="$(aws_source ec2 copy-snapshot \
    --source-region "${SOURCE_REGION}" \
    --source-snapshot-id "${SOURCE_DATA_ORIGINAL_SNAPSHOT_ID}" \
    --encrypted \
    --kms-key-id "${SOURCE_SHARE_KEY_ARN}" \
    --description "Shareable CMK copy of ${DEPLOYMENT_ID} data snapshot for target ${TARGET_ACCOUNT} ${MIGRATION_RUN}" \
    --query SnapshotId \
    --output text)"
  save_var SOURCE_DATA_SHAREABLE_SNAPSHOT_ID "${SOURCE_DATA_SHAREABLE_SNAPSHOT_ID}"
  tag_ec2_source_snapshot "${SOURCE_DATA_SHAREABLE_SNAPSHOT_ID}" "${DEPLOYMENT_ID}-data-shareable-migration-${MIGRATION_RUN}" data-shareable
  log "Created source CMK-encrypted data snapshot copy ${SOURCE_DATA_SHAREABLE_SNAPSHOT_ID}"
  wait_snapshot source "${SOURCE_DATA_SHAREABLE_SNAPSHOT_ID}"
}

share_source_snapshots() {
  aws_source ec2 modify-snapshot-attribute \
    --snapshot-id "${SOURCE_ROOT_SNAPSHOT_ID}" \
    --attribute createVolumePermission \
    --operation-type add \
    --user-ids "${TARGET_ACCOUNT}" >/dev/null
  aws_source ec2 modify-snapshot-attribute \
    --snapshot-id "${SOURCE_DATA_SHAREABLE_SNAPSHOT_ID}" \
    --attribute createVolumePermission \
    --operation-type add \
    --user-ids "${TARGET_ACCOUNT}" >/dev/null
  log "Shared source snapshots with target account ${TARGET_ACCOUNT}"
}

copy_snapshots_to_target() {
  load_state
  if [ -z "${TARGET_ROOT_SNAPSHOT_ID:-}" ]; then
    TARGET_ROOT_SNAPSHOT_ID="$(aws_target ec2 copy-snapshot \
      --source-region "${SOURCE_REGION}" \
      --source-snapshot-id "${SOURCE_ROOT_SNAPSHOT_ID}" \
      --encrypted \
      --description "Target encrypted root snapshot copy for ${DEPLOYMENT_ID} ${MIGRATION_RUN}" \
      --query SnapshotId \
      --output text)"
    save_var TARGET_ROOT_SNAPSHOT_ID "${TARGET_ROOT_SNAPSHOT_ID}"
    tag_ec2_target "${TARGET_ROOT_SNAPSHOT_ID}" "${DEPLOYMENT_ID}-root-target-migration-${MIGRATION_RUN}" personal-edition confidential "Key=Role,Value=root"
    log "Started target root snapshot copy ${TARGET_ROOT_SNAPSHOT_ID}"
  fi

  if [ -z "${TARGET_DATA_SNAPSHOT_ID:-}" ]; then
    TARGET_DATA_SNAPSHOT_ID="$(aws_target ec2 copy-snapshot \
      --source-region "${SOURCE_REGION}" \
      --source-snapshot-id "${SOURCE_DATA_SHAREABLE_SNAPSHOT_ID}" \
      --encrypted \
      --description "Target encrypted data snapshot copy for ${DEPLOYMENT_ID} ${MIGRATION_RUN}" \
      --query SnapshotId \
      --output text)"
    save_var TARGET_DATA_SNAPSHOT_ID "${TARGET_DATA_SNAPSHOT_ID}"
    tag_ec2_target "${TARGET_DATA_SNAPSHOT_ID}" "${DEPLOYMENT_ID}-data-target-migration-${MIGRATION_RUN}" personal-edition confidential "Key=Role,Value=data"
    log "Started target data snapshot copy ${TARGET_DATA_SNAPSHOT_ID}"
  fi

  wait_snapshot target "${TARGET_ROOT_SNAPSHOT_ID}"
  wait_snapshot target "${TARGET_DATA_SNAPSHOT_ID}"
}

create_target_network() {
  load_state
  if [ -z "${TARGET_VPC_ID:-}" ]; then
    TARGET_VPC_ID="$(aws_target ec2 create-vpc --cidr-block "${VPC_CIDR}" --query Vpc.VpcId --output text)"
    save_var TARGET_VPC_ID "${TARGET_VPC_ID}"
    tag_ec2_target "${TARGET_VPC_ID}" "${DEPLOYMENT_ID}-vpc" networking public
    aws_target ec2 modify-vpc-attribute --vpc-id "${TARGET_VPC_ID}" --enable-dns-hostnames '{"Value":true}' >/dev/null
    aws_target ec2 modify-vpc-attribute --vpc-id "${TARGET_VPC_ID}" --enable-dns-support '{"Value":true}' >/dev/null
    log "Created target VPC ${TARGET_VPC_ID}"
  fi

  if [ -z "${TARGET_SUBNET_ID:-}" ]; then
    TARGET_SUBNET_ID="$(aws_target ec2 create-subnet \
      --vpc-id "${TARGET_VPC_ID}" \
      --cidr-block "${SUBNET_CIDR}" \
      --availability-zone "${TARGET_AZ}" \
      --query Subnet.SubnetId \
      --output text)"
    save_var TARGET_SUBNET_ID "${TARGET_SUBNET_ID}"
    tag_ec2_target "${TARGET_SUBNET_ID}" "${DEPLOYMENT_ID}-subnet" networking public
    aws_target ec2 modify-subnet-attribute --subnet-id "${TARGET_SUBNET_ID}" --map-public-ip-on-launch >/dev/null
    log "Created target subnet ${TARGET_SUBNET_ID} in ${TARGET_AZ}"
  fi

  if [ -z "${TARGET_IGW_ID:-}" ]; then
    TARGET_IGW_ID="$(aws_target ec2 create-internet-gateway --query InternetGateway.InternetGatewayId --output text)"
    save_var TARGET_IGW_ID "${TARGET_IGW_ID}"
    tag_ec2_target "${TARGET_IGW_ID}" "${DEPLOYMENT_ID}-igw" networking public
    aws_target ec2 attach-internet-gateway --internet-gateway-id "${TARGET_IGW_ID}" --vpc-id "${TARGET_VPC_ID}" >/dev/null
    log "Created and attached target IGW ${TARGET_IGW_ID}"
  fi

  if [ -z "${TARGET_ROUTE_TABLE_ID:-}" ]; then
    TARGET_ROUTE_TABLE_ID="$(aws_target ec2 create-route-table --vpc-id "${TARGET_VPC_ID}" --query RouteTable.RouteTableId --output text)"
    save_var TARGET_ROUTE_TABLE_ID "${TARGET_ROUTE_TABLE_ID}"
    tag_ec2_target "${TARGET_ROUTE_TABLE_ID}" "${DEPLOYMENT_ID}-rt" networking public
    aws_target ec2 create-route --route-table-id "${TARGET_ROUTE_TABLE_ID}" --destination-cidr-block 0.0.0.0/0 --gateway-id "${TARGET_IGW_ID}" >/dev/null
    TARGET_ROUTE_TABLE_ASSOC_ID="$(aws_target ec2 associate-route-table --route-table-id "${TARGET_ROUTE_TABLE_ID}" --subnet-id "${TARGET_SUBNET_ID}" --query AssociationId --output text)"
    save_var TARGET_ROUTE_TABLE_ASSOC_ID "${TARGET_ROUTE_TABLE_ASSOC_ID}"
    log "Created target route table ${TARGET_ROUTE_TABLE_ID}"
  fi

  if [ -z "${TARGET_SECURITY_GROUP_ID:-}" ]; then
    TARGET_SECURITY_GROUP_ID="$(aws_target ec2 create-security-group \
      --group-name "${DEPLOYMENT_ID}-sg" \
      --description "Security group for migrated Exasol Personal E2E workshop" \
      --vpc-id "${TARGET_VPC_ID}" \
      --query GroupId \
      --output text)"
    save_var TARGET_SECURITY_GROUP_ID "${TARGET_SECURITY_GROUP_ID}"
    tag_ec2_target "${TARGET_SECURITY_GROUP_ID}" "${DEPLOYMENT_ID}-sg" networking public
    local source_permissions="${STATE_DIR}/source-sg-ingress.json"
    aws_source ec2 describe-security-groups \
      --group-ids "${SOURCE_SECURITY_GROUP_ID}" \
      --query 'SecurityGroups[0].IpPermissions' \
      --output json >"${source_permissions}"
    aws_target ec2 authorize-security-group-ingress \
      --group-id "${TARGET_SECURITY_GROUP_ID}" \
      --ip-permissions "file://${source_permissions}" >/dev/null
    log "Created target security group ${TARGET_SECURITY_GROUP_ID} with source ingress rules"
  fi
}

ensure_target_key_pair() {
  if aws_target ec2 describe-key-pairs --key-names "${KEY_NAME}" >/dev/null 2>&1; then
    TARGET_KEY_PAIR_ID="$(aws_target ec2 describe-key-pairs --key-names "${KEY_NAME}" --query 'KeyPairs[0].KeyPairId' --output text)"
    save_var TARGET_KEY_PAIR_ID "${TARGET_KEY_PAIR_ID}"
    log "Target key pair ${KEY_NAME} already exists"
    return
  fi
  local public_key_file="${STATE_DIR}/${KEY_NAME}.pub"
  aws_source ec2 describe-key-pairs \
    --key-names "${SOURCE_KEY_NAME}" \
    --include-public-key \
    --query 'KeyPairs[0].PublicKey' \
    --output text >"${public_key_file}"
  if [ ! -s "${public_key_file}" ] || grep -q '^None$' "${public_key_file}"; then
    die "Could not retrieve public key material for source key pair ${KEY_NAME}"
  fi
  TARGET_KEY_PAIR_ID="$(aws_target ec2 import-key-pair \
    --key-name "${KEY_NAME}" \
    --public-key-material "fileb://${public_key_file}" \
    --query KeyPairId \
    --output text)"
  save_var TARGET_KEY_PAIR_ID "${TARGET_KEY_PAIR_ID}"
  tag_ec2_target "${TARGET_KEY_PAIR_ID}" "${DEPLOYMENT_ID}-key" networking public
  log "Imported target key pair ${KEY_NAME} as ${TARGET_KEY_PAIR_ID}"
}

ensure_target_instance_profile() {
  local trust_file="${STATE_DIR}/ec2-trust-policy.json"
  local role_name="${DEPLOYMENT_ID}-ssm-instance-role"
  local profile_name="${DEPLOYMENT_ID}-ssm-instance-profile"
  cat >"${trust_file}" <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "ec2.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
JSON

  if ! aws_target_global iam get-role --role-name "${role_name}" >/dev/null 2>&1; then
    aws_target_global iam create-role \
      --role-name "${role_name}" \
      --assume-role-policy-document "file://${trust_file}" \
      --tags \
        Key=Project,Value=exasol-personal \
        Key=Deployment,Value="${DEPLOYMENT_ID}" \
        Key=ManagedBy,Value=aws-cli-state-migration \
        Key=MigrationRun,Value="${MIGRATION_RUN}" \
        Key=exa:Department,Value=SE \
        Key=exa:Environment,Value=${EXA_ENVIRONMENT} \
        Key=exa:CostCenter,Value=20060 \
        Key=exa:Owner,Value=<OPERATOR_USER> \
        Key=exa:Project,Value=XYZPILOT \
        Key=exa:customer,Value=XYZ \
        Key=exa:Workload,Value=personal-edition \
        Key=exa:DataClassification,Value=confidential >/dev/null
    log "Created target EC2 SSM role ${role_name}"
  fi
  aws_target_global iam attach-role-policy \
    --role-name "${role_name}" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore >/dev/null || true

  if ! aws_target_global iam get-instance-profile --instance-profile-name "${profile_name}" >/dev/null 2>&1; then
    aws_target_global iam create-instance-profile --instance-profile-name "${profile_name}" >/dev/null
    aws_target_global iam add-role-to-instance-profile --instance-profile-name "${profile_name}" --role-name "${role_name}" >/dev/null
    log "Created target instance profile ${profile_name}"
  fi
  TARGET_INSTANCE_PROFILE_NAME="${profile_name}"
  save_var TARGET_INSTANCE_PROFILE_NAME "${TARGET_INSTANCE_PROFILE_NAME}"
  sleep 10
}

register_target_ami() {
  load_state
  if [ -n "${TARGET_AMI_ID:-}" ]; then
    log "Using existing target AMI ${TARGET_AMI_ID}"
    return
  fi
  local ami_name="${DEPLOYMENT_ID}-migrated-root-${MIGRATION_RUN}"
  local mappings="${STATE_DIR}/ami-block-device-mappings.json"
  python3 - "${mappings}" "${TARGET_ROOT_SNAPSHOT_ID}" <<'PY'
import json
import sys

path, snapshot_id = sys.argv[1:3]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        [
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "SnapshotId": snapshot_id,
                    "VolumeSize": 100,
                    "VolumeType": "gp3",
                    "DeleteOnTermination": True,
                },
            }
        ],
        handle,
    )
PY
  TARGET_AMI_ID="$(aws_target ec2 register-image \
    --name "${ami_name}" \
    --description "State-preserving migrated root AMI for ${DEPLOYMENT_ID} ${MIGRATION_RUN}" \
    --architecture x86_64 \
    --root-device-name /dev/sda1 \
    --virtualization-type hvm \
    --ena-support \
    --boot-mode uefi-preferred \
    --block-device-mappings "file://${mappings}" \
    --query ImageId \
    --output text)"
  save_var TARGET_AMI_ID "${TARGET_AMI_ID}"
  tag_ec2_target "${TARGET_AMI_ID}" "${ami_name}" personal-edition confidential "Key=Role,Value=root-ami"
  log "Registered target AMI ${TARGET_AMI_ID}"
}

create_target_data_volume() {
  load_state
  if [ -n "${TARGET_DATA_VOLUME_ID:-}" ]; then
    log "Using existing target data volume ${TARGET_DATA_VOLUME_ID}"
    return
  fi
  TARGET_DATA_VOLUME_ID="$(aws_target ec2 create-volume \
    --availability-zone "${TARGET_AZ}" \
    --snapshot-id "${TARGET_DATA_SNAPSHOT_ID}" \
    --volume-type gp3 \
    --query VolumeId \
    --output text)"
  save_var TARGET_DATA_VOLUME_ID "${TARGET_DATA_VOLUME_ID}"
  tag_ec2_target "${TARGET_DATA_VOLUME_ID}" "${DEPLOYMENT_ID}-n11-data" personal-edition confidential "Key=Role,Value=data" "Key=Node,Value=n11"
  aws_target ec2 wait volume-available --volume-ids "${TARGET_DATA_VOLUME_ID}"
  log "Created target data volume ${TARGET_DATA_VOLUME_ID}"
}

write_target_user_data() {
  local path="$1"
  local data_volume_id_no_dash
  data_volume_id_no_dash="${TARGET_DATA_VOLUME_ID//-/}"
  cat >"${path}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec >>/var/log/exasol-e2e-migration-firstboot.log 2>&1
echo "exasol-e2e migration first boot started \$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TARGET_DATA_VOLUME_ID="${TARGET_DATA_VOLUME_ID}"
TARGET_DATA_SERIAL="${data_volume_id_no_dash}"
TARGET_MATCH="ENV{ID_SERIAL_SHORT}==\\"${data_volume_id_no_dash}\\""

for attempt in \$(seq 1 300); do
  if ls /dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_\${TARGET_DATA_SERIAL}* >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if [ -f /etc/exasol_launcher/node.json ] && command -v jq >/dev/null 2>&1; then
  tmp="\$(mktemp)"
  jq --arg match "\${TARGET_MATCH}" '.hostDatadisk="/dev/exasol_data_01" | .hostDatadiskMatch=\$match' \
    /etc/exasol_launcher/node.json >"\${tmp}"
  install -m 0644 "\${tmp}" /etc/exasol_launcher/node.json
  rm -f "\${tmp}"
fi

cat >/etc/udev/rules.d/90-exasol.rules <<RULES
SUBSYSTEM=="block", \${TARGET_MATCH}, OWNER="ubuntu", MODE="0660"
SUBSYSTEM=="block", \${TARGET_MATCH}, SYMLINK+="exasol_data_01", MODE="0660"
RULES

udevadm control --reload-rules || true
udevadm trigger || true
for attempt in \$(seq 1 60); do
  [ -e /dev/exasol_data_01 ] && break
  udevadm trigger || true
  sleep 2
done

if command -v snap >/dev/null 2>&1; then
  snap list amazon-ssm-agent >/dev/null 2>&1 || snap install amazon-ssm-agent --classic || true
  systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service >/dev/null 2>&1 || true
fi
systemctl enable --now amazon-ssm-agent >/dev/null 2>&1 || true
systemctl reset-failed || true

echo "target data volume \${TARGET_DATA_VOLUME_ID} serial \${TARGET_DATA_SERIAL}" >/etc/exasol_launcher/migration-target-data-volume
echo "exasol-e2e migration first boot completed \$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EOF
}

launch_target_instance() {
  load_state
  if [ -n "${TARGET_INSTANCE_ID:-}" ]; then
    log "Using existing target instance ${TARGET_INSTANCE_ID}"
    return
  fi
  local user_data="${STATE_DIR}/target-user-data.sh"
  write_target_user_data "${user_data}"
  TARGET_INSTANCE_ID="$(aws_target ec2 run-instances \
    --image-id "${TARGET_AMI_ID}" \
    --instance-type "${INSTANCE_TYPE}" \
    --key-name "${KEY_NAME}" \
    --subnet-id "${TARGET_SUBNET_ID}" \
    --private-ip-address "${PRIVATE_IP}" \
    --security-group-ids "${TARGET_SECURITY_GROUP_ID}" \
    --iam-instance-profile "Name=${TARGET_INSTANCE_PROFILE_NAME}" \
    --associate-public-ip-address \
    --user-data "file://${user_data}" \
    --query 'Instances[0].InstanceId' \
    --output text)"
  save_var TARGET_INSTANCE_ID "${TARGET_INSTANCE_ID}"
  tag_ec2_target "${TARGET_INSTANCE_ID}" "${DEPLOYMENT_ID}-n11" personal-edition confidential
  log "Launched target instance ${TARGET_INSTANCE_ID}"

  aws_target ec2 wait instance-running --instance-ids "${TARGET_INSTANCE_ID}"
  local root_volume_id
  root_volume_id="$(aws_target ec2 describe-instances \
    --instance-ids "${TARGET_INSTANCE_ID}" \
    --query 'Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName==`/dev/sda1`].Ebs.VolumeId | [0]' \
    --output text)"
  TARGET_ROOT_VOLUME_ID="${root_volume_id}"
  save_var TARGET_ROOT_VOLUME_ID "${TARGET_ROOT_VOLUME_ID}"
  tag_ec2_target "${TARGET_ROOT_VOLUME_ID}" "${DEPLOYMENT_ID}-n11-root" personal-edition confidential "Key=Role,Value=root"
  log "Target root volume is ${TARGET_ROOT_VOLUME_ID}"
}

attach_target_data_volume() {
  load_state
  local attached_instance
  attached_instance="$(aws_target ec2 describe-volumes \
    --volume-ids "${TARGET_DATA_VOLUME_ID}" \
    --query 'Volumes[0].Attachments[0].InstanceId' \
    --output text)"
  if [ "${attached_instance}" = "${TARGET_INSTANCE_ID}" ]; then
    log "Target data volume ${TARGET_DATA_VOLUME_ID} is already attached"
  else
    aws_target ec2 attach-volume \
      --volume-id "${TARGET_DATA_VOLUME_ID}" \
      --instance-id "${TARGET_INSTANCE_ID}" \
      --device /dev/sdf >/dev/null
    log "Attached target data volume ${TARGET_DATA_VOLUME_ID} to ${TARGET_INSTANCE_ID}"
  fi
  aws_target ec2 wait volume-in-use --volume-ids "${TARGET_DATA_VOLUME_ID}"
  aws_target ec2 wait instance-status-ok --instance-ids "${TARGET_INSTANCE_ID}"
  TARGET_PUBLIC_DNS="$(aws_target ec2 describe-instances \
    --instance-ids "${TARGET_INSTANCE_ID}" \
    --query 'Reservations[0].Instances[0].PublicDnsName' \
    --output text)"
  save_var TARGET_PUBLIC_DNS "${TARGET_PUBLIC_DNS}"
  log "Target instance status checks are OK; public DNS: ${TARGET_PUBLIC_DNS}"
}

wait_for_ssm_and_probe() {
  log "Waiting briefly for SSM online state; validation can continue without SSM if the agent is unavailable"
  for _ in $(seq 1 40); do
    local ping
    ping="$(aws_target ssm describe-instance-information \
      --filters "Key=InstanceIds,Values=${TARGET_INSTANCE_ID}" \
      --query 'InstanceInformationList[0].PingStatus' \
      --output text 2>/dev/null || true)"
    if [ "${ping}" = "Online" ]; then
      log "SSM is online for ${TARGET_INSTANCE_ID}; running local probes"
      local command_id
      local params_file="${STATE_DIR}/ssm-health-parameters.json"
      python3 - "${params_file}" <<'PY'
import json
import sys

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(
        {
            "commands": [
                "set -eu",
                "test -e /dev/exasol_data_01 && echo data_alias=present || echo data_alias=missing",
                "systemctl is-active amazon-ssm-agent snap.amazon-ssm-agent.amazon-ssm-agent 2>/dev/null || true",
                "ss -ltn | grep -E '[:.](8563)[[:space:]]' && echo sql_port=listening || echo sql_port=not_listening",
                "systemctl --no-pager --failed || true",
            ]
        },
        handle,
    )
PY
      command_id="$(aws_target ssm send-command \
        --instance-ids "${TARGET_INSTANCE_ID}" \
        --document-name AWS-RunShellScript \
        --comment "E2E workshop migrated target health probe" \
        --parameters "file://${params_file}" \
        --query 'Command.CommandId' \
        --output text)"
      sleep 10
      aws_target ssm get-command-invocation \
        --command-id "${command_id}" \
        --instance-id "${TARGET_INSTANCE_ID}" \
        --query '{Status:Status,Stdout:StandardOutputContent,Stderr:StandardErrorContent}' \
        --output json
      return
    fi
    sleep 15
  done
  log "SSM did not become online during the short probe window"
}

allocate_target_eip() {
  # XYZ-PILOT decision #3: allocate a FRESH Elastic IP in TARGET_REGION (the source
  # EIP 3.122.35.36 is <AWS_REGION>, region-locked, cannot follow). Tagged
  # networking/internal per XYZ-PILOT-247. Idempotent via state.env.
  load_state
  if [ "${ALLOCATE_EIP}" != "1" ]; then
    log "ALLOCATE_EIP=${ALLOCATE_EIP}; skipping Elastic IP allocation"
    return
  fi
  if [ -n "${TARGET_EIP_ALLOC_ID:-}" ]; then
    log "Using existing target Elastic IP ${TARGET_EIP_ALLOC_ID} (${TARGET_EIP:-?})"
  else
    TARGET_EIP_ALLOC_ID="$(aws_target ec2 allocate-address \
      --domain vpc \
      --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=${DEPLOYMENT_ID}-eip},{Key=Deployment,Value=${DEPLOYMENT_ID}},{Key=ManagedBy,Value=aws-cli-state-migration},{Key=MigrationRun,Value=${MIGRATION_RUN}},{Key=exa:Department,Value=SE},{Key=exa:Environment,Value=${EXA_ENVIRONMENT}},{Key=exa:CostCenter,Value=20060},{Key=exa:Owner,Value=<OPERATOR_USER>},{Key=exa:Project,Value=XYZPILOT},{Key=exa:customer,Value=XYZ},{Key=exa:Workload,Value=networking},{Key=exa:DataClassification,Value=internal}]" \
      --query AllocationId --output text)"
    save_var TARGET_EIP_ALLOC_ID "${TARGET_EIP_ALLOC_ID}"
    log "Allocated target Elastic IP allocation ${TARGET_EIP_ALLOC_ID}"
  fi
  TARGET_EIP_ASSOC_ID="$(aws_target ec2 associate-address \
    --allocation-id "${TARGET_EIP_ALLOC_ID}" \
    --instance-id "${TARGET_INSTANCE_ID}" \
    --query AssociationId --output text)"
  save_var TARGET_EIP_ASSOC_ID "${TARGET_EIP_ASSOC_ID}"
  TARGET_EIP="$(aws_target ec2 describe-addresses \
    --allocation-ids "${TARGET_EIP_ALLOC_ID}" \
    --query 'Addresses[0].PublicIp' --output text)"
  save_var TARGET_EIP "${TARGET_EIP}"
  log "Elastic IP ${TARGET_EIP} associated to ${TARGET_INSTANCE_ID} (assoc ${TARGET_EIP_ASSOC_ID})"
}

print_summary() {
  load_state
  cat <<SUMMARY
State-preserving target migration created.

State file: ${STATE_FILE}
Source account: ${SOURCE_ACCOUNT}
Target account: ${TARGET_ACCOUNT}
Region: ${SOURCE_REGION} -> ${TARGET_REGION}
Target AZ label: ${TARGET_AZ}
Deployment ID: ${DEPLOYMENT_ID}

Source root snapshot: ${SOURCE_ROOT_SNAPSHOT_ID:-}
Source data original snapshot: ${SOURCE_DATA_ORIGINAL_SNAPSHOT_ID:-}
Source data shareable snapshot: ${SOURCE_DATA_SHAREABLE_SNAPSHOT_ID:-}
Target root snapshot: ${TARGET_ROOT_SNAPSHOT_ID:-}
Target data snapshot: ${TARGET_DATA_SNAPSHOT_ID:-}

Target VPC: ${TARGET_VPC_ID:-}
Target subnet: ${TARGET_SUBNET_ID:-}
Target security group: ${TARGET_SECURITY_GROUP_ID:-}
Target key pair ID: ${TARGET_KEY_PAIR_ID:-}
Target AMI: ${TARGET_AMI_ID:-}
Target instance: ${TARGET_INSTANCE_ID:-}
Target root volume: ${TARGET_ROOT_VOLUME_ID:-}
Target data volume: ${TARGET_DATA_VOLUME_ID:-}
Target public DNS: ${TARGET_PUBLIC_DNS:-}
Target Elastic IP: ${TARGET_EIP:-} (${TARGET_EIP_ALLOC_ID:-})

Source resources were not deleted or disabled by this script.
SUMMARY
}

dryrun_probe() {
  # Probe an EC2 mutating command with --dry-run; classify the result.
  # DryRunOperation = would-succeed/permitted (PASS); UnauthorizedOperation = missing
  # permission (FAIL); any other error = flag/input problem to correct (WARN).
  local label="$1"; shift
  local out
  if out="$("$@" --dry-run 2>&1)"; then
    log "DRY-RUN PASS  ${label} (accepted)"; return 0
  fi
  if printf '%s' "${out}" | grep -q "DryRunOperation"; then
    log "DRY-RUN PASS  ${label} (would succeed; permitted)"; return 0
  elif printf '%s' "${out}" | grep -q "UnauthorizedOperation"; then
    log "DRY-RUN FAIL  ${label} (UnauthorizedOperation — missing IAM permission)"; return 1
  else
    log "DRY-RUN WARN  ${label} (non-dryrun error — correct flags/inputs): $(printf '%s' "${out}" | tr '\n' ' ' | head -c 200)"; return 2
  fi
}

step0_dryrun_preflight() {
  log "===== STEP 0: DRY-RUN PREFLIGHT — validates only, creates/deletes NOTHING ====="
  local fails=0
  ensure_identity
  log "Source resource existence (read-only):"
  aws_source ec2 describe-instances --instance-ids "${SOURCE_INSTANCE_ID}" --query 'Reservations[0].Instances[0].State.Name' --output text >/dev/null 2>&1 \
    && log "  instance ${SOURCE_INSTANCE_ID} OK" || { log "  instance MISSING"; fails=$((fails+1)); }
  aws_source ec2 describe-volumes --volume-ids "${SOURCE_ROOT_VOLUME_ID}" "${SOURCE_DATA_VOLUME_ID}" --query 'Volumes[].VolumeId' --output text >/dev/null 2>&1 \
    && log "  root+data volumes OK" || { log "  volumes MISSING"; fails=$((fails+1)); }
  aws_source ec2 describe-security-groups --group-ids "${SOURCE_SECURITY_GROUP_ID}" --query 'SecurityGroups[0].GroupId' --output text >/dev/null 2>&1 \
    && log "  SG ${SOURCE_SECURITY_GROUP_ID} OK" || { log "  SG MISSING"; fails=$((fails+1)); }
  log "Target collision check (read-only):"
  ensure_no_target_collision || fails=$((fails+1))
  log "EC2 --dry-run permission/syntax probes (independent creates only):"
  dryrun_probe "source create-snapshot (data vol)" aws_source ec2 create-snapshot --volume-id "${SOURCE_DATA_VOLUME_ID}" --description "xyz-pilot-migration-dryrun" || fails=$((fails+1))
  dryrun_probe "source create-tags (data vol)"     aws_source ec2 create-tags --resources "${SOURCE_DATA_VOLUME_ID}" --tags Key=DryRun,Value=1 || fails=$((fails+1))
  dryrun_probe "target create-vpc (${VPC_CIDR})"   aws_target ec2 create-vpc --cidr-block "${VPC_CIDR}" || fails=$((fails+1))
  dryrun_probe "target allocate-address (EIP)"     aws_target ec2 allocate-address --domain vpc || fails=$((fails+1))
  log "Deferred (depend on upstream; validated at runtime): copy-snapshot, create-subnet, register-image, run-instances, attach-volume, associate-address."
  log "No EC2 --dry-run (validated at runtime / separate IAM check): KMS create-key/tag, IAM create-role/instance-profile, SSM send-command."
  log "===== STEP 0 COMPLETE: ${fails} failure(s) ====="
  [ "${fails}" -eq 0 ] || die "Preflight reported ${fails} failure(s) — correct before a real run"
  log "Preflight CLEAN. Unset DRY_RUN (or DRY_RUN=0) to execute the real migration."
}

main() {
  require_cmd aws
  require_cmd jq
  require_cmd python3
  load_state
  if [ "${DRY_RUN}" = "1" ]; then
    step0_dryrun_preflight
    exit 0
  fi
  ensure_identity
  ensure_source_stopped
  ensure_no_target_collision
  ensure_source_share_key
  create_source_snapshots
  copy_source_data_to_shareable_key
  share_source_snapshots
  copy_snapshots_to_target
  create_target_network
  ensure_target_key_pair
  ensure_target_instance_profile
  register_target_ami
  create_target_data_volume
  launch_target_instance
  attach_target_data_volume
  allocate_target_eip
  wait_for_ssm_and_probe
  print_summary
}

main "$@"
