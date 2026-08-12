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

## GitHub Actions Workflow Catalog

There are 16 GitHub Actions workflows in the neutral template. This catalog is
the central operator-facing inventory. Detailed per-workflow runbooks, required
secrets, OIDC roles, downstream AWS dependencies, and failure modes are expanded
later as generated projects activate the corresponding automation.

| Workflow | Purpose | Trigger | Documentation status |
|---|---|---|---|
| `actions-usage-watch.yml` | Weekly Actions usage and cost signal | Schedule, manual | Not centrally documented |
| `backup-health-summary.yml` | S3 repo-backup health check | Schedule, manual | Only category-level |
| `codeowners-ruleset-drift.yml` | CODEOWNERS and ruleset drift | PR, manual | Partly in GitHub runbook |
| `docs-discipline.yml` | Documentation discipline check | PR, manual | Category-level only |
| `e2e-swarm-nightly.yml` | Full E2E swarm run | Schedule, manual | Testing docs mention swarm, not workflow details |
| `markdown-link-anchor-check.yml` | Markdown links and anchors | PR, manual | Category-level only |
| `notify-on-tracker-mutation.yml` | Chatbot submission acceptance email | Tracker push, manual | Header documented, not central |
| `regression-validation.yml` | Static regression checks | PR, manual | Category-level only |
| `repo-backup.yml` | Mirror repo to S3 | Schedule, manual | Category-level only |
| `repo-hygiene.yml` | Repo hygiene and policy checks | PR, manual | Category-level only |
| `runbook-archive-companion.yml` | Runbook archive backstop | PR, manual | Header documented, not central |
| `secret-scan.yml` | Gitleaks secret scan | Push, PR, schedule, manual | Header documented; `.gitleaks.toml` required at repo root |
| `tracker-major-defect-heads-up.yml` | SES heads-up for major defects | Tracker push, manual | Not centrally documented |
| `tracker-major-defect-resolved-sns.yml` | SNS resolution notice | Tracker push, manual | Not centrally documented |
| `vscode-autoapprove-safety.yml` | VS Code auto-approve guardrail | Push, PR, manual | Header documented, not central |
| `xyz-master-background-filing.yml` | Manual candidate filing and status flow | Manual only | Not centrally documented enough |

Short answer: the workflows are partially documented, but not sufficiently for
full generated-project operations.

What exists:

- This runbook documents branch governance, CODEOWNERS, rulesets, direct-write
  automation paths, and required local checks.
- The template extraction runbook lists workflow categories to neutralize.
- The project dependency graph models `.github/workflows/**` as a dependency
  node.
- Several workflow files have strong inline headers.

Until the per-workflow details are added, operators must treat workflows with
AWS side effects, notification fan-out, write permissions, or scheduled runtime
execution as requiring explicit activation review in the generated project.

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
