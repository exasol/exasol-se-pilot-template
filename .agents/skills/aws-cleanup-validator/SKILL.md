---
name: aws-cleanup-validator
description: Use when asked to destroy, clean up, delete, or validate cleanup of AWS workshop resources, Exasol Personal deployments, orphaned VPC scaffolding, S3 archive buckets, key pairs, SSM parameters, or cost-bearing AWS resources; requires explicit destructive scope and verifies deletion with direct service checks.
---

# AWS Cleanup Validator

Use this skill for AWS cleanup and post-destroy validation. It complements `infra-provisioning-auditor`: this skill is specifically for deleting scoped AWS resources and proving that billable resources are gone.

## Safety Rules

- Never delete resources without explicit user confirmation of the exact scope: account, region, deployment ID, owner tag, and resource classes.
- Prefer read-only inventory first. Produce the candidate delete set before deleting.
- Do not read secret values while cleaning up. Delete SSM parameter names only after scope confirmation.
- Do not use Resource Groups Tagging API alone as proof of existence or deletion; it can return previously tagged or recently deleted resources.
- For Exasol Personal, do not run local `exasol destroy` from a stale `deployment/` directory unless local state matches the target deployment exactly.

## Cleanup Workflow

1. Confirm identity:

```bash
aws sts get-caller-identity --query '{Account:Account,Arn:Arn,UserId:UserId}' --output json --no-cli-pager
```

2. Build a candidate set with direct service calls and tags. For Exasol Personal, expected resources include:
   - EC2 instance(s)
   - root/data EBS volumes
   - key pair
   - SSM private-key parameter name
   - security group
   - route table and association
   - subnet
   - internet gateway
   - VPC
   - optional S3 archive bucket
   - optional S3 gateway VPC endpoint
   - optional IAM role/instance profile/policy for archive access

3. Report candidate set and wait for confirmation if the user has not already given exact destructive instruction.

4. Delete in dependency order:
   - terminate EC2 instances
   - wait for termination
   - delete detached non-root EBS volumes
   - delete snapshots only if explicitly in scope
   - delete key pair and SSM parameter
   - empty and delete S3 archive buckets only if explicitly in scope
   - delete VPC endpoints
   - disassociate and delete route tables
   - detach and delete internet gateways
   - delete security groups and subnets
   - delete VPC
   - delete archive IAM policies/instance profiles/roles if present and in scope

5. Verify with direct service checks:
   - instance state is `terminated` or no active instances remain
   - volume IDs return `InvalidVolume.NotFound` or no volumes for deployment tags
   - VPC/subnet/route table/IGW/security group direct queries are empty
   - key pair query is empty
   - SSM parameter query is empty
   - S3 bucket absent or empty/deleted according to scope

## Cost-Sensitive Priority

Prioritize deletion/validation of:

- EC2 instances
- EBS volumes and snapshots
- Elastic IPs
- NAT gateways
- load balancers
- RDS/EFS/OpenSearch/Redshift/EKS resources
- non-empty S3 buckets
- customer-managed KMS keys, if explicitly in scope

Network scaffolding like VPCs, subnets, route tables, internet gateways, and security groups usually has no direct charge, but leaving it behind hides incomplete cleanup and can hit quotas.

## Reporting Cleanup Results

Report:

- exact resource IDs deleted
- resource IDs verified absent
- resources intentionally retained
- direct service verification commands or summarized output
- any stale Resource Groups Tagging API entries as lag, not live resources, when direct checks prove deletion

Keep the final answer concise and include any residual cost risk.

## Official AWS References

- Resource Groups Tagging API `GetResources`: https://docs.aws.amazon.com/resourcegroupstagging/latest/APIReference/API_GetResources.html
- AWS CLI `resourcegroupstaggingapi get-resources`: https://docs.aws.amazon.com/cli/latest/reference/resourcegroupstaggingapi/get-resources.html
- Resource Explorer search: https://docs.aws.amazon.com/resource-explorer/latest/userguide/using-search.html
- AWS Free Tier charge avoidance guidance: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/avoid-charges-after-free-tier.html
