#!/usr/bin/env bash
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$script_dir/../../../../scripts/common.sh" ]; then
  source "$script_dir/../../../../scripts/common.sh"
else
  source "$script_dir/../../scripts/common.sh"
fi

xyz_agent_begin "Integration checks for AWS, Exasol, MCP, Lambda, scheduler, and service wiring."

repo_backup_bucket="${XYZ_REPO_BACKUP_BUCKET:-xyz-pilot-repo-backups-<SOURCE_AWS_ACCOUNT_ID>}"

status="$(xyz_cmd_sh "aws_identity" "aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output json --no-cli-pager")"
if [ "$status" = "0" ]; then
  xyz_result "AWS identity" "valid caller" "$(xyz_artifact_text "aws_identity")" "PASS" "Account/ARN only"
else
  xyz_result "AWS identity" "valid caller" "$(xyz_artifact_text "aws_identity")" "FAIL" ""
fi

status="$(xyz_cmd_sh "ec2_status_checks" "aws ec2 describe-instance-status --region '$XYZ_REGION' --instance-ids '$XYZ_EC2_INSTANCE_ID' --include-all-instances --query 'InstanceStatuses[].{State:InstanceState.Name,System:SystemStatus.Status,Instance:InstanceStatus.Status}' --output json --no-cli-pager")"
actual="$(xyz_artifact_text "ec2_status_checks")"
if [ "$status" = "0" ] && grep -q '"ok"' artifacts/ec2_status_checks.out; then
  xyz_result "EC2 status checks" "running with ok checks" "$actual" "PASS" ""
else
  xyz_result "EC2 status checks" "running with ok checks" "$actual" "FAIL" ""
fi

status="$(xyz_cmd "ec2_host_aws_cli" tests/scripts/probe_ec2_host_aws_cli.sh)"
actual="$(xyz_artifact_text "ec2_host_aws_cli")"
if [ "$status" = "0" ] && grep -q "aws-cli/2" artifacts/ec2_host_aws_cli.out; then
  xyz_result "EC2 host AWS CLI" "AWS CLI v2 available and instance role resolves" "$actual" "PASS" "XYZ-PILOT-151 host operational tooling"
else
  xyz_result "EC2 host AWS CLI" "AWS CLI v2 available and instance role resolves" "$actual" "FAIL" "Host AWS CLI missing or cannot use instance profile"
fi

status="$(xyz_cmd_sh "lambda_inventory" "aws lambda list-functions --region '$XYZ_REGION' --query 'Functions[?starts_with(FunctionName, \`xyz-pilot-\`)].{Name:FunctionName,Runtime:Runtime,State:State,LastUpdateStatus:LastUpdateStatus}' --output json --no-cli-pager")"
actual="$(xyz_artifact_text "lambda_inventory")"
if [ "$status" = "0" ] && grep -q "xyz-pilot" artifacts/lambda_inventory.out; then
  xyz_result "Lambda inventory" "xyz-pilot functions listed" "$actual" "PASS" "No env values read"
else
  xyz_result "Lambda inventory" "xyz-pilot functions listed" "$actual" "FAIL" ""
fi

status="$(xyz_cmd_sh "lambda_core_configs" "for fn in xyz-pilot-start xyz-pilot-stop xyz-pilot-daily-status xyz-pilot-user-mgmt xyz-pilot-candidate-drain; do aws lambda get-function-configuration --region '$XYZ_REGION' --function-name \"\$fn\" --query '{Name:FunctionName,Runtime:Runtime,State:State,LastUpdateStatus:LastUpdateStatus,Timeout:Timeout,MemorySize:MemorySize}' --output json --no-cli-pager; done")"
actual="$(xyz_artifact_text "lambda_core_configs")"
if [ "$status" = "0" ] && grep -q '"State": "Active"' artifacts/lambda_core_configs.out; then
  xyz_result "Core Lambda configurations" "key xyz-pilot Lambdas active" "$actual" "PASS" "No env values read"
