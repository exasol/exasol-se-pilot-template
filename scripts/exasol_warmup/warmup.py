"""XYZ-PILOT-059 — Exasol Personal warm-up script.

Issues a representative set of the queries that the Streamlit QBR dashboard +
Grafana <PILOT_SCHEMA> dashboards run on first load, so the underlying columnar
blocks are pre-loaded into Exasol's in-memory cache before the first
subscriber hits the dashboards after an EC2 start.

Invoked from systemd unit `xyz-exasol-warmup.service` at boot, once
`exasol_launcher.target` is reached. Logs to stdout/stderr (systemd journal
captures); a short summary is also written to /var/log/xyz-warmup.log via
the wrapper script.

Connection: XYZ_DASH read-only user (same credential as the chatbot's
exasol_query tool + the services/streamlit-insights container). Creds sourced from
/opt/xyz-chatbot/mcp-internal.env via the wrapper shell script.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

import pyexasol

EXA_DSN  = os.environ.get("EXA_DSN", "127.0.0.1:8563")
EXA_USER = os.environ.get("EXA_USER", "XYZ_DASH")
EXA_PASSWORD = os.environ["EXA_PASSWORD"]   # required, no default
EXA_SCHEMA = os.environ.get("EXA_SCHEMA", "<PILOT_SCHEMA>")
# XYZ_BIZ warm-up creds — mirror the XYZ_DASH pattern (EXA_USER/EXA_PASSWORD)
# but for the external semantic layer business-layer user. The password has NO default: when
# EXA_BIZ_PASSWORD is unset/empty the BIZ leg is SKIPPED so the demo-critical
# XYZ_DASH warm-up never depends on BIZ creds being present. No schema is set on
# the BIZ connection — XYZ_BIZ has no <PILOT_SCHEMA> base access and the BIZ queries
# are fully schema-qualified (<SEMANTIC_LAYER_SCHEMA> / <PILOT_SCHEMA>.V_ESL_*).
EXA_BIZ_USER = os.environ.get("EXA_BIZ_USER", "XYZ_BIZ")
EXA_BIZ_PASSWORD = os.environ.get("EXA_BIZ_PASSWORD", "")  # no default; empty = skip
# XYZ-PILOT-291: bound every warm-up connect so a hung TCP/TLS handshake can never
# stall the unit (and thus delay the "xyz-warmup finished" marker the START
# guard waits on). 20 s is ample once _wait_for_exasol has confirmed readiness;
# in the readiness poll it just bounds each attempt before the retry sleep.
WARMUP_CONNECT_TIMEOUT_S = int(os.environ.get("XYZ_WARMUP_CONNECT_TIMEOUT_S", "20"))
# XYZ-PILOT-292: per-query timeout (Exasol QUERY_TIMEOUT) on the warm-up session.
# The cold V_* COUNTs are ~46s each, but under node contention a single one can
# queue/hang for minutes — and the between-views budget can't catch a mid-query
# stall. Bounding each query (default 90s) means a contended/hung primer aborts
# and the best-effort handler moves on, so no single query can run the unit past
# its systemd timeout. 0 = no limit.
WARMUP_QUERY_TIMEOUT_S = int(os.environ.get("XYZ_WARMUP_QUERY_TIMEOUT_S", "90"))

# XYZ-PILOT-291 Option B: warm-up progress is written to a small local JSON file
# that run_warmup.sh ships to S3, so the Operator Dashboard can poll it and
# stream warm-up state into its log in real time. Best-effort and fully
# decoupled: if XYZ_WARMUP_PROGRESS_FILE is empty the publisher is a no-op, and
# a write failure NEVER affects the warm-up (the unit's success depends only on
# the XYZ_DASH query path). The atomic os.replace keeps readers from seeing a
# half-written file.
XYZ_WARMUP_PROGRESS_FILE = os.environ.get("XYZ_WARMUP_PROGRESS_FILE", "")
_PROGRESS_STARTED_UTC = datetime.now(timezone.utc).isoformat()
_PROGRESS_T0 = time.time()


def _publish_progress(payload: dict) -> None:
    """Best-effort: write the warm-up progress JSON locally. Never raises."""
    if not XYZ_WARMUP_PROGRESS_FILE:
        return
    try:
        doc = {
            "schema_version": 1,
            "started_utc": _PROGRESS_STARTED_UTC,
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": int(time.time() - _PROGRESS_T0),
            **payload,
        }
        tmp = XYZ_WARMUP_PROGRESS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        os.replace(tmp, XYZ_WARMUP_PROGRESS_FILE)
    except Exception:  # noqa: BLE001 — progress is best-effort, must not break warm-up
        pass
# Normal <PILOT_NAME> starts prioritize ready dashboards over a lightning boot:
# include live V_* count priming by default. Set XYZ_WARMUP_DYNAMIC_VIEWS=0
# for exceptional fast-start scenarios where this diagnostic priming is not
# wanted.
WARMUP_DYNAMIC_VIEWS = os.environ.get("XYZ_WARMUP_DYNAMIC_VIEWS", "1") == "1"
WARMUP_HEAVY_ANALYTICS = os.environ.get("XYZ_WARMUP_HEAVY_ANALYTICS", "0") == "1"
# XYZ-PILOT-292: cold-start V_* priming was the dominant warm-up cost — each
# semantic V_* view ran 4 quoted/aliased COUNT variants at ~46s each cold, so
# the V_* tail (~430s+) pushed the unit past TimeoutStartSec and the warm-up
# FAILED with Result=timeout. Cold starts now prime ONE base COUNT per view; the
# quoted/aliased variants (XYZ-PILOT-217 result-cache priming for Streamlit/exapump
# diagnostics) only run when XYZ_WARMUP_VVIEW_ALL_VARIANTS=1. A cumulative budget
# caps the phase as a belt, and the V_* step is best-effort (see _run_warmup) so
# it can never fail the demo-critical unit.
WARMUP_VVIEW_ALL_VARIANTS = os.environ.get("XYZ_WARMUP_VVIEW_ALL_VARIANTS", "0") == "1"
WARMUP_VVIEW_BUDGET_S = int(os.environ.get("XYZ_WARMUP_VVIEW_BUDGET_S", "300"))

# XYZ-PILOT-285: warm-up primer labels whose failure must NOT fail the systemd
# unit. These are raw-fact-scan / heavy diagnostics that are legitimately slow
# or transiently fragile on a cold node — e.g. `header_metadata` scans the full
# 170M-row FACT_PRIMARY fact. The MV_* dashboard primers stay strict so a
# genuine regression in a subscriber-facing warm path still fails the unit (the
# warm-up is the canonical perf regression suite — CLAUDE.md rule #7).
#
# XYZ-PILOT-269: `esl_base_layer_year_sales_dash` is also best-effort. The <SEMANTIC_LAYER_VENDOR>
# ESL BASE_LAYER semantic view is UDF-backed and runs ~3 s under xyz-pilot-ro
# even warm — well above the <0.5 s warm target — and is a third-party deploy
# lane (CLAUDE.md rule #13) outside this repo's control, so a transient ESL
# fault must never regress the demo-critical XYZ_DASH warm-up unit. The light
# `esl_object_count` catalog query stays strict.
BEST_EFFORT_WARMUP_LABELS = {"header_metadata", "esl_base_layer_year_sales_dash"}


# Pairs of (label, sql). Order is intentional: filter-options first (fast +
# touches all 3 dimension columns), then the 4 dashboard tabs' main
# aggregations, then catalog queries the Data tab needs. Every column listed
# in the dashboards is touched at least once across these statements, so
# Exasol's column-block LRU cache is primed with what subscribers will hit.
WARMUP_QUERIES: list[tuple[str, str]] = [
    # --- Streamlit QBR · filter_options() (every page render) ---
    ("filter_options",
     "SELECT DISTINCT PERIOD_YEAR, XYZ_REGION_1, SEGMENT "
     "FROM <PILOT_SCHEMA>.MV_MONTHLY_METRICS"),

    # --- Streamlit QBR · header metadata ---
    ("header_metadata",
     "SELECT COUNT(*) AS N, MIN(INVOICE_DATE) AS MIN_D, MAX(INVOICE_DATE) AS MAX_D "
     "FROM <PILOT_SCHEMA>.FACT_PRIMARY"),

    # --- Streamlit QBR · Executive Sales tab ---
    ("exec_sales_main",
     "SELECT PERIOD_YEAR, PERIOD_MONTH, XYZ_REGION_1, SEGMENT, "
     "       SUM(MONTHLY_VALUE)        AS SALES, "
     "       SUM(MONTHLY_GROSS_MARGIN) AS GM, "
     "       SUM(MONTHLY_QTY)          AS QTY, "
     "       SUM(YTD_VALUE)            AS YTD "
     "FROM <PILOT_SCHEMA>.MV_MONTHLY_METRICS "
     "GROUP BY 1,2,3,4"),

    # --- Streamlit QBR · Sales vs Target tab ---
    ("sales_vs_target_main",
     "SELECT PERIOD_YEAR, PERIOD_MONTH, XYZ_REGION_1, SEGMENT, SUBCATEGORY, "
     "       SUM(ACTUAL_VALUE)   AS ACTUAL, "
     "       SUM(YTD_ACTUAL)     AS YTD_ACTUAL, "
     "       SUM(MONTHLY_TARGET) AS TGT, "
     "       SUM(YTD_TARGET)     AS YTD_TGT, "
     "       SUM(TARGET_VARIANCE) AS VARIANCE "
     "FROM <PILOT_SCHEMA>.MV_ACTUAL_VS_TARGET "
     "GROUP BY 1,2,3,4,5"),

    # --- Streamlit QBR · Year-over-Year tab ---
    ("yoy_main",
     "SELECT PERIOD_YEAR, PERIOD_MONTH, XYZ_REGION_1, SEGMENT, SUBCATEGORY, "
     "       SUM(SALES_CY) AS SALES_CY, "
     "       SUM(SALES_PY) AS SALES_PY, "
     "       SUM(QTY_CY)   AS QTY_CY, "
     "       SUM(QTY_PY)   AS QTY_PY, "
     "       AVG(SALES_YOY_PCT) AS SALES_YOY_PCT, "
     "       AVG(QTY_YOY_PCT)   AS QTY_YOY_PCT "
     "FROM <PILOT_SCHEMA>.MV_YOY_METRICS "
     "GROUP BY 1,2,3,4,5"),

    # --- Streamlit QBR · Inventory tab (XYZ-PILOT-058 with Year filter applied) ---
    ("inventory_main",
     "SELECT INVENTORY_DATE, SEGMENT, SUBCATEGORY, "
     "       SUM(QTY_ON_HAND)   AS QOH, "
     "       SUM(QTY_AVAILABLE) AS AVAIL, "
     "       SUM(INV_COST)      AS INV_COST, "
     "       SUM(QTY_SOLD)      AS QTY_SOLD, "
     "       AVG(INV_VALUE_TURNS) AS TURNS, "
     "       AVG(COVERAGE_DAYS)   AS COVERAGE "
     "FROM <PILOT_SCHEMA>.MV_STOCK_ANALYTICS "
     "GROUP BY 1,2,3"),

    # --- Streamlit QBR · Data tab catalog walk ---
    ("data_tab_tables",
     "SELECT TABLE_NAME, TABLE_ROW_COUNT "
     "FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA='<PILOT_SCHEMA>'"),
    ("data_tab_views",
     "SELECT VIEW_NAME FROM EXA_ALL_VIEWS WHERE VIEW_SCHEMA='<PILOT_SCHEMA>'"),
    ("data_tab_columns",
     "SELECT COLUMN_TABLE AS OBJECT_NAME, COUNT(*) AS N "
     "FROM EXA_ALL_COLUMNS WHERE COLUMN_SCHEMA='<PILOT_SCHEMA>' "
     "GROUP BY COLUMN_TABLE"),

    # --- Grafana · <PILOT_SCHEMA> Data Load Monitor (fact / dim row counts) ---
    ("grafana_fact_counts",
     "SELECT 'FACT_PRIMARY' AS T, COUNT(*) AS N FROM <PILOT_SCHEMA>.FACT_PRIMARY "
     "UNION ALL "
     "SELECT 'FACT_STOCK',         COUNT(*) FROM <PILOT_SCHEMA>.FACT_STOCK "
     "UNION ALL "
     "SELECT 'FACT_TARGET',      COUNT(*) FROM <PILOT_SCHEMA>.FACT_TARGET"),
    ("grafana_table_count",
     "SELECT COUNT(*) AS METRIC_VALUE "
     "FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = '<PILOT_SCHEMA>'"),
    ("grafana_total_fact_rows",
     "SELECT (SELECT COUNT(*) FROM <PILOT_SCHEMA>.FACT_PRIMARY) + "
     "       (SELECT COUNT(*) FROM <PILOT_SCHEMA>.FACT_TARGET) + "
     "       (SELECT COUNT(*) FROM <PILOT_SCHEMA>.FACT_STOCK) AS METRIC_VALUE"),
    ("grafana_date_coverage",
     "SELECT (MAX(INVOICE_DATE) - MIN(INVOICE_DATE)) AS METRIC_VALUE "
     "FROM <PILOT_SCHEMA>.FACT_PRIMARY"),
    ("grafana_per_table_counts",
     "SELECT 'FACT_PRIMARY' AS TABLE_NAME, COUNT(*) AS ROW_COUNT "
     "FROM <PILOT_SCHEMA>.FACT_PRIMARY "
     "UNION ALL SELECT 'FACT_TARGET', COUNT(*) FROM <PILOT_SCHEMA>.FACT_TARGET "
     "UNION ALL SELECT 'FACT_STOCK', COUNT(*) FROM <PILOT_SCHEMA>.FACT_STOCK "
     "UNION ALL SELECT 'DIM_ITEM', COUNT(*) FROM <PILOT_SCHEMA>.DIM_ITEM "
     "UNION ALL SELECT 'DIM_CATEGORY', COUNT(*) FROM <PILOT_SCHEMA>.DIM_CATEGORY "
     "UNION ALL SELECT 'DIM_ACCOUNT', COUNT(*) FROM <PILOT_SCHEMA>.DIM_ACCOUNT "
     "UNION ALL SELECT 'DIM_REGION', COUNT(*) FROM <PILOT_SCHEMA>.DIM_REGION "
     "UNION ALL SELECT 'DIM_DOCUMENT', COUNT(*) FROM <PILOT_SCHEMA>.DIM_DOCUMENT "
     "UNION ALL SELECT 'FACT_LEDGER', COUNT(*) FROM <PILOT_SCHEMA>.FACT_LEDGER "
     "UNION ALL SELECT 'DIM_CURRENCY', COUNT(*) FROM <PILOT_SCHEMA>.DIM_CURRENCY "
     "UNION ALL SELECT 'BETA_SEGMENT_ONE', COUNT(*) FROM <PILOT_SCHEMA>.BETA_SEGMENT_ONE "
     "UNION ALL SELECT 'BETA_SEGMENT_TWO', COUNT(*) FROM <PILOT_SCHEMA>.BETA_SEGMENT_TWO "
     "UNION ALL SELECT 'FACT_DELIVERY', COUNT(*) FROM <PILOT_SCHEMA>.FACT_DELIVERY "
     "ORDER BY ROW_COUNT DESC"),
    ("grafana_top3_brands_sales",
     "SELECT pg.SEGMENT AS SEGMENT, ROUND(SUM(i.UNIT_VALUE), 0) AS TOTAL_VALUE "
     "FROM <PILOT_SCHEMA>.FACT_PRIMARY i "
     "JOIN <PILOT_SCHEMA>.DIM_CATEGORY pg ON i.CATEGORY_KEY = pg.CATEGORY_KEY "
     "GROUP BY pg.SEGMENT ORDER BY TOTAL_VALUE DESC LIMIT 3"),
    ("grafana_top_brand_sales_by_year",
     "SELECT EXTRACT(YEAR FROM i.INVOICE_DATE) AS PERIOD_YEAR, "
     "       pg.SEGMENT AS SEGMENT, ROUND(SUM(i.UNIT_VALUE), 0) AS SALES "
     "FROM <PILOT_SCHEMA>.FACT_PRIMARY i "
     "JOIN <PILOT_SCHEMA>.DIM_CATEGORY pg ON i.CATEGORY_KEY = pg.CATEGORY_KEY "
     "GROUP BY EXTRACT(YEAR FROM i.INVOICE_DATE), pg.SEGMENT "
     "ORDER BY PERIOD_YEAR, SALES DESC"),
    ("grafana_region_performance_rank",
     "SELECT ROW_NUMBER() OVER (ORDER BY SUM(i.UNIT_VALUE) DESC) AS RANK, "
     "       r.XYZ_REGION_1 AS REGION, ROUND(SUM(i.UNIT_VALUE), 0) AS TOTAL_VALUE, "
     "       ROUND(SUM(i.GROSS_MARGIN), 0) AS TOTAL_GM, COUNT(*) AS LINE_COUNT "
     "FROM <PILOT_SCHEMA>.FACT_PRIMARY i "
     "JOIN <PILOT_SCHEMA>.DIM_REGION r ON i.REGION_KEY = r.REGION_KEY "
     "GROUP BY r.XYZ_REGION_1 ORDER BY TOTAL_VALUE DESC"),

    # --- Grafana · Exasol Personal Monitor (system-side; warms catalog) ---
    ("grafana_session_info",
     "SELECT COUNT(*) FROM EXA_ALL_SESSIONS"),
    ("grafana_active_sql_sessions",
     "SELECT COUNT(*) AS METRIC_VALUE FROM EXA_DBA_SESSIONS "
     "WHERE STATUS = 'EXECUTE SQL'"),
    ("grafana_total_sessions",
     "SELECT COUNT(*) AS METRIC_VALUE FROM EXA_DBA_SESSIONS"),
    ("grafana_sessions_by_status",
     "SELECT STATUS, COUNT(*) AS SESSION_COUNT "
     "FROM EXA_DBA_SESSIONS GROUP BY STATUS ORDER BY STATUS"),
    ("grafana_db_raw_gib",
     "SELECT ROUND(SUM(RAW_OBJECT_SIZE)/POWER(1024,3), 3) AS METRIC_VALUE "
     "FROM EXA_ALL_OBJECT_SIZES WHERE OBJECT_TYPE='TABLE'"),
    ("grafana_queries_last_24h",
     "SELECT NVL(SUM(QUERIES), 0) AS METRIC_VALUE FROM EXA_USAGE_LAST_DAY"),
    ("grafana_recent_sql_workload",
     "SELECT COMMAND_NAME, CASE WHEN SUCCESS THEN 'Y' ELSE 'N' END AS SUCCESS, "
     "       COUNT(*) AS STATEMENT_COUNT, ROUND(SUM(NVL(CPU,0)), 2) AS TOTAL_CPU, "
     "       ROUND(MAX(NVL(TEMP_DB_RAM_PEAK,0)), 2) AS TEMP_DB_RAM_PEAK, "
     "       NVL(SUM(HDD_READ), 0) AS HDD_READ, "
     "       NVL(SUM(HDD_WRITE), 0) AS HDD_WRITE, NVL(SUM(NET), 0) AS NET "
     "FROM EXA_DBA_AUDIT_SQL "
     "WHERE START_TIME >= ADD_HOURS(CURRENT_TIMESTAMP, -24) "
     "GROUP BY COMMAND_NAME, SUCCESS ORDER BY STATEMENT_COUNT DESC"),
    ("grafana_db_size",
     "SELECT MEASURE_TIME, RAW_OBJECT_SIZE, MEM_OBJECT_SIZE "
     "FROM EXA_DB_SIZE_LAST_DAY ORDER BY MEASURE_TIME DESC LIMIT 24"),

    # --- Performance demo / pilot validation queries (§8 runbook) ---
    # These are the high-value "prove the cube replacement is hot" shapes:
    # multi-dimensional GROUPING SETS, time-series windows, account
    # penetration, and latest-inventory analytics. They prime the raw fact
    # columns that demos and ad-hoc expert checks tend to hit after cold boot.
    ("perf_grouping_sets_rollup",
     "SELECT EXTRACT(YEAR FROM i.INVOICE_DATE) AS YR, "
     "       r.XYZ_REGION_1, r.XYZ_REGION_2, pg.SEGMENT, pg.SERIES_DESC, "
     "       SUM(i.UNIT_VALUE) AS TOTAL_VALUE, "
     "       SUM(i.COGS_AMOUNT) AS TOTAL_COGS, "
     "       SUM(i.GROSS_MARGIN) AS TOTAL_GM, "
     "       CASE WHEN SUM(i.UNIT_VALUE) > 0 "
     "            THEN SUM(i.GROSS_MARGIN) / SUM(i.UNIT_VALUE) END AS GM_PCT, "
     "       COUNT(DISTINCT i.ACCOUNT_KEY) AS UNIQUE_CUSTOMERS, "
     "       COUNT(*) AS TRANSACTION_COUNT "
     "FROM <PILOT_SCHEMA>.FACT_PRIMARY i "
     "JOIN <PILOT_SCHEMA>.DIM_REGION r ON i.REGION_KEY = r.REGION_KEY "
     "JOIN <PILOT_SCHEMA>.DIM_CATEGORY pg ON i.CATEGORY_KEY = pg.CATEGORY_KEY "
     "GROUP BY GROUPING SETS ("
     "  (EXTRACT(YEAR FROM i.INVOICE_DATE), r.XYZ_REGION_1, r.XYZ_REGION_2, "
     "   pg.SEGMENT, pg.SERIES_DESC), "
     "  (EXTRACT(YEAR FROM i.INVOICE_DATE), r.XYZ_REGION_1, r.XYZ_REGION_2), "
     "  (EXTRACT(YEAR FROM i.INVOICE_DATE), pg.SEGMENT, pg.SERIES_DESC), "
     "  (EXTRACT(YEAR FROM i.INVOICE_DATE)), ()"
     ") ORDER BY 1, 2, 3"),
    ("perf_rolling_12m_sales",
     "SELECT EXTRACT(YEAR FROM i.INVOICE_DATE) AS YR, "
     "       EXTRACT(MONTH FROM i.INVOICE_DATE) AS MO, pg.SEGMENT, "
     "       SUM(i.UNIT_VALUE) AS MONTHLY_VALUE, "
     "       SUM(SUM(i.UNIT_VALUE)) OVER ("
     "         PARTITION BY EXTRACT(YEAR FROM i.INVOICE_DATE), pg.SEGMENT "
     "         ORDER BY EXTRACT(MONTH FROM i.INVOICE_DATE) "
     "         ROWS UNBOUNDED PRECEDING) AS YTD_VALUE, "
     "       SUM(SUM(i.UNIT_VALUE)) OVER ("
     "         PARTITION BY pg.SEGMENT "
     "         ORDER BY EXTRACT(YEAR FROM i.INVOICE_DATE), "
     "                  EXTRACT(MONTH FROM i.INVOICE_DATE) "
     "         ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS ROLLING_12M "
     "FROM <PILOT_SCHEMA>.FACT_PRIMARY i "
     "JOIN <PILOT_SCHEMA>.DIM_CATEGORY pg ON i.CATEGORY_KEY = pg.CATEGORY_KEY "
     "GROUP BY 1, 2, 3 ORDER BY 3, 1, 2"),
    ("perf_account_penetration",
     "SELECT a.GEO_REGION, a.ACCOUNT_RECORD_TYPE, a.TIER_OVERALL, "
     "       COUNT(DISTINCT a.ACCOUNT_KEY) AS ACCOUNT_COUNT, "
     "       SUM(i.UNIT_VALUE) AS TOTAL_VALUE, "
     "       SUM(i.UNIT_VALUE) / COUNT(DISTINCT a.ACCOUNT_KEY) AS VALUE_PER_ACCOUNT, "
     "       RATIO_TO_REPORT(SUM(i.UNIT_VALUE)) OVER ("
     "         PARTITION BY a.GEO_REGION) AS REGION_SHARE "
     "FROM <PILOT_SCHEMA>.FACT_PRIMARY i "
     "JOIN <PILOT_SCHEMA>.DIM_ACCOUNT a ON i.ACCOUNT_KEY = a.ACCOUNT_KEY "
     "GROUP BY 1, 2, 3 ORDER BY 5 DESC"),
    ("perf_inventory_latest_snapshot",
     "SELECT SEGMENT, SUBCATEGORY, SUM(QTY_ON_HAND) AS CURRENT_QTY, "
     "       SUM(INV_COST) AS CURRENT_INV_VALUE, SUM(QTY_SOLD) AS QTY_SOLD, "
     "       AVG(INV_VALUE_TURNS) AS INV_TURNS, AVG(COVERAGE_DAYS) AS COVERAGE_DAYS "
     "FROM <PILOT_SCHEMA>.MV_STOCK_ANALYTICS "
     "WHERE INVENTORY_DATE = ("
     "  SELECT MAX(INVENTORY_DATE) FROM <PILOT_SCHEMA>.MV_STOCK_ANALYTICS"
     ") GROUP BY 1, 2 HAVING SUM(QTY_ON_HAND) > 0 "
     "ORDER BY INV_TURNS DESC NULLS LAST"),

    # --- Chatbot chart-rendering patterns (XYZ-PILOT-109 / XYZ-PILOT-112) ---
    # Lessons from the BI-chart latency incident: subscribers ask Claude
    # for things like "Total Sales for North America during 2024 and 2025"
    # which produces filter-by-region + filter-by-year shapes. The chatbot
    # SYSTEM_PROMPT + `_rewrite_chart_views_to_mv` now redirect these to
    # MV_* (was hitting V_YOY_METRICS at 43 s cold). Pre-warm the exact
    # filter combinations Claude commonly generates so even the first
    # post-boot chart request stays under <1 s.
    ("chatbot_chart_yoy_na_2yr",
     "SELECT PERIOD_YEAR, PERIOD_MONTH, "
     "       ROUND(SUM(SALES_CY)/1e6, 2) AS SALES_M_USD "
     "FROM <PILOT_SCHEMA>.MV_YOY_METRICS "
     "WHERE XYZ_REGION_1 = 'North America' AND PERIOD_YEAR IN (2024, 2025) "
     "GROUP BY PERIOD_YEAR, PERIOD_MONTH"),
    ("chatbot_chart_yoy_intl_2yr",
     "SELECT PERIOD_YEAR, PERIOD_MONTH, "
     "       ROUND(SUM(SALES_CY)/1e6, 2) AS SALES_M_USD "
     "FROM <PILOT_SCHEMA>.MV_YOY_METRICS "
     "WHERE XYZ_REGION_1 = 'International' AND PERIOD_YEAR IN (2024, 2025) "
     "GROUP BY PERIOD_YEAR, PERIOD_MONTH"),
    ("chatbot_chart_top10_brands_2025",
     "SELECT SEGMENT, ROUND(SUM(SALES_CY)/1e6, 2) AS SALES_M_USD "
     "FROM <PILOT_SCHEMA>.MV_YOY_METRICS "
     "WHERE PERIOD_YEAR = 2025 "
     "GROUP BY SEGMENT ORDER BY SALES_M_USD DESC LIMIT 10"),
    ("chatbot_chart_monthly_by_region_brand",
     "SELECT PERIOD_YEAR, PERIOD_MONTH, XYZ_REGION_1, SEGMENT, "
     "       ROUND(SUM(MONTHLY_VALUE)/1e6, 2) AS SALES_M_USD "
     "FROM <PILOT_SCHEMA>.MV_MONTHLY_METRICS "
     "WHERE PERIOD_YEAR >= 2024 "
     "GROUP BY PERIOD_YEAR, PERIOD_MONTH, XYZ_REGION_1, SEGMENT"),
    ("chatbot_chart_sales_vs_target",
     "SELECT PERIOD_YEAR, PERIOD_MONTH, XYZ_REGION_1, "
     "       SUM(ACTUAL_VALUE) AS ACTUAL, SUM(MONTHLY_TARGET) AS TGT "
     "FROM <PILOT_SCHEMA>.MV_ACTUAL_VS_TARGET "
     "WHERE PERIOD_YEAR = 2025 "
     "GROUP BY PERIOD_YEAR, PERIOD_MONTH, XYZ_REGION_1"),
    ("chatbot_chart_inventory_turns",
     "SELECT INVENTORY_DATE, SEGMENT, AVG(INV_VALUE_TURNS) AS TURNS, "
     "       AVG(COVERAGE_DAYS) AS COVERAGE "
     "FROM <PILOT_SCHEMA>.MV_STOCK_ANALYTICS "
     "GROUP BY INVENTORY_DATE, SEGMENT"),
    ("chatbot_chart_region_sales_2025",
     "SELECT XYZ_REGION_1, ROUND(SUM(MONTHLY_VALUE)/1e6, 2) AS SALES_M_USD "
     "FROM <PILOT_SCHEMA>.MV_MONTHLY_METRICS "
     "WHERE PERIOD_YEAR = 2025 "
     "GROUP BY XYZ_REGION_1 ORDER BY SALES_M_USD DESC"),
    ("chatbot_chart_product_line_margin_2025",
     "SELECT SEGMENT, SUBCATEGORY, "
     "       ROUND(SUM(MONTHLY_VALUE)/1e6, 2) AS SALES_M_USD, "
     "       CASE WHEN SUM(MONTHLY_VALUE) <> 0 "
     "            THEN SUM(MONTHLY_GROSS_MARGIN) / SUM(MONTHLY_VALUE) END AS GM_PCT "
     "FROM <PILOT_SCHEMA>.MV_MONTHLY_METRICS "
     "WHERE PERIOD_YEAR = 2025 "
     "GROUP BY SEGMENT, SUBCATEGORY ORDER BY SALES_M_USD DESC"),
    ("chatbot_chart_target_variance_by_brand",
     "SELECT SEGMENT, ROUND(SUM(ACTUAL_VALUE)/1e6, 2) AS ACTUAL_M_USD, "
     "       ROUND(SUM(MONTHLY_TARGET)/1e6, 2) AS TARGET_M_USD, "
     "       ROUND(SUM(TARGET_VARIANCE)/1e6, 2) AS VARIANCE_M_USD "
     "FROM <PILOT_SCHEMA>.MV_ACTUAL_VS_TARGET "
     "WHERE PERIOD_YEAR = 2025 "
     "GROUP BY SEGMENT ORDER BY VARIANCE_M_USD DESC"),

    # --- XYZ_DASH ESL visibility (XYZ-PILOT-269) ---
    # XYZ_DASH now sees the external semantic layer deployment lane (CLAUDE.md rule #13):
    # ~57 ESL_* objects across the ESL_* schemas, and it can execute the
    # BASE_LAYER business semantic view. Two light visibility primers:
    #  - esl_object_count: a SYS-catalog COUNT over ESL_* roots. Light + fast
    #    (~1 s), stays STRICT — a regression here means XYZ_DASH lost ESL
    #    catalog visibility, which is genuinely worth failing the unit.
    #  - esl_base_layer_year_sales_dash: the validated BIZ-shape BASE_LAYER
    #    query, proving XYZ_DASH can execute the UDF-backed ESL semantic view.
    #    It is ~3 s even warm (UDF-backed third-party lane), so its label is in
    #    BEST_EFFORT_WARMUP_LABELS above — logged-but-never-fails the unit.
    ("esl_object_count",
     "SELECT COUNT(*) AS N FROM SYS.EXA_ALL_OBJECTS "
     "WHERE ROOT_NAME LIKE 'ESL%'"),
    ("esl_base_layer_year_sales_dash",
     'SELECT "Calendar: Year", "Sales Price (USD)", "Gross Margin (USD)" '
     "FROM <SEMANTIC_LAYER_SCHEMA>.BASE_LAYER ORDER BY 1"),
]


# XYZ_BIZ warm-up pack — scoped to the external semantic layer business layer. These run
# under the `xyz-pilot-biz` exapump profile (DB user XYZ_BIZ), which sees only
# BASE_LAYER (<SEMANTIC_LAYER_SCHEMA>) + the helper view
# <PILOT_SCHEMA>.V_BUSINESS_METRICS — it has NO <PILOT_SCHEMA> base/fact access.
# BASE_LAYER is an <SEMANTIC_LAYER_VENDOR> semantic view backed by a UDF: a bare `SELECT *` is
# rejected, so the query MUST name dimension attributes + measures (the shape
# below is the validated one). Keep the double-quoted column identifiers exact.
WARMUP_QUERIES_XYZ_BIZ: list[tuple[str, str]] = [
    # --- ESL business layer · BASE_LAYER semantic view (UDF-backed) ---
    ("biz_esl_base_layer_year_sales",
     'SELECT "Calendar: Year", "Sales Price (USD)", "Gross Margin (USD)" '
     "FROM <SEMANTIC_LAYER_SCHEMA>.BASE_LAYER ORDER BY 1"),
    ("biz_esl_base_layer_minimal_year",
     'SELECT "Calendar: Year" '
     "FROM <SEMANTIC_LAYER_SCHEMA>.BASE_LAYER_MINIMAL ORDER BY 1"),

    # --- ESL business layer · V_BUSINESS_METRICS helper view ---
    ("biz_esl_business_metrics_brand",
     "SELECT SEGMENT, SUM(MONTHLY_VALUE_USD) AS SALES, "
     "       SUM(MONTHLY_GROSS_MARGIN_USD) AS GM "
     "FROM <PILOT_SCHEMA>.V_BUSINESS_METRICS "
     "GROUP BY SEGMENT ORDER BY SALES DESC"),
]


# XYZ-PILOT-285: a light MV query that proves the node can serve an *analytical*
# read, not merely accept a connection. MV_MONTHLY_METRICS is small (480 rows)
# but columnar — answering it confirms the node has paged at least one
# <PILOT_SCHEMA> segment off EBS after a cold boot.
READY_MV_PROBE = "SELECT COUNT(*) FROM <PILOT_SCHEMA>.MV_MONTHLY_METRICS"


def _wait_for_exasol(max_attempts: int = 60, sleep_s: float = 5.0) -> None:
    """Poll Exasol until it serves a trivial SELECT *and* a light MV query.

    Exasol Personal accepts connections (SELECT 1) within ~60–120 s of EC2
    boot, but a cold node still pages columnar segments off EBS on first
    touch — so SELECT 1 succeeding does NOT mean the node can yet serve an
    analytical query. XYZ-PILOT-285: the bare SELECT 1 readiness gate let the
    heavy warm-up queries fire before the node could serve them, failing the
    whole unit on the 2026-06-23 cold start. We now also require a light MV
    COUNT to serve before declaring ready, so the heavier primers only run
    against a node that has proven it can answer analytics. If it never comes
    up the warmup exits non-zero — the systemd journal carries the trail.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            conn = pyexasol.connect(
                dsn=EXA_DSN, user=EXA_USER, password=EXA_PASSWORD,
                encryption=True,
                websocket_sslopt={"cert_reqs": 0},
                autocommit=True,
                connection_timeout=WARMUP_CONNECT_TIMEOUT_S,
            )
            conn.execute("SELECT 1").fetchone()
            conn.execute(READY_MV_PROBE).fetchone()
            conn.close()
            print(f"[warmup] Exasol serving analytics after {attempt} attempt(s)", flush=True)
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[warmup] attempt {attempt}/{max_attempts}: not ready yet "
                  f"({type(exc).__name__})", flush=True)
            _publish_progress({"state": "waiting_db", "done": 0,
                               "message": f"waiting for Exasol (attempt {attempt}/{max_attempts})"})
            time.sleep(sleep_s)
    print(f"[warmup] FAILED: Exasol never served analytics after "
          f"{max_attempts * sleep_s:.0f} s", file=sys.stderr, flush=True)
    sys.exit(2)


