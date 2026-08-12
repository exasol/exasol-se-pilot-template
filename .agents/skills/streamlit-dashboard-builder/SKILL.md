---
name: streamlit-dashboard-builder
description: Use when asked to design, implement, validate, or operate Streamlit dashboards for workshop analytics, AWS cost/resource monitors, Exasol-backed BI views, or operational data apps; emphasizes cached database reads, interactive charts, dense dashboard layout, secret-safe errors, and service URL validation.
---

# Streamlit Dashboard Builder

Use this skill for Streamlit apps, dashboards, BI views, operational monitors,
interactive filters, Altair charts, Exasol-backed data apps, and local or
Docker-based Streamlit service validation.

## Workflow

1. Identify the dashboard data contract first:
   - Source tables, schemas, and refresh cadence.
   - Required filters, grain, and expected empty states.
   - Whether values are historical facts or current resource snapshots.

2. Prefer existing repository patterns:
   - Reuse `reference/pyproject.toml` dependencies.
   - Reuse `reference/utils/db.py` for Exasol connections.
   - Keep credentials in deployment files or environment-backed helpers; never
     paste secrets into Streamlit code or `st.secrets`.

3. Build a dense operational UI:
   - Sidebar filters for accounts, periods, regions, and resource types.
   - KPI rows with `st.metric` for headline values.
   - `st.tabs` for related views.
   - `st.dataframe` for audit tables and report links.
   - Altair charts through `st.altair_chart(..., width="stretch")`.

4. Cache safely:
   - Use `st.cache_resource` for shared database connections plus a lock.
   - Use `st.cache_data(ttl=...)` for query result dataframes.
   - Add a visible refresh button that clears cached query data.

5. Redact failures:
   - User-facing database and query errors must not expose passwords, tokens,
     keys, or full credential material.
   - Show exception type and sanitized message only inside an expander.

6. Validate before handoff:
   - Compile or run the Streamlit module.
   - Start the app locally or via the repository Docker configuration.
   - Check that charts render non-empty when data exists and empty states are
     clear when data is absent.
   - Report exact service URLs and note whether they are local, LAN, forwarded,
     or public.

## Official Streamlit APIs To Prefer

- `st.cache_data` for dataframe/query result caching.
- `st.cache_resource` for shared connections and long-lived clients.
- `st.connection` only when the app uses Streamlit-supported connection
  configuration intentionally.
- `st.metric` for KPI values.
- `st.dataframe` with column configuration for inspectable tables and links.
- `st.altair_chart` for interactive charts and selection-ready visualizations.