else
  xyz_result "Core Lambda configurations" "key xyz-pilot Lambdas active" "$actual" "FAIL" ""
fi

status="$(xyz_cmd_sh "aws_resource_inventory_count" "aws resourcegroupstaggingapi get-resources --region '$XYZ_REGION' --no-paginate --query 'length(ResourceTagMappingList[])' --output text --no-cli-pager")"
if [ "$status" = "0" ]; then
  xyz_result "AWS tag-index inventory" "read-only resource index query succeeds" "$(xyz_artifact_text "aws_resource_inventory_count")" "PASS" "Tag index is advisory; direct service checks remain authoritative"
else
  xyz_result "AWS tag-index inventory" "read-only resource index query succeeds" "$(xyz_artifact_text "aws_resource_inventory_count")" "WARN" ""
fi

status="$(xyz_cmd_sh "eventbridge_rules" "aws events list-rules --region '$XYZ_REGION' --name-prefix xyz-pilot --query 'Rules[].{Name:Name,State:State,Schedule:ScheduleExpression}' --output json --no-cli-pager")"
actual="$(xyz_artifact_text "eventbridge_rules")"
if [ "$status" = "0" ] && grep -q "xyz-pilot" artifacts/eventbridge_rules.out; then
  xyz_result "EventBridge xyz-pilot rules" "rules listed" "$actual" "PASS" ""
else
  xyz_result "EventBridge xyz-pilot rules" "rules listed" "$actual" "WARN" "May use Scheduler or names changed"
fi

status="$(xyz_cmd_sh "scheduler_schedules" "aws scheduler list-schedules --region '$XYZ_REGION' --query 'Schedules[?starts_with(Name, \`xyz-pilot-\`)].{Name:Name,State:State}' --output json --no-cli-pager")"
if [ "$status" = "0" ]; then
  xyz_result "EventBridge Scheduler" "read-only list succeeds" "$(xyz_artifact_text "scheduler_schedules")" "PASS" "Empty is acceptable when EventBridge Rules are used"
else
  xyz_result "EventBridge Scheduler" "read-only list succeeds" "$(xyz_artifact_text "scheduler_schedules")" "WARN" ""
fi

status="$(xyz_cmd_sh "exasol_current_user" "exapump sql --profile '$XYZ_EXASOL_PROFILE' 'SELECT CURRENT_USER'")"
actual="$(xyz_artifact_text "exasol_current_user")"
if [ "$status" = "0" ]; then
  if grep -q "SYS" artifacts/exasol_current_user.out; then
    xyz_result "Exasol current user" "XYZ_DASH read-only user" "$actual" "FAIL" "Regression: read-only suite is using admin user"
    xyz_add_candidate "Exasol CLI profile uses admin user for routine reads" \
