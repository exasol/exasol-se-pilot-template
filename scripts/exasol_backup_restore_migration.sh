#!/usr/bin/env bash
# <PILOT_NAME> Exasol migration via NATIVE DB BACKUP/RESTORE (Option C).
#
# WHY THIS (protocol Issues 1-8): the state-preserving EBS approach hit 8 walls
# across the cross-region boundary (IP binding -> volume lock -> c4-host-play wipe
# -> lazy-load STE_READ -> readHeader/metadata mismatch). Backup/restore
# re-materializes the data on FRESH storage in the target region, so none of those recur.
#
# DESIGN: runs UNATTENDED (nohup) so it does NOT consume an interactive Claude
# Code session. Idempotent + resumable via a state file. All Exasol work is driven
# through SSM + nsenter into the cos container (the proven access path). confd jobs
# run via a cos LOGIN shell (sets CONFD_HOST) with </dev/null (avoids the ssh-pty
# stdin footgun). NEVER use c4 host play (it wipes EXAStorage).
#
# OPERATOR-CONFIRM points are marked [CONFIRM]; the script PAUSES (or you set the
# matching APPROVE_* env) before destructive / cost / judgement steps.
#
#   Dry plan:   DRY_RUN=1 bash scripts/exasol_backup_restore_migration.sh
#   Real run:   nohup bash scripts/exasol_backup_restore_migration.sh > /tmp/exa-bkp-restore.log 2>&1 &
set -euo pipefail

# ---- Source account / region ----
SOURCE_PROFILE="${SOURCE_PROFILE:-<AWS_PROFILE>}"
SOURCE_REGION="${SOURCE_REGION:-<SOURCE_AWS_REGION>}"
SOURCE_INSTANCE="${SOURCE_INSTANCE:-<EC2_INSTANCE_ID>}"
SOURCE_S3_BUCKET="${SOURCE_S3_BUCKET:-<EXASOL_ARCHIVE_BUCKET>}"  # pre-provisioned (archive.enabled was false)

# ---- Target account / region ----
TARGET_PROFILE="${TARGET_PROFILE:-default}"
TARGET_REGION="${TARGET_REGION:-<AWS_REGION>}"
TARGET_INSTANCE="${TARGET_INSTANCE:-}"          # [CONFIRM] a CLEAN Exasol Personal in the target region (fresh `exasol deploy`); set its instance id here
TARGET_S3_BUCKET="${TARGET_S3_BUCKET:-}"        # [CONFIRM] a target-account-readable bucket for the backup (or make SOURCE_S3_BUCKET cross-account readable)

# ---- DB / backup ----
DB_NAME="${DB_NAME:-Exasol}"
ARCHIVE_NAME="${ARCHIVE_NAME:-ArchiveVolume1}"
BACKUP_LEVEL="${BACKUP_LEVEL:-0}"               # 0 = full
S3_CRED_TYPE="${S3_CRED_TYPE:-IAM}"             # IAM (instance role) | root | MFA  [CONFIRM: instance role must allow s3:* on the bucket]

