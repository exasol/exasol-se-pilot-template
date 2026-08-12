#!/usr/bin/env bash
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$script_dir/../../../../scripts/common.sh" ]; then
  source "$script_dir/../../../../scripts/common.sh"
else
  source "$script_dir/../../scripts/common.sh"
fi

xyz_agent_begin "Migration-aware infrastructure topology checks across source and target AWS accounts, regions, AZs, runtime endpoints, and control-plane plumbing."

agent_dir="$PWD"
mkdir -p artifacts/aws-tag-inventory/source artifacts/aws-tag-inventory/target artifacts/architecture artifacts/json

canonical_name="XYZ_INFRASTRUCTURE"
requested_alias="XYZ_INRFASTRUCTURE"

source_profile="${XYZ_INFRA_SOURCE_PROFILE:-${XYZ_INFRA_LEGACY_PROFILE:-<OPS_USER_1>Degen}}"
source_account="${XYZ_INFRA_SOURCE_ACCOUNT:-<SOURCE_AWS_ACCOUNT_ID>}"
source_region="${XYZ_INFRA_SOURCE_REGION:-<AWS_REGION>}"
source_az="${XYZ_INFRA_SOURCE_AZ:-<AWS_REGION>c}"
source_instance="${XYZ_INFRA_SOURCE_EC2_INSTANCE_ID:-<EC2_INSTANCE_ID>}"
source_dashboard_bucket="${XYZ_INFRA_SOURCE_DASHBOARD_BUCKET:-xyz-pilot-status-<SOURCE_AWS_ACCOUNT_ID>}"
source_backup_bucket="${XYZ_INFRA_SOURCE_BACKUP_BUCKET:-xyz-pilot-repo-backups-<SOURCE_AWS_ACCOUNT_ID>}"

target_profile="${XYZ_INFRA_TARGET_PROFILE:-default}"
target_account="${XYZ_INFRA_TARGET_ACCOUNT:-<AWS_ACCOUNT_ID>}"
target_region="${XYZ_INFRA_TARGET_REGION:-<AWS_REGION>}"
target_az="${XYZ_INFRA_TARGET_AZ:-<AWS_REGION>f}"
target_instance="${XYZ_INFRA_TARGET_EC2_INSTANCE_ID:-<EC2_INSTANCE_ID>}"
target_dashboard_bucket="${XYZ_INFRA_TARGET_DASHBOARD_BUCKET:-xyz-pilot-status-<AWS_ACCOUNT_ID>}"
target_backup_bucket="${XYZ_INFRA_TARGET_BACKUP_BUCKET:-xyz-pilot-repo-backups-<AWS_ACCOUNT_ID>}"
public_dashboard_bucket="${XYZ_INFRA_PUBLIC_DASHBOARD_BUCKET:-$target_dashboard_bucket}"
public_dashboard_region="${XYZ_INFRA_PUBLIC_DASHBOARD_REGION:-$target_region}"
public_dashboard_url="${XYZ_INFRA_PUBLIC_DASHBOARD_URL:-https://<PROJECT_SLUG>-dashboard.<PILOT_DOMAIN>/dashboard.html}"

scenario="${XYZ_INFRA_SCENARIO:-<PILOT_NAME> AWS migration source ${source_account}/${source_region}/${source_az} -> target ${target_account}/${target_region}/${target_az}}"
endpoint_strict="${XYZ_INFRA_ENDPOINT_STRICT:-0}"
emit_cross_boundary_candidates="${XYZ_INFRA_EMIT_CROSS_BOUNDARY_CANDIDATES:-0}"
source_live_checks="${XYZ_INFRA_SOURCE_LIVE_CHECKS:-1}"

source_tag_out="$agent_dir/artifacts/aws-tag-inventory/source"
target_tag_out="$agent_dir/artifacts/aws-tag-inventory/target"

aws_cmd_prefix() {
  local profile="$1"
  if [ -z "$profile" ] || [ "$profile" = "env" ]; then
    printf 'aws'
  else
    printf "aws --profile '%s'" "$profile"
  fi
}

source_aws="$(aws_cmd_prefix "$source_profile")"
target_aws="$(aws_cmd_prefix "$target_profile")"

cat > artifacts/json/scenario.json <<JSON
{
  "agent": "$canonical_name",
  "requested_alias": "$requested_alias",
  "run_id": "$XYZ_RUN_ID",
  "scenario": "$scenario",
  "source": {
    "profile": "$source_profile",
    "account": "$source_account",
    "region": "$source_region",
    "availability_zone": "$source_az",
    "ec2_instance_id": "$source_instance",
    "dashboard_bucket": "$source_dashboard_bucket",
    "backup_bucket": "$source_backup_bucket"
  },
  "target": {
    "profile": "$target_profile",
    "account": "$target_account",
    "region": "$target_region",
    "availability_zone": "$target_az",
    "ec2_instance_id": "$target_instance",
    "dashboard_bucket": "$target_dashboard_bucket",
    "backup_bucket": "$target_backup_bucket"
  },
  "public_operator_dashboard": {
    "bucket": "$public_dashboard_bucket",
    "region": "$public_dashboard_region",
    "url": "$public_dashboard_url"
  }
}
JSON

