# Pilot Daily Status

> **Report date:** <YYYY-MM-DD>

This document is the project-local status snapshot that daily-status automation
may refresh. It starts empty in the template and is populated by generated
projects.

## Current Environment

| Surface | Status | URL / Notes |
|---|---|---|
| Exasol Personal | Not deployed | `<EXASOL_DEPLOYMENT_DIR>` |
| Exasol MCP Server | Not deployed | `<xyz_mcp_url>` |
| Streamlit Insights | Not deployed | `<PILOT_INSIGHTS_URL>` |
| Grafana | Not deployed | `<PILOT_GRAFANA_URL>` |
| Chatbot | Not deployed | `<PILOT_CHATBOT_URL>` |
| Operator Dashboard | Not deployed | `<PILOT_OPERATOR_DASHBOARD_URL>` |

## Recipient Policy

| Cohort | Purpose | Source |
|---|---|---|
| OPS-only | Operational status, start/stop, defects, deploy alerts | `config/recipients.yml` |
| Business users | Pilot access and business-facing announcements | `config/recipients.yml` |

