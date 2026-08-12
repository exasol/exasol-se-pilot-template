# Documentation Home

This folder is the documentation control plane for an Exasol SE pilot project.

The highest-level project document is the central tracker:

- [Pilot Issue Tracker](XYZ_PILOT_ISSUE_TRACKER.md)

Core navigation:

- [Project Documentation TOC](20-architecture/XYZ_PILOT_PROJECT_DOCUMENTATION_TOC.md)
- [Project Dependency Graph](20-architecture/XYZ_PILOT_PROJECT_DEPENDENCY_GRAPH.md)
- [Template Rationale](20-architecture/XYZ_PILOT_TEMPLATE_RATIONALE.md)
- [Current Daily Status](00-live-status/XYZ_PILOT_DAILY_STATUS.md)
- [Progress Log](00-live-status/XYZ_PILOT_PROGRESS_LOG.md)
- [Deployment Runbook](30-runbooks/deployment/XYZ_PILOT_DEPLOYMENT_RUNBOOK.md)
- [Developer Runbook](30-runbooks/operations/XYZ_PILOT_DEVELOPER_RUNBOOK.md)
- [Exasol License Operations](30-runbooks/operations/XYZ_PILOT_EXASOL_LICENSE_OPERATIONS.md)
- [GitHub Runbook](30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md)
- [Testing Overview](50-testing-validation/XYZ_PILOT_TEST_OVERVIEW.md)
- [AWS Tagging Runbook](60-evidence-inventory/aws-tagging/AWS_RESOURCE_TAGGING_RUNBOOK.md)

Documentation discipline:

1. Keep `XYZ_PILOT_ISSUE_TRACKER.md` at the root of `docs/`.
2. Add new docs under the topic folder that owns them.
3. Update the TOC and dependency graph whenever docs, runtime modules,
   workflows, candidate paths, or evidence ownership change.
4. Run Markdown link checks before committing.
5. Archive runbook-class documents before structural rewrites.
