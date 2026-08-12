# <PILOT_NAME> Tester Swarm Skill Matrix

**Last refresh:** 2026-06-13

This matrix maps installed local skills under `.agents/skills/` to the seven
tester roles. The goal is to make each test pass more efficient by giving every
agent a narrow, explicit operating lens while keeping the default execution mode
read-only and secret-safe.

`xyz_stress`, `xyz_regression`, and `xyz_infrastructure` are opt-in eighth,
ninth, and tenth roles. None is part of the default full swarm. `xyz_stress` activates via
`xyz_PILOT_INCLUDE_STRESS=1` or `tests/scripts/run_stress_swarm.sh`;
`xyz_regression` activates via `xyz_PILOT_INCLUDE_REGRESSION=1` and runs in
every nightly via the workflow env-var injection (per
`XYZ-PILOT-184`);
`xyz_infrastructure` activates via `xyz_PILOT_INCLUDE_INFRASTRUCTURE=1` or
`tests/scripts/run_infrastructure_swarm.sh` for migration-aware account,
region, AZ, endpoint, inventory, and architecture checks.

## Selection Rationale

The installed skills cover five practical domains:

- **Runtime and workshop discipline:** `workshop-runner`,
  `workshop-fidelity-reviewer`, `runtime-debugger`, `student`, `instructor`.
- **Exasol and data path:** `exasol-database`, `exasol-setup-personal`,
  `exasol-prometheus-exporter`, `exasol-bucketfs`, `exasol-udfs`.
- **AWS and infrastructure:** `aws-cli-resource-inventory`,
  `aws-lambda-operations`, `infra-provisioning-auditor`,
  `aws-ec2-ssm-runtime-validator`, `aws-eventbridge-scheduler-validator`,
  `aws-cognito-oauth-validator`, `aws-bedrock-runtime-validator`,
  `aws-ses-deliverability-validator`, `aws-ssm-parameter-store-safety`,
  `aws-s3-operator-dashboard-validator`,
  `aws-cloudwatch-observability-validator`,
  `aws-iam-least-privilege-reviewer`, `hashicorp-terraform`,
  `aws-cost-reporting`.
- **Dashboard and BI:** `streamlit-dashboard-builder`,
  `grafana-dashboard-validator`, `grafana-oss`, `prometheus-grafana-validator`,
  `bi-analyst`, `xyz-ui-ux-dashboard-reviewer`.
- **Telemetry and resilience:** `container-network-telemetry-validator`,
  `prometheus-api`, `promql`, `sflow-rt-visualization-debugger`,
  `docker-compose-ec2`.
- **GitHub governance and automation:** `github-actions-governance`,
  `github-security-supply-chain`, `github-tracker-notification-automation`.

There is currently no dedicated local MCP server testing skill. The swarm
covers MCP behavior through `workshop-runner`, `runtime-debugger`, `student`,
`exasol-database`, and explicit OAuth/metadata endpoint probes. A future skill
could specialize in MCP OAuth, protected-resource metadata, JSON-RPC lifecycle,
tool discovery, and per-client behavior.

## AWS Service-Specific Skill Candidates

The E2E architecture in `docs/20-architecture/xyz_PILOT_AWS_E2E_VISUAL.md` and
`docs/20-architecture/xyz_PILOT_AWS_C4_CONTAINER.md` makes several AWS managed
services first-class runtime boundaries. These local skills have been added so
the swarm can use service-specific read-only lenses instead of relying only on
generic AWS inventory.

