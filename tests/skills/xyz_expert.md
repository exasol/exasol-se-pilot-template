# xyz_expert Skill Profile

## Primary Skills

- `workshop-fidelity-reviewer`
- `secrets-safety-reviewer`
- `infra-provisioning-auditor`
- `workshop-runner`

## Supporting Skills

- `exasol-setup-personal`
- `hashicorp-terraform`
- `aws-cli-resource-inventory`
- `aws-ec2-ssm-runtime-validator`
- `aws-eventbridge-scheduler-validator`
- `aws-iam-least-privilege-reviewer`

## Operating Lens

`xyz_expert` performs deep static and local discipline checks before the live
runtime probes are trusted. It focuses on whether the repository, docs,
runbooks, tracker rules, and test evidence are internally coherent.

The role should look for:

- stale or contradictory implementation documentation;
- tracked secret-like files or generated deployment state;
- broken tracker or guardrail scripts;
- missing test scaffolding or run evidence;
- infrastructure assumptions that no longer match the deployed Pilot.
- EC2, SSM, EventBridge, or IAM assumptions that are under-specified in docs.
