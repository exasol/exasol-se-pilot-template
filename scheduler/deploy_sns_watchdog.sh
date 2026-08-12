#!/usr/bin/env bash
# Deploy XYZ-PILOT-209 onboarding-confirmation watchdog.
set -euo pipefail

cd "$(dirname "$0")"

REGION="${AWS_REGION:-eu-central-1}"
ACCOUNT_ID="${PILOT_AWS_ACCOUNT_ID:-<AWS_ACCOUNT_ID>}"
FUNCTION_NAME="${xyz_PILOT_SNS_WATCHDOG_FUNCTION:-<PROJECT_SLUG>-sns-confirmation-watchdog}"
ROLE_NAME="${xyz_PILOT_SNS_WATCHDOG_ROLE_NAME:-<PROJECT_SLUG>-scheduler}"
ROLE_ARN="${xyz_PILOT_SNS_WATCHDOG_ROLE_ARN:-arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}}"
ZIP_FILE="sns_watchdog.zip"
RULE_NAME="${xyz_PILOT_SNS_WATCHDOG_RULE:-<PROJECT_SLUG>-sns-watchdog-bizdays-13cest}"
WEEKLY_RULE_NAME="${xyz_PILOT_SNS_WATCHDOG_WEEKLY_RULE:-<PROJECT_SLUG>-sns-watchdog-weekly-pending-15cest}"
OLD_RULE_NAME="${xyz_PILOT_SNS_WATCHDOG_OLD_RULE:-<PROJECT_SLUG>-sns-watchdog-mon-thu}"
STATE_BUCKET="${xyz_PILOT_SNS_WATCHDOG_STATE_BUCKET:-<PROJECT_SLUG>-status-${ACCOUNT_ID}}"
STATE_KEY="${xyz_PILOT_SNS_WATCHDOG_STATE_KEY:-sns-watchdog/state.json}"
SNS_TOPIC_ARN="${xyz_PILOT_SNS_WATCHDOG_TOPIC_ARN:-arn:aws:sns:${REGION}:${ACCOUNT_ID}:<PROJECT_SLUG>-major-resolution-heads-up}"
REJECTION_TOPIC_ARN="${xyz_PILOT_SNS_WATCHDOG_REJECTION_TOPIC_ARN:-${SNS_TOPIC_ARN}}"
USER_POOL_ID="${xyz_PILOT_SNS_WATCHDOG_USER_POOL_ID:-<COGNITO_USER_POOL_ID>}"
SES_FROM="${xyz_PILOT_SNS_WATCHDOG_SES_FROM:-<OPERATOR_EMAIL>}"
OPERATOR_TO="${xyz_PILOT_SNS_WATCHDOG_OPERATOR_TO:-<OPS_ONLY_RECIPIENTS>}"
WATCHDOG_CC="${xyz_PILOT_SNS_WATCHDOG_CC:-${OPERATOR_TO}}"
EXPECTED_SNS_EMAILS="${xyz_PILOT_SNS_WATCHDOG_EXPECTED_EMAILS:-${OPERATOR_TO}}"
REJECTION_NOTIFY_TO="${xyz_PILOT_SNS_WATCHDOG_REJECTION_TO:-${OPERATOR_TO}}"
DEACTIVATED_USERS_JSON="${xyz_PILOT_SNS_WATCHDOG_DEACTIVATED_USERS_JSON:-}"
DEACTIVATED_EMAILS="${xyz_PILOT_SNS_WATCHDOG_DEACTIVATED_EMAILS:-}"
TAG_ENV_FILE="${xyz_PILOT_TAG_ENV_FILE:-../config/project.env}"

bash ../scripts/require_exasol_aws_tags.sh "$TAG_ENV_FILE"
set -a
# shellcheck disable=SC1090
source "$TAG_ENV_FILE"
set +a

