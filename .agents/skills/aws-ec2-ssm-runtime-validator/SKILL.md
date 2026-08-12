---
name: aws-ec2-ssm-runtime-validator
description: Use when validating AWS EC2 runtime state, instance status checks, Elastic IP, EBS volumes, security groups, SSM managed-instance registration, EC2 IAM instance profile, or start/stop readiness for the <PILOT_NAME> Exasol Personal host.
---

# AWS EC2 SSM Runtime Validator

Use this skill for the EC2 host that runs Exasol Personal and the Docker
Compose projects.

## Safety Rules

- Default to `describe-*` and inventory-only checks.
- Do not start, stop, reboot, terminate, resize, attach, detach, or mutate EC2
  resources unless explicitly approved.
- Do not run `aws ssm send-command` unless explicitly scoped and approved.
- Do not print SSH keys, user data, private IP-sensitive context beyond what is
  needed for operational diagnosis.

## Read-Only Workflow

1. Confirm the expected region and caller identity.
2. Check instance state and status:
   `aws ec2 describe-instances` and `aws ec2 describe-instance-status`.
3. Confirm Elastic IP association, instance profile, subnet, VPC, security
   groups, and relevant tags.
4. Inspect EBS volumes for attachment state, size, type, encryption, and delete
   behavior.
5. Validate SSM registration with
   `aws ssm describe-instance-information`; report `PingStatus`, agent version,
   and last ping time.
6. Review security groups for expected ingress only; flag broad SSH, Exasol,
   or admin UI exposure.

## Report Signals

- `PASS`: instance state matches the test window, status checks pass, SSM is
  online, EIP is attached, and SG ingress matches runbook expectations.
- `WARN`: stopped outside expected schedule, stale SSM ping, unattached EIP,
  broad but known pilot ingress, or volume cost risk.
- `FAIL`: instance missing, impaired status checks, no SSM registration, wrong
  EIP, or security group drift blocking expected service paths.
