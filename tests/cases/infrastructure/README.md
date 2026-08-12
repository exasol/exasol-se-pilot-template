# XYZ_INFRASTRUCTURE Test Cases

These cases define the opt-in infrastructure tester scope for account, region,
AZ, endpoint, and control-plane migrations.

## INFRA-01 Profile And Account Identity

- Prove the source AWS profile resolves to the expected source account ID.
- Prove the target AWS profile resolves to the expected target account ID.
- Emit a major candidate if a profile resolves to the wrong account or cannot
  be proven.

## INFRA-02 EC2 Region And AZ Binding

- Prove source and target Exasol Personal EC2 instance IDs exist in the
  declared regions.
- Capture instance state, VPC, subnet, IAM profile ARN, public IP, private IP,
  and observed AZ.
- Emit a major candidate if an expected EC2 instance is missing or bound to an
  unexpected AZ.

## INFRA-03 Public Endpoint Plumbing

- Probe QBR, Chatbot, Grafana, and MCP public health or metadata endpoints.
- Probe the active S3-hosted Operator Dashboard and retain an explicit legacy
  dashboard probe while the source account remains available for soak or a
  possible DEV lane.
- Keep public endpoint downtime advisory during planned outage windows unless
  strict mode is enabled.
- Emit a minor candidate only for a concrete cross-account or cross-AZ public
  route whose current owner is ambiguous or stale in run evidence.

## INFRA-04 AWS Control Plane Inventory

- Capture source and target Lambda, EventBridge, Scheduler, Cognito, SES, SNS,
  SSM parameter metadata, S3 bucket, subnet, and security-group metadata.
- Do not read secrets or decrypt parameters.
- Treat empty target inventories as advisory before their migration phase, but
  emit minor candidates for undocumented cross-account dependencies.

## INFRA-05 Tag Inventory Evidence

- Run `scripts/aws_tag_inventory.sh` for source and target.
- Store CSV, JSON, and Markdown inventory output below the run's
  `artifacts/aws-tag-inventory/` tree.
- Use direct service probes as fallback or supplement because tag inventories
  can be stale or incomplete.

## INFRA-06 Architecture Snapshot

- Generate Mermaid architecture snapshots per run under
  `artifacts/architecture/`.
- Include scenario, run ID, account IDs, regions, AZs, instance IDs, public
  endpoint names, and control-plane boundaries.
- Use snapshots for time-travel RCA when investigating stale mappings, DNS or
  tunnel drift, wrong-profile operations, and network routing mistakes.