LAMBDA_TAGS="Name=${FUNCTION_NAME},ManagedBy=scheduler-deploy-sns-watchdog,TrackerIssue=XYZ-PILOT-209,exa:Project=${PROJECT_SLUG},exa:Environment=${ENVIRONMENT},exa:Department=${DEPARTMENT},exa:Workload=automation,exa:CostCenter=${COST_CENTER},exa:Owner=${OPERATOR_EMAIL},exa:DataClassification=${DATA_CLASSIFICATION},exa:customer=${CUSTOMER_NAME}"
EVENT_RULE_TAGS=(
  "Key=Name,Value=${RULE_NAME}"
  "Key=ManagedBy,Value=scheduler-deploy-sns-watchdog"
  "Key=TrackerIssue,Value=XYZ-PILOT-209"
  "Key=exa:Project,Value=${PROJECT_SLUG}"
  "Key=exa:Environment,Value=${ENVIRONMENT}"
  "Key=exa:Department,Value=${DEPARTMENT}"
  "Key=exa:Workload,Value=automation"
  "Key=exa:CostCenter,Value=${COST_CENTER}"
  "Key=exa:Owner,Value=${OPERATOR_EMAIL}"
  "Key=exa:DataClassification,Value=${DATA_CLASSIFICATION}"
  "Key=exa:customer,Value=${CUSTOMER_NAME}"
)
WEEKLY_EVENT_RULE_TAGS=(
  "Key=Name,Value=${WEEKLY_RULE_NAME}"
  "Key=ManagedBy,Value=scheduler-deploy-sns-watchdog"
  "Key=TrackerIssue,Value=XYZ-PILOT-209"
  "Key=exa:Project,Value=${PROJECT_SLUG}"
  "Key=exa:Environment,Value=${ENVIRONMENT}"
  "Key=exa:Department,Value=${DEPARTMENT}"
  "Key=exa:Workload,Value=automation"
  "Key=exa:CostCenter,Value=${COST_CENTER}"
  "Key=exa:Owner,Value=${OPERATOR_EMAIL}"
  "Key=exa:DataClassification,Value=${DATA_CLASSIFICATION}"
  "Key=exa:customer,Value=${CUSTOMER_NAME}"
)

zip -q "$ZIP_FILE" sns_watchdog.py

policy_file="$(mktemp -t xyz-sns-watchdog-policy.XXXXXX.json)"
env_file="$(mktemp -t xyz-sns-watchdog-env.XXXXXX.json)"
target_file="$(mktemp -t xyz-sns-watchdog-target.XXXXXX.json)"
cleanup() {
  rm -f "$policy_file" "$env_file" "$target_file"
}
trap cleanup EXIT

export REGION ACCOUNT_ID SNS_TOPIC_ARN REJECTION_TOPIC_ARN USER_POOL_ID STATE_BUCKET STATE_KEY
python3 - <<'PY' > "$policy_file"
import json
import os

region = os.environ["REGION"]
account = os.environ["ACCOUNT_ID"]
pool = os.environ["USER_POOL_ID"]
bucket = os.environ["STATE_BUCKET"]
key = os.environ["STATE_KEY"]
topic_arns = sorted(set([
    os.environ["SNS_TOPIC_ARN"],
    os.environ["REJECTION_TOPIC_ARN"],
]))

print(json.dumps({
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "SnsWatchdogTopicLifecycle",
            "Effect": "Allow",
            "Action": [
                "sns:ListSubscriptionsByTopic",
                "sns:Subscribe",
                "sns:Publish",
            ],
            "Resource": topic_arns,
        },
        {
            "Sid": "SnsWatchdogSes",
            "Effect": "Allow",
            "Action": [
                "ses:SendEmail",
                "ses:VerifyEmailIdentity",
                "ses:GetIdentityVerificationAttributes",
            ],
            "Resource": "*",
        },
        {
            "Sid": "SnsWatchdogCognito",
            "Effect": "Allow",
            "Action": [
                "cognito-idp:ListUsers",
                "cognito-idp:AdminCreateUser",
                "cognito-idp:AdminDisableUser",
            ],
            "Resource": f"arn:aws:cognito-idp:{region}:{account}:userpool/{pool}",
        },
        {
            "Sid": "SnsWatchdogStateBucketList",
            "Effect": "Allow",
            "Action": ["s3:ListBucket"],
            "Resource": f"arn:aws:s3:::{bucket}",
            "Condition": {"StringLike": {"s3:prefix": [key, key.rsplit("/", 1)[0] + "/*"]}},
        },
        {
            "Sid": "SnsWatchdogStateObject",
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:PutObject"],
            "Resource": f"arn:aws:s3:::{bucket}/{key}",
        },
        {
            "Sid": "SnsWatchdogLogs",
            "Effect": "Allow",
            "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": "*",
        },
    ],
}))
PY

aws iam put-role-policy \
  --region "$REGION" \
  --role-name "$ROLE_NAME" \
  --policy-name SnsConfirmationWatchdog \
  --policy-document "file://${policy_file}" \
  --no-cli-pager >/dev/null

