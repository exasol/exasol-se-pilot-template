---
name: aws-ssm-parameter-store-safety
description: Use when validating AWS SSM Parameter Store metadata, SecureString usage, naming conventions, access boundaries, and Lambda secret retrieval paths for <PILOT_NAME> without decrypting or printing parameter values.
---

# AWS SSM Parameter Store Safety

Use this skill for SSM Parameter Store checks around <PILOT_NAME> secret retrieval.

## Safety Rules

- Never call `get-parameter --with-decryption` for validation.
- Do not print parameter values, encrypted blobs, tokens, or passwords.
- List only names, types, tiers, key IDs when needed, last modified metadata,
  and policy metadata.
- Do not create, overwrite, delete, label, or rotate parameters unless
  explicitly requested.

## Read-Only Workflow

1. List relevant metadata with
   `aws ssm describe-parameters --region eu-central-1`.
2. Filter to expected prefixes such as `/xyz-pilot/secrets/` and
   `/xyz-pilot/chatbot/`.
3. Confirm secret-like entries use `SecureString`, not `String`.
4. Review Lambda IAM permissions for least-privilege access to expected
   parameter ARNs.
5. Check CloudWatch logs for redacted errors such as `ParameterNotFound`,
   `AccessDeniedException`, or decryption failures.

## Report Signals

- `PASS`: expected parameters exist as `SecureString`, names follow the
  documented prefixes, and access is scoped.
- `WARN`: unused/stale parameters, broad `ssm:GetParameter*`, missing tags, or
  ambiguous names.
- `FAIL`: missing required parameter metadata, plaintext `String` for secrets,
  secret value exposed in logs/docs, or retrieval Lambda lacks access.
