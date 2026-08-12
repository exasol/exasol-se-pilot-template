---
name: prometheus-grafana-validator
description: Use when asked to validate, debug, or fix Grafana dashboards backed by Prometheus in this workshop, especially Prometheus datasource provisioning, PromQL, target health, empty panels, and drill-down panel behavior.
---

# Prometheus Grafana Validator

Use this skill when Grafana panels use Prometheus or PromQL, or when the user
reports `No Data` in Prometheus-backed dashboard panels.

## Goals

- Verify Prometheus datasource provisioning in Grafana.
- Verify Prometheus scrape target health before debugging panel JSON.
- Validate every PromQL expression against the Prometheus HTTP API.
- Keep drill-down panels embedded in the main Grafana canvas and preserve the
  existing workshop dashboards unless a panel must be corrected.

## Safety Rules

- Do not print datasource passwords, AWS credentials, Exasol credentials, or
  Terraform state.
- Do not delete Grafana, Prometheus, or PostgreSQL volumes without explicit
  confirmation.
- Do not expose Grafana or Prometheus beyond localhost without an approved
  access-control plan.

## Public References

- Grafana Prometheus datasource:
  https://grafana.com/docs/grafana/latest/datasources/prometheus/
- Grafana provisioning:
  https://grafana.com/docs/grafana/latest/administration/provisioning/
- Prometheus query API:
  https://prometheus.io/docs/prometheus/latest/querying/api/
- Prometheus querying basics:
  https://prometheus.io/docs/prometheus/latest/querying/basics/

## Validation Workflow

1. Check services:
   - `docker compose ps grafana prometheus`
   - `curl -fsS http://localhost:${PROMETHEUS_PORT:-9090}/-/ready`
   - `curl -fsS http://localhost:${GRAFANA_PORT:-3000}/api/health`

2. Check Prometheus targets:
   - `curl -fsS http://localhost:${PROMETHEUS_PORT:-9090}/api/v1/targets`
   - Confirm each dashboard job is `up`.
   - If a target is down, fix scrape/network/exporter before changing Grafana.

3. Check datasource provisioning:
   - Inspect `code/kestra/grafana/provisioning/datasources/`.
   - Prometheus datasource should use a stable UID such as
     `workshop_prometheus`.
   - The URL should point to the Compose service name, not a localhost URL from
     Grafana's container perspective.

4. Validate PromQL:
   - Extract panel expressions from dashboard JSON.
   - Query Prometheus API with `/api/v1/query` or `/api/v1/query_range`.
   - Confirm the returned vector or matrix has samples in the dashboard time
     range.

5. Fix panel JSON:
   - Use datasource UID `workshop_prometheus`.
   - Use units that match Prometheus metric semantics.
   - Prefer drill-down rows/panels on the same dashboard canvas instead of
     linking users away from the instructor monitor.

## Common Root Causes

- Grafana datasource URL is `localhost:9090`; from inside Grafana it should be
  `prometheus:9090`.
- Prometheus target is down because exporter port is bound only to the host.
- PromQL range is too short for CloudWatch exporter delay.
- Panel queries use metric names from a different exporter version.
- Grafana was already running and needs a service restart to reload
  provisioning files.

## Report Format

- `Trigger`: dashboard panel or API query.
- `Root cause`: target health, datasource, PromQL, time range, or provisioning.
- `Fix`: exact dashboard/config change.
- `Validation`: Prometheus API and Grafana API checks.
- `Residual risk`: live AWS or Exasol dependency not reproduced locally.