def _qual(name: str) -> str:
    """Quote reserved object names for optional live-view diagnostics."""
    return f'<PILOT_SCHEMA>."{name}"' if name.upper() in {"DATE", "USER"} else f"<PILOT_SCHEMA>.{name}"


def _quoted_qual(name: str) -> str:
    """Fully quote schema + view names for external diagnostic clients."""
    safe_name = name.replace('"', '""')
    return f'"<PILOT_SCHEMA>"."{safe_name}"'


def _view_count_sql_variants(name: str) -> list[tuple[str, str]]:
    """Return COUNT(*) SQL shapes that must be result-cache primed.

    Exasol result-cache matching is text-sensitive enough that external
    diagnostics using quoted identifiers or aliased COUNT output can miss the
    normal unquoted warm-up query. XYZ-PILOT-217 keeps the expensive semantic
    V_* view shapes warm together so local exapump, Streamlit's explicit COUNT
    action, and Swarm schema probes do not diverge after a start cycle.
    """
    base_qual = _qual(name)
    variants = [(f"view_count_{name}", f"SELECT COUNT(*) FROM {base_qual}")]
    if not name.upper().startswith("V_"):
        return variants
    # XYZ-PILOT-292: cold starts prime the base COUNT only; the quoted/aliased
    # variants (each a full ~46s cold scan on the expensive semantic views) run
    # only when XYZ_WARMUP_VVIEW_ALL_VARIANTS=1, so the cold V_* tail no longer
    # times out the unit. The variants stay available for warm/diagnostic runs.
    if not WARMUP_VVIEW_ALL_VARIANTS:
        return variants

    quoted_qual = _quoted_qual(name)
    variants.extend([
        (f"view_count_alias_{name}", f"SELECT COUNT(*) AS ROW_COUNT FROM {base_qual}"),
        (f"view_count_quoted_{name}", f"SELECT COUNT(*) FROM {quoted_qual}"),
        (
            f"view_count_quoted_alias_{name}",
            f'SELECT COUNT(*) AS "ROW_COUNT" FROM {quoted_qual}',
        ),
    ])
    return variants