{
  printf '\n## Infrastructure Scenario\n\n'
  printf -- '- Canonical agent: `%s`\n' "$canonical_name"
  printf -- '- Operator-requested alias: `%s`\n' "$requested_alias"
  printf -- '- Scenario: `%s`\n' "$scenario"
  printf -- '- Source profile/account/region/AZ: `%s` / `%s` / `%s` / `%s`\n' "$source_profile" "$source_account" "$source_region" "$source_az"
  printf -- '- Target profile/account/region/AZ: `%s` / `%s` / `%s` / `%s`\n' "$target_profile" "$target_account" "$target_region" "$target_az"
  printf -- '- Operator Dashboard public URL: `%s`\n' "$public_dashboard_url"
  printf -- '- Operator Dashboard bucket/region: `%s` / `%s` (private-only direct S3 posture expected)\n' "$public_dashboard_bucket" "$public_dashboard_region"
  printf -- '- Architecture artifacts: `artifacts/architecture/`\n'
  printf -- '- AWS tag inventory artifacts: `artifacts/aws-tag-inventory/{source,target}/`\n'
} >> results.md

artifact_file() {
  local label="$1"
  printf 'artifacts/%s.out' "$(xyz_safe_name "$label")"
}

aws_identity_check() {
  local side="$1" profile="$2" expected_account="$3"
  local label status actual aws_prefix profile_note
  label="${side}_aws_identity"
  aws_prefix="$(aws_cmd_prefix "$profile")"
  if [ -z "$profile" ] || [ "$profile" = "env" ]; then
    profile_note="environment credentials"
  else
    profile_note="Profile $profile"
  fi
  status="$(xyz_cmd_sh "$label" "$aws_prefix sts get-caller-identity --query '{Account:Account,Arn:Arn,UserId:UserId}' --output json --no-cli-pager")"
  actual="$(xyz_artifact_text "$label")"
  if [ "$status" = "0" ] && grep -q "\"Account\": \"$expected_account\"" "$(artifact_file "$label")"; then
    xyz_result "${side} AWS identity" "account $expected_account" "$actual" "PASS" "$profile_note"
  else
    xyz_result "${side} AWS identity" "account $expected_account" "$actual" "FAIL" "Profile/account binding drift or credentials unavailable"
    xyz_add_candidate "Infrastructure AWS account binding drift for ${side}" \
"- **Type:** defect
- **Severity:** major
- **Area:** AWS · Infrastructure · Migration
- **Observed:** \`$canonical_name\` could not prove that profile \`$profile\` resolves to account \`$expected_account\`.
- **Expected:** Source and target profile bindings must resolve to their declared migration account IDs before E2E migration validation is trusted.
- **Evidence:** \`$XYZ_RUN_DIR/$canonical_name/artifacts/$(xyz_safe_name "$label").out\` and \`.err\`.
- **Duplicate hints:** Check XYZ-PILOT-225 / XYZ-PILOT-226 migration tracker entries before filing."
  fi
}

ec2_instance_check() {
  local side="$1" profile="$2" region="$3" instance_id="$4" expected_az="$5"
  local label status actual out aws_prefix
  label="${side}_ec2_expected_instance"
  aws_prefix="$(aws_cmd_prefix "$profile")"
  status="$(xyz_cmd_sh "$label" "$aws_prefix ec2 describe-instances --region '$region' --instance-ids '$instance_id' --query \"Reservations[].Instances[].{InstanceId:InstanceId,State:State.Name,AZ:Placement.AvailabilityZone,PrivateIp:PrivateIpAddress,PublicIp:PublicIpAddress,InstanceType:InstanceType,SubnetId:SubnetId,VpcId:VpcId,IamProfile:IamInstanceProfile.Arn,Name:Tags[?Key=='Name']|[0].Value,Project:Tags[?Key=='exa:Project']|[0].Value}\" --output json --no-cli-pager")"
  actual="$(xyz_artifact_text "$label")"
  out="$(artifact_file "$label")"
  if [ "$status" = "0" ] && grep -q "$expected_az" "$out"; then
    xyz_result "${side} EC2 instance AZ binding" "$instance_id in $region/$expected_az" "$actual" "PASS" "Includes VPC/subnet/public-private IP metadata"
  elif [ "$status" = "0" ]; then
    xyz_result "${side} EC2 instance AZ binding" "$instance_id in $region/$expected_az" "$actual" "WARN" "Instance exists but expected AZ was not observed"
    xyz_add_candidate "Infrastructure EC2 AZ drift for ${side}" \
"- **Type:** defect
- **Severity:** major
- **Area:** AWS · EC2 · Migration
- **Observed:** Expected EC2 instance \`$instance_id\` did not report expected AZ \`$expected_az\` in the infrastructure run.
- **Expected:** Migration source/target EC2 instances must stay bound to documented AZs so RCA can distinguish account, region, AZ, and DNS/tunnel drift.
- **Evidence:** \`$XYZ_RUN_DIR/$canonical_name/artifacts/$(xyz_safe_name "$label").out\`.
- **Duplicate hints:** Check XYZ-PILOT-225 / XYZ-PILOT-226 before filing."
  else
    xyz_result "${side} EC2 instance AZ binding" "$instance_id inventory succeeds" "$actual" "FAIL" "Expected instance missing or credentials/region wrong"
  fi
}

readonly_aws_probe() {
  local label="$1" expected="$2" warn_note="$3" cmd="$4"
  local status actual
  status="$(xyz_cmd_sh "$label" "$cmd")"
  actual="$(xyz_artifact_text "$label")"
  if [ "$status" = "0" ]; then
    xyz_result "$label" "$expected" "$actual" "PASS" "$warn_note"
  else
    xyz_result "$label" "$expected" "$actual" "WARN" "$warn_note"
  fi
}

tag_inventory_probe() {
  local side="$1" profile="$2" region="$3" out_dir="$4"
  local label status actual aws_prefix
  label="${side}_aws_tag_inventory"
  if [ -z "$profile" ] || [ "$profile" = "env" ]; then
    aws_prefix="$(aws_cmd_prefix "$profile")"
    status="$(xyz_cmd_sh "$label" "$aws_prefix resourcegroupstaggingapi get-resources --region '$region' --query 'length(ResourceTagMappingList[])' --output text --no-cli-pager")"
  else
    status="$(xyz_cmd_sh "$label" "scripts/aws_tag_inventory.sh --profile '$profile' --region '$region' --project XYZPILOT --format all --out-dir '$out_dir'")"
  fi
  actual="$(xyz_artifact_text "$label")"
  if [ "$status" = "0" ]; then
    xyz_result "${side} AWS tag inventory" "tag inventory succeeds" "$actual" "PASS" "Named profiles use scripts/aws_tag_inventory.sh; env credentials use Resource Groups Tagging API count"
  else
    xyz_result "${side} AWS tag inventory" "scripts/aws_tag_inventory.sh succeeds" "$actual" "WARN" "Direct service probes still run; check bash version/profile permissions"
  fi
}

endpoint_probe() {
  local label="$1" url="$2" expected_codes="$3" notes="$4"
  local result
  if xyz_http_probe "$label" "$url" "$expected_codes"; then
    xyz_result "$label" "HTTP $expected_codes" "$(xyz_status_code "$label") $(xyz_body_preview "$label")" "PASS" "$notes"
  else
    if [ "$endpoint_strict" = "1" ]; then
      result="FAIL"
    else
      result="WARN"
    fi
    xyz_result "$label" "HTTP $expected_codes" "$(xyz_status_code "$label") $(xyz_body_preview "$label")" "$result" "$notes"
  fi
}

write_architecture_artifacts() {
  local generated_utc
  generated_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  cat > artifacts/architecture/README.md <<EOF_ARCH
# XYZ_INFRASTRUCTURE Architecture Snapshot

- Run: \`$XYZ_RUN_ID\`
- Generated UTC: \`$generated_utc\`
- Scenario: \`$scenario\`
- Source: \`$source_account/$source_region/$source_az\` via profile \`$source_profile\`
- Target: \`$target_account/$target_region/$target_az\` via profile \`$target_profile\`

These diagrams are generated for time-travel RCA. They intentionally embed
account IDs, regions, AZs, expected EC2 IDs, and endpoint names so future runs
can be compared for stale mappings, wrong-account operations, DNS/tunnel drift,
and migration cutover mistakes.

| Diagram | Purpose |
|---|---|
| [01-migration-account-az.md](01-migration-account-az.md) | Source to target account/region/AZ migration topology |
| [02-public-endpoint-plumbing.md](02-public-endpoint-plumbing.md) | Public URLs, Cloudflare/S3 ingress, and expected runtime landing points |
| [03-app-db-runtime.md](03-app-db-runtime.md) | EC2, Docker app stack, Exasol Personal, and local port plumbing |
| [04-control-plane.md](04-control-plane.md) | Lambdas, EventBridge/Scheduler, Cognito, SES/SNS, SSM, S3, GitHub |
EOF_ARCH

  cat > artifacts/architecture/01-migration-account-az.md <<EOF_ARCH
# Migration Account / AZ Topology

\`\`\`mermaid
flowchart LR
  subgraph SRC["Source account $source_account / $source_region / $source_az"]
    SRC_EC2["Legacy EC2<br/>$source_instance"]
    SRC_DB[("Exasol Personal<br/>legacy DB + app host")]
    SRC_STACK["Docker app stack<br/>QBR / Chatbot / Grafana / MCP / cloudflared"]
    SRC_EC2 --> SRC_DB
    SRC_EC2 --> SRC_STACK
  end

  subgraph TGT["Target account $target_account / $target_region / $target_az"]
    TGT_EC2["Target EC2<br/>$target_instance"]
    TGT_DB[("Exasol Personal<br/>restored DB")]
    TGT_STACK["Phase 02b app-stack re-host<br/>staged artifacts + patched runtime"]
    TGT_EC2 --> TGT_DB
    TGT_EC2 -. "Phase 02b" .-> TGT_STACK
  end

  SRC_DB == "native backup / restore" ==> TGT_DB
  SRC_STACK -. "S3 staged stack artifacts" .-> TGT_STACK
  TGT_STACK -. "public cutover gated" .-> PUBLIC["Cloudflare DNS / service URLs"]
\`\`\`
EOF_ARCH

  cat > artifacts/architecture/02-public-endpoint-plumbing.md <<EOF_ARCH
# Public Endpoint Plumbing

\`\`\`mermaid
flowchart TB
  USER["Pilot users / operators"]
  CF["Cloudflare zone<br/><PILOT_DOMAIN>"]
  S3D["Legacy S3 dashboard<br/>$source_dashboard_bucket/dashboard.html"]
  S3T["Private S3 dashboard assets<br/>$target_dashboard_bucket/dashboard.html"]

  USER --> QBR["QBR<br/>$XYZ_QBR_URL"]
  USER --> CHAT["Chatbot<br/>$XYZ_CHAT_URL"]
  USER --> GRAFANA["Grafana<br/>$XYZ_GRAFANA_URL"]
  USER --> MCP["MCP<br/>$XYZ_MCP_URL"]
  USER --> DASH_PUBLIC["Operator Dashboard<br/>$public_dashboard_url"]
  USER -. "403 expected on direct S3 after private-only hardening" .-> DASH_S3["Operator Dashboard assets<br/>https://$public_dashboard_bucket.s3.$public_dashboard_region.amazonaws.com/dashboard.html"]

  QBR --> CF
  CHAT --> CF
  GRAFANA --> CF
  MCP --> CF
  DASH_PUBLIC -. "CloudFront over private S3 origin" .-> DASH_S3
  DASH_S3 -. "private bucket; authenticated delivery path required" .-> S3T
  DASH_LEGACY["Legacy dashboard retained for soak<br/>https://$source_dashboard_bucket.s3.$source_region.amazonaws.com/dashboard.html"] --> S3D

  CF -. "legacy/source quiesced unless DEV lane is approved" .-> SRC_TUNNEL["Source cloudflared tunnels"]
  CF --> TGT_TUNNEL["Target cloudflared tunnels<br/>live after cutover"]
  SRC_TUNNEL -.-> SRC_EC2["Source EC2 $source_instance"]
  TGT_TUNNEL --> TGT_EC2["Target EC2 $target_instance"]
\`\`\`
EOF_ARCH

  cat > artifacts/architecture/03-app-db-runtime.md <<EOF_ARCH
# App / DB Runtime Plumbing

\`\`\`mermaid
flowchart LR
  subgraph EC2["Target/runtime EC2 under test"]
    DB[("Exasol Personal<br/>port 8563 TLS")]
    CHAT["xyz-pilot-chatbot<br/>Streamlit"]
    QBR["xyz-qbr-streamlit<br/>Streamlit"]
    GRAF["xyz-grafana"]
    MCP_P["xyz-mcp-server<br/>public OAuth MCP"]
    MCP_I["xyz-mcp-internal<br/>no-auth docker bridge"]
    PROXY["xyz-exasol-proxy<br/>Grafana Infinity path"]
    CHAT --> MCP_I
    CHAT --> DB
    QBR --> DB
    GRAF --> PROXY --> DB
    MCP_P --> DB
    MCP_I --> DB
  end

  CF["Cloudflare tunnels / DNS"] --> CHAT
  CF --> QBR
  CF --> GRAF
  CF --> MCP_P
  SSM["SSM Parameter Store<br/>runtime secrets metadata only"] --> CHAT
  SSM --> QBR
  SSM --> MCP_P
\`\`\`
EOF_ARCH

  cat > artifacts/architecture/04-control-plane.md <<EOF_ARCH
# AWS Control Plane Plumbing

\`\`\`mermaid
flowchart TB
  GH["GitHub repo<br/>source + candidate queue + run artifacts"]
  S3["S3 buckets<br/>operator dashboard / repo backups / migration transfer"]
  SSM["SSM Parameter Store<br/>/xyz-pilot/* metadata checked without values"]
  COG["Cognito<br/>Hosted UI / user pool / identity pool"]
  SES["SES + SNS<br/>notifications"]
  EB["EventBridge rules / Scheduler"]
  LAMBDA["xyz-pilot-* Lambdas<br/>start / stop / daily-status / user-mgmt / candidate-drain"]
  EC2["EC2 Exasol Personal host<br/>source $source_instance / target $target_instance"]
  BEDROCK["Bedrock runtime<br/>chatbot + daily-status narrative"]

  GH <--> LAMBDA
  LAMBDA --> SSM
  LAMBDA --> EC2
  LAMBDA --> SES
  LAMBDA --> BEDROCK
  EB --> LAMBDA
  COG --> LAMBDA
  S3 --> GH
  COG --> CHAT["Chatbot / MCP OAuth gates"]
\`\`\`
EOF_ARCH
}

emit_cross_boundary_candidates() {
  if [ "$source_account" = "$target_account" ] && [ "$source_region" = "$target_region" ] && [ "$source_az" = "$target_az" ]; then
    xyz_result "Cross-account/AZ dependency candidate scan" "no cross-boundary migration scenario" "source and target match" "PASS" "No migration-specific dependency candidate emitted"
    return
  fi

  if [ "$emit_cross_boundary_candidates" != "1" ]; then
    xyz_result "Cross-account/AZ dependency candidate scan" "cross-boundary scenario documented without automatic filing" \
      "${source_account}/${source_region}/${source_az} -> ${target_account}/${target_region}/${target_az}" \
      "PASS" "Set XYZ_INFRA_EMIT_CROSS_BOUNDARY_CANDIDATES=1 only when the run has identified a concrete stale mapping or owner ambiguity"
    return
  fi

  xyz_result "Cross-account/AZ dependency candidate scan" "cross-boundary dependencies queued as minor candidates" \
    "${source_account}/${source_region}/${source_az} -> ${target_account}/${target_region}/${target_az}" \
    "CANDIDATE" "Intentional migration bridges can be denied or deferred by XYZ_MASTER; stale bridges become cutover blockers"

  xyz_add_candidate "Cross-account/AZ public endpoint dependency requires migration triage" \
"- **Type:** defect
- **Severity:** minor
- **Area:** AWS · Cloudflare · Migration
- **Observed:** Public service URLs are validated while the scenario spans source \`$source_account/$source_region/$source_az\` and target \`$target_account/$target_region/$target_az\`, and this run was explicitly configured to emit cross-boundary findings.
- **Expected:** Each QBR, Chatbot, Grafana, and MCP URL must have an explicit source-or-target owner, tunnel origin, account ID, region, and AZ mapping.
- **Why minor:** The cross-account boundary is not automatically wrong after cutover, but unidentified public-route ownership is a stale-mapping and wrong-account RCA risk.
- **Evidence:** \`$XYZ_RUN_DIR/$canonical_name/artifacts/architecture/02-public-endpoint-plumbing.md\`, \`$XYZ_RUN_DIR/$canonical_name/artifacts/json/scenario.json\`, endpoint probe artifacts."

  xyz_add_candidate "Cross-account/AZ Operator Dashboard dependency requires migration triage" \
"- **Type:** defect
- **Severity:** minor
- **Area:** AWS · S3 · Operator Dashboard · Migration
- **Observed:** The Operator Dashboard public bucket is \`$public_dashboard_bucket\` in \`$public_dashboard_region\`; the legacy bucket \`$source_dashboard_bucket\` in \`$source_account/$source_region\` is retained for soak/DEV-lane evidence.
- **Expected:** The run evidence must show whether embedded Lambda actions, target status links, and displayed account/region labels are source-owned, target-owned, or disabled.
- **Why minor:** A legacy dashboard that remains visible with stale embedded controls can mislead operators even when the static page itself is healthy.
- **Evidence:** \`$XYZ_RUN_DIR/$canonical_name/artifacts/architecture/04-control-plane.md\`, \`$XYZ_RUN_DIR/$canonical_name/artifacts/source_operator_dashboard_bucket.out\`, endpoint dashboard probe artifacts."

  xyz_add_candidate "Cross-account/AZ app-stack-to-database dependency requires migration triage" \
"- **Type:** defect
- **Severity:** minor
- **Area:** AWS · EC2 · Docker · Exasol · Migration
- **Observed:** The scenario models restored target Exasol state on \`$target_instance\` while app-stack artifacts may still originate from legacy source \`$source_instance\` and legacy staged transfer paths.
- **Expected:** Phase 02b evidence must prove each container's Exasol host, AWS region, Cognito, SSM, Cloudflare, and callback configuration points at the intended account/region before public routing changes.
- **Why minor:** The dependency is expected during re-hosting, but stale container-side \`localhost\`, legacy \`<AWS_REGION>\`, or source-account identifiers can survive unnoticed until startup.
- **Evidence:** \`$XYZ_RUN_DIR/$canonical_name/artifacts/architecture/03-app-db-runtime.md\`, EC2 instance artifacts, AWS tag inventory artifacts."

  xyz_add_candidate "Cross-account/AZ target control-plane dependency requires migration triage" \
"- **Type:** defect
- **Severity:** minor
- **Area:** AWS · Control Plane · Migration
- **Observed:** The run checks Lambda, EventBridge/Scheduler, Cognito, SES/SNS, SSM, S3, and Bedrock-adjacent plumbing across both \`$source_account/$source_region\` and \`$target_account/$target_region\`.
- **Expected:** Each control-plane resource must either be recreated in target, intentionally source-quiesced, or documented as not-yet-in-scope for the current migration phase.
- **Why minor:** Empty target inventories are expected before their phases, but undocumented cross-account control-plane dependencies become cutover and rollback risk.
- **Evidence:** \`$XYZ_RUN_DIR/$canonical_name/artifacts/architecture/04-control-plane.md\`, source/target AWS probe artifacts, and \`scripts/aws_tag_inventory.sh\` outputs under \`artifacts/aws-tag-inventory/\`."
}

write_architecture_artifacts
xyz_result "Architecture snapshot" "module diagrams generated per run" "artifacts/architecture/README.md" "PASS" "Mermaid diagrams include account/region/AZ/run scenario"
emit_cross_boundary_candidates

if [ "$source_live_checks" = "1" ]; then
  aws_identity_check "source" "$source_profile" "$source_account"
else
  xyz_result "source AWS identity" "account $source_account" "skipped in target-only OIDC run" "PASS" "Target-only OIDC mode; set XYZ_INFRA_SOURCE_LIVE_CHECKS=1 only when legacy source credentials are available"
fi
aws_identity_check "target" "$target_profile" "$target_account"

if [ "$source_live_checks" = "1" ]; then
  ec2_instance_check "source" "$source_profile" "$source_region" "$source_instance" "$source_az"
else
  xyz_result "source EC2 instance AZ binding" "$source_instance in $source_region/$source_az" "skipped in target-only OIDC run" "PASS" "Legacy source remains documented in scenario JSON and diagrams"
fi
ec2_instance_check "target" "$target_profile" "$target_region" "$target_instance" "$target_az"

if [ "$source_live_checks" = "1" ]; then
  tag_inventory_probe "source" "$source_profile" "$source_region" "$source_tag_out"
else
  xyz_result "source AWS tag inventory" "scripts/aws_tag_inventory.sh succeeds" "skipped in target-only OIDC run" "PASS" "Run locally with <OPS_USER_1>Degen profile for legacy source inventory"
fi
tag_inventory_probe "target" "$target_profile" "$target_region" "$target_tag_out"

if [ "$source_live_checks" = "1" ]; then
  readonly_aws_probe "source_vpc_subnet_inventory" "VPC/subnet metadata listed" "No secret values" \
    "$source_aws ec2 describe-subnets --region '$source_region' --query \"Subnets[].{SubnetId:SubnetId,VpcId:VpcId,AZ:AvailabilityZone,Cidr:CidrBlock,Name:Tags[?Key=='Name']|[0].Value}\" --output json --no-cli-pager"
fi
readonly_aws_probe "target_vpc_subnet_inventory" "VPC/subnet metadata listed" "No secret values" \
  "$target_aws ec2 describe-subnets --region '$target_region' --query \"Subnets[].{SubnetId:SubnetId,VpcId:VpcId,AZ:AvailabilityZone,Cidr:CidrBlock,Name:Tags[?Key=='Name']|[0].Value}\" --output json --no-cli-pager"

if [ "$source_live_checks" = "1" ]; then
  readonly_aws_probe "source_security_groups" "security groups listed" "Flag broad ingress manually from artifact" \
    "$source_aws ec2 describe-security-groups --region '$source_region' --query \"SecurityGroups[].{GroupId:GroupId,GroupName:GroupName,VpcId:VpcId,Ingress:IpPermissions[].{Proto:IpProtocol,From:FromPort,To:ToPort,Cidr:IpRanges[].CidrIp}}\" --output json --no-cli-pager"
fi
readonly_aws_probe "target_security_groups" "security groups listed" "Flag broad ingress manually from artifact" \
  "$target_aws ec2 describe-security-groups --region '$target_region' --query \"SecurityGroups[].{GroupId:GroupId,GroupName:GroupName,VpcId:VpcId,Ingress:IpPermissions[].{Proto:IpProtocol,From:FromPort,To:ToPort,Cidr:IpRanges[].CidrIp}}\" --output json --no-cli-pager"

if [ "$source_live_checks" = "1" ]; then
  readonly_aws_probe "source_xyz_lambdas" "xyz-pilot Lambda inventory listed" "No environment values read" \
    "$source_aws lambda list-functions --region '$source_region' --query \"Functions[?starts_with(FunctionName, 'xyz-pilot')].{Name:FunctionName,State:State,Runtime:Runtime,LastUpdateStatus:LastUpdateStatus}\" --output json --no-cli-pager"
fi
readonly_aws_probe "target_xyz_lambdas" "xyz-pilot Lambda inventory listed or confirmed empty" "Expected empty until target control-plane phase lands" \
  "$target_aws lambda list-functions --region '$target_region' --query \"Functions[?starts_with(FunctionName, 'xyz-pilot')].{Name:FunctionName,State:State,Runtime:Runtime,LastUpdateStatus:LastUpdateStatus}\" --output json --no-cli-pager"

if [ "$source_live_checks" = "1" ]; then
  readonly_aws_probe "source_eventbridge_rules" "EventBridge xyz-pilot rules listed" "Rules may be disabled while source is quiesced" \
    "$source_aws events list-rules --region '$source_region' --name-prefix xyz-pilot --query \"Rules[].{Name:Name,State:State,Schedule:ScheduleExpression}\" --output json --no-cli-pager"
fi
readonly_aws_probe "target_eventbridge_rules" "EventBridge xyz-pilot rules listed or confirmed empty" "Expected empty until target control-plane phase lands" \
  "$target_aws events list-rules --region '$target_region' --name-prefix xyz-pilot --query \"Rules[].{Name:Name,State:State,Schedule:ScheduleExpression}\" --output json --no-cli-pager"

if [ "$source_live_checks" = "1" ]; then
  readonly_aws_probe "source_scheduler_schedules" "Scheduler xyz-pilot schedules listed" "Empty is acceptable when classic rules are used" \
    "$source_aws scheduler list-schedules --region '$source_region' --query \"Schedules[?starts_with(Name, 'xyz-pilot')].{Name:Name,State:State}\" --output json --no-cli-pager"
fi
readonly_aws_probe "target_scheduler_schedules" "Scheduler xyz-pilot schedules listed or confirmed empty" "Expected empty until target control-plane phase lands" \
  "$target_aws scheduler list-schedules --region '$target_region' --query \"Schedules[?starts_with(Name, 'xyz-pilot')].{Name:Name,State:State}\" --output json --no-cli-pager"

if [ "$source_live_checks" = "1" ]; then
  readonly_aws_probe "source_cognito_pools" "Cognito pools listed" "Metadata only; no users or tokens" \
    "$source_aws cognito-idp list-user-pools --region '$source_region' --max-results 60 --query \"UserPools[?contains(Name, 'xyz') || contains(Name, 'pilot')].{Id:Id,Name:Name,Status:Status}\" --output json --no-cli-pager"
fi
readonly_aws_probe "target_cognito_pools" "Cognito pools listed or confirmed empty" "Expected empty until identity migration phase lands" \
  "$target_aws cognito-idp list-user-pools --region '$target_region' --max-results 60 --query \"UserPools[?contains(Name, 'xyz') || contains(Name, 'pilot')].{Id:Id,Name:Name,Status:Status}\" --output json --no-cli-pager"

if [ "$source_live_checks" = "1" ]; then
  readonly_aws_probe "source_ssm_xyz_parameters" "SSM /xyz-pilot parameter metadata listed" "No values, no decryption" \
    "$source_aws ssm describe-parameters --region '$source_region' --parameter-filters \"Key=Name,Option=BeginsWith,Values=/xyz-pilot\" --query \"Parameters[].{Name:Name,Type:Type,LastModifiedDate:LastModifiedDate}\" --output json --no-cli-pager"
fi
readonly_aws_probe "target_ssm_xyz_parameters" "SSM /xyz-pilot parameter metadata listed or confirmed empty" "No values, no decryption" \
  "$target_aws ssm describe-parameters --region '$target_region' --parameter-filters \"Key=Name,Option=BeginsWith,Values=/xyz-pilot\" --query \"Parameters[].{Name:Name,Type:Type,LastModifiedDate:LastModifiedDate}\" --output json --no-cli-pager"

if [ "$source_live_checks" = "1" ]; then
  readonly_aws_probe "source_ses_account" "SES account metadata reads" "No email sent" \
    "$source_aws sesv2 get-account --region '$source_region' --query \"{SendingEnabled:SendingEnabled,ProductionAccessEnabled:ProductionAccessEnabled,EnforcementStatus:EnforcementStatus}\" --output json --no-cli-pager"
fi
readonly_aws_probe "target_ses_account" "SES account metadata reads" "No email sent; sandbox state expected until notifications phase" \
  "$target_aws sesv2 get-account --region '$target_region' --query \"{SendingEnabled:SendingEnabled,ProductionAccessEnabled:ProductionAccessEnabled,EnforcementStatus:EnforcementStatus}\" --output json --no-cli-pager"

if [ "$source_live_checks" = "1" ]; then
  readonly_aws_probe "source_sns_topics" "SNS xyz-pilot topics listed" "Metadata only" \
    "$source_aws sns list-topics --region '$source_region' --query \"Topics[?contains(TopicArn, 'xyz-pilot')].TopicArn\" --output json --no-cli-pager"
fi
readonly_aws_probe "target_sns_topics" "SNS xyz-pilot topics listed or confirmed empty" "Expected empty until notifications phase" \
  "$target_aws sns list-topics --region '$target_region' --query \"Topics[?contains(TopicArn, 'xyz-pilot')].TopicArn\" --output json --no-cli-pager"

if [ "$source_live_checks" = "1" ]; then
  readonly_aws_probe "source_operator_dashboard_bucket" "$source_dashboard_bucket bucket head succeeds" "Operator dashboard should remain up during migration" \
    "$source_aws s3api head-bucket --bucket '$source_dashboard_bucket' --no-cli-pager"
else
  xyz_result "source_operator_dashboard_bucket" "$source_dashboard_bucket bucket head succeeds" "skipped in target-only OIDC run" "PASS" "Legacy dashboard endpoint is still probed over HTTPS below"
fi
readonly_aws_probe "target_operator_dashboard_bucket" "$target_dashboard_bucket bucket head succeeds or confirmed absent" "Expected absent until target state/dashboard phase" \
  "$target_aws s3api head-bucket --bucket '$target_dashboard_bucket' --no-cli-pager"
if [ "$source_live_checks" = "1" ]; then
  readonly_aws_probe "source_repo_backup_bucket" "$source_backup_bucket bucket head succeeds" "Backup path continuity" \
    "$source_aws s3api head-bucket --bucket '$source_backup_bucket' --no-cli-pager"
fi
readonly_aws_probe "target_repo_backup_bucket" "$target_backup_bucket bucket head succeeds or confirmed absent" "Expected absent until target state/dashboard phase" \
  "$target_aws s3api head-bucket --bucket '$target_backup_bucket' --no-cli-pager"

endpoint_probe "endpoint_qbr_health" "$XYZ_QBR_URL/_stcore/health" "200" "Public route may be intentionally offline during account/region cutover"
endpoint_probe "endpoint_chatbot_health" "$XYZ_CHAT_URL/_stcore/health" "200" "Public route may be intentionally offline during account/region cutover"
endpoint_probe "endpoint_grafana_health" "$XYZ_GRAFANA_URL/api/health" "200" "Public route may be intentionally offline during account/region cutover"
endpoint_probe "endpoint_mcp_metadata" "https://<PROJECT_SLUG>.<PILOT_DOMAIN>/.well-known/oauth-protected-resource/mcp" "200" "OAuth protected-resource metadata"
endpoint_probe "endpoint_operator_dashboard_public" "$public_dashboard_url" "200" "CloudFront URL is the canonical browser entrypoint for the Operator Dashboard"
endpoint_probe "endpoint_operator_dashboard_private_s3" "https://${public_dashboard_bucket}.s3.${public_dashboard_region}.amazonaws.com/dashboard.html" "403" "Direct S3 access must remain private-only; use an approved authenticated delivery layer before expecting browser HTTP 200"
endpoint_probe "endpoint_legacy_operator_dashboard" "https://${source_dashboard_bucket}.s3.${source_region}.amazonaws.com/dashboard.html" "200" "Legacy dashboard is retained for soak/DEV-lane review; embedded controls must not drive the live target unless explicitly documented"

{
  printf '\n## Architecture Artifacts\n\n'
  printf -- '- Scenario JSON: `artifacts/json/scenario.json`\n'
  printf -- '- Architecture TOC: `artifacts/architecture/README.md`\n'
  printf -- '- Migration account/AZ diagram: `artifacts/architecture/01-migration-account-az.md`\n'
  printf -- '- Public endpoint plumbing diagram: `artifacts/architecture/02-public-endpoint-plumbing.md`\n'
  printf -- '- App/DB runtime diagram: `artifacts/architecture/03-app-db-runtime.md`\n'
  printf -- '- Control-plane diagram: `artifacts/architecture/04-control-plane.md`\n'
  printf '\n## Review Notes\n\n'
  printf 'Compare these artifacts across runs when investigating stale mappings, wrong AWS profile/account use, DNS/tunnel routing drift, AZ mismatch, or missing target control-plane resources.\n'
} >> results.md

xyz_agent_finish
