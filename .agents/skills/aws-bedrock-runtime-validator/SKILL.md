---
name: aws-bedrock-runtime-validator
description: Use when validating AWS Bedrock dependencies for the <PILOT_NAME> chatbot, daily-status narrative generation, Converse API configuration, model availability, tool-use behavior, Bedrock IAM permissions, or Bedrock-related Lambda/chatbot failures without exposing prompts or sensitive data.
---

# AWS Bedrock Runtime Validator

Use this skill for Bedrock checks in the <PILOT_NAME>, especially the chatbot and
daily-status Lambda paths that call the Converse API.

## Safety Rules

- Default to read-only metadata and log-shape checks.
- Do not print full prompts, completions, tool payloads, customer data, or
  tracker text sent to a model.
- Do not run live `bedrock-runtime converse` calls unless explicitly requested;
  they can incur cost and may process sensitive context.
- When inspecting logs, redact tokens, emails, passwords, prompts, and model
  payload fragments.

## Read-Only Workflow

1. Confirm region and account identity with `aws sts get-caller-identity`.
2. Verify model/catalog availability without invoking inference:
   `aws bedrock list-foundation-models --region eu-central-1`.
3. Inspect Lambda metadata for Bedrock-facing functions with
   `aws lambda get-function-configuration`, returning only state, runtime,
   timeout, memory, role, and environment variable names.
4. Review IAM policy shape for Bedrock permissions with
   `aws iam list-role-policies`, `aws iam get-role-policy`, and
   `aws iam list-attached-role-policies`.
5. Check CloudWatch logs only for redacted error signatures such as
   `AccessDeniedException`, `ValidationException`, throttling, timeout, or
   model-not-found.

## Report Signals

- `PASS`: model family is available, functions are healthy, IAM has the
  expected scoped Bedrock actions, and no recent redacted error signatures.
- `WARN`: model version/name drift, broad permissions, missing log retention,
  throttling, or stale docs.
- `FAIL`: Bedrock access denied, configured model unavailable, runtime errors,
  or chatbot/daily-status path cannot reach Bedrock.
