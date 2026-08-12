---
name: aws-cloudwatch-observability-validator
description: Use when validating AWS CloudWatch Logs and metrics for <PILOT_NAME> Lambdas and runtime services, including log retention, recent error signatures, invocation/error metrics, throttles, and alarm coverage without exposing secrets from logs.
---

# AWS CloudWatch Observability Validator

Use this skill for CloudWatch log and metric checks around the <PILOT_NAME>.

## Safety Rules

- Logs can contain secrets. Retrieve minimal windows and redact aggressively.
- Do not print tokens, cookies, passwords, authorization headers, prompt bodies,
  parameter values, or full environment dumps.
- Prefer metrics and counts over raw logs.
- Do not create/delete log groups, alarms, metric filters, or retention policies
  unless explicitly approved.

## Read-Only Workflow

1. List relevant log groups with
   `aws logs describe-log-groups --log-group-name-prefix /aws/lambda/xyz-pilot`.
2. Report retention, stored bytes, and last event time where available.
3. Pull Lambda metrics with CloudWatch `get-metric-statistics` for invocations,
   errors, duration, throttles, and concurrent executions.
4. If needed, inspect recent log events for redacted signatures:
   `ERROR`, `WARN`, `Exception`, `AccessDenied`, `Throttling`, `timeout`.
5. Check whether expected alarms or dashboards exist when docs claim them.

## Report Signals

- `PASS`: log groups exist with retention, metrics are healthy, and no recent
  redacted error signatures.
- `WARN`: no retention, growing log storage, sparse metrics, warnings, or
  missing alarms for important paths.
- `FAIL`: repeated errors, throttles, timeouts, missing log group for active
  Lambda, or logs expose secrets.
