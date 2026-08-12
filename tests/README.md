# <PILOT_NAME> Runtime Test Harness

This folder holds ad-hoc runtime validation evidence for the XYZ Exasol
Pilot. It complements `docs/50-testing-validation/XYZ_PILOT_TEST_OVERVIEW.md` and the central issue
register at `docs/XYZ_PILOT_ISSUE_TRACKER.md`.

## Current Run Pattern

Use a UTC timestamp as the run ID, for example `2026-05-28T181924Z`.
Evidence is written to `tests/logs/runs/<run_id>/`.

## Scripted Swarm Harness

Initialize a new default seven-agent run without executing tests:

```bash
tests/scripts/init_swarm_run.sh 2026-05-27T150000Z
```

Run the full read-only swarm:

```bash
tests/scripts/run_e2e_swarm.sh 2026-05-27T150000Z
```

Run the full swarm plus the opt-in bounded stress agent:

```bash
XYZ_STRESS_USERS=5 tests/scripts/run_stress_swarm.sh 2026-05-27T150000Z
```

Run the full swarm plus the opt-in migration infrastructure agent:

```bash
tests/scripts/run_infrastructure_swarm.sh 2026-05-27T150000Z
```

The harness creates one local execution subfolder per agent under
`tests/logs/runs/<run_id>/`, copies each agent's `run.sh` into that folder, then
runs `XYZ_EXPERT`, `XYZ_SMOKE`, `XYZ_INTEGRATION`, `XYZ_E2E`, `XYZ_UAT`, and
`XYZ_CHAOS` in parallel. Three opt-in tester agents extend the default set
when their gates are enabled: `XYZ_STRESS` (via `XYZ_INCLUDE_STRESS=1`)
`XYZ_REGRESSION` (via `XYZ_INCLUDE_REGRESSION=1`, per
[XYZ-PILOT-184](../docs/XYZ_PILOT_ISSUE_TRACKER.md)), and
`XYZ_INFRASTRUCTURE` (via `XYZ_INCLUDE_INFRASTRUCTURE=1`, or
`tests/scripts/run_infrastructure_swarm.sh`) for migration-aware AWS
account/region/AZ and endpoint topology checks.
`XYZ_MASTER` runs last and writes the run summary. It does not mutate
`docs/XYZ_PILOT_ISSUE_TRACKER.md` during the default swarm pass; tracker mutation
is a separate guarded filing action via
`tests/scripts/run_xyz_master_filing.sh <run_id>` after explicit operator
approval.

Each agent execution folder also receives a copied `skills.md` profile from
`tests/skills/<agent>.md`. The canonical cross-agent mapping is
`tests/skills/SWARM_SKILL_MATRIX.md`.

## Tester Personas

| Tester | Mode | Evidence folder |
|---|---|---|
| `XYZ_EXPERT` | Expert static and local runtime discipline checks | `tests/logs/runs/2026-05-27T091831Z/XYZ_EXPERT/` |
| `XYZ_SMOKE` | Smoke tests for public service health and auth gates | `tests/logs/runs/2026-05-27T091831Z/XYZ_SMOKE/` |
| `XYZ_INTEGRATION` | Integration tests for AWS, Exasol, MCP, and service wiring | `tests/logs/runs/2026-05-27T091831Z/XYZ_INTEGRATION/` |
| `XYZ_E2E` | E2E user journeys across QBR, Grafana, Chatbot, and MCP | `tests/logs/runs/2026-05-27T091831Z/XYZ_E2E/` |
| `XYZ_UAT` | User acceptance tests for documented demo workflows | `tests/logs/runs/2026-05-27T091831Z/XYZ_UAT/` |
| `XYZ_CHAOS` | Controlled chaos and negative-path probes | `tests/logs/runs/2026-05-27T091831Z/XYZ_CHAOS/` |
| `XYZ_STRESS` | Optional QBR/Chatbot plus Exasol read-path pressure checks: scheduled default 10 concurrent users; approved extraordinary/manual runs up to 20 | created only when `XYZ_INCLUDE_STRESS=1` |
| `XYZ_REGRESSION` | Optional chatbot RP-series regression harness with machine-readable pass/fail assertions | created only when `XYZ_INCLUDE_REGRESSION=1` |
| `XYZ_INFRASTRUCTURE` | Optional migration-aware AWS account, region, AZ, endpoint, inventory, and architecture snapshot checks | created only when `XYZ_INCLUDE_INFRASTRUCTURE=1` |

## Skill Assignments

