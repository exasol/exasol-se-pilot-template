---
name: prometheus-sidecar-integrator
description: Use when asked to add, audit, or debug Prometheus sidecars for this Exasol workshop, especially Prometheus, node_exporter, AWS CloudWatch exporters, scrape configs, security groups, and Grafana Prometheus datasource wiring.
---

# Prometheus Sidecar Integrator

Use this skill when adding Prometheus-based monitoring to the workshop runtime
or to an Exasol Personal AWS deployment.

## Goals

- Add Prometheus components with the smallest safe change to the workshop.
- Prefer local Compose integration first; treat AWS-side install as a
  separately approved infrastructure change.
- Capture host metrics through `node_exporter` and AWS metrics through a
  CloudWatch exporter when needed.
- Keep Grafana connected through a provisioned Prometheus datasource.
- Preserve existing PostgreSQL mirror dashboards; do not replace working panels
  unless the user explicitly asks.

## Safety Rules

- Do not SSH into, install packages on, or restart the Exasol Personal AWS
  instance without explicit approval for that action.
- Do not expose Prometheus, exporter, or node_exporter ports publicly. Bind
  local services to `127.0.0.1`; for AWS, allow only instructor/client CIDRs or
  private VPC access.
- Do not print AWS credentials, Exasol passwords, Terraform state, private keys,
  or generated deployment secrets.
- Do not destroy or resize AWS resources without confirmation.
- Prefer IAM roles or short-lived credentials for CloudWatch exporters; do not
  put long-lived AWS keys in Compose, Grafana, or Prometheus config.

## Public References

- Prometheus configuration:
  https://prometheus.io/docs/prometheus/latest/configuration/configuration/
- Prometheus exporters and integrations:
  https://prometheus.io/docs/instrumenting/exporters/
- Prometheus node_exporter guide:
  https://prometheus.io/docs/guides/node-exporter/
- node_exporter repository:
  https://github.com/prometheus/node_exporter
- Grafana Prometheus datasource:
  https://grafana.com/docs/grafana/latest/datasources/prometheus/
- Prometheus CloudWatch exporter:
  https://github.com/prometheus/cloudwatch_exporter
- Yet Another CloudWatch Exporter:
  https://github.com/prometheus-community/yet-another-cloudwatch-exporter

## Workflow

1. Inspect current runtime:
   - `git status --short`
   - `docker compose ps`
   - `rg -n "prometheus|node_exporter|cloudwatch|datasources" code/kestra`

2. Add local Prometheus service:
   - Create a tracked `prometheus.yml` under `code/kestra/prometheus/`.
   - Add a Compose service using the official Prometheus image.
   - Mount the config read-only.
   - Bind the Prometheus UI to localhost only.
   - Scrape Prometheus itself, Grafana, and any approved local exporters.

3. Add exporter services only when justified:
   - `node_exporter` for host CPU, memory, disk, and network metrics.
   - CloudWatch exporter or YACE for AWS EC2/EBS metrics by tag.
   - A custom Exasol SQL exporter only if Grafana needs time-series metrics not
     already materialized by the PostgreSQL mirror.

4. Add Grafana provisioning:
   - Datasource UID should be stable, for example `workshop_prometheus`.
   - Keep existing `workshop_analytics` PostgreSQL datasource intact.
   - Avoid secrets in datasource YAML.

5. Validate:
   - `promtool check config code/kestra/prometheus/prometheus.yml` if available.
   - `curl -fsS http://localhost:${PROMETHEUS_PORT:-9090}/-/ready`
   - `curl -fsS http://localhost:${PROMETHEUS_PORT:-9090}/api/v1/targets`
   - `curl -fsS http://localhost:${GRAFANA_PORT:-3000}/api/health`

## AWS-Side Design Guidance

For an Exasol Personal AWS instance, prefer this order:

1. Private or localhost-only node_exporter on the EC2 host.
2. Prometheus running locally in the workshop Compose stack and scraping over a
   tightly scoped security group rule.
3. CloudWatch exporter for EC2/EBS metrics when direct host scraping is not
   approved or not reachable.
4. Custom Exasol SQL exporter for database internals, using a least-privilege
   monitoring database user.

The AWS sidecar path is not a default student action. Treat it as instructor
operations unless the README explicitly makes it part of the workshop.

## Report Format

- `Trigger`: panel, target, scrape job, or AWS step being validated.
- `Root cause`: config, network, IAM, exporter, query, or datasource issue.
- `Fix`: exact file or command.
- `Validation`: command proving Prometheus and Grafana can see the metric.
- `Residual risk`: anything requiring AWS access, SSH, or live traffic.
