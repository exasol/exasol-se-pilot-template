---
name: aws-lambda-operations
description: Use when asked to inventory, audit, report, debug, cost-check, or safely operate AWS Lambda functions, function URLs, event source mappings, CloudWatch metrics/logs, Lambda IAM roles, or workshop credential/alert Lambdas using AWS CLI and official AWS Lambda guidance.
---

# AWS Lambda Operations

Use this skill for AWS Lambda-specific inventory, reporting, monitoring, cost checks, and safe operational review. It applies to workshop Lambdas such as `exasol-cred-vendor-*` and `exasol-codespaces-alert`.

## Safety Rules

- Do not print Lambda environment variable values. They can contain URLs, credentials, tokens, passphrases, or authorization material.
- For environment review, list only variable names and whether suspicious secret-like keys exist.
- Do not call `aws lambda invoke` unless the user asks to test runtime behavior and understands side effects.
- Do not update, delete, publish, or change configuration without explicit confirmation.
- Do not download deployment packages unless code review is explicitly requested; downloaded packages can contain secrets.
- Keep all reports metadata-only: function name, ARN, runtime, memory, timeout, code size, last modified, role ARN, logging config, invocation/error metrics, log bytes, and cost interpretation.

## Read-Only Inventory

List functions:

```bash
aws lambda list-functions \
  --region eu-central-1 \
  --query 'Functions[].{FunctionName:FunctionName,Runtime:Runtime,MemorySize:MemorySize,Timeout:Timeout,CodeSize:CodeSize,LastModified:LastModified,Role:Role}' \
  --output json --no-cli-pager
```

For additional state fields, use `get-function-configuration` but redact environment values:

```bash
aws lambda get-function-configuration \
  --region eu-central-1 \
  --function-name <name> \
  --query '{FunctionName:FunctionName,Runtime:Runtime,State:State,LastUpdateStatus:LastUpdateStatus,MemorySize:MemorySize,Timeout:Timeout,EphemeralStorage:EphemeralStorage,EnvironmentKeys:keys(Environment.Variables)}' \
  --output json --no-cli-pager
```

List integration surfaces:

```bash
aws lambda list-function-url-configs --region eu-central-1 --function-name <name> --output json --no-cli-pager
aws lambda list-event-source-mappings --region eu-central-1 --function-name <name> --output json --no-cli-pager
aws lambda get-policy --region eu-central-1 --function-name <name> --output json --no-cli-pager
```

## Monitoring and Cost Checks

Lambda sends invocation metrics to CloudWatch. Check month-to-date invocation and error counts:

```bash
aws cloudwatch get-metric-statistics \
  --region eu-central-1 \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=<name> \
  --start-time YYYY-MM-01T00:00:00Z \
  --end-time YYYY-MM-DDT00:00:00Z \
  --period 2592000 \
  --statistics Sum \
  --output json --no-cli-pager
```

```bash
aws cloudwatch get-metric-statistics \
  --region eu-central-1 \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions Name=FunctionName,Value=<name> \
  --start-time YYYY-MM-01T00:00:00Z \
  --end-time YYYY-MM-DDT00:00:00Z \
  --period 2592000 \
  --statistics Sum \
  --output json --no-cli-pager
```

Check log storage and retention:

```bash
aws logs describe-log-groups \
  --region eu-central-1 \
  --log-group-name-prefix /aws/lambda/<name> \
  --query 'logGroups[].{LogGroupName:logGroupName,StoredBytes:storedBytes,RetentionInDays:retentionInDays,CreationTime:creationTime}' \
  --output json --no-cli-pager
```

Cost interpretation:

- Low invocation volumes are usually covered by Lambda Free Tier/Always Free scale, but report actual Cost Explorer Lambda cost when available.
- CloudWatch logs for Lambda can incur ingestion/storage charges even when Lambda compute cost is zero.
- Custom metrics, alarms, dashboards, X-Ray, and Lambda Insights can add CloudWatch/X-Ray costs; check them when the user asks for above-free-tier exposure.

## Security Review Heuristics

Flag:

- environment variable names matching `SECRET`, `TOKEN`, `KEY`, `PASSWORD`, `PASS`, `AUTH`, `CREDENTIAL`, or `URL` when values are present, without printing values
- public function URLs or resource policies that allow broad principals
- execution roles with broad permissions outside the workshop need
- missing log retention on frequently invoked functions
- deprecated runtimes or high timeout/memory values without reason

Do not treat the presence of a Lambda function as a cost problem by itself. Cost risk comes from invocation volume, duration, memory, provisioned concurrency, log ingestion/storage, and attached observability features.

## Reporting Fields

For GitHub Markdown reports include:

- Function name
- Runtime
- Memory and timeout
- Last modified
- Role ARN, if useful
- Function URL auth type, if present
- Event source mapping count
- Month-to-date invocations/errors
- CloudWatch log group stored bytes and retention
- Cost Explorer Lambda cost for the period
- Security note: environment variable values not read

## Official AWS References

- AWS CLI Lambda command reference: https://docs.aws.amazon.com/cli/latest/reference/lambda/index.html
- AWS CLI `lambda list-functions`: https://docs.aws.amazon.com/cli/latest/reference/lambda/list-functions.html
- Lambda monitoring overview: https://docs.aws.amazon.com/lambda/latest/dg/lambda-monitoring.html
- Lambda CloudWatch metrics: https://docs.aws.amazon.com/lambda/latest/dg/monitoring-metrics.html
- Lambda logs: https://docs.aws.amazon.com/lambda/latest/dg/monitoring-logs.html
- Lambda CloudWatch Logs destination and pricing note: https://docs.aws.amazon.com/lambda/latest/dg/monitoring-cloudwatchlogs.html
- Lambda environment variables and Secrets Manager recommendation: https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars-retrieve.html
- Lambda function configuration: https://docs.aws.amazon.com/lambda/latest/dg/lambda-functions.html
