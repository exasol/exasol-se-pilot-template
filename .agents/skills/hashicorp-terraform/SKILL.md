---
name: hashicorp-terraform
description: Use when asked to design, audit, validate, plan, apply, destroy, import, or troubleshoot Terraform/OpenTofu infrastructure-as-code for AWS, IBM Cloud, HCP Terraform, or local workshop deployments; emphasizes plan-first workflows, state safety, drift detection, and cost/security guardrails.
---

# HashiCorp Terraform

Use this skill for Terraform or OpenTofu infrastructure lifecycle work, including `deployment/infrastructure`, AWS workshop provisioning, HCP Terraform, Terraform state review, drift detection, cleanup planning, and GitHub-ready IaC changes.

## Safety Rules

- Never print or commit state files, `*.tfstate*`, private keys, provider credentials, `.terraform/`, `.terraform.lock.hcl` churn unless intentionally changed, or generated `deployment/` secrets.
- Prefer `terraform plan`/`tofu plan` before any apply or destroy.
- Do not run `apply`, `destroy`, `state rm`, `import`, or `force-unlock` without explicit user confirmation and exact working directory.
- For cleanup, use saved plans when possible: `plan -destroy -out=tfplan-destroy` followed by reviewed `apply tfplan-destroy`.
- Treat `-target` as an exceptional recovery tool; note that it can create partial-state risk.

## Workflow

1. Identify the binary and working directory:

```bash
terraform version
tofu version
```

2. Inspect repo context without reading secrets:

```bash
rg --files -g '*.tf' -g '*.tfvars' -g '!*.tfstate*'
terraform -chdir=<dir> fmt -check
terraform -chdir=<dir> validate
```

3. Initialize only when needed and safe:

```bash
terraform -chdir=<dir> init
```

4. Plan before changing anything:

```bash
terraform -chdir=<dir> plan -out=tfplan
terraform -chdir=<dir> show tfplan
```

5. For drift-only inspection:

```bash
terraform -chdir=<dir> plan -refresh-only
```

6. For destroy:

```bash
terraform -chdir=<dir> plan -destroy -out=tfplan-destroy
terraform -chdir=<dir> show tfplan-destroy
```

Then ask for explicit confirmation before applying.

## Review Heuristics

- Verify provider default tags include owner, deployment, environment, cost center, and expiry where applicable.
- Prefer immutable, unique deployment IDs for workshop resources.
- Confirm S3 buckets, EBS volumes, and archive resources have clear cleanup behavior.
- Flag public ingress, broad IAM, unencrypted storage, missing retention, and resources that exceed the workshop cost target.
- Compare live AWS inventory with Terraform state only after confirming the state belongs to the target deployment.

## Official References

- IBM HashiCorp product page: https://www.ibm.com/products/hashicorp
- IBM Terraform overview: https://www.ibm.com/think/topics/terraform
- Terraform CLI documentation: https://developer.hashicorp.com/terraform/cli
- Terraform workflow: https://developer.hashicorp.com/terraform/cli/run
- Terraform plan tutorial: https://developer.hashicorp.com/terraform/tutorials/cli/plan
- Terraform apply command: https://developer.hashicorp.com/terraform/cli/commands/apply
- Terraform destroy command: https://developer.hashicorp.com/terraform/cli/commands/destroy
