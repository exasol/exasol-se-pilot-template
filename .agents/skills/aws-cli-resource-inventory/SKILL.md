---
name: aws-cli-resource-inventory
description: Use when asked to identify, inventory, audit, or report AWS resources with AWS CLI, Resource Groups Tagging API, Resource Explorer, or direct service describe/list calls; emphasizes read-only regional discovery, tagged and untagged resources, stale tag-index caveats, and secret-safe metadata output.
---

# AWS CLI Resource Inventory

Use this skill for AWS account or regional resource inventory, especially when the user asks "what resources remain", "identify AWS resources", "find untagged resources", or wants inventory material for GitHub reports.

## Safety Rules

- Default to read-only AWS CLI calls: `list-*`, `describe-*`, `get-*`.
- Do not read or print secret values: SSM parameter values, Secrets Manager secret values, private keys, access keys, session tokens, passwords, or deployment `secrets.json`.
- Treat tags as public billing/admin metadata. Do not put PII, credentials, or private material into tags.
- Region scope must be explicit. In this workshop, use `eu-central-1` for user wording like `central-eu-1`.
- Before destructive cleanup, switch to `aws-cleanup-validator`; require explicit user confirmation and exact resource scope.

## Inventory Workflow

1. Confirm caller and region:

```bash
aws sts get-caller-identity --query '{Account:Account,Arn:Arn,UserId:UserId}' --output json --no-cli-pager
```

2. Use Resource Groups Tagging API as a tag index only:

```bash
aws resourcegroupstaggingapi get-resources \
  --region eu-central-1 \
  --query 'ResourceTagMappingList[].{ARN:ResourceARN,Tags:Tags}' \
  --output json --no-cli-pager
```

3. Always follow with direct service inventory for live state. Tagging API can omit untagged resources and can temporarily show recently deleted or previously tagged resources.

4. For untagged or broader discovery, use Resource Explorer when available:

```bash
aws resource-explorer-2 search \
  --region eu-central-1 \
  --query-string 'region:eu-central-1 tag:none' \
  --output json --no-cli-pager
```

5. Summarize resources by:
   - service/resource type
   - resource ID and Name tag
   - deployment/owner tags
   - state
   - size or usage-relevant detail
   - likely billing impact

## Direct Service Checks

Core cost-bearing or cleanup-relevant inventory:

```bash
aws ec2 describe-instances --region eu-central-1 --filters Name=instance-state-name,Values=pending,running,stopping,stopped --output json --no-cli-pager
aws ec2 describe-volumes --region eu-central-1 --output json --no-cli-pager
aws ec2 describe-snapshots --region eu-central-1 --owner-ids self --output json --no-cli-pager
aws ec2 describe-addresses --region eu-central-1 --output json --no-cli-pager
aws ec2 describe-nat-gateways --region eu-central-1 --output json --no-cli-pager
aws elbv2 describe-load-balancers --region eu-central-1 --output json --no-cli-pager
aws rds describe-db-instances --region eu-central-1 --output json --no-cli-pager
aws efs describe-file-systems --region eu-central-1 --output json --no-cli-pager
aws eks list-clusters --region eu-central-1 --output json --no-cli-pager
aws ecs list-clusters --region eu-central-1 --output json --no-cli-pager
```

Supporting/control-plane inventory:

```bash
aws ec2 describe-vpcs --region eu-central-1 --output json --no-cli-pager
aws ec2 describe-subnets --region eu-central-1 --output json --no-cli-pager
aws ec2 describe-internet-gateways --region eu-central-1 --output json --no-cli-pager
aws ec2 describe-route-tables --region eu-central-1 --output json --no-cli-pager
aws ec2 describe-security-groups --region eu-central-1 --output json --no-cli-pager
aws ec2 describe-vpc-endpoints --region eu-central-1 --output json --no-cli-pager
aws lambda list-functions --region eu-central-1 --output json --no-cli-pager
aws logs describe-log-groups --region eu-central-1 --output json --no-cli-pager
aws cloudformation describe-stacks --region eu-central-1 --output json --no-cli-pager
aws ssm describe-parameters --region eu-central-1 --output json --no-cli-pager
```

S3 is global for bucket listing; filter by bucket location:

```bash
aws s3api list-buckets --output json --no-cli-pager
aws s3api get-bucket-location --bucket <bucket>
aws s3api list-objects-v2 --bucket <bucket> --query '{KeyCount:KeyCount,Contents:Contents[].{Key:Key,Size:Size}}'
```

## Interpretation Rules

- Live EC2 instances, EBS volumes, snapshots, Elastic IPs, NAT gateways, load balancers, RDS, EFS, OpenSearch, Redshift, ECS/EKS nodes or clusters are high-priority cost findings.
- VPCs, subnets, route tables, internet gateways, default security groups, S3 gateway endpoints, EC2 key pairs, and standard SSM parameters are usually no direct monthly charge, but they can block cleanup or indicate an incomplete deployment destroy.
- S3 buckets with zero objects have negligible storage cost but still create request-level noise.
- AWS-managed KMS keys do not have a monthly key fee; customer-managed keys can.
- CloudWatch Logs cost depends on ingestion and stored bytes; report stored bytes and retention.

## Official AWS References

- Resource Groups Tagging API `GetResources`: https://docs.aws.amazon.com/resourcegroupstagging/latest/APIReference/API_GetResources.html
- AWS CLI `resourcegroupstaggingapi get-resources`: https://docs.aws.amazon.com/cli/latest/reference/resourcegroupstaggingapi/get-resources.html
- Resource Explorer search: https://docs.aws.amazon.com/resource-explorer/latest/userguide/using-search.html
- AWS tag confidentiality warning: https://docs.aws.amazon.com/resourcegroupstagging/latest/APIReference/overview.html