DRY_RUN="${DRY_RUN:-0}"
STATE="${STATE:-/tmp/exa-bkp-restore-state.env}"
[ -f "$STATE" ] && . "$STATE" || : > "$STATE"
save() { grep -q "^$1=" "$STATE" 2>/dev/null && sed -i.bak "s|^$1=.*|$1=$2|" "$STATE" || echo "$1=$2" >> "$STATE"; }
log()  { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
die()  { log "ERROR: $*"; exit 1; }

# Run a shell snippet on an instance via SSM; echo stdout. $1=profile $2=region $3=instance $4=script
ssm() {
  local prof="$1" reg="$2" iid="$3" script="$4" b64 cid st
  b64=$(printf '%s' "$script" | base64 | tr -d '\n')
  cid=$(AWS_PROFILE="$prof" aws ssm send-command --region "$reg" --instance-ids "$iid" \
        --document-name AWS-RunShellScript --parameters "commands=[\"echo $b64 | base64 -d | sudo bash\"]" \
        --query 'Command.CommandId' --output text) || die "send-command failed"
  for _ in $(seq 1 120); do
    st=$(AWS_PROFILE="$prof" aws ssm get-command-invocation --region "$reg" --command-id "$cid" --instance-id "$iid" --query Status --output text 2>/dev/null || echo Pending)
    [ "$st" = Success ] && break
    [ "$st" = Failed ] || [ "$st" = Cancelled ] || [ "$st" = TimedOut ] && { AWS_PROFILE="$prof" aws ssm get-command-invocation --region "$reg" --command-id "$cid" --instance-id "$iid" --query 'StandardErrorContent' --output text >&2; die "SSM $st"; }
    sleep 5
  done
  AWS_PROFILE="$prof" aws ssm get-command-invocation --region "$reg" --command-id "$cid" --instance-id "$iid" --query 'StandardOutputContent' --output text
}

# Run a confd job inside the cos on an instance. $1=profile $2=region $3=instance $4=job $5=json-params(optional)
confd() {
  local prof="$1" reg="$2" iid="$3" job="$4" params="${5:-}"
  local inner="confd_client $job"
  [ -n "$params" ] && inner="confd_client $job -A '$params'"
  ssm "$prof" "$reg" "$iid" "PID=\$(pgrep -f cos_storage | head -1); nsenter -t \$PID -a -- bash -lc \"$inner\" </dev/null"
}

wait_db_up() { # $1=profile $2=region $3=instance
  log "waiting for DB 8563 on $3 ..."
  ssm "$1" "$2" "$3" 'for i in $(seq 1 40); do timeout 2 bash -c "</dev/tcp/127.0.0.1/8563" 2>/dev/null && { echo UP; exit 0; }; sleep 10; done; echo TIMEOUT' \
    | grep -q UP || die "DB did not come up on $3"
  log "DB up on $3"
}

confirm() { # $1=APPROVE_var $2=message
  local var="$1"; shift
  if [ "${!var:-0}" != "1" ]; then
    log "[CONFIRM] $* -> set $var=1 to proceed. Stopping (resumable)."
    exit 2
  fi
}

# ============================ PHASES ============================
phase_source_up() {
  [ "${P_SOURCE_UP:-}" = done ] && { log "source already up (state)"; return; }
  log "starting source $SOURCE_INSTANCE (re-fires XYZ-PILOT-077 AD-HOC email)"
  AWS_PROFILE="$SOURCE_PROFILE" aws ec2 start-instances --region "$SOURCE_REGION" --instance-ids "$SOURCE_INSTANCE" >/dev/null
  AWS_PROFILE="$SOURCE_PROFILE" aws ec2 wait instance-running --region "$SOURCE_REGION" --instance-ids "$SOURCE_INSTANCE"
  wait_db_up "$SOURCE_PROFILE" "$SOURCE_REGION" "$SOURCE_INSTANCE"
  save P_SOURCE_UP done
}

phase_archive_volume() {
  [ "${P_ARCHIVE:-}" = done ] && { log "archive volume exists (state)"; return; }
  log "creating S3 archive (object) volume '$ARCHIVE_NAME' -> bucket $SOURCE_S3_BUCKET"
  local p="{\"vol_type\":\"s3\",\"name\":\"$ARCHIVE_NAME\",\"bucket\":\"$SOURCE_S3_BUCKET\",\"type\":\"$S3_CRED_TYPE\"}"
  confd "$SOURCE_PROFILE" "$SOURCE_REGION" "$SOURCE_INSTANCE" object_volume_add "$p" || true
  confd "$SOURCE_PROFILE" "$SOURCE_REGION" "$SOURCE_INSTANCE" object_volume_info "{\"name\":\"$ARCHIVE_NAME\"}"
  save P_ARCHIVE done
}

phase_backup() {
  [ "${P_BACKUP:-}" = done ] && { log "backup already taken (state)"; return; }
  log "starting LEVEL-$BACKUP_LEVEL backup of $DB_NAME -> $ARCHIVE_NAME"
  confd "$SOURCE_PROFILE" "$SOURCE_REGION" "$SOURCE_INSTANCE" db_backup_start \
    "{\"db_name\":\"$DB_NAME\",\"backup_volume_name\":\"$ARCHIVE_NAME\",\"level\":$BACKUP_LEVEL}"
  log "polling db_backup_progress ..."
  for _ in $(seq 1 240); do  # up to ~2h
    out=$(confd "$SOURCE_PROFILE" "$SOURCE_REGION" "$SOURCE_INSTANCE" db_backup_progress "{\"db_name\":\"$DB_NAME\"}" 2>/dev/null || true)
    echo "$out" | grep -qiE "100|finished|complete|no .*backup" && { log "backup complete"; break; }
    sleep 30
  done
  confd "$SOURCE_PROFILE" "$SOURCE_REGION" "$SOURCE_INSTANCE" db_backup_list "{\"db_name\":\"$DB_NAME\"}"
  save P_BACKUP done
}

phase_cross_account_s3() {
  [ "${P_S3SYNC:-}" = done ] && { log "S3 cross-account done (state)"; return; }
  [ -z "$TARGET_S3_BUCKET" ] && confirm APPROVE_S3 "set TARGET_S3_BUCKET (target-account-readable) OR make SOURCE_S3_BUCKET cross-account readable, then APPROVE_S3=1"
  log "syncing backup source -> target bucket $TARGET_S3_BUCKET"
  AWS_PROFILE="$SOURCE_PROFILE" aws s3 sync "s3://$SOURCE_S3_BUCKET" "s3://$TARGET_S3_BUCKET" --source-region "$SOURCE_REGION" --region "$TARGET_REGION" || die "s3 sync failed (check cross-account perms)"
  save P_S3SYNC done
}

phase_restore() {
  confirm APPROVE_RESTORE "DESTRUCTIVE restore into TARGET_INSTANCE=$TARGET_INSTANCE (must be a CLEAN Exasol Personal in the target region). Set APPROVE_RESTORE=1"
  [ -z "$TARGET_INSTANCE" ] && die "TARGET_INSTANCE not set (deploy a clean Exasol Personal first)"
  wait_db_up "$TARGET_PROFILE" "$TARGET_REGION" "$TARGET_INSTANCE"
  log "creating archive volume on target -> $TARGET_S3_BUCKET"
  confd "$TARGET_PROFILE" "$TARGET_REGION" "$TARGET_INSTANCE" object_volume_add \
    "{\"vol_type\":\"s3\",\"name\":\"$ARCHIVE_NAME\",\"bucket\":\"$TARGET_S3_BUCKET\",\"type\":\"$S3_CRED_TYPE\"}" || true
  local bfiles="${BACKUP_FILES:-exa_db/id_1/level_0}"  # confirm via db_backup_list output from phase_backup
  log "restoring $DB_NAME from $bfiles"
  confd "$TARGET_PROFILE" "$TARGET_REGION" "$TARGET_INSTANCE" db_restore \
    "{\"db_name\":\"$DB_NAME\",\"backup_volume_name\":\"$ARCHIVE_NAME\",\"backup_files\":\"$bfiles\"}"
  save P_RESTORE done
}

phase_verify() {
  log "verify on target (use c4 sqlclient with the real SYS password):"
  log "  SELECT 1; SELECT COUNT(*) FROM <PILOT_SCHEMA>.FACT_PRIMARY;"
  log "  SELECT COUNT(*) FROM SYS.EXA_SCHEMAS WHERE SCHEMA_NAME LIKE 'ESL%'; (expect 9)"
  log "  SELECT COUNT(*) FROM SYS.EXA_DBA_OBJECTS WHERE ROOT_NAME LIKE 'ESL%'; (expect 221)"
  log "  SELECT USER_NAME FROM SYS.EXA_ALL_USERS; (expect sys, xyz_dash, xyz_biz, ...)"
}

main() {
  if [ "$DRY_RUN" = 1 ]; then
    log "DRY RUN — phases: source_up -> archive_volume -> backup -> cross_account_s3 -> [CONFIRM] restore -> verify"
    log "source=$SOURCE_INSTANCE bucket=$SOURCE_S3_BUCKET db=$DB_NAME archive=$ARCHIVE_NAME"
    log "target=$TARGET_INSTANCE bucket=$TARGET_S3_BUCKET (both [CONFIRM] before real run)"; exit 0
  fi
  phase_source_up
  phase_archive_volume
  phase_backup
  phase_cross_account_s3
  phase_restore
  phase_verify
  log "DONE — verify the counts above, then re-point clients/tunnels to the new target."
}
main "$@"
