#!/usr/bin/env bash
# Minimal Phase 06 — scheduled graceful start/stop for the <AWS_REGION> target (029).
#
# Creates (idempotent): an IAM execution role (least-priv: ec2 start/stop on the
# one instance + describe + logs), one `xyz-pilot-scheduler` Lambda, and two
# EventBridge cron rules (start/stop) that invoke it with {"action": ...}. Rules
# are created DISABLED — schedules stay off until Phase 10 cutover. All resources
# carry the mandatory exa:* tags. Graceful Exasol db_stop on shutdown is handled
# by the host exasol-graceful.service unit; this only triggers the EC2 lifecycle.
#
# Do not run this bootstrap after the rich `xyz-pilot-start` / `xyz-pilot-stop`
# Lambdas exist. Those functions own the post-cutover lifecycle schedules; this
# minimal bootstrap would otherwise overwrite the EventBridge targets back to
# `xyz-pilot-scheduler`.
#
#   bash scripts/phase06_scheduler/deploy.sh
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-default}"
# Env overrides (defaults = <AWS_REGION> UAT target). For the <AWS_REGION> DEV env:
#   REGION=<AWS_REGION> INSTANCE=<EC2_INSTANCE_ID> ROLE=xyz-pilot-scheduler-role-dev \
#   ENV_TAG=development AWS_PROFILE=exasol bash scripts/phase06_scheduler/deploy.sh
# ROLE must differ per env — the role is global and its policy is instance-scoped.
REGION="${REGION:-<AWS_REGION>}"; EXPECT_ACCT=<AWS_ACCOUNT_ID>
INSTANCE="${INSTANCE:-<EC2_INSTANCE_ID>}"
FN=xyz-pilot-scheduler
ROLE="${ROLE:-xyz-pilot-scheduler-role}"
RUNTIME=python3.12
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENVTAG="${ENV_TAG:-staging}"
TAGS="exa:Project=XYZPILOT,exa:customer=XYZ,exa:Environment=$ENVTAG,exa:Department=SE,exa:CostCenter=20060,exa:Owner=<OPERATOR_USER>,exa:Workload=automation,exa:DataClassification=internal,Deployment=xyz-pilot-phase06"
A() { aws --region "$REGION" "$@" --no-cli-pager; }

ACCT=$(A sts get-caller-identity --query Account --output text)
[ "$ACCT" = "$EXPECT_ACCT" ] || { echo "ABORT: account $ACCT != expected $EXPECT_ACCT" >&2; exit 3; }
echo ">> account=$ACCT region=$REGION instance=$INSTANCE"

if A lambda get-function --function-name xyz-pilot-start >/dev/null 2>&1 \
   && A lambda get-function --function-name xyz-pilot-stop >/dev/null 2>&1; then
  echo "ABORT: rich xyz-pilot-start/xyz-pilot-stop already exist; run scripts/phase06_full/deploy_start_stop.sh for lifecycle schedule ownership." >&2
  exit 4
fi

# --- IAM role -------------------------------------------------------------
if ! A iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  A iam create-role --role-name "$ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --tags Key=exa:Project,Value=XYZPILOT Key=exa:Owner,Value=<OPERATOR_USER> Key=exa:Workload,Value=automation Key=exa:Environment,Value=$ENVTAG >/dev/null
  echo ">> created role $ROLE"
