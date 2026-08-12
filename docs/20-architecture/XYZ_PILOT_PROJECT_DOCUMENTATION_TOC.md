# Project Documentation TOC

This table of contents is dependency-aware: each document has a topic owner,
primary consumers, and update triggers.

| Area | Document | Purpose | Update Trigger |
|---|---|---|---|
| Control | [Pilot Issue Tracker](../XYZ_PILOT_ISSUE_TRACKER.md) | Highest-level issue, feature, status, and decision register | Every accepted candidate or status transition |
| Live status | [Daily Status](../00-live-status/XYZ_PILOT_DAILY_STATUS.md) | Current environment status and recipient policy | Daily-status automation or operator update |
| Live status | [Progress Log](../00-live-status/XYZ_PILOT_PROGRESS_LOG.md) | Milestone narrative | Milestone-grade implementation |
| Architecture | [Dependency Graph](XYZ_PILOT_PROJECT_DEPENDENCY_GRAPH.md) | Docs/runtime/GitHub dependency graph | Runtime, docs, workflow, or candidate-path changes |
| Architecture | [Template Rationale](XYZ_PILOT_TEMPLATE_RATIONALE.md) | Why this repo structure exists | Template governance changes |
| Data | [Product Data](../10-product-data/README.md) | Customer input and source-system docs | New customer docs or schema mapping |
| Deployment | [Deployment Runbook](../30-runbooks/deployment/XYZ_PILOT_DEPLOYMENT_RUNBOOK.md) | End-to-end deployment path | AWS/Exasol/service deployment changes |
| Migrations | [Template Backport Assessment 2026-07-01](../40-migrations/template-backports/XYZ_PILOT_TEMPLATE_BACKPORT_ASSESSMENT_2026-07-01.md) | Source-template reusable delta assessment | Template backports from source pilot work |
| GitHub | [GitHub Runbook](../30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md) | Branch governance, CODEOWNERS, rulesets, GitHub Actions catalog, and direct-write paths | GitHub workflow, ruleset, or CODEOWNERS changes |
| Operations | [Developer Runbook](../30-runbooks/operations/XYZ_PILOT_DEVELOPER_RUNBOOK.md) | Agent and developer discipline | Guardrail, commit, or workflow changes |
| Operations | [Exasol License Operations](../30-runbooks/operations/XYZ_PILOT_EXASOL_LICENSE_OPERATIONS.md) | Grafana ConfD license export and guarded Exasol Personal license rotation | Exasol license/proxy/Grafana license panel changes |
| Security | [Credentials Runbook](../30-runbooks/security/XYZ_PILOT_CREDENTIALS_RUNBOOK.md) | Secret and credential handling | Credential path or IAM changes |
| MCP | [MCP Client Guide](../30-runbooks/mcp/XYZ_MCP_CLIENT_GUIDE.md) | Client access to Exasol MCP | MCP endpoint or auth changes |
| UI | [UI Runbook](../30-runbooks/ui/XYZ_PILOT_UI_RUNBOOK.md) | Streamlit/Grafana/operator dashboard UX | UI/service changes |
| Testing | [Testing Overview](../50-testing-validation/XYZ_PILOT_TEST_OVERVIEW.md) | Swarm agents and validation strategy | Test agent or workflow changes |
| Evidence | [AWS Tagging Runbook](../60-evidence-inventory/aws-tagging/AWS_RESOURCE_TAGGING_RUNBOOK.md) | Inventory and tagging evidence | AWS resource changes |
| Generated QA | [Generated QA](../70-generated-qa/qa/README.md) | Curated chatbot/operator Q&A artifacts | Approved QA artifact generation |

## Maintenance Rule

If a document is moved, renamed, added, or removed, update this TOC and run the
Markdown link checker before commit.
