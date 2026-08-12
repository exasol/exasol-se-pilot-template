# Exasol SE Pilot Template Extraction Runbook

**Status:** Draft baseline
**Owner:** Exasol SE pilot template maintainers
**Created:** 2026-06-13
**Source class:** Existing Exasol SE pilot repository with Exasol Personal,
Streamlit, Grafana, MCP, chatbot, operator dashboard, AWS automation, GitHub
governance, and central tracker practices.

## Purpose

Create a reusable, customer-neutral GitHub repository named
`exasol-se-pilot-template` that can generate future Exasol pilot repositories
from a consistent baseline.

The template must preserve the proven engineering system:

- Exasol Personal deployment pattern.
- Exasol MCP server pattern.
- Docker Compose app stack for Streamlit, Grafana, Prometheus, and sidecars.
- Operator dashboard pattern.
- Chatbot pattern with repository context and issue-candidate submission.
- Neutral Streamlit analytics dashboard connected to Exasol Personal.
- Central pilot tracker and candidate workflow.
- GitHub Actions governance, backup, link validation, tracker validation, and
  notification workflows.
- SNS/SES notification patterns.
- Cognito onboarding patterns.
- Lambda scheduler and operator automation patterns.
- Cloudflare tunnel endpoint patterns.
- OPS-only versus business-user recipient separation.
- Repo guardrails for Claude Code, OpenAI Codex, GitHub Copilot, and swarm
  tester agents.
- Skills and plugin instructions that are reusable across pilots.

The template must remove source-project and customer identity:

- No source-project name in filenames, code identifiers, URLs, emails, docs, or
  tracker text unless used in this runbook as a generic source-reference label.
- No source-customer references.
- No source AWS account IDs, Cognito pool IDs, Cloudflare tunnel IDs, SES/SNS
  ARNs, instance IDs, bucket names, or private URLs.
- No customer data, proprietary BI report files, spreadsheets, generated
  inventory snapshots,
  deployment state, zip archives, local virtual environments, cache files, or
  secrets.

## Template Vocabulary

Use these neutral terms in the template:

| Concept | Placeholder |
|---|---|
| Project display name | `<PILOT_NAME>` |
| Project slug / repo name | `<PROJECT_SLUG>` |
| Customer name | `<CUSTOMER_NAME>` |
| AWS profile | `<AWS_PROFILE>` |
| AWS account ID | `<AWS_ACCOUNT_ID>` |
| AWS region | `<AWS_REGION>` |
| AWS availability zone | `<AWS_AZ>` |
| Exasol deployment directory | `<EXASOL_DEPLOYMENT_DIR>` |
| Public base domain | `<PILOT_DOMAIN>` |
| Operator email | `<OPERATOR_EMAIL>` |
| OPS-only recipient list | `<OPS_ONLY_RECIPIENTS>` |
| Business-user recipient list | `<BIZ_USER_RECIPIENTS>` |
| Central project identifier | `xyz` in the template; generated projects replace it from `--project-id`, which must be exactly three lowercase ASCII letters. If omitted, the generator derives the first three letters from `--project`. |
| Documentation filenames | `docs/XYZ_PILOT_ISSUE_TRACKER.md` and other template docs use uppercase basenames. Generated projects use the uppercase project identifier for documentation basenames and tracker IDs. |

## Non-Negotiable Safety Rules

1. Do not copy generated deployment state into the template.
2. Do not copy `.env`, Terraform/OpenTofu state, Exasol deployment secrets,
   generated private keys, zip deployment bundles, cache files, or local virtual
   environments.
3. Do not copy customer source artifacts such as proprietary workbooks,
   vendor BI report files, OLAP cube exports, raw exports, screenshots with customer data, or generated
   inventory snapshots.
4. Do not activate AWS deployment, GitHub notification fan-out, Cloudflare
   routing, Cognito invitations, SES sends, or SNS subscriptions from template
   generation by default.
5. The generator must support a dry-run mode and default to no cloud mutation.
6. Any cloud deployment phase must require an explicit flag such as
   `--deploy-aws` and must print account, region, and estimated resource class
   before running.
7. Generated projects must start with placeholder secrets and explicit setup
   instructions, not inherited credentials.
8. GitHub Actions copied into the template must use least-privilege
   permissions and be disabled or workflow-dispatch only unless intentionally
   enabled by the generated project.

