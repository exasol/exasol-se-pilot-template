---
name: aws-cost-reporting
description: Use when asked to produce AWS monthly cost, usage, Free Tier, above-free-tier, Cost Explorer, Budgets, or GitHub-ready Markdown reports; combines Cost Explorer data with direct AWS CLI resource inventory and flags active cost risks without exposing secrets.
---

# AWS Cost Reporting

Use this skill for AWS cost reports, monthly resource usage reports, Free Tier analysis, above-free-tier checks, or committed Markdown reports under `reports/`.

## Safety Rules

- Cost reports must not include secret values, private keys, tokens, or full credential material.
- SSM parameters may be listed by name/type/tier only; never call `get-parameter --with-decryption` for reporting.
- Secrets Manager may be listed by metadata only; never call `get-secret-value`.
- Treat Cost Explorer data as authoritative for cost, and direct service inventory as authoritative for current resource state.
- Note when Cost Explorer cost allocation tags are inactive or return only empty values; do not invent per-resource costs from tags.

## Report Workflow

1. Define periods with inclusive start and exclusive end dates.
   - March 2026: `Start=2026-03-01,End=2026-04-01`
   - April 2026: `Start=2026-04-01,End=2026-05-01`
   - May 2026 MTD on 2026-05-04: `Start=2026-05-01,End=2026-05-05`

2. Collect account-level and regional costs:

```bash
aws ce get-cost-and-usage \
  --time-period Start=YYYY-MM-DD,End=YYYY-MM-DD \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --output json --no-cli-pager
```

Regional filter for Frankfurt:

```bash
aws ce get-cost-and-usage \
  --time-period Start=YYYY-MM-DD,End=YYYY-MM-DD \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --filter '{"Dimensions":{"Key":"REGION","Values":["eu-central-1"]}}' \
  --group-by Type=DIMENSION,Key=SERVICE \
  --output json --no-cli-pager
```

3. Add usage-type detail for above-free-tier diagnosis:

```bash
aws ce get-cost-and-usage \
  --time-period Start=YYYY-MM-DD,End=YYYY-MM-DD \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --filter '{"Dimensions":{"Key":"REGION","Values":["eu-central-1"]}}' \
  --group-by Type=DIMENSION,Key=SERVICE Type=DIMENSION,Key=USAGE_TYPE \
  --output json --no-cli-pager
```

4. Check whether cost allocation tags are available before using tag-based cost attribution:

```bash
aws ce get-tags --time-period Start=YYYY-MM-DD,End=YYYY-MM-DD --tag-key Deployment --output json --no-cli-pager
aws ce get-tags --time-period Start=YYYY-MM-DD,End=YYYY-MM-DD --tag-key Owner --output json --no-cli-pager
```

5. Combine with `aws-cli-resource-inventory` for current resource state and billing risk.

6. Write Markdown reports under `reports/` with:
   - period and generation date
   - account and region scope
   - account-level Cost Explorer totals
   - regional Cost Explorer totals
   - usage-type cost drivers
   - above-free-tier/ongoing cost risk table
   - current inventory summary
   - resources grouped by deployment/owner tags
   - secret-safety note

## Free Tier and Above-Free-Tier Checks

Use these signals:

- Cost Explorer service/usage-type rows with non-zero cost indicate actual billable usage.
- EC2 `BoxUsage` for non-free instance classes, EBS `VolumeUsage`, public IPv4 usage, NAT gateway hours, load balancer hours, RDS, EFS, EKS, OpenSearch, and Redshift should be called out.
- AWS Free Tier usage alerts can notify at 85 percent of a Free Tier limit, and AWS Budgets can track usage or zero-spend goals.
- The Free Tier API can programmatically track actual and forecasted usage against monthly free usage limits when available.

Free Tier CLI check, when the account/API supports it:

```bash
aws freetier get-free-tier-usage --region us-east-1 --output json --no-cli-pager
```

Budget checks:

```bash
aws budgets describe-budgets --account-id <account-id> --output json --no-cli-pager
```

## Report Quality Bar

- Reports must be reproducible from AWS CLI commands.
- Include exact dates; do not use vague "this month" language without the generated date.
- Distinguish historical month-to-date charges from ongoing charges after cleanup.
- Show both "identified live resources" and "services checked with none found".
- Use no more precision than useful: dollars to two decimals for material amounts, four decimals for tiny amounts.
- Prefer Markdown tables for GitHub readability.

## Official AWS References

- Cost Explorer `GetCostAndUsage`: https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_GetCostAndUsage.html
- AWS CLI `ce get-cost-and-usage`: https://docs.aws.amazon.com/cli/latest/reference/ce/get-cost-and-usage.html
- Cost Explorer default reports: https://docs.aws.amazon.com/cost-management/latest/userguide/ce-default-reports.html
- Tracking AWS Free Tier usage: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/tracking-free-tier-usage.html
- Using the Free Tier API: https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/using-free-tier-api.html
- AWS Budgets CLI: https://docs.aws.amazon.com/cli/latest/reference/budgets/index.html
- EC2 Free Tier usage tracking: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html
