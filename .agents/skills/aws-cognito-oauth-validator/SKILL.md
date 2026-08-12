---
name: aws-cognito-oauth-validator
description: Use when validating AWS Cognito user pools, hosted UI, OAuth/OIDC metadata, identity pools, app clients, callback/logout URLs, token TTLs, subscriber state, or Cognito-gated <PILOT_NAME> chatbot, MCP, and Operator Dashboard flows without printing secrets.
---

# AWS Cognito OAuth Validator

Use this skill for Cognito checks around chatbot SSO, public MCP OAuth, and the
Operator Dashboard secret-retrieval flow.

## Safety Rules

- Do not print Cognito tokens, refresh tokens, authorization codes, cookies, or
  app client secrets.
- Query app clients with explicit projections that exclude `ClientSecret`.
- Treat subscriber emails as personal data; prefer aggregate counts/statuses
  unless the user asks for named roster validation.
- Do not create, delete, disable, resend, or reset users unless explicitly
  requested.

## Read-Only Workflow

1. Confirm identity and region.
2. Inspect user pool and hosted UI domain:
   `aws cognito-idp describe-user-pool` and
   `aws cognito-idp describe-user-pool-domain`.
3. Inspect app-client OAuth shape with a safe query for callback URLs, logout
   URLs, scopes, supported providers, token validity, and auth flows.
4. Validate public OAuth discovery endpoints with `curl` where applicable:
   `/.well-known/oauth-authorization-server` and the MCP protected-resource
   metadata endpoint.
5. For subscriber health, aggregate `list-users` by status and enabled flag.
6. For Identity Pool flows, inspect identity pool metadata and IAM role mapping
   without printing credentials.

## Report Signals

- `PASS`: hosted UI domain resolves, OAuth metadata is coherent, callback URLs
  match the deployed public endpoints, and expected users are enabled.
- `WARN`: forced-password users, stale callback URLs, missing logout URLs, broad
  identity-pool role mapping, or TTL/doc drift.
- `FAIL`: hosted UI unavailable, OAuth metadata broken, wrong issuer/audience,
  missing app client, or unauthenticated access bypasses Cognito.