## Target Repository Layout

The template repository should converge to this structure:

```text
.
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── generate.sh
├── config/
│   ├── template.defaults.env
│   └── placeholders.yml
├── docs/
│   ├── README.md
│   ├── XYZ_PILOT_ISSUE_TRACKER.md
│   ├── 00-live-status/
│   ├── 10-product-data/
│   ├── 20-architecture/
│   ├── 30-runbooks/
│   ├── 40-protocols/
│   ├── 50-testing-validation/
│   ├── 60-evidence-inventory/
│   └── 70-generated-qa/
├── services/
│   ├── chatbot/
│   ├── exasol-mcp/
│   ├── grafana/
│   ├── operator-dashboard/
│   └── streamlit-insights/
├── scheduler/
├── scripts/
├── tests/
│   ├── agents/
│   ├── scripts/
│   └── skills/
└── .github/
    ├── workflows/
    └── dependabot.yml
```

The generated project repository should use the same layout, with
`<PROJECT_SLUG>` substituted into names where required.

## Extraction Phases

### Phase 0 - Baseline Inventory

Inventory the source repository before copying:

- Top-level folders.
- Runtime services and Docker Compose stacks.
- Lambda functions and deployment scripts.
- GitHub Actions workflows.
- Tracker and documentation dependencies.
- Skills, swarm tester agents, and plugin instructions.
- Sensitive/generated artifacts that must be excluded.

Record all findings in the protocol.

### Phase 1 - Neutral Documentation Skeleton

Create the template documentation skeleton first:

- Template README.
- This runbook.
- Extraction protocol.
- Neutral docs README.
- Neutral central tracker skeleton at `docs/XYZ_PILOT_ISSUE_TRACKER.md`.
- Guardrail documents for agent behavior.

Acceptance:

- No source-customer references in the template docs except in the extraction
  protocol as historical source context.
- Links resolve locally.

### Phase 2 - Guardrails and Tracker Discipline

Adapt the source guardrails into neutral form:

- `CLAUDE.md`
- `AGENTS.md`
- Copilot instructions
- Tracker write discipline
- Commit-attribution discipline
- Runbook archive discipline
- Daily documentation discipline
- Parallel-session guardrail

The central tracker must become:

```text
docs/XYZ_PILOT_ISSUE_TRACKER.md
```

Generated projects replace `xyz` in runtime identifiers, swarm agent names,
script names, service resource names, and docs links. The project identifier is
exactly three lowercase letters for runtime names. Documentation filenames and
tracker IDs use the uppercase form. For example, `--project gcme-analytics-pilot
--project-id gcm` produces `docs/GCM_PILOT_ISSUE_TRACKER.md`,
`docs/30-runbooks/deployment/GCM_PILOT_DEPLOYMENT_RUNBOOK.md`, and issue IDs
using the `GCM-PILOT-NNN` prefix.

Acceptance:

- Generated projects start with an initialized tracker and no inherited source
  issue history.
- Candidate-submission workflow creates new project-local entries only.

### Phase 3 - Service Skeletons

Copy and neutralize reusable service implementations:

- `services/chatbot/`
- `services/exasol-mcp/`
- `services/streamlit-insights/`
- `services/grafana/`
- the Operator dashboard, which lives under `scheduler/` rather than
  `services/`: `scheduler/status_dashboard/` (static prod page),
  `scheduler/dev_operator_dashboard/` (Flask dev parity), and its backend
  Lambda `scheduler/xyz_pilot_user_mgmt.py`
- shared Docker Compose examples

Required neutralization:

- Replace hardcoded source names with placeholders.
- Replace source URLs with `<PILOT_DOMAIN>` placeholders.
- Replace source account/region values with config-driven values.
- Replace customer-specific data schemas with sample or empty adapters.
- Keep `.env.example`; exclude `.env`.

Acceptance:

- Services can be built locally without source-specific data.
- Compose files bind local ports to loopback by default.
- Exasol host is configurable and supports `host.docker.internal`.

### Phase 4 - AWS and Exasol Personal Automation

Create generator-controlled automation for:

- AWS account/region discovery through `aws sts get-caller-identity`.
- Exasol Personal initialization.
- Optional Exasol Personal deployment.
- EC2 runtime validation.
- Docker and Cloudflare installation on the target EC2 host.
- SES/SNS/Cognito/Lambda/EventBridge setup.

