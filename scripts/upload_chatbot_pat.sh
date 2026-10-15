#!/usr/bin/env zsh
# Upload the XYZ-PILOT-043 chatbot GitHub PAT into AWS SSM Parameter Store.
#
# Why this exists as a script and not an inline snippet:
# zsh terminals hard-wrap pasted long lines, breaking multi-line shell
# commands. A real file dodges all paste-mangling.
#
# Usage:
#   ./scripts/upload_chatbot_pat.sh
#
# Prompts (silently) for the github_pat_… token, uploads to
#   /<PROJECT_SLUG>/github/service-token  (SecureString, KMS-encrypted)
# in eu-central-1. The PAT is in this shell process only; cleared on exit.
#
# Required AWS credentials: an IAM principal with
#   ssm:PutParameter
#   ssm:AddTagsToResource
# (the default AWS_PROFILE on the dev mac already has these).

set -euo pipefail

PARAM_NAME="/<PROJECT_SLUG>/github/service-token"
REGION="eu-central-1"
TAG_ENV_FILE="${xyz_PILOT_TAG_ENV_FILE:-config/project.env}"

bash scripts/require_exasol_aws_tags.sh "$TAG_ENV_FILE"
set -a
# shellcheck disable=SC1090
source "$TAG_ENV_FILE"
set +a

# Detect whether the parameter already exists so we send --overwrite when needed.
# `describe-parameters` is a metadata call; doesn't reveal the value.
EXISTING=$(aws ssm describe-parameters \
    --filters "Key=Name,Values=${PARAM_NAME}" \
    --region "${REGION}" \
    --query "Parameters[0].Name" --output text 2>/dev/null || echo "None")

OVERWRITE_FLAG=""
if [ "$EXISTING" != "None" ] && [ -n "$EXISTING" ]; then
    echo "Parameter ${PARAM_NAME} already exists — will overwrite (new version)."
    OVERWRITE_FLAG="--overwrite"
fi

read -rs "GH_PAT?Paste github_pat_… : "
echo

if [ -z "${GH_PAT}" ]; then
    echo "ERROR: empty input — aborting." >&2
    exit 1
fi
if [[ "${GH_PAT}" != github_pat_* ]]; then
    echo "WARNING: input doesn't start with 'github_pat_' — continuing anyway, but check the value if the chatbot later fails GitHub auth." >&2
fi

# Tags only make sense on the *initial* create; --overwrite ignores them.
TAG_FLAGS=()
if [ -z "$OVERWRITE_FLAG" ]; then
    TAG_FLAGS=(--tags
        "Key=Name,Value=${PARAM_NAME}"
        "Key=ManagedBy,Value=scripts-upload-chatbot-pat"
        "Key=TrackerIssue,Value=XYZ-PILOT-043"
        "Key=exa:Project,Value=${PROJECT_SLUG}"
        "Key=exa:Environment,Value=${ENVIRONMENT}"
        "Key=exa:Department,Value=${DEPARTMENT}"
        "Key=exa:Workload,Value=automation"
        "Key=exa:CostCenter,Value=${COST_CENTER}"
        "Key=exa:Owner,Value=${OPERATOR_EMAIL}"
        "Key=exa:DataClassification,Value=${DATA_CLASSIFICATION}"
        "Key=exa:customer,Value=${CUSTOMER_NAME}")
fi

VERSION=$(aws ssm put-parameter \
    --name "${PARAM_NAME}" \
    --description "XYZ-PILOT-043 chatbot · Contents:RW + Issues:RW on <GITHUB_REPOSITORY> · rotate annually" \
    --type SecureString \
    --tier Standard \
    --value "${GH_PAT}" \
    ${OVERWRITE_FLAG} \
    "${TAG_FLAGS[@]}" \
    --region "${REGION}" \
    --query Version --output text)

unset GH_PAT

echo "✓ Parameter uploaded · version ${VERSION} · ${PARAM_NAME} · region ${REGION}"
echo
echo "Verify (metadata only, no value reveal):"
echo "  aws ssm describe-parameters \\"
echo "    --filters \"Key=Name,Values=${PARAM_NAME}\" \\"
echo "    --region ${REGION} --output table"
