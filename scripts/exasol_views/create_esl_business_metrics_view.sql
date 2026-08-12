-- XYZ-PILOT-234 / XYZ-PILOT-233 companion
-- Operator-owned helper view on top of external semantic layer BASE_LAYER.
--
-- Purpose:
--   Provide a clean monthly direct-query surface for BI clients, SQL IDEs,
--   the Streamlit QBR dashboard, and the MCP chatbot without changing <SEMANTIC_LAYER_VENDOR>
--   semantic-layer DDL.
--
-- Scope:
--   - Reads explicit business dimensions + raw measures from
--     <SEMANTIC_LAYER_SCHEMA>.BASE_LAYER.
--   - Computes gross-margin %, YTD sales/qty, rolling 3M/6M sales, YoY sales
--     delta/% and share-of-brand/region.
--   - Materializes the result in <PILOT_SCHEMA>.MV_BUSINESS_METRICS so QBR
--     page reruns do not repeatedly pay the ESL BASE_LAYER query cost.
--   - Exposes the stable consumer contract as
--     <PILOT_SCHEMA>.V_BUSINESS_METRICS.
--
-- Non-scope:
--   - Does not add FACT_TARGET/budget metrics because ESL BASE_LAYER does not
--     carry target/budget fact data.
--   - Does not modify ESL_* schemas, UDFs, roles, config tables, or <SEMANTIC_LAYER_VENDOR>
--     generated objects.
--
-- Run:
--   scripts/exasol_views/run_esl_business_metrics_view.sh --profile xyz-pilot

