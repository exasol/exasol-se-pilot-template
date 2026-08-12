# AWS Resource Tagging Runbook

Generated projects must apply the Exasol IT AWS Resource Tagging Policy before
any deployment into an AWS account and target AWS availability zone.

## Mandatory Gate

No AWS resource creation, update, migration, or public cutover may start until
the policy values below are filled in `config/project.env` for the target
account, region, and AZ. This applies to Exasol Personal, EC2/EBS/networking,
Lambda, EventBridge, S3, SES, SNS, Cognito, IAM, Cloudflare-facing plumbing
where AWS resources are involved, and any deployment helper scripts.

Every taggable resource must receive the mandatory tags at create time. If an
AWS API lacks create-time tagging support, the same deployment step must tag the
resource immediately after creation before the workflow continues. Treat missing
mandatory tags as deployment defects.

## Mandatory Tags

| Tag | Value |
|---|---|
| `exa:Project` | `<PROJECT_SLUG>` |
| `exa:Environment` | `<ENVIRONMENT>` |
| `exa:Department` | `<DEPARTMENT>` |
| `exa:Workload` | `database`, `application`, `automation`, `logging`, or `networking` |
| `exa:CostCenter` | `<COST_CENTER>` |
| `exa:Owner` | `<OPERATOR_EMAIL>` |
| `exa:DataClassification` | `<DATA_CLASSIFICATION>` |
| `exa:customer` | `<CUSTOMER_NAME>` |

Recommended supporting tags:

| Tag | Purpose |
|---|---|
| `Name` | Human-readable resource name |
| `ManagedBy` | Owning automation or operator workflow |
| `Deployment` / `exa:Deployment` | Deployment or migration run grouping |
| `TrackerIssue` | Related `XYZ-PILOT-NNN` issue when applicable |

## Workload Values

Use consistent `exa:Workload` values so cost and ownership reports group cleanly:

- `database` for Exasol Personal database resources.
- `application` for Streamlit, Grafana, chatbot, MCP, and EC2 app-stack assets.
- `automation` for Lambda, EventBridge, GitHub OIDC, and deployment helpers.
- `logging` for CloudWatch Logs and observability assets.
- `networking` for VPC, subnet, route table, security group, EIP, DNS, and tunnel-related AWS assets.

## Source Pilot Sample Tags

The source pilot used the following Exasol IT tag pattern. Generated projects
must replace the placeholder values, but should keep the same mandatory `exa:*`
shape.

Base application or automation resource:

```text
Name=<PROJECT_SLUG>-<resource-purpose>
ManagedBy=<deployment-script-or-operator-flow>
Deployment=<deployment-or-migration-run-id>
exa:Project=<PROJECT_SLUG>
exa:Environment=staging
exa:Department=SE
exa:Workload=application
exa:CostCenter=20060
exa:Owner=<OPERATOR_EMAIL>
exa:DataClassification=confidential
exa:customer=<CUSTOMER_NAME>
TrackerIssue=XYZ-PILOT-NNN
```

Database resource:

```text
Name=<PROJECT_SLUG>-exasol-personal
ManagedBy=exasol-personal-deployment
exa:Project=<PROJECT_SLUG>
exa:Environment=staging
exa:Department=SE
exa:Workload=database
exa:CostCenter=20060
exa:Owner=<OPERATOR_EMAIL>
exa:DataClassification=confidential
exa:customer=<CUSTOMER_NAME>
```

Network resource:

```text
Name=<PROJECT_SLUG>-vpc
ManagedBy=aws-cli-deployment
exa:Project=<PROJECT_SLUG>
exa:Environment=staging
exa:Department=SE
exa:Workload=networking
exa:CostCenter=20060
exa:Owner=<OPERATOR_EMAIL>
exa:DataClassification=internal
exa:customer=<CUSTOMER_NAME>
```

Public-status or static-dashboard resource:

```text
Name=<PROJECT_SLUG>-operator-dashboard
ManagedBy=operator-dashboard-deployment
exa:Project=<PROJECT_SLUG>
exa:Environment=staging
exa:Department=SE
exa:Workload=application
exa:CostCenter=20060
exa:Owner=<OPERATOR_EMAIL>
exa:DataClassification=public
exa:customer=<CUSTOMER_NAME>
```

## Evidence

After any AWS resource-state change, run:

```bash
scripts/aws_tag_inventory.sh \
  --profile <AWS_PROFILE> \
  --region <AWS_REGION> \
  --project <PROJECT_SLUG>
```

Commit or attach the generated evidence under
`docs/60-evidence-inventory/aws-tagging/inventory/` whenever deployment state
changes. The inventory is the audit record proving that the target account,
region, and AZ are compliant with the Exasol IT tagging policy.
