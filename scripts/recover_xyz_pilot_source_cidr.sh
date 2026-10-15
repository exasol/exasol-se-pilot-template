#!/usr/bin/env bash
# <PILOT_NAME> — state-preserving RECOVERY into the SOURCE CIDR (target region).
#
# WHY THIS EXISTS (protocol Issues 4+5, 2026-06-11):
#   The first migration attempt changed the VPC CIDR to 10.40.0.0/16, which forced
#   the Exasol Personal node's private IP to change (172.30.1.11 -> 10.40.1.11).
#   Exasol Personal has NO supported in-place private-IP change: the only re-bind
#   path is `c4 host play`, which is a DEPLOY command and RE-INITIALIZES EXAStorage
#   (it wiped the DB; data survived only in the snapshot + untouched source volume).
#
# THE FIX (operator-approved "keep the source CIDR"):
#   Rebuild reusing the SOURCE CIDR so the instance is assigned the SAME private IP
#   172.30.1.11 as the source. Then there is NO re-IP and NO re-deploy — the launcher
#   skips install (IP matches, install.complete present on the AMI root) and the DB
#   starts on the PRESERVED data. The only required repair is the data-disk udev
#   serial (the restored volume has a new EBS serial) — a non-destructive config edit.
#
# RULE FOR FUTURE STATE-PRESERVING MIGRATIONS:
#   Preserve the source private IP by reusing the source VPC/subnet CIDR. Overlapping
#   CIDRs across isolated, UN-PEERED VPCs are safe. NEVER attempt a post-launch
#   private-IP change via `c4 host play`.
#
# This script is TARGET-ONLY (target account and region) and reuses already-existing
# cross-region snapshots + AMI + SSM profile + key — it never touches the source account.
set -euo pipefail

REGION="${REGION:-<AWS_REGION>}"
AZ="${AZ:-<AWS_AZ>}"
VPC_CIDR="${VPC_CIDR:-172.30.0.0/16}"        # MATCHES SOURCE — do not change
SUBNET_CIDR="${SUBNET_CIDR:-172.30.1.0/24}"  # MATCHES SOURCE
PRIVATE_IP="${PRIVATE_IP:-172.30.1.11}"      # MATCHES SOURCE — the whole point
DEPLOYMENT_ID="${DEPLOYMENT_ID:-<EXASOL_DEPLOYMENT_ID>}"
AMI_ID="${AMI_ID:-<AMI_ID>}"            # original migrated root (privateIp=172.30.1.11, install.complete)
DATA_SNAPSHOT_ID="${DATA_SNAPSHOT_ID:-<EBS_SNAPSHOT_ID>}"  # pre-wipe data copy in the target region
INSTANCE_TYPE="${INSTANCE_TYPE:-r6i.xlarge}"
KEY_NAME="${KEY_NAME:-<PROJECT_SLUG>-<AWS_REGION>-key}"
INSTANCE_PROFILE="${INSTANCE_PROFILE:-<EXASOL_DEPLOYMENT_ID>-ssm-instance-profile}"
STATE="${STATE:-/tmp/xyz-recovery-state.env}"
export AWS_PROFILE="${AWS_PROFILE:-default}"          # target account profile

COMMON_TAGS='{Key=exa:Project,Value=<PROJECT_SLUG>},{Key=exa:customer,Value=<CUSTOMER_NAME>},{Key=exa:Environment,Value=staging},{Key=exa:Department,Value=SE},{Key=exa:CostCenter,Value=<COST_CENTER>},{Key=exa:Owner,Value=<OPERATOR_EMAIL>},{Key=MigrationRun,Value=20260611-recovery-srcCIDR}'
TAGS_NETWORKING="${COMMON_TAGS},{Key=exa:Workload,Value=networking},{Key=exa:DataClassification,Value=internal}"
TAGS_DATABASE="${COMMON_TAGS},{Key=exa:Workload,Value=database},{Key=exa:DataClassification,Value=confidential}"

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
A() { aws --region "$REGION" "$@" --no-cli-pager; }
save() { echo "$1=$2" >> "$STATE"; }
[ -f "$STATE" ] && . "$STATE" || : > "$STATE"

