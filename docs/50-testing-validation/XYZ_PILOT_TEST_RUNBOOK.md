# Pilot Test Runbook

This runbook is the detailed execution discipline for generated pilot test
runs. It complements `XYZ_PILOT_TEST_OVERVIEW.md`.

## 1 Scope

The swarm validates repository discipline, AWS control-plane wiring, Exasol
Personal reachability, Docker service health, Streamlit/Grafana/chatbot/MCP
user journeys, and candidate generation.

## 2 Roles

Default tester roles are `xyz_expert`, `xyz_smoke`, `xyz_integration`,
`xyz_e2e`, `xyz_uat`, and `xyz_chaos`. Optional roles are `xyz_stress`,
`xyz_regression`, and `xyz_infrastructure`.

## 3 Run Folder

Every run writes evidence under:

```text
tests/logs/runs/<UTC-id>/
```

## 4 Candidate Output

Any confirmed defect or feature request from a tester must be captured as:

```text
tests/logs/runs/<UTC-id>/<agent>/issue-candidates.md
```

Only `xyz_master` may file accepted candidates into the central tracker.

## 5 Safety Limits

Default runs are read-only. Mutating cloud resources, rotating credentials,
starting or stopping EC2, deleting evidence, or dispatching public notifications
requires explicit operator scope.

## 6 Optional Agents

Enable optional agents only when their risk surface is relevant:

- `xyz_stress` for bounded concurrent-load checks.
- `xyz_regression` for chatbot reference-prompt assertions.
- `xyz_infrastructure` for migration-aware account, region, AZ, endpoint, and
  resource-mapping checks.

## 7 Execution Discipline

Before a run, record the AWS profile, account ID, region, Availability Zone,
target URLs, expected identity boundary, and whether optional agents are
enabled. During a run, keep raw logs in the run folder and summarize only
secret-safe findings in issue candidates. After a run, verify every agent has an
`agent.exit` marker and an evidence summary before treating the run as complete.

## 8 Evidence Standards

Evidence must be reproducible, timestamped, and secret-safe. Prefer command
summaries, HTTP status/latency, resource ARNs without secrets, sanitized log
excerpts, and Mermaid architecture diagrams over raw credential-bearing output.
Do not paste `.env`, SSM SecureString values, private keys, OAuth tokens,
cookies, Cognito temporary passwords, or database passwords into run evidence.
