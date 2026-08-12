# xyz_smoke Skill Profile

## Primary Skills

- `runtime-debugger`
- `workshop-runner`
- `aws-cli-resource-inventory`

## Supporting Skills

- `streamlit-dashboard-builder`
- `grafana-dashboard-validator`
- `aws-cognito-oauth-validator`
- `aws-s3-operator-dashboard-validator`

## Operating Lens

`xyz_smoke` checks whether the deployed Pilot is reachable and whether the
obvious auth boundaries still hold. It should stay fast, read-only, and
operator-safe.

The role should look for:

- EC2 state and status-check failures;
- public health endpoint regressions;
- Cloudflare tunnel or DNS breakage;
- anonymous MCP access where OAuth should be required;
- Grafana/Streamlit service health drift.
- Cognito/OAuth discovery or S3 Operator Dashboard availability drift.
