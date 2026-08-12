# Integration Checks

Cross-service probes for AWS state, Exasol connectivity, MCP OAuth gating,
Grafana datasource/API behavior, scheduler/read-only Lambda status, GitHub
backup workflow status, active GitHub ruleset state, and S3 backup manifest
availability. The integration suite also verifies that the EC2 host has AWS
CLI v2 available and can resolve the instance profile for operator handoff
tasks such as guarded deploys and temporary S3 artifact retrievals.
