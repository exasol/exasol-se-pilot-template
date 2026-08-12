---
name: hashicorp-nomad
description: Use when asked to design, audit, deploy, troubleshoot, or report HashiCorp Nomad jobs, allocations, evaluations, clients, servers, job specifications, workload scheduling, or Nomad-based workshop orchestration.
---

# HashiCorp Nomad

Use this skill for Nomad workload orchestration, job specs, job status, allocation debugging, and scheduler-related reporting.

## Safety Rules

- Default to read-only commands: `nomad status`, `nomad job status`, `nomad alloc status`, `nomad node status`.
- Do not run `nomad job run`, `nomad job stop`, `nomad system gc`, or destructive namespace operations without explicit confirmation.
- Do not print Nomad ACL tokens, Vault tokens, Consul tokens, environment secrets, or task logs that may contain credentials.
- When logs are needed, filter for error signatures and redact secret-like strings.

## Workflow

1. Confirm target cluster context:

```bash
nomad status
nomad node status
```

2. Inspect jobs and deployments:

```bash
nomad job status
nomad job status -json <job>
nomad deployment status <deployment-id>
```

3. Debug allocations:

```bash
nomad alloc status <allocation-id>
nomad alloc status -json <allocation-id>
nomad alloc logs -stderr <allocation-id> <task>
```

4. Validate jobspecs before running:

```bash
nomad job validate <job.hcl>
nomad job plan <job.hcl>
```

## Review Heuristics

- Check job type: service, batch, system, sysbatch.
- Check requested CPU/memory and whether it fits client capacity.
- Inspect constraints, affinities, restart/reschedule policy, update stanza, network ports, service registration, and lifecycle hooks.
- For workshop environments, prefer explicit namespaces and small resource requests.
- For reporting, map job status to allocations, nodes, task events, and deployment health.

## Official References

- IBM HashiCorp product page: https://www.ibm.com/products/hashicorp
- IBM HashiCorp support details: https://www.ibm.com/support/pages/node/7184931
- Nomad documentation: https://developer.hashicorp.com/nomad/docs
- What is Nomad: https://developer.hashicorp.com/nomad/docs/what-is-nomad
- Nomad job concept: https://developer.hashicorp.com/nomad/docs/concepts/job
- Nomad scheduling: https://developer.hashicorp.com/nomad/docs/concepts/scheduling/how-scheduling-works
- Nomad job status command: https://developer.hashicorp.com/nomad/commands/job/status
- Nomad allocation status command: https://developer.hashicorp.com/nomad/docs/commands/alloc/status
