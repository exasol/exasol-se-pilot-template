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

There are 17 GitHub Actions workflows in the neutral template. This catalog is
the central operator-facing inventory. Detailed per-workflow runbooks, required
secrets, OIDC roles, downstream AWS dependencies, and failure modes are expanded
later as generated projects activate the corresponding automation.

| Workflow | Purpose | Trigger | Documentation status |
|---|---|---|---|
| `actions-usage-watch.yml` | Weekly Actions usage and cost signal | Schedule, manual | Not centrally documented |
| `backup-health-summary.yml` | S3 repo-backup health check | Manual only in template | Only category-level; add schedule only after AWS backup is configured |
| `codeowners-ruleset-drift.yml` | CODEOWNERS and ruleset drift | PR, manual | Partly in GitHub runbook |
| `docs-discipline.yml` | Documentation discipline check | PR, manual | Category-level only |
| `e2e-swarm-nightly.yml` | Full E2E swarm run | Manual only in template | Testing docs mention swarm, not workflow details; add schedule only after runtime endpoints are configured |
| `github-actions-e2e.yml` | End-to-end workflow definition and generated-repo safety suite | PR, manual | Central suite for workflow inventory, trigger posture, permissions, catalog coverage, and generated-repository smoke |
| `markdown-link-anchor-check.yml` | Markdown links and anchors | PR, manual | Category-level only |
| `notify-on-tracker-mutation.yml` | Chatbot submission acceptance email | Manual dry-run only in template | Header documented, not central; restore tracker push only after SES is configured |
| `regression-validation.yml` | Static regression checks | PR, manual | Category-level only |
| `repo-backup.yml` | Mirror repo to S3 | Manual only in template | Category-level only; add schedule only after AWS backup is configured |
| `repo-hygiene.yml` | Repo hygiene and policy checks | PR, manual | Category-level only |
| `runbook-archive-companion.yml` | Runbook archive backstop | PR, manual | Header documented, not central |
| `secret-scan.yml` | Gitleaks secret scan | Push, PR, schedule, manual | Header documented; `.gitleaks.toml` required at repo root |
| `tracker-major-defect-heads-up.yml` | SES heads-up for major defects | Manual only in template | Not centrally documented; restore tracker push only after SES is configured |
| `tracker-major-defect-resolved-sns.yml` | SNS resolution notice | Manual only in template | Not centrally documented; restore tracker push only after SNS is configured |
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
The neutral template intentionally keeps AWS backup, backup-health, E2E swarm,
and tracker-notification fan-out workflows manual-only so placeholder AWS
account IDs, roles, endpoints, and recipients cannot fire from cron or tracker
pushes. This activation posture is part of
[`XYZ-PILOT-001`](../../XYZ_PILOT_ISSUE_TRACKER.md), the
generated repository bootstrap and governance baseline.

### GitHub Actions E2E Suite

The repository-level suite is
[`scripts/check_github_actions_e2e.py`](../../../scripts/check_github_actions_e2e.py)
and is exposed through
[`.github/workflows/github-actions-e2e.yml`](../../../.github/workflows/github-actions-e2e.yml).
It validates:

- all expected workflow files exist and no unexpected workflow file appears;
- every workflow is represented in this catalog;
- unsafe AWS/runtime workflows remain manual-only in the neutral template;
- only approved workflows keep `push` or `schedule` triggers;
- workflow permissions, concurrency, timeout, and full-SHA action pinning pass
  the workflow policy checker;
- source-pilot EC2/account/host values do not leak into workflow definitions;
- a fresh generated pilot repository keeps rendered workflow names and the same
  safe trigger posture.

The suite is deterministic and does not dispatch live AWS, SES, SNS, Exasol, or
Cloudflare operations. Generated projects may re-enable schedules and
notification fan-out only after their AWS account, OIDC roles, runtime
endpoints, and recipient policy are configured and documented.

### GitHub Actions E2E Verification Evidence

The neutral template's GitHub Actions E2E workflow test suite was prepared,
committed, pushed, and verified on 2026-06-15 as part of the
[`XYZ-PILOT-001`](../../XYZ_PILOT_ISSUE_TRACKER.md) governance
baseline.

- Commit pushed:
  `deeef221c43625b10f740b4eb2cf3f180c1ee958`
- GitHub Actions run verified:
  [`27569642159`](https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/actions/runs/27569642159),
  `<PILOT_NAME> GitHub Actions E2E (Advisory)`, passed.
- New suite:
  [`scripts/check_github_actions_e2e.py`](../../../scripts/check_github_actions_e2e.py)
- New advisory workflow:
  [`.github/workflows/github-actions-e2e.yml`](../../../.github/workflows/github-actions-e2e.yml)
- Repo hygiene compile coverage was extended to include the E2E suite.
- Testing documentation was updated in
  [`tests/README.md`](../../../tests/README.md) and
  [`docs/50-testing-validation/XYZ_PILOT_TEST_OVERVIEW.md`](../../50-testing-validation/XYZ_PILOT_TEST_OVERVIEW.md).

The verified suite covers:

- all expected workflow files exist, currently 17;
- every workflow is cataloged in this runbook;
- neutral template workflows do not retain source or target pilot account IDs,
  EC2 instance IDs, or EC2 host markers;
- unsafe AWS/runtime workflows are not scheduled or push-triggered in the
  neutral template;
- all workflows remain manually dispatchable;
- `contents: write` is limited to intentional tracker/scheduler paths;
- generated-repository smoke validation works, including
  `gcm-master-background-filing.yml`.

The verification passed locally and in GitHub Actions. The only expected
annotations were intentional `contents: write` warnings for
`e2e-swarm-nightly.yml`, `xyz-master-background-filing.yml`, and the generated
`gcm-master-background-filing.yml`.

After the push, only the safe push-triggered workflows fired automatically:
`secret-scan` and `vscode-autoapprove-safety`; both passed. The AWS
repo-backup and runtime workflows did not retrigger. At verification time the
template worktree was clean and `HEAD == origin/main`.

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
