#!/usr/bin/env bash
# Deploy Operator Dashboard user-management Lambda after onboarding-state changes.
set -euo pipefail

cd "$(dirname "$0")"

REGION="${AWS_REGION:-<AWS_REGION>}"
ACCOUNT_ID="${XYZ_AWS_ACCOUNT_ID:-<SOURCE_AWS_ACCOUNT_ID>}"
FUNCTION_NAME="${XYZ_USER_MGMT_FUNCTION:-xyz-pilot-user-mgmt}"
ROLE_NAME="${XYZ_USER_MGMT_ROLE_NAME:-xyz-pilot-user-mgmt-role}"
ZIP_FILE="xyz_pilot_user_mgmt.zip"
STATE_BUCKET="${XYZ_USER_MGMT_STATE_BUCKET:-xyz-pilot-status-${ACCOUNT_ID}}"
STATE_KEY="${XYZ_USER_MGMT_STATE_KEY:-sns-watchdog/state.json}"
WARMUP_PROGRESS_KEY="${XYZ_USER_MGMT_WARMUP_PROGRESS_KEY:-warmup/progress.json}"
LICENSE_EVIDENCE_KEY="${XYZ_USER_MGMT_LICENSE_EVIDENCE_KEY:-license/evidence.json}"
GITHUB_TOKEN_SECRET_ID="${XYZ_USER_MGMT_GITHUB_TOKEN_SECRET_ID:-xyz-pilot/github/service-token}"
GITHUB_TOKEN_PARAM="${XYZ_USER_MGMT_GITHUB_TOKEN_PARAM:-/xyz-pilot/github/service-token}"
BACKLOG_MIN_RUN_ID="${XYZ_USER_MGMT_BACKLOG_MIN_RUN_ID:-2026-06-03T000000Z}"
BACKLOG_LIMIT="${XYZ_USER_MGMT_BACKLOG_LIMIT:-50}"
CANDIDATE_FILE_VISIBLE_LIMIT="${XYZ_USER_MGMT_CANDIDATE_FILE_VISIBLE_LIMIT:-10}"
CANDIDATE_FILE_SCAN_LIMIT="${XYZ_USER_MGMT_CANDIDATE_FILE_SCAN_LIMIT:-40}"
USER_POOL_ID="${XYZ_USER_POOL_ID:-${COGNITO_USER_POOL_ID:-}}"
if [ -z "$USER_POOL_ID" ]; then
  if [ "$REGION" = "<AWS_REGION>" ]; then
    USER_POOL_ID="<COGNITO_USER_POOL_ID>"
  else
    USER_POOL_ID="<COGNITO_USER_POOL_ID>"
  fi
fi

zip -q "$ZIP_FILE" xyz_pilot_user_mgmt.py

policy_file="$(mktemp -t xyz-user-mgmt-state-policy.XXXXXX.json)"
cognito_policy_file="$(mktemp -t xyz-user-mgmt-cognito-policy.XXXXXX.json)"
github_token_policy_file="$(mktemp -t xyz-user-mgmt-github-token-policy.XXXXXX.json)"
current_env_file="$(mktemp -t xyz-user-mgmt-current-env.XXXXXX.json)"
env_file="$(mktemp -t xyz-user-mgmt-env.XXXXXX.json)"
cleanup() {
  rm -f "$policy_file" "$cognito_policy_file" "$github_token_policy_file" "$current_env_file" "$env_file"
}
trap cleanup EXIT

export STATE_BUCKET STATE_KEY WARMUP_PROGRESS_KEY LICENSE_EVIDENCE_KEY
python3 - <<'PY' > "$policy_file"
import json
import os

bucket = os.environ["STATE_BUCKET"]
keys = [
    os.environ["STATE_KEY"],
    os.environ["WARMUP_PROGRESS_KEY"],
    os.environ["LICENSE_EVIDENCE_KEY"],
]
print(json.dumps({
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "ReadOperatorDashboardState",
            "Effect": "Allow",
            "Action": ["s3:GetObject"],
            "Resource": [f"<AWS_ARN>{bucket}/{key}" for key in keys],
        }
    ],
}))
PY

aws iam put-role-policy \
  --region "$REGION" \
  --role-name "$ROLE_NAME" \
  --policy-name UserMgmtOnboardingStateRead \
  --policy-document "file://${policy_file}" \
  --no-cli-pager >/dev/null

export REGION ACCOUNT_ID GITHUB_TOKEN_SECRET_ID GITHUB_TOKEN_PARAM
python3 - <<'PY' > "$github_token_policy_file"
import json
import os

region = os.environ["REGION"]
account = os.environ["ACCOUNT_ID"]
secret_id = os.environ["GITHUB_TOKEN_SECRET_ID"].lstrip("/")
param = os.environ["GITHUB_TOKEN_PARAM"].lstrip("/")
print(json.dumps({
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "ReadGithubTokenSecret",
            "Effect": "Allow",
            "Action": ["secretsmanager:GetSecretValue"],
            "Resource": f"<AWS_ARN>{region}:{account}:secret:{secret_id}-*",
        },
        {
            "Sid": "ReadGithubTokenParameter",
            "Effect": "Allow",
            "Action": ["ssm:GetParameter"],
            "Resource": f"<AWS_ARN>{region}:{account}:parameter/{param}",
        },
        {
            "Sid": "DecryptGithubTokenParameter",
            "Effect": "Allow",
            "Action": "kms:Decrypt",
            "Resource": f"<AWS_ARN>{region}:{account}:alias/aws/ssm",
        },
    ],
}))
PY

aws iam put-role-policy \
  --region "$REGION" \
  --role-name "$ROLE_NAME" \
  --policy-name UserMgmtGithubTokenRead \
  --policy-document "file://${github_token_policy_file}" \
  --no-cli-pager >/dev/null