acct="$(A sts get-caller-identity --query Account --output text)"
[ "$acct" = "<AWS_ACCOUNT_ID>" ] || { log "WRONG DIM_ACCOUNT $acct (want <AWS_ACCOUNT_ID>)"; exit 1; }

create_network() {
  [ -n "${REC_VPC:-}" ] && { log "network exists ($REC_VPC)"; return; }
  REC_VPC=$(A ec2 create-vpc --cidr-block "$VPC_CIDR" --tag-specifications "ResourceType=vpc,Tags=[{Key=Name,Value=${DEPLOYMENT_ID}-vpc-rec},$TAGS_NETWORKING]" --query Vpc.VpcId --output text)
  A ec2 modify-vpc-attribute --vpc-id "$REC_VPC" --enable-dns-hostnames '{"Value":true}'
  A ec2 modify-vpc-attribute --vpc-id "$REC_VPC" --enable-dns-support '{"Value":true}'
  REC_SUBNET=$(A ec2 create-subnet --vpc-id "$REC_VPC" --cidr-block "$SUBNET_CIDR" --availability-zone "$AZ" --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=${DEPLOYMENT_ID}-subnet-rec},$TAGS_NETWORKING]" --query Subnet.SubnetId --output text)
  A ec2 modify-subnet-attribute --subnet-id "$REC_SUBNET" --map-public-ip-on-launch
  REC_IGW=$(A ec2 create-internet-gateway --tag-specifications "ResourceType=internet-gateway,Tags=[{Key=Name,Value=${DEPLOYMENT_ID}-igw-rec},$TAGS_NETWORKING]" --query InternetGateway.InternetGatewayId --output text)
  A ec2 attach-internet-gateway --internet-gateway-id "$REC_IGW" --vpc-id "$REC_VPC"
  REC_RT=$(A ec2 create-route-table --vpc-id "$REC_VPC" --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=${DEPLOYMENT_ID}-rt-rec},$TAGS_NETWORKING]" --query RouteTable.RouteTableId --output text)
  A ec2 create-route --route-table-id "$REC_RT" --destination-cidr-block 0.0.0.0/0 --gateway-id "$REC_IGW" >/dev/null
  A ec2 associate-route-table --route-table-id "$REC_RT" --subnet-id "$REC_SUBNET" >/dev/null
  REC_SG=$(A ec2 create-security-group --group-name "${DEPLOYMENT_ID}-sg-rec" --description "<PILOT_NAME> recovery (source-CIDR)" --vpc-id "$REC_VPC" --tag-specifications "ResourceType=security-group,Tags=[{Key=Name,Value=${DEPLOYMENT_ID}-sg-rec},$TAGS_NETWORKING]" --query GroupId --output text)
  for p in 22 2581 8443 8563 20002 20003; do A ec2 authorize-security-group-ingress --group-id "$REC_SG" --protocol tcp --port "$p" --cidr 0.0.0.0/0 >/dev/null; done
  A ec2 authorize-security-group-ingress --group-id "$REC_SG" --protocol -1 --cidr "$VPC_CIDR" >/dev/null
  save REC_VPC "$REC_VPC"; save REC_SUBNET "$REC_SUBNET"; save REC_IGW "$REC_IGW"; save REC_RT "$REC_RT"; save REC_SG "$REC_SG"
  log "network: vpc=$REC_VPC subnet=$REC_SUBNET sg=$REC_SG (NEVER PEER this overlapping CIDR)"
}

