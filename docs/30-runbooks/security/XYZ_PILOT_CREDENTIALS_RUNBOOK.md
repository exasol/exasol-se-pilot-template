# Pilot Credentials Runbook

Do not commit secrets.

Use these storage patterns:

| Secret Class | Storage |
|---|---|
| AWS access | Local AWS profile, IAM Identity Center, or OIDC |
| Exasol credentials | Deployment-local secret files or SSM Parameter Store |
| GitHub tokens | GitHub Actions secrets or SSM Parameter Store |
| Cloudflare tunnel credentials | SSM Parameter Store or EC2-local protected file |
| SES/SNS/Cognito config | AWS resources and non-secret config files |

Generated projects must start with placeholder `.env.example` files only.

