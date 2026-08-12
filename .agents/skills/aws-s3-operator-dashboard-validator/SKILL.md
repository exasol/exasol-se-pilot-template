---
name: aws-s3-operator-dashboard-validator
description: Use when validating the AWS S3-hosted <PILOT_NAME> Operator Dashboard static website, bucket metadata, website configuration, public-read posture, index object freshness, and Lambda/SSM/Cognito dashboard flow without downloading secrets.
---

# AWS S3 Operator Dashboard Validator

Use this skill for the S3 static status/operator dashboard.

## Safety Rules

- Do not upload, delete, overwrite, or change bucket policy/configuration unless
  explicitly approved.
- Do not print embedded trigger tokens, credentials, or dashboard secret
  material if the HTML is downloaded.
- Prefer `head-object`, website config, public access block, and HTTP status
  checks over full object reads.
- Summarize bucket policy posture instead of pasting long policies.

## Read-Only Workflow

1. Identify the dashboard bucket from docs or Lambda config metadata.
2. Check bucket location, website configuration, public-access block, and policy
   status with `aws s3api get-*` calls.
3. Check `index.html` freshness and metadata with `aws s3api head-object`.
4. Fetch the public website URL headers or status with `curl -I`; do not print
   body content by default.
5. Validate that documented dashboard URLs match live bucket website endpoints.

## Report Signals

- `PASS`: bucket exists in expected region, website config serves `index.html`,
  object is fresh enough, and public URL returns expected status.
- `WARN`: public-read posture is intentional but broad, stale dashboard object,
  docs mention old URL, or no cache headers.
- `FAIL`: missing bucket/object, website disabled, access denied to public
  dashboard when it should be public, or dashboard leaks sensitive material.
