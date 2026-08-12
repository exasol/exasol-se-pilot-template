---
name: aws-iam-least-privilege-reviewer
description: Use when reviewing AWS IAM roles, instance profiles, Lambda execution roles, Cognito Identity Pool role mappings, inline/attached policies, public Function URL permissions, and least-privilege boundaries for <PILOT_NAME> without changing permissions.
---

# AWS IAM Least-Privilege Reviewer

Use this skill for IAM review of <PILOT_NAME> execution roles and access
boundaries.

## Safety Rules

- Read-only review only by default.
- Do not attach, detach, create, delete, or update IAM policies, roles, users,
  instance profiles, or Lambda permissions unless explicitly approved.
- Do not print access keys, secret keys, session tokens, or credential reports
  containing sensitive data.
- IAM policies are not secrets, but summarize broad statements instead of
  dumping long documents unless necessary.

## Read-Only Workflow

1. Identify roles from docs and Lambda/EC2 metadata:
   scheduler Lambda role, EC2 SSM instance role, Cognito identity roles, and any
   Function URL resource policies.
2. Inspect role trust policies with `aws iam get-role`.
3. List inline and attached policies with `list-role-policies` and
   `list-attached-role-policies`.
4. Inspect inline policies with `get-role-policy`; summarize actions/resources,
   especially wildcards.
5. Check Lambda resource policies and Function URL auth type for public invoke
   posture.
6. Map permissions back to documented flows: EC2 start/stop, SSM parameter
   reads, SES send, S3 dashboard write, Cognito sync, Bedrock converse, and
   GitHub token retrieval.

## Report Signals

- `PASS`: permissions are scoped to documented resources and trust boundaries
  match intended services.
- `WARN`: wildcard resources/actions accepted for Pilot speed, public Function
  URL trade-offs, missing condition keys, or stale roles.
- `FAIL`: credentials exposed, unintended public invoke, privilege escalation
  risk, missing required permissions, or broad access unrelated to the Pilot.
