# Exasol View Refresh

Purpose: repair or re-baseline native `<PILOT_SCHEMA>` view definitions after an
Exasol Personal restore, region/account migration, or controlled semantic-model
maintenance.

Use this only for the repository-owned `<PILOT_SCHEMA>` views:

- `FACT_PRIMARY_SOURCE`
- `FACT_TARGET_SOURCE`
- `FACT_STOCK_SOURCE`
- `FACT_PRIMARY_MEASURES`
- `V_MONTHLY_METRICS`
- `V_YOY_METRICS`
- `V_ACTUAL_VS_TARGET`
- `V_STOCK_ANALYTICS`

The ESL helper metric surface has its own script:

- `MV_BUSINESS_METRICS`
- `V_BUSINESS_METRICS`

Run:

```bash
scripts/exasol_views/run_sales_cube_view_refresh.sh --profile xyz-pilot-target-sys
```

Preview:

```bash
scripts/exasol_views/run_sales_cube_view_refresh.sh \
  --profile xyz-pilot-target-sys \
  --dry-run
```

Create or refresh the ESL helper-measure snapshot and view:

```bash
scripts/exasol_views/run_esl_business_metrics_view.sh --profile xyz-pilot-target-sys
```

Preview:

```bash
scripts/exasol_views/run_esl_business_metrics_view.sh \
  --profile xyz-pilot-target-sys \
  --dry-run
```

Create or refresh the ESL minimal load-test view:

```bash
scripts/exasol_views/run_esl_base_layer_minimal_view.sh --profile xyz-pilot-target-sys
```

## Safety Boundary

The refresh SQL uses `CREATE OR REPLACE VIEW`; it does not drop physical
tables, does not rebuild `MV_*` snapshot tables, and does not modify optimizer
statistics.

This is safer than an ad hoc `DROP VIEW` sequence because dependent objects
keep their database identity whenever the replacement compiles. If a future
repair truly requires dropping broken view objects first, capture the current
view inventory and DDL, then drop only the exact `<PILOT_SCHEMA>` views in reverse
dependency order.

## ESL Boundary

Do not use `run_sales_cube_view_refresh.sh` for the <SEMANTIC_LAYER_VENDOR> Exasol Semantic
Layer.

The `ESL_*` estate is not just a set of simple views. It includes:

- `ESL_POC_SEMANTIC_LAYER_HOME` config tables, generated views, and scripts
- `ESL_POC_PHYSICAL_MODEL`, `ESL_POC_RELATIONAL_MODEL`, and
  `ESL_POC_LOGICAL_MODEL`
- `<SEMANTIC_LAYER_SCHEMA>`
- `ESL_SCRIPTS` and `ESL_MODEL` UDF/script libraries
- two virtual schemas:
  `ESL_POC_SEMANTIC_LAYER_VIRTUAL_SCHEMA` and
  `ESL_POC_SEMANTIC_LAYER_VIRTUAL_SCHEMA_TST`
- roles, grants, config seeds, and generated state

Generated projects may add a project-local `external_semantic_layer/` tree,
plus a matching deployment runbook, when they need to version a partner-owned
semantic layer.

For ESL after an AWS/account migration, verify the carried/restored footprint
instead of re-running this `<PILOT_SCHEMA>` script:

- expected schemas: `ESL_POC_*`, `ESL_SCRIPTS`, `ESL_MODEL`
- expected virtual schemas: 2
- latest documented extract: 9 schemas / 221 objects

2026-06-18 target sanity check: the migrated <AWS_REGION> database had all 8 native
`<PILOT_SCHEMA>` views, but only 6 ESL schemas and 1 ESL virtual schema visible in
the `SYS` catalog checks. Treat that as an ESL migration-verification finding,
not as a reason to run this native view refresh script.

## Relationship to XYZ-PILOT-217

XYZ-PILOT-217 is primarily about cold/live-view latency and stress-probe
classification. Recreating the native views is a repair option if the target
catalog shows missing or stale `<PILOT_SCHEMA>` view definitions after restore.

It is not expected to fix the intrinsic cost of exact live-view counts against
`V_MONTHLY_METRICS`, `V_ACTUAL_VS_TARGET`, or `V_YOY_METRICS`. Normal QBR,
Grafana, chatbot, and MCP query paths should prefer the `MV_*` snapshot tables
for user-facing interactions.

## Relationship to XYZ-PILOT-234

`MV_BUSINESS_METRICS` / `V_BUSINESS_METRICS` are a separate XYZ-owned
bridge on top of `<SEMANTIC_LAYER_SCHEMA>.BASE_LAYER`. They do not change <SEMANTIC_LAYER_VENDOR>
generated ESL objects. The script materializes the monthly metric rows in
`<PILOT_SCHEMA>.MV_BUSINESS_METRICS`, then exposes
`<PILOT_SCHEMA>.V_BUSINESS_METRICS` as the stable read contract for BI clients,
QBR, DBVisualizer, and MCP consumers. The snapshot avoids making every
Streamlit rerun or MCP prompt re-evaluate the full ESL semantic-layer query.

The helper computes:

- gross-margin %
- YTD sales and quantity
- rolling 3M and 6M sales
- sales YoY delta and %
- sales share of brand and region

It intentionally does not add target/budget metrics because current ESL
`BASE_LAYER` does not contain the `FACT_TARGET` fact data. Those dashboard
pages continue to use `<PILOT_SCHEMA>.MV_ACTUAL_VS_TARGET` until target data is
modeled in ESL.
