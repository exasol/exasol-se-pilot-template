# Exasol SE Pilot Template

Neutral template repository for creating new Exasol SE pilot, MVP, and
customer-evaluation projects.

This template contains the reusable project structure, documentation discipline,
tracker workflow, Streamlit/Grafana/chatbot/MCP services, AWS control-plane
automation, GitHub Actions, swarm tester harness, and local guardrails for a new
Exasol SE pilot repository.

## Current Template Guarantees

- Generated runtime identifiers use exactly three lowercase letters, for
  example `gcm`, while tracker IDs and documentation filenames use uppercase
  project identifiers such as `GCM-PILOT-001` and
  `GCM_PILOT_ISSUE_TRACKER.md`.
- Documentation filenames under `docs/` use uppercase basenames and lowercase
  `.md` extensions for predictable cross-document linking.
- Proof-of-concept wording is intentionally avoided; generated repositories use
  `pilot` terminology throughout.
- AWS deployment is blocked until the Exasol IT AWS Resource Tagging Policy
  values are complete for the target account, region, and AZ. Use
  [`scripts/require_exasol_aws_tags.sh`](scripts/require_exasol_aws_tags.sh)
  before any AWS resource creation.
- The tagging runbook includes neutralized source-pilot sample tag sets for
  application, automation, database, networking, and public-dashboard resources:
  [`AWS_RESOURCE_TAGGING_RUNBOOK.md`](docs/60-evidence-inventory/aws-tagging/AWS_RESOURCE_TAGGING_RUNBOOK.md).
- Deployment helpers tag Lambda, EventBridge, and SSM Parameter Store resources
  with the mandatory `exa:*` tags at creation time where the AWS API supports
  it.
- GitHub governance is documented through
  [`XYZ_PILOT_GITHUB_RUNBOOK.md`](docs/30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md),
  including CODEOWNERS, rulesets, and direct-write automation exceptions.
- A fresh generated repository is expected to pass template neutrality,
  tracker-completeness, Markdown-link, CODEOWNERS/ruleset drift, and focused
  tracker-candidate tests before publication.

## Quick Start

From this template checkout:

```bash
cd ~/GitHub/exasol-se-pilot-template
./generate \
  -project <project-slug> \
  --project-id <three-letter-id> \
  -pilot-name "<pilot-name>" \
  -customer-name "<customer-name>" \
  -awsprofile <aws-cli-profile> \
  -awsregion <aws-region> \
  -awsaz <availability-zone>
```

Example. **GCME - German Companies Make Everything is a fictional stand-in
company used throughout this template's examples.** Replace it, the project
slug, and the project ID with the real target project and company name when
generating an actual pilot.

```bash
./generate \
  -project gcme-analytics-pilot \
  --project-id gcm \
  -pilot-name "GCME Analytics Pilot" \
  -customer-name "GCME - German Companies Make Everything" \
  --customer-domain gcme.example \
  --pilot-schema PILOT_DW \
  -awsprofile default \
  -awsregion eu-central-1 \
  -awsaz eu-central-1a
```

This creates the local repository checkout
`~/GitHub/gcme-analytics-pilot/`. It does not create the GitHub remote and
does not deploy AWS resources.

Use `-awsregion` for the AWS region, for example `eu-central-1`, and `-awsaz`
for a concrete availability zone, for example `eu-central-1a`.

The generator initializes the new local repository on `main`, renders
placeholders, creates an initial central tracker, and makes the first local
commit.

## Naming Rules

- `--project` is the target repository and AWS resource slug.
- `--project-id` is exactly three lowercase ASCII letters. It drives lowercase
  runtime identifiers, agent names, profiles, scripts, and service names.
  Valid examples: `gcm`, `abc`, `xyz`; invalid examples: `ba`, `demo`, `b4a`.
- Documentation filenames under `docs/` use the uppercase project identifier in
  their basenames. The central tracker is `GCM_PILOT_ISSUE_TRACKER.md`,
  `ABC_PILOT_ISSUE_TRACKER.md`, etc.
- Issue IDs use the uppercase project identifier plus `PILOT`, for example
  `GCM-PILOT-001`.
- Avoid proof-of-concept terminology in generated projects; use `pilot`.

If `--project-id` is omitted, the generator derives the first three lowercase
letters from `--project`. Pass it explicitly when the slug-derived code is not
the desired project code, for example `--project-id=gcm`.

## After Generation

```bash
cd ~/GitHub/gcme-analytics-pilot
cp config/project.env.example config/project.env
```

Review and fill `config/project.env`, especially:

- `PROJECT_SLUG`
- `PILOT_NAME`
- `CUSTOMER_NAME`
- `AWS_PROFILE`
- `AWS_REGION`
- `AWS_AZ`
- `AWS_TAGGING_POLICY`
- `ENVIRONMENT`
- `DEPARTMENT`
- `COST_CENTER`
- `DATA_CLASSIFICATION`
- `DEFAULT_AWS_WORKLOAD`
- `PILOT_DOMAIN`
- recipient lists and operator email

