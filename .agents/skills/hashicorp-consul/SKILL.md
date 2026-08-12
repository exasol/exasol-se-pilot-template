---
name: hashicorp-consul
description: Use when asked to design, audit, troubleshoot, or report HashiCorp Consul service discovery, service mesh, DNS, health checks, intentions, gateways, catalog state, or Consul-backed workshop networking.
---

# HashiCorp Consul

Use this skill for Consul service discovery, service mesh, catalog health, DNS, gateways, intentions, and network automation reviews.

## Safety Rules

- Do not print Consul ACL tokens, service credentials, certificates, private keys, or bootstrap tokens.
- Default to read-only catalog, health, and config checks.
- Do not change intentions, ACLs, config entries, gateways, or service registrations without explicit confirmation.
- Treat service metadata and tags as potentially sensitive when reports leave the local repo.

## Workflow

1. Confirm cluster and members:

```bash
consul members
consul operator raft list-peers
```

2. Inspect service catalog and health:

```bash
consul catalog services
consul catalog nodes
consul health service <service-name>
```

3. Inspect service mesh state:

```bash
consul config list -kind service-defaults
consul config list -kind service-resolver
consul config list -kind service-router
consul intention list
```

4. DNS checks:

```bash
dig @127.0.0.1 -p 8600 <service>.service.consul
```

## Review Heuristics

- For service discovery, verify service registration, health checks, DNS resolution, and datacenter/namespace/partition scope.
- For service mesh, verify sidecar proxies, intentions, mTLS expectations, upstreams, and gateway exposure.
- Flag broad allow intentions, missing health checks, stale nodes, and service names that leak environment secrets.
- For workshop reports, summarize catalog/service health counts rather than raw secrets or full service metadata.

## Official References

- IBM HashiCorp product page: https://www.ibm.com/products/hashicorp
- IBM HashiCorp support details: https://www.ibm.com/support/pages/node/7184931
- Consul documentation: https://developer.hashicorp.com/consul/docs
- What is Consul: https://developer.hashicorp.com/consul/docs/intro
- Consul service discovery: https://developer.hashicorp.com/consul/docs/concepts/service-discovery
- Consul service mesh: https://developer.hashicorp.com/consul/docs/use-case/service-mesh
