# Pilot GitHub Runbook

This runbook defines the generated repository's GitHub governance baseline for
[`XYZ-PILOT-001`](../../XYZ_PILOT_ISSUE_TRACKER.md): repository
bootstrap, branch governance, CODEOWNERS, direct-write automation exceptions,
and drift checks.

## Branch Governance

- Default branch: `main`.
- Ruleset payload: `.github/rulesets/XYZ-PILOT-088-main-governance.json`.
- CODEOWNERS file: `.github/CODEOWNERS`.
- Drift checker: `scripts/check_codeowners_ruleset_drift.py`.

The ruleset payload is expected to target `refs/heads/main`, require pull
requests with code-owner review, block deletion and non-fast-forward updates,
and preserve the documented Admin repository-role bypass actor.

### Critical Paths Prepared For Review Gating

- `.github/CODEOWNERS`
- `.github/rulesets/XYZ-PILOT-088-main-governance.json`
- `.github/workflows/*`
- `CLAUDE.md`
- `AGENTS.md`
- `README.md`
- `docs/XYZ_PILOT_ISSUE_TRACKER.md`
- `docs/30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md`
- `docs/30-runbooks/deployment/XYZ_PILOT_DEPLOYMENT_RUNBOOK.md`
- `docs/60-evidence-inventory/aws-tagging/AWS_RESOURCE_TAGGING_RUNBOOK.md`
- `scripts/require_exasol_aws_tags.sh`
- `scripts/aws_tag_inventory.sh`
- `scheduler/*`
- `services/chatbot/*`
- `tests/agents/*`

### Direct-Write Automation Paths To Preserve

- `docs/XYZ_PILOT_ISSUE_TRACKER.md`
- `docs/archive/XYZ_PILOT_ISSUE_TRACKER_*.md`
- `docs/70-generated-qa/qa/*`
- `docs/70-generated-qa/architecture/*`

Direct-write automation paths are exceptions for controlled bot flows such as
`xyz_master` tracker filing and generated QA archive writes. They do not permit
manual bypass of tracker discipline, tag discipline, or branch governance.

## Required Checks

Run these before changing CODEOWNERS, rulesets, or GitHub Actions governance:

```bash
python3 scripts/check_codeowners_ruleset_drift.py
python3 scripts/check_markdown_links.py
python3 scripts/check_docs_discipline.py
```

When a generated project activates GitHub Actions for deployment, also verify
that AWS OIDC roles, GitHub secrets, and workflow permissions are documented in
the deployment runbook before the first AWS resource is created.
