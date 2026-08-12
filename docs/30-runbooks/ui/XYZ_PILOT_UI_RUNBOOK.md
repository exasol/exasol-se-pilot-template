# Pilot UI Runbook

UI surfaces:

- Streamlit insights dashboard.
- Grafana operational dashboards.
- Operator dashboard.
- Chatbot.

Design rules:

- Prefer dense operational layouts over marketing pages.
- Keep service URLs and deployment provenance visible to operators.
- Keep customer data out of screenshots unless approved.
- Validate desktop and mobile where applicable.

## UI provenance contract

Every user-facing UI must show enough deployment metadata for an operator to
tie the rendered surface back to one single last deployment-cause issue.

- Static operator dashboard pages carry a `BUILD_INFO` block with
  `lastDeploymentIssue`, `trackerUrl`, `builtAtUtc`, and a session-start
  timestamp.
- Docker-stamped Streamlit services carry `XYZ_LAST_DEPLOYMENT_ISSUE`,
  `XYZ_BUILD_AT_UTC`, `XYZ_DEPLOYED_AT_UTC`, and `APP_STARTED_AT_UTC`.
- Streamlit insights services that are rebuilt from candidate evidence also
  carry `XYZ_DEPLOYMENT_REF_URL`.
- Generated projects replace `XYZ-PILOT-NNN` references with their local
  tracker prefix during template rendering.