ensure_group() { # $1 group $2 precedence $3 description
  local group="$1" precedence="$2" description="$3"
  if aws cognito-idp get-group \
      --region "$REGION" \
      --user-pool-id "$USER_POOL_ID" \
      --group-name "$group" \
      --no-cli-pager >/dev/null 2>&1; then
    echo "Cognito group exists: ${group}"
  else
    aws cognito-idp create-group \
      --region "$REGION" \
      --user-pool-id "$USER_POOL_ID" \
      --group-name "$group" \
      --precedence "$precedence" \
      --description "$description" \
      --no-cli-pager >/dev/null
    echo "Created Cognito group: ${group}"
  fi
}

ensure_group OPS_ONLY  10 "Full <PILOT_NAME> operator access including XYZ_MASTER, GitHub and swarm panels"
ensure_group XYZ_OPS   20 "Limited Operator Dashboard access plus ability to invite XYZ_OPS and XYZ_BIZ users"
ensure_group XYZ_PILOT 30 "Limited Operator Dashboard access without invite rights"
ensure_group XYZ_BIZ   40 "Chatbot and business-data access only; no Operator Dashboard access"

export REGION ACCOUNT_ID USER_POOL_ID
python3 - <<'PY' > "$cognito_policy_file"
import json
import os

region = os.environ["REGION"]
account = os.environ["ACCOUNT_ID"]
pool = os.environ["USER_POOL_ID"]
resource = f"<AWS_ARN>{region}:{account}:userpool/{pool}"
print(json.dumps({
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "UserMgmtCognitoGroupAndUserAccess",
            "Effect": "Allow",
            "Action": [
                "cognito-idp:AdminAddUserToGroup",
                "cognito-idp:AdminCreateUser",
                "cognito-idp:AdminGetUser",
                "cognito-idp:AdminListGroupsForUser",
                "cognito-idp:GetGroup",
                "cognito-idp:ListUsers",
            ],
            "Resource": resource,
        }
    ],
}))
PY

aws iam put-role-policy \
  --region "$REGION" \
  --role-name "$ROLE_NAME" \
  --policy-name UserMgmtCognitoGroupRbac \
  --policy-document "file://${cognito_policy_file}" \
  --no-cli-pager >/dev/null

aws lambda get-function-configuration \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --query 'Environment.Variables' \
  --output json \
  --no-cli-pager > "$current_env_file"

export current_env_file STATE_BUCKET STATE_KEY WARMUP_PROGRESS_KEY LICENSE_EVIDENCE_KEY GITHUB_TOKEN_SECRET_ID GITHUB_TOKEN_PARAM BACKLOG_MIN_RUN_ID BACKLOG_LIMIT CANDIDATE_FILE_VISIBLE_LIMIT CANDIDATE_FILE_SCAN_LIMIT USER_POOL_ID
python3 - <<'PY' > "$env_file"
import json
import os
from pathlib import Path

current = json.loads(Path(os.environ["current_env_file"]).read_text() or "{}")
if not isinstance(current, dict):
    current = {}
current.pop("GITHUB_TOKEN", None)
current["GITHUB_TOKEN_SECRET_ID"] = os.environ["GITHUB_TOKEN_SECRET_ID"]
current["GITHUB_TOKEN_PARAM"] = os.environ["GITHUB_TOKEN_PARAM"]
current["ONBOARDING_STATE_BUCKET"] = os.environ["STATE_BUCKET"]
current["ONBOARDING_STATE_KEY"] = os.environ["STATE_KEY"]
current["WARMUP_PROGRESS_KEY"] = os.environ["WARMUP_PROGRESS_KEY"]
current["LICENSE_EVIDENCE_KEY"] = os.environ["LICENSE_EVIDENCE_KEY"]
current["XYZ_MASTER_BACKLOG_MIN_RUN_ID"] = os.environ["BACKLOG_MIN_RUN_ID"]
current["XYZ_MASTER_BACKLOG_LIMIT"] = os.environ["BACKLOG_LIMIT"]
current["XYZ_MASTER_CANDIDATE_FILE_VISIBLE_LIMIT"] = os.environ["CANDIDATE_FILE_VISIBLE_LIMIT"]
current["XYZ_MASTER_CANDIDATE_FILE_SCAN_LIMIT"] = os.environ["CANDIDATE_FILE_SCAN_LIMIT"]
current["USER_POOL_ID"] = os.environ["USER_POOL_ID"]
print(json.dumps({"Variables": current}))
PY

aws lambda update-function-code \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --zip-file "fileb://${ZIP_FILE}" \
  --query '{FunctionName:FunctionName,LastUpdateStatus:LastUpdateStatus}' \
  --output json \
  --no-cli-pager

aws lambda wait function-updated \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --no-cli-pager

aws lambda update-function-configuration \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --environment "file://${env_file}" \
  --query '{FunctionName:FunctionName,State:State,LastUpdateStatus:LastUpdateStatus}' \
  --output json \
  --no-cli-pager

aws lambda wait function-updated \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --no-cli-pager

echo "Deployed ${FUNCTION_NAME}; user pool ${USER_POOL_ID}; state read paths s3://${STATE_BUCKET}/${STATE_KEY}, s3://${STATE_BUCKET}/${WARMUP_PROGRESS_KEY}, s3://${STATE_BUCKET}/${LICENSE_EVIDENCE_KEY}; GitHub token secret ${GITHUB_TOKEN_SECRET_ID}; SSM fallback ${GITHUB_TOKEN_PARAM}; backlog cutoff ${BACKLOG_MIN_RUN_ID}; scan limit ${CANDIDATE_FILE_SCAN_LIMIT}."