fi
A iam put-role-policy --role-name "$ROLE" --policy-name ec2-lifecycle --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
    {\"Effect\":\"Allow\",\"Action\":[\"ec2:StartInstances\",\"ec2:StopInstances\"],\"Resource\":\"<AWS_ARN>$REGION:$ACCT:instance/$INSTANCE\"},
    {\"Effect\":\"Allow\",\"Action\":\"ec2:DescribeInstances\",\"Resource\":\"*\"},
    {\"Effect\":\"Allow\",\"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",\"logs:PutLogEvents\"],\"Resource\":\"<AWS_ARN>$REGION:$ACCT:*\"}
  ]}"
ROLE_ARN=$(A iam get-role --role-name "$ROLE" --query Role.Arn --output text)
A iam tag-role --role-name "$ROLE" --tags Key=exa:Project,Value=XYZPILOT Key=exa:customer,Value=XYZ Key=exa:Environment,Value=$ENVTAG Key=exa:Department,Value=SE Key=exa:CostCenter,Value=20060 Key=exa:Owner,Value=<OPERATOR_USER> Key=exa:Workload,Value=automation Key=exa:DataClassification,Value=internal Key=Deployment,Value=xyz-pilot-phase06 >/dev/null 2>&1 || true

# --- package + Lambda (create or update) ----------------------------------
TMP=$(mktemp -d); cp "$SRC_DIR/lambda_function.py" "$TMP/"; ( cd "$TMP" && zip -q fn.zip lambda_function.py )
if A lambda get-function --function-name "$FN" >/dev/null 2>&1; then
  A lambda update-function-code --function-name "$FN" --zip-file "fileb://$TMP/fn.zip" >/dev/null
  A lambda update-function-configuration --function-name "$FN" --environment "Variables={EC2_INSTANCE_ID=$INSTANCE}" --timeout 60 --runtime "$RUNTIME" --handler lambda_function.lambda_handler >/dev/null
  echo ">> updated lambda $FN"
else
  for i in 1 2 3 4 5 6; do
    if A lambda create-function --function-name "$FN" --runtime "$RUNTIME" --role "$ROLE_ARN" \
        --handler lambda_function.lambda_handler --timeout 60 \
        --environment "Variables={EC2_INSTANCE_ID=$INSTANCE}" \
        --zip-file "fileb://$TMP/fn.zip" --tags "$TAGS" >/dev/null 2>/tmp/p6cf.err; then echo ">> created lambda $FN"; break; fi
    if grep -q 'cannot be assumed' /tmp/p6cf.err; then echo "   role propagating ($i)…"; sleep 8; else cat /tmp/p6cf.err >&2; exit 1; fi
  done
fi
FN_ARN=$(A lambda get-function --function-name "$FN" --query Configuration.FunctionArn --output text)
A lambda tag-resource --resource "$FN_ARN" --tags "$TAGS" >/dev/null 2>&1 || true

# --- EventBridge rules (DISABLED) + targets + invoke perms ----------------
mkrule() { # $1=rule name  $2=cron (no 'cron(...)')  $3=action
  A events put-rule --name "$1" --schedule-expression "cron($2)" --state DISABLED \
    --description "<PILOT_NAME> minimal Phase 06 $3 — DISABLED until Phase 10 cutover" >/dev/null
  local rarn; rarn=$(A events describe-rule --name "$1" --query Arn --output text)
  A lambda add-permission --function-name "$FN" --statement-id "evb-$1" --action lambda:InvokeFunction \
    --principal events.amazonaws.com --source-arn "$rarn" >/dev/null 2>&1 || true
  printf '[{"Id":"1","Arn":"%s","Input":"{\\"action\\":\\"%s\\"}"}]' "$FN_ARN" "$3" > "$TMP/tgt.json"
  A events put-targets --rule "$1" --targets "file://$TMP/tgt.json" >/dev/null
  A events tag-resource --resource-arn "$rarn" --tags Key=exa:Project,Value=XYZPILOT Key=exa:customer,Value=XYZ Key=exa:Environment,Value=$ENVTAG Key=exa:Department,Value=SE Key=exa:CostCenter,Value=20060 Key=exa:Owner,Value=<OPERATOR_USER> Key=exa:Workload,Value=automation Key=exa:DataClassification,Value=internal Key=Deployment,Value=xyz-pilot-phase06 >/dev/null 2>&1 || true
  echo ">> rule $1 ($3) → DISABLED, target=$FN"
}
mkrule xyz-pilot-start-schedule "0 10 ? * MON-FRI *" start   # 10:00 UTC = 12:00 CEST, Mon-Fri
mkrule xyz-pilot-stop-schedule  "0 22 * * ? *"       stop    # 22:00 UTC = 24:00 CEST, daily
rm -rf "$TMP"
echo ">> DONE — minimal Phase 06 deployed. Schedules are DISABLED; enable at Phase 10 cutover with:"
echo "   aws --profile default --region $REGION events enable-rule --name xyz-pilot-start-schedule"
echo "   aws --profile default --region $REGION events enable-rule --name xyz-pilot-stop-schedule"
