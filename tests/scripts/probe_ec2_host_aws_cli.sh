#!/usr/bin/env bash
set -euo pipefail

region="${xyz_PILOT_REGION:-eu-central-1}"
instance_id="${xyz_PILOT_EC2_INSTANCE_ID:-<EC2_INSTANCE_ID>}"

params="$(mktemp)"
trap 'rm -f "$params"' EXIT

printf '%s\n' '{"commands":["aws --version","aws sts get-caller-identity --query Account --output text --no-cli-pager"]}' > "$params"

command_id="$(
  aws ssm send-command \
    --region "$region" \
    --instance-ids "$instance_id" \
    --document-name AWS-RunShellScript \
    --comment "<CUSTOMER_NAME> swarm probe: EC2 host AWS CLI" \
    --parameters "file://$params" \
    --query 'Command.CommandId' \
    --output text \
    --no-cli-pager
)"

status="Pending"
for _ in $(seq 1 30); do
  status="$(
    aws ssm get-command-invocation \
      --region "$region" \
      --command-id "$command_id" \
      --instance-id "$instance_id" \
      --query 'Status' \
      --output text \
      --no-cli-pager 2>/dev/null || true
  )"
  case "$status" in
    Success|Failed|Cancelled|TimedOut)
      break
      ;;
  esac
  sleep 1
done

aws ssm get-command-invocation \
  --region "$region" \
  --command-id "$command_id" \
  --instance-id "$instance_id" \
  --query '{Status:Status,ResponseCode:ResponseCode,Stdout:StandardOutputContent,Stderr:StandardErrorContent}' \
  --output json \
  --no-cli-pager

test "$status" = "Success"