CREATE OR REPLACE TABLE <PILOT_SCHEMA>.MV_BUSINESS_METRICS AS
WITH raw_monthly AS (
    SELECT
        "Calendar: Year" AS CALENDAR_YEAR_RAW,
        "Calendar: Date" AS MONTH_DATE,
        "Calendar: Month" AS MONTH_LABEL,
        "Calendar: Quarter" AS QUARTER_LABEL,
        "Sales Region: XYZ Region 1" AS XYZ_REGION_1,
        "Sales Region: XYZ Region 2" AS XYZ_REGION_2,
        "Brand" AS SEGMENT,
        "Product: Product Line" AS SUBCATEGORY,
        "Product: Product Line Name" AS PRODUCT_LINE_NAME,
        SUM("Total Sales (USD)") AS MONTHLY_VALUE_USD,
        SUM("COGS (USD)") AS MONTHLY_COGS_USD,
        SUM("Gross Margin (USD)") AS MONTHLY_GROSS_MARGIN_USD,
        SUM("Shipped (qty)") AS MONTHLY_QTY_SHIPPED
    FROM "<SEMANTIC_LAYER_SCHEMA>"."BASE_LAYER"
    GROUP BY 1,2,3,4,5,6,7,8,9
),
monthly AS (
    SELECT
        CAST(CALENDAR_YEAR_RAW AS DECIMAL(9,0)) AS CALENDAR_YEAR,
        CAST(EXTRACT(MONTH FROM MONTH_DATE) AS DECIMAL(2,0)) AS CALENDAR_MONTH,
        MONTH_DATE,
        MONTH_LABEL,
        QUARTER_LABEL,
        XYZ_REGION_1,
        XYZ_REGION_2,
        SEGMENT,
        SUBCATEGORY,
        PRODUCT_LINE_NAME,
        MONTHLY_VALUE_USD,
        MONTHLY_COGS_USD,
        MONTHLY_GROSS_MARGIN_USD,
        MONTHLY_QTY_SHIPPED
    FROM raw_monthly
),
windowed AS (
    SELECT
        m.CALENDAR_YEAR,
        m.CALENDAR_MONTH,
        m.MONTH_DATE,
        m.MONTH_LABEL,
        m.QUARTER_LABEL,
        m.XYZ_REGION_1,
        m.XYZ_REGION_2,
        m.SEGMENT,
        m.SUBCATEGORY,
        m.PRODUCT_LINE_NAME,
        m.MONTHLY_VALUE_USD,
        m.MONTHLY_COGS_USD,
        m.MONTHLY_GROSS_MARGIN_USD,
        m.MONTHLY_QTY_SHIPPED,
        CASE
            WHEN m.MONTHLY_VALUE_USD <> 0
            THEN m.MONTHLY_GROSS_MARGIN_USD / m.MONTHLY_VALUE_USD
            ELSE NULL
        END AS GROSS_MARGIN_PCT,
        SUM(m.MONTHLY_VALUE_USD) OVER (
            PARTITION BY
                m.CALENDAR_YEAR,
                m.XYZ_REGION_1,
                m.XYZ_REGION_2,
                m.SEGMENT,
                m.SUBCATEGORY,
                m.PRODUCT_LINE_NAME
            ORDER BY m.CALENDAR_MONTH
            ROWS UNBOUNDED PRECEDING
        ) AS YTD_VALUE_USD,
        SUM(m.MONTHLY_QTY_SHIPPED) OVER (
            PARTITION BY
                m.CALENDAR_YEAR,
                m.XYZ_REGION_1,
                m.XYZ_REGION_2,
                m.SEGMENT,
                m.SUBCATEGORY,
                m.PRODUCT_LINE_NAME
            ORDER BY m.CALENDAR_MONTH
            ROWS UNBOUNDED PRECEDING
        ) AS YTD_QTY_SHIPPED,
        SUM(m.MONTHLY_VALUE_USD) OVER (
            PARTITION BY
                m.XYZ_REGION_1,
                m.XYZ_REGION_2,
                m.SEGMENT,
                m.SUBCATEGORY,
                m.PRODUCT_LINE_NAME
            ORDER BY m.CALENDAR_YEAR, m.CALENDAR_MONTH
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ) AS ROLLING_3M_VALUE_USD,
        SUM(m.MONTHLY_VALUE_USD) OVER (
            PARTITION BY
                m.XYZ_REGION_1,
                m.XYZ_REGION_2,
                m.SEGMENT,
                m.SUBCATEGORY,
                m.PRODUCT_LINE_NAME
            ORDER BY m.CALENDAR_YEAR, m.CALENDAR_MONTH
            ROWS BETWEEN 5 PRECEDING AND CURRENT ROW
        ) AS ROLLING_6M_VALUE_USD,
        RATIO_TO_REPORT(m.MONTHLY_VALUE_USD) OVER (
            PARTITION BY m.CALENDAR_YEAR, m.CALENDAR_MONTH, m.SEGMENT
        ) AS SALES_PCT_OF_BRAND,
        RATIO_TO_REPORT(m.MONTHLY_VALUE_USD) OVER (
            PARTITION BY m.CALENDAR_YEAR, m.CALENDAR_MONTH, m.XYZ_REGION_1
        ) AS SALES_PCT_OF_REGION
    FROM monthly m
)
SELECT
    cy.CALENDAR_YEAR,
    cy.CALENDAR_MONTH,
    cy.MONTH_DATE,
    cy.MONTH_LABEL,
    cy.QUARTER_LABEL,
    cy.XYZ_REGION_1,
    cy.XYZ_REGION_2,
    cy.SEGMENT,
    cy.SUBCATEGORY,
    cy.PRODUCT_LINE_NAME,
    cy.MONTHLY_VALUE_USD,
    cy.MONTHLY_COGS_USD,
    cy.MONTHLY_GROSS_MARGIN_USD,
    cy.MONTHLY_QTY_SHIPPED,
    cy.GROSS_MARGIN_PCT,
    cy.YTD_VALUE_USD,
    cy.YTD_QTY_SHIPPED,
    cy.ROLLING_3M_VALUE_USD,
    cy.ROLLING_6M_VALUE_USD,
    cy.SALES_PCT_OF_BRAND,
    cy.SALES_PCT_OF_REGION,
    py.MONTHLY_VALUE_USD AS SALES_PY_USD,
    cy.MONTHLY_VALUE_USD - py.MONTHLY_VALUE_USD AS SALES_YOY_VAR_USD,
    CASE
        WHEN py.MONTHLY_VALUE_USD <> 0
        THEN (cy.MONTHLY_VALUE_USD - py.MONTHLY_VALUE_USD) / py.MONTHLY_VALUE_USD
        ELSE NULL
    END AS SALES_YOY_PCT
FROM windowed cy
LEFT JOIN windowed py
    ON cy.CALENDAR_YEAR = py.CALENDAR_YEAR + 1
   AND cy.CALENDAR_MONTH = py.CALENDAR_MONTH
   AND cy.XYZ_REGION_1 = py.XYZ_REGION_1
   AND cy.XYZ_REGION_2 = py.XYZ_REGION_2
   AND cy.SEGMENT = py.SEGMENT
   AND cy.SUBCATEGORY = py.SUBCATEGORY
   AND cy.PRODUCT_LINE_NAME = py.PRODUCT_LINE_NAME;

CREATE OR REPLACE VIEW <PILOT_SCHEMA>.V_BUSINESS_METRICS AS
SELECT *
FROM <PILOT_SCHEMA>.MV_BUSINESS_METRICS;

GRANT SELECT ON <PILOT_SCHEMA>.V_BUSINESS_METRICS TO XYZ_DASH, XYZ_BIZ;
