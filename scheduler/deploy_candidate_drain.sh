#!/usr/bin/env bash
# Deploy XYZ-PILOT-208 candidate-drain Lambda and its hourly EventBridge rule.
set -euo pipefail

cd "$(dirname "$0")"

REGION="${AWS_REGION:-<AWS_REGION>}"
ACCOUNT_ID="${XYZ_AWS_ACCOUNT_ID:-<SOURCE_AWS_ACCOUNT_ID>}"
FUNCTION_NAME="${XYZ_CANDIDATE_DRAIN_FUNCTION:-xyz-pilot-candidate-drain}"
ROLE_ARN="${XYZ_CANDIDATE_DRAIN_ROLE_ARN:-arn:aws:iam::${ACCOUNT_ID}:role/xyz-pilot-scheduler}"
ZIP_FILE="xyz_master_candidate_drain.zip"
RULE_NAME="${XYZ_CANDIDATE_DRAIN_RULE:-xyz-pilot-candidate-drain-hourly}"
STATE_BUCKET="${XYZ_CANDIDATE_DRAIN_STATE_BUCKET:-xyz-pilot-status-${ACCOUNT_ID}}"
STATE_KEY="${XYZ_CANDIDATE_DRAIN_STATE_KEY:-candidate-drain/state.json}"
GITHUB_REPO="${XYZ_CANDIDATE_DRAIN_GITHUB_REPO:-<GITHUB_OWNER>/<PROJECT_SLUG>}"
GITHUB_REF="${XYZ_CANDIDATE_DRAIN_GITHUB_REF:-main}"
SES_FROM="${XYZ_CANDIDATE_DRAIN_SES_FROM:-<OPERATOR_EMAIL>}"
SES_TO="${XYZ_CANDIDATE_DRAIN_SES_TO:-<OPERATOR_EMAIL>,<OPS_USER_2_EMAIL>}"
GITHUB_TOKEN_SECRET_ID="${XYZ_CANDIDATE_DRAIN_GITHUB_TOKEN_SECRET_ID:-xyz-pilot/github/service-token}"
GITHUB_TOKEN_PARAM="${XYZ_CANDIDATE_DRAIN_GITHUB_TOKEN_PARAM:-/xyz-pilot/github/service-token}"
AUTO_FILE_LOW_SEVERITY="${XYZ_CANDIDATE_DRAIN_AUTO_FILE_LOW_SEVERITY:-true}"
AUTO_FILE_WORKFLOW_ID="${XYZ_CANDIDATE_DRAIN_AUTO_FILE_WORKFLOW_ID:-xyz-master-background-filing.yml}"
AUTO_FILE_MODE="${XYZ_CANDIDATE_DRAIN_AUTO_FILE_MODE:-direct-main}"
MAX_AUTO_DISPATCH_PER_FILE="${XYZ_CANDIDATE_DRAIN_MAX_AUTO_DISPATCH_PER_FILE:-10}"

zip -q "$ZIP_FILE" xyz_master_candidate_drain.py

aws iam put-role-policy \
  --region "$REGION" \
  --role-name xyz-pilot-scheduler \
  --policy-name xyz-pilot-scheduler-inline \
  --policy-document file://iam/lambda-permissions.json \
  --no-cli-pager >/dev/null

env_file="$(mktemp -t xyz-candidate-drain-env.XXXXXX.json)"
target_file="$(mktemp -t xyz-candidate-drain-target.XXXXXX.json)"
cleanup() {
  rm -f "$env_file"
  rm -f "$target_file"
}
trap cleanup EXIT

export GITHUB_REPO GITHUB_REF GITHUB_TOKEN_SECRET_ID GITHUB_TOKEN_PARAM STATE_BUCKET STATE_KEY SES_FROM SES_TO
export AUTO_FILE_LOW_SEVERITY AUTO_FILE_WORKFLOW_ID AUTO_FILE_MODE MAX_AUTO_DISPATCH_PER_FILE
python3 - <<'PY' > "$env_file"
import json
import os

