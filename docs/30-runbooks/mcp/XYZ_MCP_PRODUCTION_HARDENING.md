# MCP Production Hardening Runbook

This runbook captures production-hardening work for the generated pilot's
public Exasol MCP endpoint.

## Baseline

- Public endpoint must be protected by the configured identity provider.
- Database access must use a least-privilege read-only user by default.
- SQL-capable tools must enforce read-only verb allow lists and row/character
  caps.
- Client secrets and database passwords must stay in the configured secret
  store, not in repository files.

## Hardening Checklist

1. Verify OAuth/OIDC protected-resource metadata.
2. Verify anonymous requests fail closed.
3. Verify token-authenticated initialization works.
4. Verify guarded read-only SQL aliases reject DDL and DML.
5. Verify audit logs identify client, user, tool, status, and latency without
   printing secrets.

