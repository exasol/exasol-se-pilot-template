# Exasol Statistics Refresh

Purpose: refresh Exasol optimizer statistics after database restore, account or
region migration, large data reload, or semantic-model reorganization.

These scripts are operator maintenance tools. Tester Swarm agents such as
`XYZ_STRESS` may recommend them in issue candidates, but must not execute them
automatically because that would mutate database optimizer state and invalidate
read-only test timing evidence.

Use [`analyze_all_tables.sh`](analyze_all_tables.sh) with an `exapump` profile
that has permission to analyze user tables:

```bash
scripts/exasol_statistics/analyze_all_tables.sh --profile xyz-pilot-target-sys
```

Target one schema during focused troubleshooting:

```bash
scripts/exasol_statistics/analyze_all_tables.sh \
  --profile xyz-pilot-target-sys \
  --schema <PILOT_SCHEMA>
```

Preview the generated statements:

```bash
scripts/exasol_statistics/analyze_all_tables.sh \
  --profile xyz-pilot-target-sys \
  --schema <PILOT_SCHEMA> \
  --dry-run
```

For large fact tables where `exapump sql` reaches its client timeout, use the
`pyexasol` companion with `EXA_PASSWORD` supplied by the caller:

```bash
EXA_PASSWORD="$SYS_PASSWORD" uv run --with pyexasol python3 \
  scripts/exasol_statistics/analyze_table_pyexasol.py \
  --dsn <EXASOL_HOSTNAME>:8563 \
  --user sys \
  --schema <PILOT_SCHEMA> \
  --table <PILOT_SCHEMA>.FACT_PRIMARY
```

## Exasol Syntax

The accepted command on the migrated Exasol Personal target during XYZ-PILOT-217
triage was:

```sql
ANALYZE TABLE "<PILOT_SCHEMA>"."MV_MONTHLY_METRICS";
```

The following forms were rejected by the target parser and should not be used
in this project:

```sql
UPDATE STATISTICS <PILOT_SCHEMA>.MV_MONTHLY_METRICS;
UPDATE STATISTICS SET <PILOT_SCHEMA>.MV_MONTHLY_METRICS;
ALTER TABLE <PILOT_SCHEMA>.MV_MONTHLY_METRICS UPDATE STATISTICS;
```

Views do not own optimizer table statistics. Analyze the physical tables below
the views instead. The script discovers targets from `EXA_ALL_TABLES`, skips
system schemas, and excludes the semantic-layer virtual-schema pseudo entry
`ESL_POC_SEMANTIC_LAYER_VIRTUAL_SCHEMA` by default.

## XYZ-PILOT-217 Usage Note

During the <AWS_REGION> migration triage, `XYZ_STRESS` showed HTTP routes were
healthy while DB/view probes timed out around 120 seconds. Refreshing
statistics is one valid remediation step, alongside installing the EC2 warm-up
service and avoiding automatic exact `COUNT(*)` against live analytical views
from normal QBR page renders.

Observed 2026-06-18 during XYZ-PILOT-217 triage:

- `ANALYZE TABLE` succeeded for 64 of 65 physical user tables through
  `exapump`.
- `<PILOT_SCHEMA>.FACT_PRIMARY` (170,072,000 rows) hit the `exapump` 120-second
  client timeout.
- The same `ANALYZE TABLE "<PILOT_SCHEMA>"."FACT_PRIMARY"` completed through
  `pyexasol` in 113.0 seconds.