| Skill | Service boundary | Default assigned agents | Default posture |
|---|---|---|---|
| `aws-bedrock-runtime-validator` | Bedrock Converse/tool-use for chatbot and daily-status Lambda | `xyz_integration`, `xyz_e2e`, `xyz_uat` | Metadata/log-shape checks; live inference only with explicit scope. |
| `aws-ec2-ssm-runtime-validator` | EC2 host, EIP, EBS, security groups, SSM managed-instance health | `xyz_expert`, `xyz_integration`, `xyz_chaos`, `xyz_infrastructure` | `describe-*` only; no start/stop/reboot or SSM command by default. |
| `aws-cognito-oauth-validator` | Cognito Hosted UI, OAuth/OIDC, Identity Pool, MCP/chatbot auth | `xyz_smoke`, `xyz_integration`, `xyz_e2e`, `xyz_uat`, `xyz_infrastructure` | No token, code, cookie, password, or client-secret output. |
| `aws-eventbridge-scheduler-validator` | Start/stop/status/cognito-sync/oneshot cron and target wiring | `xyz_expert`, `xyz_integration`, `xyz_chaos`, `xyz_infrastructure` | List/describe/get only; no enable/disable/update. |
| `aws-ses-deliverability-validator` | SES sandbox, verified identities, notification deliverability | `xyz_integration`, `xyz_uat`, `xyz_infrastructure` | No email sends by default; aggregate recipient status. |
| `aws-ssm-parameter-store-safety` | Parameter Store SecureString metadata and secret retrieval boundary | `xyz_master`, `xyz_integration`, `xyz_infrastructure` | Never decrypt or print parameter values. |
| `aws-s3-operator-dashboard-validator` | S3 static Operator Dashboard and public website posture | `xyz_smoke`, `xyz_integration`, `xyz_uat`, `xyz_infrastructure` | Prefer metadata/header checks; do not print embedded tokens. |
| `aws-cloudwatch-observability-validator` | Lambda logs, metrics, retention, redacted error signatures | `xyz_master`, `xyz_integration`, `xyz_chaos` | Metrics first; minimal redacted log excerpts only. |
| `aws-iam-least-privilege-reviewer` | Scheduler role, EC2 SSM role, Identity Pool roles, Function URL policies | `xyz_master`, `xyz_expert`, `xyz_infrastructure` | Read-only policy review; summarize wildcards and public invoke risks. |

## GitHub Governance Skill Candidates

The GitHub operator path now has first-class local skills for proposing and
reviewing automation before activation. These are planning/review lenses by
default; they do not mutate branch protection, rulesets, workflows, secrets, or
tracker state unless `xyz_master` explicitly scopes an implementation.

| Skill | Service boundary | Default assigned agents | Default posture |
|---|---|---|---|
| `github-actions-governance` | GitHub Actions triggers, permissions, OIDC, concurrency, cost, workflow activation sequencing | `xyz_master`, `xyz_expert`, `xyz_integration` | Proposal/review first; no workflow activation by default. |
| `github-security-supply-chain` | CodeQL, Dependabot, dependency review, secret scanning, push protection, action pinning, CODEOWNERS/rulesets | `xyz_master`, `xyz_expert`, `xyz_chaos` | Verify availability/licensing and enforcement impact before enabling controls. |
| `github-tracker-notification-automation` | `docs/XYZ_PILOT_ISSUE_TRACKER.md` diff parsing and major-defect heads-up notification design | `xyz_master`, `xyz_integration`, `xyz_uat` | Read-only diff parsing; email fan-out only after GitHub Operator approval. |

## UI / UX Skill Candidates

The UI/UX track adds a local dashboard-review lens for Streamlit and
BI-style work. It is based on the current QBR/Chatbot surfaces and the project
visual-test discipline in `docs/30-runbooks/ui/XYZ_PILOT_UI.md`.

| Skill | Service boundary | Default assigned agents | Default posture |
|---|---|---|---|
| `xyz-ui-ux-dashboard-reviewer` | QBR dashboard, chatbot visual surfaces, KPI hierarchy, chart readability, filter UX, visual regression test cases | `xyz_e2e`, `xyz_uat`, `xyz_master` | Visual review and low-risk polish first; no new dependencies or tracker writes without explicit scope. |

## Agent Assignment