def _warmup_v_views(conn) -> tuple[int, int, int, list[tuple[str, str]]]:
    """Live-view COUNT(*) warmup for schema-inventory diagnostics.

    XYZ-PILOT-217 moved automatic Streamlit inventory rendering away from exact
    live-view counts, but normal starts still prime these paths so operator
    diagnostics and schema inventory do not pay the cold-cache penalty. Set
    `XYZ_WARMUP_DYNAMIC_VIEWS=0` for exceptional fast-start scenarios.
    """
    inv_sql = (
        "SELECT VIEW_NAME FROM EXA_ALL_VIEWS "
        "WHERE VIEW_SCHEMA='<PILOT_SCHEMA>' ORDER BY VIEW_NAME"
    )
    view_names = [r[0] for r in conn.execute(inv_sql).fetchall()]
    print(f"[warmup] V_views: priming {len(view_names)} views in <PILOT_SCHEMA>", flush=True)

    queries_run = 0
    total_rows = 0
    total_ms = 0
    failures: list[tuple[str, str]] = []

    # Step (a): individual COUNT(*) per view. The base count runs for every
    # view; expensive semantic V_* views also prime quoted/aliased SQL texts
    # observed from Streamlit/exapump diagnostics.
    for _vi, name in enumerate(view_names, 1):
        for label, sql in _view_count_sql_variants(name):
            t0 = time.time()
            try:
                rows = conn.execute(sql).fetchall()
                n = int(rows[0][0]) if rows else 0
                ms = int((time.time() - t0) * 1000)
                queries_run += 1
                total_rows += 1
                total_ms += ms
                print(f"[warmup]   ✓ {label:<35s} {n:>7d} rows · {ms:>5d} ms",
                      flush=True)
            except Exception as exc:  # noqa: BLE001
                ms = int((time.time() - t0) * 1000)
                total_ms += ms
                failures.append((label, f"{type(exc).__name__}: {exc}"))
                print(f"[warmup]   ✗ {label:<35s} FAIL · {ms} ms · "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        # XYZ-PILOT-291 Option B: publish per-view progress so the dashboard log
        # shows live movement through the slow cold V_* phase (~43s each cold),
        # instead of freezing on the last static-query count until done.
        _publish_progress({"state": "vviews", "done": _vi, "total": len(view_names),
                           "current": name,
                           "message": f"dynamic V_* views {_vi}/{len(view_names)} · {name}"})
        # XYZ-PILOT-292: belt — if the cumulative V_* phase exceeds its budget,
        # stop priming the remaining views so a slow cold node can't run the
        # unit past TimeoutStartSec. Best-effort: the MV_* paths are already warm.
        if total_ms > WARMUP_VVIEW_BUDGET_S * 1000:
            print(f"[warmup]   ~ V_* budget {WARMUP_VVIEW_BUDGET_S}s exceeded "
                  f"({total_ms} ms) — skipping remaining {len(view_names) - _vi} "
                  f"view(s) + union (best-effort)", flush=True)
            return queries_run, total_rows, total_ms, failures

    # Step (b): replicate Streamlit's exact UNION ALL — result cache prime
    if view_names:
        union_all_sql = " UNION ALL ".join(
            f"SELECT '{name}' AS OBJECT_NAME, COUNT(*) AS RC FROM {_qual(name)}"
            for name in view_names
        )
        t0 = time.time()
        try:
            rows = conn.execute(union_all_sql).fetchall()
            ms = int((time.time() - t0) * 1000)
            queries_run += 1
            total_rows += len(rows)
            total_ms += ms
            print(f"[warmup]   ✓ view_count_union_all      {len(rows):>7d} rows · "
                  f"{ms:>5d} ms (result cache primed for Streamlit Data tab)",
                  flush=True)
        except Exception as exc:  # noqa: BLE001
            ms = int((time.time() - t0) * 1000)
            total_ms += ms
            failures.append(("view_count_union_all", f"{type(exc).__name__}: {exc}"))
            print(f"[warmup]   ✗ view_count_union_all      FAIL · {ms} ms · "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    return queries_run, total_rows, total_ms, failures


def _warmup_xyz_biz() -> None:
    """Best-effort XYZ_BIZ (external semantic layer business-layer) warm-up leg.

    Runs AFTER the entire XYZ_DASH flow on a SEPARATE pyexasol connection as
    XYZ_BIZ. It is wrapped by its caller in a single outer try/except so NOTHING
    here can raise into the XYZ_DASH path, add to the XYZ_DASH `failures` list,
    or affect the unit's exit code — that depends ONLY on the XYZ_DASH flow.

    Cred sourcing mirrors XYZ_DASH (EXA_BIZ_USER / EXA_BIZ_PASSWORD env). If
    EXA_BIZ_PASSWORD is unset/empty the leg is skipped with a clear log line.
    No `schema=` is set: XYZ_BIZ has no <PILOT_SCHEMA> base access and the BIZ
    queries are fully schema-qualified.
    """
    if not EXA_BIZ_PASSWORD:
        print("[warmup] BIZ warm-up skipped: EXA_BIZ_PASSWORD not set", flush=True)
        return

    print(f"[warmup] BIZ leg: priming {len(WARMUP_QUERIES_XYZ_BIZ)} XYZ_BIZ "
          f"queries as {EXA_BIZ_USER}", flush=True)
    biz_conn = pyexasol.connect(
        dsn=EXA_DSN, user=EXA_BIZ_USER, password=EXA_BIZ_PASSWORD,
        encryption=True,
        websocket_sslopt={"cert_reqs": 0},
        autocommit=True,
        connection_timeout=WARMUP_CONNECT_TIMEOUT_S,
    )
    try:
        for _bi, (label, sql) in enumerate(WARMUP_QUERIES_XYZ_BIZ, 1):
            t0 = time.time()
            try:
                rows = biz_conn.execute(sql).fetchall()
                ms = int((time.time() - t0) * 1000)
                print(f"[warmup]   ✓ {label:<32s} {len(rows):>7d} rows · {ms:>5d} ms",
                      flush=True)
                _publish_progress({"state": "biz", "done": _bi,
                                   "total": len(WARMUP_QUERIES_XYZ_BIZ), "current": label,
                                   "message": f"XYZ_BIZ {_bi}/{len(WARMUP_QUERIES_XYZ_BIZ)} · {label}"})
            except Exception as exc:  # noqa: BLE001 — best-effort, never fails the unit
                ms = int((time.time() - t0) * 1000)
                print(f"[warmup]   ~ {label:<32s} best-effort miss · {ms} ms · "
                      f"{type(exc).__name__}: {exc}", flush=True)
    finally:
        biz_conn.close()


def _run_warmup() -> None:
    dynamic_label = "enabled" if WARMUP_DYNAMIC_VIEWS else "disabled"
    heavy_label = "enabled" if WARMUP_HEAVY_ANALYTICS else "disabled"
    warmup_queries = [
        (label, sql)
        for label, sql in WARMUP_QUERIES
        if WARMUP_HEAVY_ANALYTICS or not label.startswith("perf_")
    ]
    print(f"[warmup] starting at {datetime.now(timezone.utc).isoformat()} "
          f"({len(warmup_queries)} static queries; heavy analytics {heavy_label}; "
          f"dynamic V_view step {dynamic_label})", flush=True)
    _publish_progress({"state": "starting", "done": 0, "total": len(warmup_queries),
                       "message": f"XYZ_DASH warm-up starting ({len(warmup_queries)} queries)"})
    conn = pyexasol.connect(
        dsn=EXA_DSN, user=EXA_USER, password=EXA_PASSWORD,
        schema=EXA_SCHEMA,
        encryption=True,
        websocket_sslopt={"cert_reqs": 0},
        autocommit=True,
        fetch_size_bytes=4_000_000,
        connection_timeout=WARMUP_CONNECT_TIMEOUT_S,
        query_timeout=WARMUP_QUERY_TIMEOUT_S,
    )
    total_ms = 0
    total_rows = 0
    failures: list[tuple[str, str]] = []
    for _qi, (label, sql) in enumerate(warmup_queries, 1):
        best_effort = label in BEST_EFFORT_WARMUP_LABELS
        ok = False
        last_exc: Exception | None = None
        # XYZ-PILOT-285: one retry after a short pause absorbs the transient
        # connection / segment-load blips a cold node throws right after boot,
        # without masking a persistently broken query (a real regression fails
        # both attempts → counted below).
        for attempt in (1, 2):
            t0 = time.time()
            try:
                rows = conn.execute(sql).fetchall()
                ms = int((time.time() - t0) * 1000)
                total_ms += ms
                total_rows += len(rows)
                suffix = " (after 1 retry)" if attempt == 2 else ""
                print(f"[warmup]   ✓ {label:<24s} {len(rows):>7d} rows · {ms:>5d} ms{suffix}",
                      flush=True)
                ok = True
                break
            except Exception as exc:  # noqa: BLE001
                total_ms += int((time.time() - t0) * 1000)
                last_exc = exc
                if attempt == 1:
                    time.sleep(3)
        _publish_progress({"state": "running", "done": _qi,
                           "total": len(warmup_queries), "current": label,
                           "message": f"XYZ_DASH {_qi}/{len(warmup_queries)} · {label}"})
        if ok:
            continue
        if best_effort:
            # logged, not counted — cold-tolerant raw-fact-scan / heavy diagnostic
            print(f"[warmup]   ~ {label:<24s} best-effort miss · "
                  f"{type(last_exc).__name__}: {last_exc}", flush=True)
        else:
            failures.append((label, f"{type(last_exc).__name__}: {last_exc}"))
            print(f"[warmup]   ✗ {label:<24s} FAIL · {type(last_exc).__name__}: {last_exc}",
                  file=sys.stderr, flush=True)

    total_queries = len(warmup_queries) - len(failures)
    skipped = len(WARMUP_QUERIES) - len(warmup_queries)
    if skipped:
        print(f"[warmup]   - heavy analytics skipped ({skipped} perf_* queries; "
              "set XYZ_WARMUP_HEAVY_ANALYTICS=1 to enable)",
              flush=True)
    if WARMUP_DYNAMIC_VIEWS:
        # XYZ-PILOT-292: the dynamic V_* counts are diagnostic priming, not a
        # subscriber-facing warm path (the MV_* primers above are). They are now
        # BEST-EFFORT — their failures (or a budget-truncated phase) are logged
        # but never added to the unit-failing `failures`, so a slow/cold V_*
        # phase can no longer fail the demo-critical warm-up unit.
        try:
            v_q, v_r, v_ms, v_failures = _warmup_v_views(conn)
            total_rows += v_r
            total_ms += v_ms
            total_queries += v_q
            if v_failures:
                print(f"[warmup]   ~ {len(v_failures)} V_* best-effort miss(es) "
                      "(logged, not failing the unit)", flush=True)
        except Exception as exc:  # noqa: BLE001 — V_* is best-effort
            print(f"[warmup]   ~ v_view_warmup best-effort error (ignored): "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    else:
        print("[warmup]   - dynamic V_view counts skipped "
              "(XYZ_WARMUP_DYNAMIC_VIEWS=0 fast-start mode)",
              flush=True)

    conn.close()

    # XYZ_BIZ leg — runs AFTER the entire XYZ_DASH flow above. Fully isolated:
    # a single outer try/except guarantees nothing in the BIZ path can raise
    # into the XYZ_DASH path, mutate `failures`, or change the unit's exit code
    # (which depends ONLY on the XYZ_DASH `failures` list checked below).
    try:
        _warmup_xyz_biz()
    except Exception as exc:  # noqa: BLE001 — best-effort, never fails the unit
        print(f"[warmup]   ~ BIZ leg error (ignored, XYZ_DASH unaffected) · "
              f"{type(exc).__name__}: {exc}", flush=True)

    print(f"[warmup] done at {datetime.now(timezone.utc).isoformat()} · "
          f"{total_queries} queries · "
          f"{total_rows:,} rows · {total_ms} ms wall", flush=True)
    _publish_progress({"state": "failed" if failures else "done", "finished": True,
                       "ok": not failures, "done": total_queries, "total": total_queries,
                       "message": (f"done · {total_queries} queries · {total_ms} ms"
                                   + (f" · {len(failures)} FAILED" if failures else " · OK"))})
    if failures:
        print(f"[warmup] FAILURES ({len(failures)}):", file=sys.stderr, flush=True)
        for label, err in failures:
            print(f"  - {label}: {err}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    _wait_for_exasol()
    _run_warmup()