print(json.dumps({
    "Variables": {
        "GITHUB_REPO": os.environ["GITHUB_REPO"],
        "GITHUB_REF": os.environ["GITHUB_REF"],
        "GITHUB_TOKEN_SECRET_ID": os.environ["GITHUB_TOKEN_SECRET_ID"],
        "GITHUB_TOKEN_PARAM": os.environ["GITHUB_TOKEN_PARAM"],
        "STATE_BUCKET": os.environ["STATE_BUCKET"],
        "STATE_KEY": os.environ["STATE_KEY"],
        "SES_FROM": os.environ["SES_FROM"],
        "SES_TO": os.environ["SES_TO"],
        "MAX_CANDIDATES": "25",
        "MAX_AUTO_DISPATCH_PER_FILE": os.environ["MAX_AUTO_DISPATCH_PER_FILE"],
        "AUTO_FILE_LOW_SEVERITY": os.environ["AUTO_FILE_LOW_SEVERITY"],
        "AUTO_FILE_WORKFLOW_ID": os.environ["AUTO_FILE_WORKFLOW_ID"],
        "AUTO_FILE_MODE": os.environ["AUTO_FILE_MODE"],
    }
}))
PY

if aws lambda get-function-configuration \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --query FunctionName \
  --output text \
  --no-cli-pager >/dev/null 2>&1; then
  aws lambda update-function-code \
    --region "$REGION" \
    --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://${ZIP_FILE}" \
    --query '{FunctionName:FunctionName,LastUpdateStatus:LastUpdateStatus}' \
    --output json \
    --no-cli-pager
else
  aws lambda create-function \
    --region "$REGION" \
    --function-name "$FUNCTION_NAME" \
    --runtime python3.13 \
    --role "$ROLE_ARN" \
    --handler xyz_master_candidate_drain.lambda_handler \
    --zip-file "fileb://${ZIP_FILE}" \
    --timeout 120 \
    --memory-size 256 \
    --environment "file://${env_file}" \
    --tags Pilot=XYZ-PILOT,Owner=<OPERATOR_EMAIL>,XYZPOC=XYZ-PILOT-208 \
    --query '{FunctionName:FunctionName,State:State,LastUpdateStatus:LastUpdateStatus}' \
    --output json \
    --no-cli-pager
fi

aws lambda wait function-updated \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --no-cli-pager

aws lambda update-function-configuration \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --handler xyz_master_candidate_drain.lambda_handler \
  --timeout 120 \
  --memory-size 256 \
  --environment "file://${env_file}" \
  --query '{FunctionName:FunctionName,State:State,LastUpdateStatus:LastUpdateStatus,Timeout:Timeout,MemorySize:MemorySize}' \
  --output json \
  --no-cli-pager

aws lambda wait function-updated \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --no-cli-pager

rule_arn="$(aws events put-rule \
  --region "$REGION" \
  --name "$RULE_NAME" \
  --schedule-expression 'cron(0 11-23 ? * MON-FRI *)' \
  --state ENABLED \
  --description 'XYZ-PILOT-208/215 hourly candidate drain with low-severity auto-filing during Pilot ops +1h (13:00-01:00 CEST)' \
  --query RuleArn \
  --output text \
  --no-cli-pager)"

lambda_arn="$(aws lambda get-function-configuration \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --query FunctionArn \
  --output text \
  --no-cli-pager)"

export FUNCTION_NAME lambda_arn
python3 - <<'PY' > "$target_file"
import json
import os

print(json.dumps([{
    "Id": os.environ["FUNCTION_NAME"],
    "Arn": os.environ["lambda_arn"],
    "Input": json.dumps({"source": "aws.events", "xyz_pilot": "XYZ-PILOT-208"}),
}]))
PY

aws events put-targets \
  --region "$REGION" \
  --rule "$RULE_NAME" \
  --targets "file://${target_file}" \
  --query 'FailedEntryCount' \
  --output text \
  --no-cli-pager

aws lambda add-permission \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --statement-id "eventbridge-${RULE_NAME}" \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "$rule_arn" \
  --no-cli-pager >/dev/null 2>&1 || true

echo "Deployed ${FUNCTION_NAME} and ${RULE_NAME} in ${REGION}."