Runtime AWS resource identifiers (account IDs, EC2 instance, EBS
volumes/snapshots, AMI, security group, Exasol launcher deployment ID and
archive bucket) are deliberately left as unrendered placeholders. They are
listed under `runtime_placeholders` in
[`config/placeholders.yml`](config/placeholders.yml), together with the
migration and recovery scripts that use them. Pass the live values as
environment overrides when you run those scripts and never commit them; the
neutrality check rejects raw resource IDs.

Then run:

```bash
./scripts/require_exasol_aws_tags.sh
./scripts/validate_template_neutrality.sh
./scripts/verify_tracker_completeness.sh
git status --short --branch
```

Expected result: Exasol IT AWS tagging preflight OK, neutrality check OK,
tracker completeness OK, clean worktree.

## Deployment Handoff

Use the generated runbooks before creating cost-bearing infrastructure:

- `docs/30-runbooks/deployment/<PROJECT-ID-UPPER>_PILOT_DEPLOYMENT_RUNBOOK.md`
- `docs/30-runbooks/operations/<PROJECT-ID-UPPER>_PILOT_DEVELOPER_RUNBOOK.md`
- `docs/30-runbooks/security/<PROJECT-ID-UPPER>_PILOT_CREDENTIALS_RUNBOOK.md`
- `docs/50-testing-validation/<PROJECT-ID-UPPER>_PILOT_TEST_OVERVIEW.md`

The intended sequence is:

1. Confirm AWS account, region, AZ, and cost guardrails.
2. Confirm Exasol IT AWS Resource Tagging Policy values in
   `config/project.env`; no AWS deployment may start without complete tags for
   the target account, region, and AZ.
3. Configure GitHub repository secrets and Actions permissions.
4. Deploy Exasol Personal.
5. Deploy the Docker app stack and local service bindings.
6. Recreate Cognito, SES, SNS, SSM parameters, Lambdas, schedules, and
   Cloudflare endpoints.
7. Run `scripts/aws_tag_inventory.sh` and commit the generated evidence when
   resource state changes.
8. Run the swarm tester harness.
9. Use the generated tracker as the single source of issue and feature truth.

## Tracker And Candidate Discipline

Generated projects use a single-writer tracker model:

- Sources such as chatbot, swarm testers, Codex, Claude Code, and the Operator
  Dashboard submit `issue-candidates.md`.
- Only the generated `<project-id>_master` flow writes the central tracker.
- Use dry-run first for ambiguous or high-impact candidates.
- Use PR mode for major/critical or review-heavy changes.
- Use direct-main only for low-risk minor/feature candidates or explicit
  operator-approved status/severity transitions.

## Template Maintenance

Source-project names, customer names, account IDs, URLs, credentials, and
historical operational state must not be copied into this template except as
documented placeholders.

Validate template changes with:

```bash
PYTHONPYCACHEPREFIX=/tmp/exasol-template-pycache \
  python3 -m py_compile scripts/render_template_placeholders.py
bash -n generate.sh scripts/validate_template_neutrality.sh
./scripts/verify_tracker_completeness.sh
```

Smoke-test generation into `/tmp` before publishing template changes.

## Reference Docs

- [Template Extraction Runbook](docs/30-runbooks/template/EXASOL_SE_PILOT_TEMPLATE_EXTRACTION_RUNBOOK.md)
- [Template Extraction Protocol](docs/40-protocols/template/EXASOL_SE_PILOT_TEMPLATE_EXTRACTION_PROTOCOL.md)

## Key Outcomes

- Enforced exactly three-letter lowercase `--project-id` values for runtime
  identifiers.
- Kept tracker and documentation IDs uppercase, for example `GCM-PILOT-001`
  and `GCM_PILOT_ISSUE_TRACKER.md`.
- Renamed documentation files under `docs/` to uppercase basenames.
- Removed remaining proof-of-concept and source-specific naming patterns from
  the neutral template.
- Added the mandatory Exasol IT AWS Resource Tagging Policy gate:
  - [`scripts/require_exasol_aws_tags.sh`](scripts/require_exasol_aws_tags.sh)
  - [`AWS_RESOURCE_TAGGING_RUNBOOK.md`](docs/60-evidence-inventory/aws-tagging/AWS_RESOURCE_TAGGING_RUNBOOK.md)
  - [`XYZ_PILOT_DEPLOYMENT_RUNBOOK.md`](docs/30-runbooks/deployment/XYZ_PILOT_DEPLOYMENT_RUNBOOK.md)
- Added source-pilot sample tag sets in neutralized form, including `SE`,
  `staging`, `20060`, workload classes, and data classification examples.
- Added the GitHub governance runbook:
  - [`XYZ_PILOT_GITHUB_RUNBOOK.md`](docs/30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md)
- Updated deployment helpers so Lambda, EventBridge, and SSM Parameter Store
  creation paths use the mandatory `exa:*` policy tags.

## Validation Snapshot

The template hardening pass was implemented in commit
`ea32269a815f6f715a37186cd9a2049b02984e60`.

Validation passed:

- `validate_template_neutrality.sh`
- `verify_tracker_completeness.sh`
- `check_codeowners_ruleset_drift.py`
- `check_markdown_links.py`
- `check_docs_discipline.py`
- 41 focused unit tests
- Fresh `generate` smoke test for `--project-id=gcm`
- Generated-repository tag preflight with sample AWS tag values