launch() {
  [ -n "${REC_INSTANCE:-}" ] && { log "instance exists ($REC_INSTANCE)"; return; }
  REC_DATAVOL=$(A ec2 create-volume --availability-zone "$AZ" --snapshot-id "$DATA_SNAPSHOT_ID" --volume-type gp3 --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=${DEPLOYMENT_ID}-n11-data-rec},{Key=Role,Value=data},$TAGS_DATABASE]" --query VolumeId --output text)
  A ec2 wait volume-available --volume-ids "$REC_DATAVOL"
  local serial; serial=$(echo "$REC_DATAVOL" | tr -d -)
  local ud; ud=$(mktemp)
  cat > "$ud" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec >>/var/log/exasol-recovery-firstboot.log 2>&1
SERIAL="$serial"; MATCH="ENV{ID_SERIAL_SHORT}==\\"$serial\\""
for a in \$(seq 1 300); do ls /dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_\${SERIAL}* >/dev/null 2>&1 && break; sleep 2; done
if [ -f /etc/exasol_launcher/node.json ] && command -v jq >/dev/null 2>&1; then
  tmp=\$(mktemp); jq --arg m "\$MATCH" '.hostDatadisk="/dev/exasol_data_01" | .hostDatadiskMatch=\$m' /etc/exasol_launcher/node.json > "\$tmp"; install -m 0644 "\$tmp" /etc/exasol_launcher/node.json; rm -f "\$tmp"
fi
cat >/etc/udev/rules.d/90-exasol.rules <<RULES
SUBSYSTEM=="block", \$MATCH, OWNER="ubuntu", MODE="0660"
SUBSYSTEM=="block", \$MATCH, SYMLINK+="exasol_data_01", MODE="0660"
RULES
udevadm control --reload-rules || true
for a in \$(seq 1 60); do [ -e /dev/exasol_data_01 ] && break; udevadm trigger || true; sleep 2; done
systemctl enable --now amazon-ssm-agent >/dev/null 2>&1 || true
EOF
  REC_INSTANCE=$(A ec2 run-instances --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" --key-name "$KEY_NAME" --subnet-id "$REC_SUBNET" --private-ip-address "$PRIVATE_IP" --security-group-ids "$REC_SG" --iam-instance-profile "Name=$INSTANCE_PROFILE" --associate-public-ip-address --user-data "file://$ud" --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${DEPLOYMENT_ID}-n11-rec},$TAGS_DATABASE]" --query 'Instances[0].InstanceId' --output text)
  rm -f "$ud"
  A ec2 wait instance-running --instance-ids "$REC_INSTANCE"
  A ec2 attach-volume --volume-id "$REC_DATAVOL" --instance-id "$REC_INSTANCE" --device /dev/sdf >/dev/null
  save REC_DATAVOL "$REC_DATAVOL"; save REC_INSTANCE "$REC_INSTANCE"
  log "launched $REC_INSTANCE (private IP $PRIVATE_IP), data $REC_DATAVOL attached"
}

# verify(): run AFTER ~10-15 min. If 8563 is not listening, the DB did not auto-start;
# run `c4 up` (NOT `c4 host play` — that wipes) to start the existing play on the
# preserved data, then re-probe. SELECT 1 first, then <PILOT_SCHEMA> + external semantic layer counts.
verify() {
  local iid="${REC_INSTANCE:?launch first}"
  aws ssm send-command --region "$REGION" --instance-ids "$iid" --document-name AWS-RunShellScript \
    --parameters 'commands=[
"(timeout 3 bash -c \"</dev/tcp/127.0.0.1/8563\" && echo 8563_LISTENING) || echo 8563_DOWN_run_c4_up",
"cd /home/ubuntu && sudo -iu ubuntu /home/ubuntu/c4 ps 2>/dev/null | tail -5",
"echo IF_DOWN: sudo -iu ubuntu bash -lc \"cd ~ && ./c4 up\""
]' --query Command.CommandId --output text
}

create_network
launch
log "RECOVERY LAUNCHED. Wait ~10-15 min, then: STATE=$STATE bash -c 'source $0; verify'"
log "If 8563 stays down: SSM in and run 'c4 up' (existing play, IP-matched) — NOT 'c4 host play'."
