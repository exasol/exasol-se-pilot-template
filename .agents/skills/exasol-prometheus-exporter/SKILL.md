---
name: exasol-prometheus-exporter
description: Use when asked to design, build, validate, or debug a Prometheus exporter that connects directly to Exasol Personal and exposes database runtime metrics such as sessions, recent SQL, storage, volume usage, audit workload, and network I/O.
---

# Exasol Prometheus Exporter

Use this skill when Prometheus needs direct Exasol database metrics instead of
or in addition to the existing PostgreSQL mirror used by Grafana.

## Goals

- Expose Exasol runtime metrics in Prometheus text format.
- Use read-only SQL against Exasol system views.
- Keep credentials out of source files, dashboard JSON, and logs.
- Make exported metric names stable and low-cardinality.
- Validate the exporter with Prometheus scrape and Grafana PromQL queries.

## Safety Rules

- Do not print Exasol passwords, deployment secrets, TLS private keys, or AWS
  credentials.
- Use a dedicated monitoring database user with the minimum required privileges.
- Do not use the `SYS` user for a long-running exporter unless the user
  explicitly approves that temporary local test.
- Do not expose the exporter publicly. Bind local exporters to `127.0.0.1`;
  for AWS, use a private listener or a tightly scoped security group.
- Do not enable or truncate Exasol audit logging automatically.

## Public References

- Prometheus exposition formats:
  https://prometheus.io/docs/instrumenting/exposition_formats/
- Prometheus writing exporters:
  https://prometheus.io/docs/instrumenting/writing_exporters/
- Prometheus client libraries:
  https://prometheus.io/docs/instrumenting/clientlibs/
- Exasol auditing system tables:
  https://docs.exasol.com/db/latest/database_concepts/auditing.htm

## Metric Sources

Prefer these read-only sources when available:

- `EXA_DBA_SESSIONS` for current sessions and status counts.
- `EXA_DBA_AUDIT_SQL` for recent SQL workload, CPU, memory, HDD, and NET usage.
- `EXA_DB_SIZE_LAST_DAY` for raw, compressed, and storage size snapshots.
- `EXA_VOLUME_USAGE` for persistent volume capacity and swap usage.
- `EXA_ALL_TABLES` for workshop table row counts.

## Exporter Design

- Emit numeric gauges and counters only; avoid free-form labels.
- Keep labels bounded: `category`, `metric`, `status`, `command_name`,
  `success`, and `tablespace` are acceptable.
- Avoid per-session or per-statement labels unless the user needs a debug-only
  endpoint; those can create high cardinality.
- Include a scrape health metric such as `exasol_exporter_up`.
- Redact connection errors before logging.

## Validation Workflow

1. Test a connection without printing secrets:
   - Use the workshop connection utility or an approved environment-based
     connection path.
   - Run `SELECT 1`.

2. Validate SQL sources:
   - Count sessions by status.
   - Read latest DB size and persistent volume usage.
   - Summarize recent audit SQL over a bounded time range.

3. Validate exporter output:
   - `curl -fsS http://127.0.0.1:${EXASOL_EXPORTER_PORT:-9199}/metrics`
   - Confirm `# HELP`, `# TYPE`, and numeric samples.

4. Validate Prometheus scrape:
   - Add a scrape job with a stable job name, for example `exasol_personal`.
   - Check `/api/v1/targets` until the target is `up`.

5. Validate Grafana:
   - Query Prometheus API for each PromQL expression.
   - Confirm dashboard panels use datasource UID `workshop_prometheus`.

## Report Format

- `Trigger`: exporter endpoint, SQL query, Prometheus target, or dashboard panel.
- `Root cause`: privilege, network, SQL, scrape config, or cardinality issue.
- `Fix`: exact code/config change.
- `Validation`: command proving metrics are exposed and scraped.
- `Residual risk`: live AWS, audit settings, or privilege assumptions.