"- **Type:** defect
- **Severity:** major
- **Area:** Exasol
- **Observed:** \`exapump\` profile \`$XYZ_EXASOL_PROFILE\` reports \`CURRENT_USER=SYS\`.
- **Expected:** routine tester reads should use \`XYZ_DASH\` or another read-only profile.
- **Evidence:** see \`artifacts/exasol_current_user.out\`.
- **Duplicate hints:** regression of \`XYZ-PILOT-131\`; do not file a new issue unless the read-only profile is absent or broken after reconfiguration."
  elif grep -q "XYZ_DASH" artifacts/exasol_current_user.out; then
    xyz_result "Exasol current user" "XYZ_DASH read-only user" "$actual" "PASS" ""
  else
    xyz_result "Exasol current user" "XYZ_DASH read-only user" "$actual" "WARN" "Non-SYS user, but not the expected Pilot read-only principal"
  fi
else
  xyz_result "Exasol current user" "query succeeds" "$actual" "FAIL" ""
fi

status="$(xyz_cmd_sh "exasol_fact_count" "exapump sql --profile '$XYZ_EXASOL_PROFILE' 'SELECT COUNT(*) FROM <PILOT_SCHEMA>.FACT_PRIMARY'")"
if [ "$status" = "0" ]; then
  xyz_result "Exasol fact count" "read succeeds" "$(xyz_artifact_text "exasol_fact_count")" "PASS" ""
else
  xyz_result "Exasol fact count" "read succeeds" "$(xyz_artifact_text "exasol_fact_count")" "FAIL" ""
fi

status="$(xyz_cmd_sh "exasol_session_count" "exapump sql --profile '$XYZ_EXASOL_PROFILE' 'SELECT USER_NAME, STATUS, COUNT(*) AS SESSION_COUNT FROM EXA_DBA_SESSIONS GROUP BY USER_NAME, STATUS ORDER BY USER_NAME, STATUS'")"
if [ "$status" = "0" ]; then
  xyz_result "Exasol session inventory" "session grouping query succeeds" "$(xyz_artifact_text "exasol_session_count")" "PASS" "Useful for leak/drift comparison across runs"
else
  xyz_result "Exasol session inventory" "session grouping query succeeds" "$(xyz_artifact_text "exasol_session_count")" "FAIL" ""
fi

if xyz_http_probe "grafana_unauth_datasources" "$XYZ_GRAFANA_URL/api/datasources" "401,403"; then
  xyz_result "Grafana unauth API boundary" "401/403" "$(xyz_status_code "grafana_unauth_datasources")" "PASS" ""
else
  xyz_result "Grafana unauth API boundary" "401/403" "$(xyz_status_code "grafana_unauth_datasources")" "FAIL" ""
fi

status="$(xyz_cmd_sh "github_backup_workflow_latest" "gh run list --workflow repo-backup.yml --limit 1 --json databaseId,status,conclusion,headSha,createdAt --jq '.[0]'")"
actual="$(xyz_artifact_text "github_backup_workflow_latest")"
if [ "$status" = "0" ] && grep -q '"conclusion":"success"' artifacts/github_backup_workflow_latest.out; then
  xyz_result "GitHub backup workflow latest run" "latest run succeeded" "$actual" "PASS" "XYZ-PILOT-088 Phase 1/2 continuity"
else
  xyz_result "GitHub backup workflow latest run" "latest run succeeded" "$actual" "WARN" "Manual or scheduled backup may still be in progress; inspect GitHub Actions"
fi

status="$(xyz_cmd_sh "github_ruleset_remote_active" "gh api repos/<GITHUB_OWNER>/<PROJECT_SLUG>/rulesets/16995273 --jq '{id,name,enforcement,target,conditions}'")"
actual="$(xyz_artifact_text "github_ruleset_remote_active")"
if [ "$status" = "0" ] && grep -q '"enforcement":"active"' artifacts/github_ruleset_remote_active.out; then
  xyz_result "GitHub remote ruleset state" "ruleset present and active" "$actual" "PASS" "XYZ-PILOT-088 Phase 3 active governance"
else
  xyz_result "GitHub remote ruleset state" "ruleset present and active" "$actual" "FAIL" "XYZ-PILOT-088 Phase 3 governance drift"
fi

status="$(xyz_cmd_sh "github_backup_s3_latest_manifest" "aws s3api head-object --bucket '$repo_backup_bucket' --key latest/latest.json --query '{Size:ContentLength,LastModified:LastModified,Metadata:Metadata}' --output json --no-cli-pager")"
actual="$(xyz_artifact_text "github_backup_s3_latest_manifest")"
if [ "$status" = "0" ] && grep -q '"Size"' artifacts/github_backup_s3_latest_manifest.out; then
  xyz_result "S3 repo backup latest manifest" "latest/latest.json exists" "$actual" "PASS" "Metadata only; object body not downloaded"
else
  xyz_result "S3 repo backup latest manifest" "latest/latest.json exists" "$actual" "FAIL" "XYZ-PILOT-088 backup drift"
fi

xyz_agent_finish
