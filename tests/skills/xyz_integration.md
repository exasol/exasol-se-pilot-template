# xyz_integration Skill Profile

## Primary Skills

- `exasol-database`
- `aws-lambda-operations`
- `aws-cli-resource-inventory`
- `grafana-dashboard-validator`
- `workshop-runner`

## Supporting Skills

- `aws-ec2-ssm-runtime-validator`
- `aws-eventbridge-scheduler-validator`
- `aws-cognito-oauth-validator`
- `aws-bedrock-runtime-validator`
- `aws-ses-deliverability-validator`
- `aws-ssm-parameter-store-safety`
- `aws-s3-operator-dashboard-validator`
- `aws-cloudwatch-observability-validator`
- `exasol-prometheus-exporter`
- `prometheus-grafana-validator`
- `runtime-debugger`
- `streamlit-dashboard-builder`

## Operating Lens

`xyz_integration` validates service wiring across AWS, Exasol, Grafana,
Streamlit, MCP, Lambda, EventBridge, and Cloudflare-facing endpoints.

The role should look for:

- missing or unhealthy `<PROJECT_SLUG>-*` Lambdas;
- scheduler/rule drift for start, stop, status, and recap jobs;
- Exasol least-privilege violations in routine read profiles;
- Grafana auth or datasource boundary regressions;
- AWS inventory drift that signals unintended resource or cost exposure.
- Bedrock, Cognito, SES, SSM, S3, CloudWatch, EC2/SSM, IAM, and EventBridge
  service-boundary drift that a generic inventory would miss.

There is no dedicated local MCP server tester skill yet; MCP coverage is
composed from `workshop-runner`, `runtime-debugger`, `student`, and the
endpoint probes in this agent.
