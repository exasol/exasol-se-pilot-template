---
name: aws-eventbridge-scheduler-validator
description: Use when validating AWS EventBridge rules or EventBridge Scheduler schedules for <PILOT_NAME> start/stop, daily-status, weekend recap, Cognito sync, one-shot jobs, target wiring, cron/rate expressions, enabled state, and DST risk without mutating schedules.
---

# AWS EventBridge Scheduler Validator

Use this skill for scheduled operational wiring around the <PILOT_NAME> runtime.

## Safety Rules

- Use only read-only `list-*`, `describe-*`, and `get-*` calls by default.
- Do not enable, disable, delete, create, or update rules/schedules unless
  explicitly approved.
- Do not invoke targets from this skill.
- Treat schedule input payloads as potentially sensitive; summarize shape and
  redact tokens.

## Read-Only Workflow

1. List EventBridge rules with the `xyz-pilot` prefix:
   `aws events list-rules --region eu-central-1`.
2. For each relevant rule, inspect targets with
   `aws events list-targets-by-rule`.
3. Check EventBridge Scheduler separately with
   `aws scheduler list-schedules` and `aws scheduler get-schedule` when present.
4. Compare cron/rate expressions against runbook expectations:
   start 12:00 CEST, stop 24:00 CEST, daily status 06:00 CEST, cognito sync
   hourly, and one-shot date-bound jobs.
5. Flag DST-sensitive fixed-UTC expressions and expired one-shot schedules.

## Report Signals

- `PASS`: expected rules exist, are enabled/disabled as intended, target the
  correct Lambdas, and payload shape is coherent.
- `WARN`: DST drift exposure, fired one-shot left enabled, empty Scheduler
  namespace when docs mention schedules, or stale target names.
- `FAIL`: missing rules, disabled required rule, wrong target Lambda, malformed
  cron/rate expression, or target permission likely prevents invocation.