export SNS_TOPIC_ARN REJECTION_TOPIC_ARN SES_FROM WATCHDOG_CC EXPECTED_SNS_EMAILS REJECTION_NOTIFY_TO USER_POOL_ID STATE_BUCKET STATE_KEY DEACTIVATED_USERS_JSON DEACTIVATED_EMAILS
python3 - <<'PY' > "$env_file"
import json
import os

variables = {
    "SNS_TOPIC_ARN": os.environ["SNS_TOPIC_ARN"],
    "REJECTION_TOPIC_ARN": os.environ["REJECTION_TOPIC_ARN"],
    "SES_FROM": os.environ["SES_FROM"],
    "WATCHDOG_CC": os.environ["WATCHDOG_CC"],
    "EXPECTED_SNS_EMAILS": os.environ["EXPECTED_SNS_EMAILS"],
    "REJECTION_NOTIFY_TO": os.environ["REJECTION_NOTIFY_TO"],
    "USER_POOL_ID": os.environ["USER_POOL_ID"],
    "STATE_BUCKET": os.environ["STATE_BUCKET"],
    "STATE_KEY": os.environ["STATE_KEY"],
    "SNS_TTL_HOURS": "72",
    "SES_VERIFICATION_TTL_HOURS": "24",
    "COGNITO_TEMP_PASSWORD_TTL_DAYS": "7",
}
if os.environ.get("DEACTIVATED_USERS_JSON"):
    variables["DEACTIVATED_USERS_JSON"] = os.environ["DEACTIVATED_USERS_JSON"]
if os.environ.get("DEACTIVATED_EMAILS"):
    variables["DEACTIVATED_EMAILS"] = os.environ["DEACTIVATED_EMAILS"]
print(json.dumps({"Variables": variables}))
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
    --handler sns_watchdog.lambda_handler \
    --zip-file "fileb://${ZIP_FILE}" \
    --timeout 120 \
    --memory-size 256 \
    --environment "file://${env_file}" \
    --tags "$LAMBDA_TAGS" \
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
  --handler sns_watchdog.lambda_handler \
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
  --schedule-expression 'cron(0 11 ? * MON-FRI *)' \
  --state ENABLED \
  --description 'XYZ-PILOT-209 onboarding confirmation watchdog at 13:00 CEST on Pilot operational business days' \
  --tags "${EVENT_RULE_TAGS[@]}" \
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
    "Input": json.dumps({"source": "aws.events", "xyz_pilot": "XYZ-PILOT-209"}),
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

weekly_rule_arn="$(aws events put-rule \
  --region "$REGION" \
  --name "$WEEKLY_RULE_NAME" \
  --schedule-expression 'cron(0 13 ? * MON *)' \
  --state ENABLED \
  --description 'XYZ-PILOT-209 weekly pending-user explainer email at Monday 15:00 CEST; no AWS confirmation reissue' \
  --tags "${WEEKLY_EVENT_RULE_TAGS[@]}" \
  --query RuleArn \
  --output text \
  --no-cli-pager)"

export FUNCTION_NAME lambda_arn
python3 - <<'PY' > "$target_file"
import json
import os

print(json.dumps([{
    "Id": os.environ["FUNCTION_NAME"],
    "Arn": os.environ["lambda_arn"],
    "Input": json.dumps({
        "source": "aws.events",
        "xyz_pilot": "XYZ-PILOT-209",
        "mode": "weekly_pending_reminder",
    }),
}]))
PY

aws events put-targets \
  --region "$REGION" \
  --rule "$WEEKLY_RULE_NAME" \
  --targets "file://${target_file}" \
  --query 'FailedEntryCount' \
  --output text \
  --no-cli-pager

aws lambda add-permission \
  --region "$REGION" \
  --function-name "$FUNCTION_NAME" \
  --statement-id "eventbridge-${WEEKLY_RULE_NAME}" \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "$weekly_rule_arn" \
  --no-cli-pager >/dev/null 2>&1 || true

aws events disable-rule \
  --region "$REGION" \
  --name "$OLD_RULE_NAME" \
  --no-cli-pager >/dev/null 2>&1 || true

echo "Deployed ${FUNCTION_NAME}; enabled ${RULE_NAME} and ${WEEKLY_RULE_NAME}; disabled ${OLD_RULE_NAME} if present."
