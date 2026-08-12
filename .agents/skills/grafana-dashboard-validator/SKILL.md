---
name: grafana-dashboard-validator
description: Use when asked to validate, debug, or fix Grafana dashboards in this Exasol workshop repository, especially datasource provisioning, dashboard JSON, panel SQL, PostgreSQL mirror tables, and Grafana API query results.
---

# Grafana Dashboard Validator

Use this skill to validate the workshop Grafana integration end to end without
printing Exasol, AWS, or PostgreSQL secrets.

## Goals

- Verify Grafana starts and loads provisioned datasources and dashboards.
- Validate the PostgreSQL datasource points to the local Kestra PostgreSQL mirror.
- Validate every panel SQL query against the actual PostgreSQL container.
- Diagnose `No Data`, datasource errors, dashboard provisioning drift, and panel JSON issues.
- Prefer reproducible checks and small fixes in provisioning, dashboard JSON, or sync scripts.

## Safety Rules

- Do not print database passwords, Exasol secrets, AWS credentials, Terraform state, or full secret-bearing files.
- Treat `deployment/`, `secrets.json`, `.env`, and generated credential files as sensitive.
- Do not delete Grafana/PostgreSQL volumes or reset dashboards without explicit confirmation.
- If a command uses Docker, inspect before mutating. Preserve unrelated local containers and volumes.
- Prefer local PostgreSQL mirror checks before querying Exasol directly.

## Validation Workflow

1. Inspect repo and service state:
   - `git status --short`
   - `docker compose ps`
   - `docker compose logs --tail=200 grafana`

2. Verify provisioning files:
   - Datasource file: `code/kestra/grafana/provisioning/datasources/exasol.yml`
   - Dashboard provider: `code/kestra/grafana/provisioning/dashboards/default.yml`
   - Dashboard JSON files under `code/kestra/grafana/dashboards/`
   - Confirm dashboards use datasource UID `workshop_analytics`.

3. Verify the PostgreSQL mirror:
   - Check schema exists:
     `SELECT table_name FROM information_schema.tables WHERE table_schema = 'grafana_workshop' ORDER BY table_name;`
   - Check row counts for all mirror tables.
   - If empty, run the sync script from `code/kestra`:
     `uv run python grafana/sync_exasol_to_postgres.py`

4. Validate panel SQL:
   - Extract each `targets[].rawSql` from dashboard JSON with `jq`.
   - Run each query with `psql` in the `kestra_postgres` service.
   - For time-series panels, confirm a column named `time` exists and values are timestamp-compatible.
   - For stat and table panels, confirm at least one row is returned.

5. Validate Grafana API behavior:
   - Check HTTP: `curl -I http://localhost:${GRAFANA_PORT:-3000}`
   - Check datasource list through the API when local admin credentials are known.
   - Use Grafana logs to distinguish provisioning failures from query failures.

## Common Root Causes

- Grafana shows `No Data` because `grafana_workshop` tables are empty.
- The sync populated a different Compose project or PostgreSQL container.
- Dashboard JSON references a stale datasource UID.
- A panel SQL query returns rows in `psql` but not Grafana because the panel expects time-series format.
- Grafana was already running with stale provisioned files and needs a service restart.

## Report Format

For every issue, report:

- `Trigger`: dashboard, panel, command, or service state that exposes it.
- `Root cause`: datasource, provisioning, SQL, sync, service, or Compose mismatch.
- `Fix`: exact file or command.
- `Validation`: command proving the panel/datasource now returns data.
- `Residual risk`: anything not validated, especially live Exasol connectivity.
