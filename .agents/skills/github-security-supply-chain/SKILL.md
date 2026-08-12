---
name: github-security-supply-chain
description: Use when reviewing GitHub repository security posture for the <PILOT_NAME>, including CodeQL, Dependabot, dependency review, secret scanning, push protection, action pinning, CODEOWNERS, rulesets, and supply-chain risk tradeoffs.
---

# GitHub Security And Supply Chain

Use this skill for GitHub security proposals, review gates, and repository hygiene.

## Operating Rules

- Treat private-repository feature availability and billing as a first-class constraint.
- Prefer GitHub-native controls before custom scripts: Dependabot, CodeQL, secret scanning, push protection, CODEOWNERS, rulesets, dependency review.
- Do not assume GitHub Advanced Security / Code Security / Secret Protection is licensed; verify before activation.
- For private repos, call out that CodeQL and Secret Protection may require an eligible plan or paid security feature.
- Separate advisory checks from enforced required checks until Phase 3/4 governance is approved.
- Preserve direct-write automation paths listed in `docs/30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md`.

## Review Checklist

- Dependency surfaces include GitHub Actions, Dockerfiles, Python `pyproject.toml` / `uv.lock`, and any future package manifests.
- Secret checks cover committed docs, workflow logs, Action env vars, and OIDC role trust.
- Code scanning scope matches actual repo languages; avoid expensive no-op scans.
- Branch/ruleset proposals identify bypass needs for chatbot, daily-status Lambda, and HPC_MASTER tracker updates.
- Findings that affect live credentials or destructive write paths are major or critical until resolved.

## References

- CodeQL code scanning: https://docs.github.com/en/code-security/code-scanning/introduction-to-code-scanning/about-code-scanning-with-codeql
- Dependabot version updates: https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuring-dependabot-version-updates
- Secret scanning and push protection: https://docs.github.com/en/code-security/secret-scanning/enabling-secret-scanning-features
- Microsoft Learn GitHub Actions CI module: https://learn.microsoft.com/en-us/training/modules/learn-continuous-integration-github-actions/
