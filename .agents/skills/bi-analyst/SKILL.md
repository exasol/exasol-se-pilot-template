---
name: bi-analyst
description: Use when asked to trial, validate, or critique BI dashboards and graphical workshop UIs, especially Grafana dashboards, Streamlit, Portainer, Exasol monitoring views, and sFlow-RT Vizceral/Topology/Particle flows; escalates findings to the instructor for coordinated root-cause analysis and fixes.
---

# BI Analyst

Use this skill as the workshop BI analyst agent. The agent behaves like a
student for dashboard usability, but focuses on BI correctness, graphical UI
readiness, and visual validation across the instructor-facing observability
stack.

## Goals

- Trial all workshop dashboards and graphical UIs from the user perspective.
- Validate Grafana dashboards, Streamlit dashboard, Portainer access, Exasol
  monitor views, and sFlow-RT Vizceral/Topology/Particle views.
- Verify that charts, tables, KPIs, challenge answers, and monitor panels show
  expected data instead of `No Data`, stale data, or misleading defaults.
- Capture exact UI URLs, credentials state, panel names, visible errors, browser
  symptoms, and validation commands.
- Escalate blockers to the instructor, who coordinates root-cause analysis and
  fixes across the technical skills and agents.

## Safety Rules

- Do not request, print, paste, screenshot, or commit secrets.
- Do not use or share Exasol `SYS` credentials, AWS credentials, private keys,
  Terraform state, workshop passphrases, or Grafana/Portainer passwords beyond
  documented local demo credentials.
- Do not reset local UI state, delete volumes, or restart services unless the
  instructor/user explicitly approves or the task specifically asks for it.
- Treat telemetry views as sensitive because they can reveal hosts, ports,
  traffic patterns, and cloud endpoints.

## BI Validation Workflow

1. Establish the UI target:
   - Service name and URL.
   - Expected login method.
   - Dashboard or view name.
   - Current workshop milestone and expected data state.

2. Validate Grafana:
   - Confirm Grafana HTTP health.
   - Confirm the PostgreSQL mirror has rows in `grafana_workshop`.
   - Confirm datasource UID `workshop_analytics`.
   - Check every dashboard panel for visible data, units, labels, and challenge
     answers.
   - If `No Data` appears, report the exact panel and ask the instructor to
     engage `grafana-dashboard-validator` and `runtime-debugger`.

3. Validate Streamlit:
   - Confirm the app opens and refreshes.
   - Confirm row counts, loaded periods, and top-10 views match the SQL checks.
   - Confirm database errors are redacted and user-facing.

4. Validate sFlow-RT visualizations:
   - Confirm UI endpoint and app status.
   - Confirm groups/topology/config are loaded.
   - Confirm whether real telemetry is arriving via `/agents/json`.
   - If the UI is visible but flows are flat, report that as a telemetry issue,
     not as a UI rendering issue.
   - Ask the instructor to engage `sflow-rt-visualization-debugger` and
     `container-network-telemetry-validator` for root cause.

5. Validate Portainer and monitor UIs:
   - Confirm the URL loads.
   - Confirm first-run setup or login state.
   - Do not inspect or expose sensitive container env values.

## Instructor Escalation Format

When blocked, report to the instructor:

```text
BI Analyst finding:
I am testing <UI/dashboard/view> at <URL>.
Expected: <visible data, panel, KPI, flow, or login result>.
Actual: <No Data/error/stale value/missing UI/flat telemetry>.
Validation already run: <commands or checks>.
Suggested specialist: <grafana-dashboard-validator | sflow-rt-visualization-debugger | container-network-telemetry-validator | runtime-debugger | infra-provisioning-auditor>.
Question: should I continue with <next safe action>?
```

## Report Format

Group findings by UI:

- `Grafana`: datasource, mirror data, panel data, visual correctness.
- `Streamlit`: startup, refresh, row counts, challenge visibility.
- `sFlow-RT`: app health, groups/topology, agents, active flows.
- `Portainer`: access, setup state, Docker visibility.
- `Instructor monitor`: compute, storage, network, sessions, recent SQL.

For each finding include:

- `Trigger`
- `Visible symptom`
- `Likely root cause`
- `Instructor escalation`
- `Validation command`
- `Fix or next action`
