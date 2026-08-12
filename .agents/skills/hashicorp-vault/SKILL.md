---
name: hashicorp-vault
description: Use when asked to design, audit, troubleshoot, or document HashiCorp Vault secrets management, identity-based access, policies, auth methods, audit logs, dynamic secrets, PKI, token hygiene, or migration from plaintext credentials in the workshop.
---

# HashiCorp Vault

Use this skill for Vault architecture, security review, secret migration, policy design, auth method selection, audit logging, and safe handling of workshop credentials.

## Safety Rules

- Never print Vault tokens, root tokens, unseal keys, recovery keys, secret values, private keys, or dynamic credentials.
- Prefer metadata checks: mount paths, policy names, auth method types, accessor IDs, lease counts, audit device status, and redacted capabilities.
- Do not run `vault kv get`, `vault read`, `vault token lookup`, or any command that reveals values unless the user explicitly asks and a redaction path is in place.
- Do not initialize, unseal, rotate, revoke, or delete secrets without explicit confirmation.

## Workflow

1. Establish target and auth context without exposing token values:

```bash
vault status
vault token capabilities <path>
```

2. Inventory safe metadata:

```bash
vault secrets list
vault auth list
vault policy list
vault audit list
```

3. For policy review:

```bash
vault policy read <policy-name>
```

4. For KV metadata without values, prefer metadata endpoints where available:

```bash
vault kv metadata get <mount>/<path>
```

5. For dynamic secrets, report lease TTLs, engines, roles, and revoke/renew strategy without printing issued credentials.

## Workshop Guidance

- Prefer Vault or AWS Secrets Manager over Lambda environment variables for long-lived database credentials, API keys, and passphrases.
- For Codespaces and workshop flows, keep participant-facing secrets in GitHub Codespaces secrets or short-lived STS credentials; Vault is useful for centralized instructor/automation control.
- Enable audit devices before production-like use.
- Use least-privilege policies and short TTLs for dynamic cloud credentials.

## Official References

- IBM HashiCorp product page: https://www.ibm.com/products/hashicorp
- IBM HashiCorp support details: https://www.ibm.com/support/pages/node/7184931
- Vault documentation: https://developer.hashicorp.com/vault/docs
- What is Vault: https://developer.hashicorp.com/vault/docs/about-vault/what-is-vault
- How Vault works: https://developer.hashicorp.com/vault/docs/about-vault/how-vault-works