| Tester | Assigned valid skills |
|---|---|
| `XYZ_MASTER` | `workshop-fidelity-reviewer`, `secrets-safety-reviewer`, `instructor`, `workshop-runner`, `runtime-debugger`, `aws-cli-resource-inventory` |
| `XYZ_EXPERT` | `workshop-fidelity-reviewer`, `secrets-safety-reviewer`, `infra-provisioning-auditor`, `workshop-runner`, `exasol-setup-personal`, `hashicorp-terraform`, `aws-cli-resource-inventory` |
| `XYZ_SMOKE` | `runtime-debugger`, `workshop-runner`, `aws-cli-resource-inventory`, `streamlit-dashboard-builder`, `grafana-dashboard-validator` |
| `XYZ_INTEGRATION` | `exasol-database`, `aws-lambda-operations`, `aws-cli-resource-inventory`, `grafana-dashboard-validator`, `workshop-runner`, `exasol-prometheus-exporter`, `prometheus-grafana-validator`, `runtime-debugger`, `streamlit-dashboard-builder` |
| `XYZ_E2E` | `student`, `bi-analyst`, `exasol-database`, `streamlit-dashboard-builder`, `workshop-runner`, `grafana-dashboard-validator`, `runtime-debugger` |
| `XYZ_UAT` | `student`, `instructor`, `bi-analyst`, `streamlit-dashboard-builder`, `workshop-fidelity-reviewer`, `grafana-dashboard-validator`, `workshop-runner` |
| `XYZ_CHAOS` | `runtime-debugger`, `container-network-telemetry-validator`, `prometheus-grafana-validator`, `workshop-runner`, `prometheus-api`, `promql`, `sflow-rt-visualization-debugger`, `docker-compose-ec2` |
| `XYZ_STRESS` | `runtime-debugger`, `streamlit-dashboard-builder`, `exasol-database`, `aws-cloudwatch-observability-validator`, `container-network-telemetry-validator`, `workshop-runner`, `xyz-ui-ux-dashboard-reviewer` |
| `XYZ_REGRESSION` | `aws-bedrock-runtime-validator`, `exasol-database`, `runtime-debugger`, `student` |
| `XYZ_INFRASTRUCTURE` | `aws-cli-resource-inventory`, `aws-ec2-ssm-runtime-validator`, `aws-eventbridge-scheduler-validator`, `aws-cognito-oauth-validator`, `aws-ses-deliverability-validator`, `aws-ssm-parameter-store-safety`, `aws-s3-operator-dashboard-validator`, `aws-iam-least-privilege-reviewer`, `docker-compose-ec2`, `container-network-telemetry-validator`, `workshop-fidelity-reviewer` |

There is no dedicated installed MCP server testing skill yet. MCP behavior is
covered through `workshop-runner`, `runtime-debugger`, `student`,
`exasol-database`, and explicit OAuth/metadata endpoint probes.

## Single-Writer Rule

The tester personas are read-only evidence producers. `XYZ_MASTER` is the only
role allowed to edit `docs/XYZ_PILOT_ISSUE_TRACKER.md` during a test campaign.
It deduplicates issue candidates, assigns the next sequential ID, and executes
the full archive/verify/edit/verify discipline only in the explicit filing
step.

After `XYZ_MASTER` decides a candidate, it writes a sibling outcome marker under
`tests/logs/runs/<run_id>/<source>/candidate-outcomes/`, for example
`candidate-001.ACCEPTED.MINOR.XYZ-PILOT-211.md` or
`candidate-003.DENIED.DUPLICATE.XYZ-PILOT-189.md`. These markers are the queue's
source of truth for whether a candidate is still pending. The helper is
`scripts/write_candidate_outcome.py`; the filing wrapper displays markers in
`tests/scripts/run_xyz_master_filing.sh <run_id> --list` and refuses to generate
a prompt for already-decided candidates.
Use `scripts/find_orphan_candidates.py` to report undecided candidate files that
already landed in the tracker or are structurally malformed before attempting
another `XYZ_MASTER` drain.

## Shipped-Feature Test Review

Every shipped feature should be reviewed for new swarm coverage before the work
is closed. Add static discipline checks to `XYZ_EXPERT`, service wiring checks
to `XYZ_INTEGRATION`, endpoint health checks to `XYZ_SMOKE`, multi-service
journeys to `XYZ_E2E`, subscriber-facing wording checks to `XYZ_UAT`, and
negative-path probes to `XYZ_CHAOS`.

For performance-sensitive shipped features, also decide whether `XYZ_STRESS`
should replay them concurrently. Its default lane now runs public QBR/Chatbot
HTTP probes, an observable `pyexasol` `XYZ_DASH` session-hold proof, and the
read-only Exasol warm-up/regression query pack from
`scripts/exasol_warmup/warmup.py` through `XYZ_DASH`. New QBR panels, Grafana
panels, chatbot chart SQL shapes, and post-start warm-up queries should be
added to that warm-up pack when they become user-facing.

For migration-sensitive shipped features, also decide whether
`XYZ_INFRASTRUCTURE` should run. Any change that touches AWS account IDs,
regions, Availability Zones, Cloudflare/DNS, EC2 hosts, S3 buckets, Cognito,
SES/SNS, SSM, EventBridge/Scheduler, Lambda, Bedrock runtime bindings, or
Docker-to-Exasol host mapping should either include the infrastructure agent or
explicitly document why the migration topology check is out of scope.

The runbook details this daily practice in
[`docs/50-testing-validation/XYZ_PILOT_TEST_RUNBOOK.md`](../docs/50-testing-validation/XYZ_PILOT_TEST_RUNBOOK.md).

## Tracker Discipline

Any confirmed new issue found during these tests must first be captured as
`tests/logs/runs/<run_id>/<agent>/issue-candidates.md`. Central tracker filing
is performed only by `XYZ_MASTER`, one candidate at a time, after:

1. `./scripts/archive_tracker.sh "<reason>"`
2. `./scripts/verify_tracker_completeness.sh`
3. Add both the top-table row and the matching full-register detail section.
4. `./scripts/verify_tracker_completeness.sh`

If the pre-edit verifier fails, fix the tracker integrity problem before filing
the runtime issue.

## Safety Limits

- Do not print secrets from `/tmp`, `.env`, `secrets.json`, SSM, or AWS output.
- Prefer read-only checks first.
- Container kill/restart chaos is allowed only when explicitly in scope and must
  be documented with before/after health evidence.
- No Terraform, Exasol destroy, AWS stop/start, password rotation, or credential
  mutation is part of this test harness.