Default mode must be dry-run.

Example target command:

```bash
./generate.sh \
  --project gcme-analytics-pilot \
  --project-id gcm \
  --pilot-name "GCME Analytics Pilot" \
  --aws-profile default \
  --aws-region eu-central-1 \
  --aws-az eu-central-1a
```

Suggested future deployment command:

```bash
./generate.sh \
  --project gcme-analytics-pilot \
  --project-id gcm \
  --pilot-name "GCME Analytics Pilot" \
  --aws-profile default \
  --aws-region eu-central-1 \
  --aws-az eu-central-1a \
  --deploy-aws
```

Acceptance:

- Without `--deploy-aws`, no AWS resources are created.
- With `--deploy-aws`, the script prints account ID, region, AZ, cost-bearing
  resources, and asks for explicit confirmation.

### Phase 5 - GitHub Automation

Copy and neutralize workflow patterns:

- Markdown link checks.
- Tracker completeness checks.
- Docs discipline checks.
- Secret scan.
- Backup health checks.
- E2E swarm workflow.
- Tracker notification workflows.

Default generated-project behavior:

- Validation workflows can run on pull requests.
- Notification and deployment workflows are disabled or
  `workflow_dispatch`-only until configured.
- Cloud credentials should use OIDC where possible.

Acceptance:

- No workflow contains source account IDs, source repo names, source domains, or
  source-only secrets.
- Workflow permissions are least-privilege.

### Phase 6 - Swarm Tester Fleet

Neutralize the swarm tester agents:

- Smoke
- Integration
- Regression
- E2E
- UAT
- Stress
- Chaos
- Expert
- Infrastructure
- Master tracker filer

The infrastructure agent must be preserved as a reusable pattern for:

- AWS account and AZ dependency checks.
- Endpoint-to-resource mapping checks.
- Architecture diagram generation per run.
- Minor defect candidate filing for stale or cross-account/cross-AZ mappings.

Acceptance:

- Run logs are always under `tests/logs/runs/<UTC-id>/`.
- Candidate files are generated but not directly written into the tracker.
- Test scripts do not refer to source project names.

### Phase 7 - Generator

Implement `generate.sh` to create a new project repository from the template.

Minimum flags:

| Flag | Required | Meaning |
|---|---:|---|
| `--project` | yes | New repo/directory slug. |
| `--pilot-name` | yes | Human-readable project name. |
| `--aws-profile` | no | AWS CLI profile. Defaults to `default`. |
| `--aws-region` | no | AWS region. |
| `--aws-az` | no | AWS availability zone. |
| `--customer-name` | no | Customer display name. |
| `--project-id` | no | Exactly three lowercase ASCII letters replacing `xyz`; defaults to the first three letters from `--project`. |
| `--domain` | no | Public pilot base domain. |
| `--dry-run` | no | Show actions only. Default. |
| `--deploy-aws` | no | Permit cost-bearing AWS deployment steps. |
| `--create-github-repo` | no | Create remote GitHub repository when `gh` is configured. |

Acceptance:

- `./generate.sh --help` works.
- Dry-run prints the intended target path and substitutions.
- Generated repository has a clean `git status` after initial commit when commit
  creation is enabled.

## Validation Checklist

Run these checks before declaring the template usable:

```bash
rg -n "<source-project-term>|<source-customer-term>|<source-account-id>|<source-domain>" .
rg -n "gho_|AKIA|SECRET|PASSWORD|TOKEN|PRIVATE KEY|BEGIN RSA" .
python3 scripts/check_markdown_links.py
python3 scripts/check_pilot_issue_links.py
./scripts/verify_tracker_completeness.sh
git status --short --branch
```

Expected:

- The first search returns only documented placeholder/blocked-term policy
  mentions, not operational references.
- The second search returns only placeholder examples and script variable names,
  never live values.
- Link and tracker checks pass.

## Deferred Work

The first template extraction may intentionally defer:

- Fully automated AWS deployment.
- GitHub remote creation.
- Cloudflare named tunnel creation.
- Cognito hosted UI customization.
- SES production-access checks.
- Customer document ingestion UI.
- Full neutral rewrite of advanced dashboards.

Deferred items must be tracked in `docs/XYZ_PILOT_ISSUE_TRACKER.md`.
