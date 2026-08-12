# Exasol SE Pilot Template Extraction Protocol

**Protocol started:** 2026-06-13
**Template repository:** `/Users/<LOCAL_USER>/GitHub/exasol-se-pilot-template`
**Source repository:** existing Exasol SE pilot implementation repository
**Mode:** staged extraction with safety-first neutralization
**Current status:** Phase 1 started

## Objective

Create a neutral Exasol SE pilot template repository that can generate new pilot
repositories such as:

```bash
./generate.sh \
  --project gcme-analytics-pilot \
  --project-id gcm \
  --pilot-name "GCME Analytics Pilot" \
  --aws-profile default \
  --aws-region eu-central-1 \
  --aws-az eu-central-1a
```

The generated project should contain the aligned repository structure,
initialized central tracker, documentation discipline, service skeletons,
operator automation, and optional deployment path for Exasol Personal plus the
supporting pilot technology stack.

## Execution Log

### 2026-06-13 - Phase 0 / Phase 1 kickoff

Actions completed:

- Confirmed the source repository working tree was clean before extraction
  planning.
- Confirmed the target template path did not exist.
- Created `/Users/<LOCAL_USER>/GitHub/exasol-se-pilot-template`.
- Initialized a fresh local Git repository and renamed its branch to `main`.
- Created the first neutral documentation artifacts:
  - `README.md`
  - `docs/30-runbooks/template/EXASOL_SE_PILOT_TEMPLATE_EXTRACTION_RUNBOOK.md`
  - `docs/40-protocols/template/EXASOL_SE_PILOT_TEMPLATE_EXTRACTION_PROTOCOL.md`

Initial source inventory signals:

- Source contains reusable runtime stacks:
  - chatbot stack
  - Exasol MCP stack
  - Streamlit dashboard stack
  - Grafana/Prometheus stack
  - scheduler/Lambda automation
  - static operator dashboard
- Source contains reusable governance patterns:
  - central tracker
  - master-filer style background filing
  - documentation discipline checks
  - markdown link checks
  - tracker completeness checks
  - notification workflows
  - swarm tester fleet
  - AWS inventory scripts
  - skill instructions
- Source also contains non-template artifacts that must not be copied:
  - customer-specific report assets
  - source-project docs and tracker history
  - deployment directories
  - generated Lambda zip bundles
  - `.env` files
  - local virtual environments
  - cache files
  - source AWS identifiers and URLs

## Neutralization Rules

The extraction must remove operational references to:

- Source project names.
- Source-customer names.
- Source-specific tracker prefix.
- Source AWS account IDs, regions, AZ assumptions, VPCs, subnets, EC2 instance
  IDs, EBS volume IDs, S3 bucket names, Cognito pool IDs, SES/SNS ARNs, Lambda
  ARNs, EventBridge rule names, and Cloudflare tunnel IDs.
- Source-specific domains and endpoint URLs.
- Source-specific people lists unless converted to neutral
  `OPS_ONLY_RECIPIENTS` and `BIZ_USER_RECIPIENTS` placeholders.
- Customer-specific data schemas, dashboards, workbooks, and generated
  inventory snapshots.

Allowed references:

- Generic documentation that describes a source-pilot extraction, without
  carrying customer identity or live operational values.
- Placeholder examples using `<PLACEHOLDER_NAME>`.
- Synthetic example project names such as `gcme-analytics-pilot` when used
  as generator examples.

## Open Decisions

| Decision | Default Proposal | Status |
|---|---|---|
| Central project identifier | `xyz` placeholder replaced by `--project-id`; exactly three lowercase ASCII letters, defaulting to the first three letters from `--project` | Accepted baseline |
| Generated repo default path | `~/GitHub/<PROJECT_SLUG>` | Open |
| Cloud deployment default | Dry-run only | Accepted baseline |
| GitHub remote creation | Optional `--create-github-repo` | Open |
| AWS deployment flag | Optional `--deploy-aws` with confirmation | Accepted baseline |
| Customer docs intake | `docs/10-product-data/customer-input/` | Open |
| OPS/BIZ recipient config | `config/recipients.yml` | Open |

## Acceptance Gates

Gate A - Repository scaffold:

- Template repository exists.
- Branch is `main`.
- README links to runbook and protocol.
- Runbook and protocol are present.

Gate B - Neutralization:

- No operational source-project or customer references outside documented
  policy sections.
- No live secrets or generated deployment state.

Gate C - Generator dry-run:

- `./generate.sh --help` works.
- `./generate.sh --project sample-pilot --pilot-name "Sample Pilot"` prints
  planned actions and performs no AWS mutations.

Gate D - Generated repository:

- New project directory is created.
- Project README, central tracker, docs structure, guardrails, service skeletons,
  and tests structure exist.
- Generated files contain project-specific substitutions and no source
  references.

Gate E - Optional AWS deployment:

- Only runs with `--deploy-aws`.
- Prints AWS account ID, region, AZ, and cost-bearing resources.
- Requires explicit operator confirmation.

## Next Steps

1. Add neutral docs README and central tracker skeleton.
2. Add placeholder/config model, including central `xyz` project identifier replacement.
3. Add first dry-run-only `generate.sh`.
4. Copy and neutralize guardrails.
5. Copy reusable validation scripts with neutral names.
6. Copy service skeletons without source customer data.
7. Add neutralization scan scripts.
8. Commit the initial template documentation baseline.
