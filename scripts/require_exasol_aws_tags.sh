#!/usr/bin/env bash
set -euo pipefail

env_file="${1:-config/project.env}"

if [[ -f "$env_file" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
fi

required=(
  AWS_PROFILE
  AWS_REGION
  AWS_AZ
  PROJECT_SLUG
  CUSTOMER_NAME
  OPERATOR_EMAIL
  ENVIRONMENT
  DEPARTMENT
  COST_CENTER
  DATA_CLASSIFICATION
)

missing=()
for key in "${required[@]}"; do
  value="${!key:-}"
  if [[ -z "$value" || "$value" == "<"*">" ]]; then
    missing+=("$key")
  fi
done

if [[ "${AWS_TAGGING_POLICY:-}" != "exasol-it" ]]; then
  missing+=("AWS_TAGGING_POLICY=exasol-it")
fi

if [[ "${#missing[@]}" -gt 0 ]]; then
  printf 'Exasol IT AWS Resource Tagging Policy preflight failed.\n' >&2
  printf 'Populate these values before any AWS deployment into an account, region, or AZ:\n' >&2
  printf '  - %s\n' "${missing[@]}" >&2
  exit 2
fi

cat <<EOF
Exasol IT AWS tagging preflight OK
  AWS profile: ${AWS_PROFILE}
  Region/AZ: ${AWS_REGION}/${AWS_AZ}
  exa:Project=${PROJECT_SLUG}
  exa:Environment=${ENVIRONMENT}
  exa:Department=${DEPARTMENT}
  exa:CostCenter=${COST_CENTER}
  exa:Owner=${OPERATOR_EMAIL}
  exa:DataClassification=${DATA_CLASSIFICATION}
  exa:customer=${CUSTOMER_NAME}
EOF
