# Pilot Deployment Runbook

This runbook describes the generated project's deployment path.

## Phases

1. Confirm AWS identity with `aws sts get-caller-identity`.
2. Configure `config/project.env`.
3. Confirm Exasol IT AWS Resource Tagging Policy values for the target AWS
   account, region, and AZ with `scripts/require_exasol_aws_tags.sh`.
4. Initialize Exasol Personal.
5. Deploy Exasol Personal only after operator confirmation and tag validation.
6. Install Docker/Compose/Cloudflare on the EC2 host.
7. Deploy service stack locally on the EC2 host.
8. Configure SES, SNS, Cognito, Lambda, EventBridge, and S3.
9. Run `scripts/aws_tag_inventory.sh` and resolve tagging gaps.
10. Validate endpoints privately.
11. Activate public Cloudflare/DNS routing.
12. Run swarm validation.
13. Announce pilot availability.

## Guardrail

No cost-bearing step may run from generator dry-run mode. Use explicit deploy
commands and record account, region, AZ, and resource names in the tracker.

No AWS deployment step may create or update resources unless the mandatory
Exasol IT AWS Resource Tagging Policy values are present in `config/project.env`
and used by the deployment command. Every taggable AWS resource must carry:

- `exa:Project`
- `exa:Environment`
- `exa:Department`
- `exa:Workload`
- `exa:CostCenter`
- `exa:Owner`
- `exa:DataClassification`
- `exa:customer`

If create-time tagging is unavailable for a service/API, tag immediately after
creation in the same controlled step and record the exception. Run
`scripts/aws_tag_inventory.sh` after resource-state changes and keep the
generated evidence with the deployment record.

Use
[`AWS_RESOURCE_TAGGING_RUNBOOK.md`](../../60-evidence-inventory/aws-tagging/AWS_RESOURCE_TAGGING_RUNBOOK.md)
for the mandatory policy, source-pilot sample tag sets, and post-deployment
inventory evidence commands.
