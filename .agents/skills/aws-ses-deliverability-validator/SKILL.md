---
name: aws-ses-deliverability-validator
description: Use when validating AWS SES sandbox or production status, verified identities, send quota, bounce/complaint indicators, and <PILOT_NAME> notification deliverability for daily-status, start/stop, invite, or weekend recap emails without sending mail by default.
---

# AWS SES Deliverability Validator

Use this skill for SES checks behind scheduler notifications and subscriber
onboarding.

## Safety Rules

- Do not send email unless explicitly requested.
- Do not print message bodies or full recipient lists by default.
- Treat email addresses as personal data; prefer aggregate verification counts
  and redact local parts unless named roster validation is requested.
- Do not modify SES identities, suppression lists, configuration sets, or
  production-access settings without explicit approval.

## Read-Only Workflow

1. Check account/sandbox status with SESv2:
   `aws sesv2 get-account --region eu-central-1`.
2. Check send quota/statistics with `aws ses get-send-quota` and
   `aws ses get-send-statistics`.
3. List identities and verification status with
   `aws ses list-identities` and
   `aws ses get-identity-verification-attributes`; report counts by status.
4. Compare Lambda `SES_TO` environment key presence without printing values.
5. Inspect recent Lambda log error signatures for `MessageRejected`,
   `Throttling`, or unverified recipient warnings with redaction.

## Report Signals

- `PASS`: sender and expected recipient set are verified, quota is adequate,
  and no recent deliverability errors.
- `WARN`: sandbox mode, unverified candidates, stale runbook roster, or low
  quota margin.
- `FAIL`: sender unverified, production path depends on unverified recipients,
  repeated `MessageRejected`, or SES permissions missing.