| Agent | Primary skills | Supporting skills | Main payoff |
|---|---|---|---|
| `xyz_master` | `workshop-fidelity-reviewer`, `secrets-safety-reviewer`, `instructor`, `workshop-runner` | `runtime-debugger`, `aws-cli-resource-inventory`, `aws-iam-least-privilege-reviewer`, `aws-ssm-parameter-store-safety`, `aws-cloudwatch-observability-validator`, `github-actions-governance`, `github-security-supply-chain`, `github-tracker-notification-automation`, `xyz-ui-ux-dashboard-reviewer` | Better duplicate triage, tracker discipline, run closure, evidence quality, cloud-control-plane safety review, GitHub automation sequencing, and visual-test acceptance triage. |
| `xyz_expert` | `workshop-fidelity-reviewer`, `secrets-safety-reviewer`, `infra-provisioning-auditor`, `workshop-runner` | `exasol-setup-personal`, `hashicorp-terraform`, `aws-cli-resource-inventory`, `aws-ec2-ssm-runtime-validator`, `aws-eventbridge-scheduler-validator`, `aws-iam-least-privilege-reviewer`, `github-actions-governance`, `github-security-supply-chain` | Detect repo/runbook/guardrail drift before live checks are trusted. |
| `xyz_smoke` | `runtime-debugger`, `workshop-runner`, `aws-cli-resource-inventory` | `streamlit-dashboard-builder`, `grafana-dashboard-validator`, `aws-cognito-oauth-validator`, `aws-s3-operator-dashboard-validator` | Fast readiness checks with better service-boundary interpretation. |
| `xyz_integration` | `exasol-database`, `aws-lambda-operations`, `aws-cli-resource-inventory`, `grafana-dashboard-validator`, `workshop-runner` | `aws-ec2-ssm-runtime-validator`, `aws-eventbridge-scheduler-validator`, `aws-cognito-oauth-validator`, `aws-bedrock-runtime-validator`, `aws-ses-deliverability-validator`, `aws-ssm-parameter-store-safety`, `aws-s3-operator-dashboard-validator`, `aws-cloudwatch-observability-validator`, `exasol-prometheus-exporter`, `prometheus-grafana-validator`, `runtime-debugger`, `streamlit-dashboard-builder`, `github-actions-governance`, `github-tracker-notification-automation` | Stronger AWS/Exasol/Grafana/service wiring and GitHub workflow integration coverage. |
| `xyz_e2e` | `student`, `bi-analyst`, `exasol-database`, `streamlit-dashboard-builder`, `workshop-runner` | `grafana-dashboard-validator`, `runtime-debugger`, `aws-cognito-oauth-validator`, `aws-bedrock-runtime-validator`, `xyz-ui-ux-dashboard-reviewer` | Better subscriber-path validation across Streamlit, Grafana, Chatbot, MCP, Bedrock, Cognito, Exasol, and visual-regression surfaces. |
| `xyz_uat` | `student`, `instructor`, `bi-analyst`, `streamlit-dashboard-builder`, `workshop-fidelity-reviewer`, `xyz-ui-ux-dashboard-reviewer` | `grafana-dashboard-validator`, `workshop-runner`, `aws-cognito-oauth-validator`, `aws-bedrock-runtime-validator`, `aws-ses-deliverability-validator`, `aws-s3-operator-dashboard-validator`, `github-tracker-notification-automation` | Better demo, UX, wording, visual hierarchy, notification, and acceptance-criteria review. |
| `xyz_chaos` | `runtime-debugger`, `container-network-telemetry-validator`, `prometheus-grafana-validator`, `workshop-runner` | `aws-ec2-ssm-runtime-validator`, `aws-eventbridge-scheduler-validator`, `aws-cloudwatch-observability-validator`, `prometheus-api`, `promql`, `sflow-rt-visualization-debugger`, `docker-compose-ec2`, `github-security-supply-chain` | Better non-destructive negative-path, supply-chain, and resilience testing. |
| `xyz_stress` | `runtime-debugger`, `streamlit-dashboard-builder`, `exasol-database`, `aws-cloudwatch-observability-validator`, `container-network-telemetry-validator` | `workshop-runner`, `xyz-ui-ux-dashboard-reviewer`, `aws-ec2-ssm-runtime-validator` | Bounded 5-10 virtual-user QBR/Chatbot and Exasol read-path pressure checks before UAT, with candidate output on latency/error breaches. |
| `xyz_regression` | `aws-bedrock-runtime-validator`, `exasol-database`, `runtime-debugger`, `student` | `aws-cognito-oauth-validator`, `streamlit-dashboard-builder`, `python-tool-validator` | Chatbot RP-series regression harness (`XYZ-PILOT-184`); machine-readable pass/fail assertions per reference prompt against the live Bedrock + pyexasol backend tool-loop. |
| `xyz_infrastructure` | `aws-cli-resource-inventory`, `aws-ec2-ssm-runtime-validator`, `aws-eventbridge-scheduler-validator`, `aws-cognito-oauth-validator`, `aws-ses-deliverability-validator`, `aws-ssm-parameter-store-safety`, `aws-s3-operator-dashboard-validator`, `aws-iam-least-privilege-reviewer` | `docker-compose-ec2`, `container-network-telemetry-validator`, `aws-cloudwatch-observability-validator`, `github-actions-governance`, `workshop-fidelity-reviewer` | Migration-aware account/region/AZ, endpoint, EC2, S3, control-plane, and per-run Mermaid architecture evidence; emits minor candidates for cross-account/AZ dependencies that need cutover triage. |

## Excluded By Default

These skills stay outside the default dry run because they can imply mutation,
cost, or broad cleanup:

- `aws-cleanup-validator`
- `aws-cost-reporting`
- `hashicorp-vault`
- `hashicorp-consul`
- `hashicorp-nomad`
- `hashicorp-vagrant`
- `exasol-bucketfs`
- `exasol-udfs`

They may be activated by `xyz_master` only when a run has explicit scope and
safe prerequisites.
