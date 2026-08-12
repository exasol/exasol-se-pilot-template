---
name: github-actions-governance
description: Use when designing, reviewing, or operating GitHub Actions workflows for the <PILOT_NAME> repository, especially workflow triggers, permissions, concurrency, OIDC, branch/ruleset interaction, cost posture, and safe activation sequencing.
---

# GitHub Actions Governance

Use this skill for repository automation proposals and workflow reviews.

## Operating Rules

- Prefer proposal mode until the GitHub Operator Team approves activation.
- Start from `docs/30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md` and link the relevant `XYZ-PILOT-NNN`.
- Keep workflow `permissions` least-privilege; default to `contents: read`.
- Use OIDC for cloud access where possible; avoid long-lived cloud credentials.
- Add `concurrency` for scheduled or notification workflows that could overlap.
- Pin third-party actions deliberately. If using tags for maintainability, document the trust decision and keep Dependabot monitoring `github-actions`.
- Use UTC for `schedule` triggers and document the CEST/CET mapping.
- Estimate GitHub-hosted runner minutes, artifact/cache storage, and any downstream AWS cost.
- Do not enable branch protection, required checks, notification fan-out, or deployment behavior as a side effect of a proposal.

## Review Checklist

- Trigger scope: `push`, `pull_request`, `workflow_dispatch`, or `schedule` is justified.
- Token scope: `GITHUB_TOKEN` has only required permissions.
- Secret scope: no plaintext secrets, no structured blob secrets, no secret values in logs.
- Failure behavior: job fails closed for required checks, but notification jobs do not block unrelated commits unless approved.
- Cost control: timeout, concurrency, and artifact retention are bounded.
- Rollback: workflow can be disabled without breaking existing repo backup or tracker writer paths.

## References

- GitHub secure-use guidance: https://docs.github.com/en/actions/reference/security/secure-use
- GitHub workflow syntax and permissions: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
- GitHub scheduled workflows: https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows
