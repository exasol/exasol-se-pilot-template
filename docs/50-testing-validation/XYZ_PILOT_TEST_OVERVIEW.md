# Pilot Test Overview

Swarm tester roles:

| Agent | Purpose |
|---|---|
| `xyz_smoke` | Fast endpoint and repo sanity checks |
| `xyz_integration` | AWS, Exasol, service, and workflow integration |
| `xyz_infrastructure` | Account/AZ/resource mapping and architecture evidence |
| `xyz_e2e` | End-to-end user journey |
| `xyz_uat` | Business-user acceptance |
| `xyz_regression` | Known defect regression suite |
| `xyz_stress` | Controlled load probes |
| `xyz_chaos` | Negative-path and resilience checks |
| `xyz_expert` | Code/doc/security expert review |
| `xyz_master` | Candidate consolidation and tracker filing |

GitHub Actions workflow definitions are covered by the deterministic E2E suite
`scripts/check_github_actions_e2e.py`, also exposed as the advisory
`.github/workflows/github-actions-e2e.yml` workflow. It validates the workflow
inventory, safe trigger posture, permissions, runbook catalog coverage, and a
fresh generated-repository smoke test before a generated project activates
AWS-backed schedules or notification fan-out.

Run logs go under:

```text
tests/logs/runs/<UTC-id>/
```

Candidate submissions go under:

```text
tests/logs/runs/<UTC-id>/<agent-or-tool>/issue-candidates.md
```
