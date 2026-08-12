# XYZ QBR — Streamlit Dashboard

A Streamlit QBR dashboard backed by the live Exasol Personal
`<PILOT_SCHEMA>` schema. Runs anywhere Python runs — no proprietary BI
desktop client required.

## What it shows

| Tab | Backing view | Visuals |
|-----|-------------|---------|
| Executive Sales | `MV_MONTHLY_METRICS` | Total Sales / GM% / Units / YTD KPIs · monthly trend · sorted brand-mix bar · region bar · YTD-by-year line |
| Sales vs Target | `MV_ACTUAL_VS_TARGET` | Actual / Target / Variance% / YTD KPIs · monthly variance bars · brand actual-vs-target · variance detail table |
| Year-over-Year | `MV_YOY_METRICS` | CY / PY / YoY% KPIs · CY-vs-PY brand bars · YoY% trend · top-movers table |
| Inventory | `MV_STOCK_ANALYTICS` | QoH / Inv$ / Turns / Coverage KPIs · QoH area chart · inventory-vs-sales scatter · brand snapshot table |
| ESL Metrics | `V_BUSINESS_METRICS` | XYZ-owned view backed by `MV_BUSINESS_METRICS`, refreshed from external semantic layer `BASE_LAYER` · gross-margin % · YTD · rolling 3M/6M · YoY · product-line and region mix |
| **Data** (XYZ-PILOT-028) | `EXA_ALL_TABLES` / `EXA_ALL_VIEWS` / `EXA_ALL_COLUMNS` | Schema inventory (tables + views + column counts) · interactive `COUNT(*)` / sample-rows runner with optional WHERE clause |

Sidebar provides Year / Region / Brand multiselect filters and a refresh
button that clears the query cache. Filter cookies are used only to initialise
stable Streamlit widget state; after first render, `st.session_state` is the
source of truth so each filter chip add/remove applies on the first click
(XYZ-PILOT-135 follow-up). Each tab carries an **"Active filters →"** caption at
the top so subscribers see exactly which selections are in scope
(XYZ-PILOT-058). The Inventory tab applies Year via
`EXTRACT(YEAR FROM INVENTORY_DATE)`; Region doesn't apply there (inventory
snapshots are not regional).

The sidebar also shows a compact `Scope · years · regions · brands` caption so
subscribers can see whether they are looking at all values or a narrowed subset
before changing tabs. KPI rows across tabs share the same XYZ metric-card style
via `streamlit-extras::style_metric_cards` (XYZ-PILOT-134).

QBR chart and dashboard chrome follow the visual-regression discipline in
[XYZ-PILOT-149](../../docs/XYZ_PILOT_ISSUE_TRACKER.md): shared Altair
axis/legend/grid styling, stable chart containers, compact tabs, carded KPIs,
and framed data tables. This is visual-only polish; it does not change the
underlying SQL or filter data contracts.

The top header also carries QBR deployment provenance ([XYZ-PILOT-202](../../docs/XYZ_PILOT_ISSUE_TRACKER.md)):
the single last deployment-cause issue, Docker build timestamp, deployment
timestamp, and current QBR Streamlit process start time.
The shared Operator Dashboard / Chatbot / QBR provenance contract is documented
in [`XYZ_PILOT_UI_RUNBOOK.md`](../../docs/30-runbooks/ui/XYZ_PILOT_UI_RUNBOOK.md).

## Project layout

```
services/streamlit-insights/
  app.py            ← UI: 6 tabs (Exec Sales · Sales-vs-Target · YoY · Inventory · ESL Metrics · Data) + sidebar filters + Active-filter caption per tab
  db.py             ← cached pyexasol connection + query helpers
  pyproject.toml    ← uv-managed dependencies
  .env.example      ← copy to .env and fill in EXASOL_PASSWORD
  .env              ← gitignored runtime secrets
```

## Run it

```bash
cd services/streamlit-insights
cp .env.example .env                   # then edit EXASOL_PASSWORD
set -a && . ./.env && set +a
uv sync
uv run streamlit run app.py --server.headless true \
    --server.port 8501 --server.address 0.0.0.0
```

Streamlit will print three URLs:

| Label | Example | When to use |
|-------|---------|-------------|
| **Local URL** | `http://localhost:8501` | This machine only |
| **Network URL** | `http://192.168.0.218:8501` | Other devices on the same LAN (verified reachable) |
| **External URL** | `http://87.157.242.205:8501` | Streamlit echoes your public IP — **NOT actually reachable** unless your router forwards 8501. Treat as informational only. |

Health check: `curl http://localhost:8501/_stcore/health` → `ok`.

## Deployment provenance

For live redeploys, stamp the QBR container with the single issue that caused
the rebuild plus build/deploy timestamps:

```bash
cd /opt/xyz-qbr
sudo env \
  XYZ_LAST_DEPLOYMENT_ISSUE=XYZ-PILOT-262 \
  XYZ_BUILD_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  XYZ_DEPLOYED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  docker compose up -d --build streamlit
```

Important deployment guardrail: `/opt/xyz-qbr` on the EC2 host may be
host-local (no `.git` checkout). In that case, a GitHub commit alone does not
change the live UI. Always verify the host source before rebuild:

```bash
test -d /opt/xyz-qbr/.git && echo git-backed || echo host-local
```

If `host-local`, copy/patch `app.py` in `/opt/xyz-qbr` first, then rebuild.

If the fix is deployed before `XYZ_MASTER` assigns a formal tracker ID, use a
short candidate label and point `XYZ_DEPLOYMENT_REF_URL` at the committed
evidence file:

```bash
cd /opt/xyz-qbr
sudo env \
  XYZ_LAST_DEPLOYMENT_ISSUE=QBR-FILTER-HOTFIX \
  XYZ_DEPLOYMENT_REF_URL=https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG> \
  XYZ_BUILD_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  XYZ_DEPLOYED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  docker compose up -d --build streamlit
```

The rendered line is intentionally compact:

```text
XYZ-PILOT-262 · build <UTC> · deploy <UTC> · QBR process started <UTC>
```

## Connection notes (Exasol-specific)

- **TLS is mandatory.** Exasol Personal rejects unencrypted clients with
  `08004 Only TLS connections are allowed`. The CLAUDE.md and MCP config
  flag `EXASOL_TLS=false` is misleading — leave it at `true` here.
- **Self-signed cert.** `db.py` passes `websocket_sslopt={'cert_reqs': 0}`
  to skip CA verification. Acceptable for a single-tenant Pilot; revisit
  before any production exposure.
- **Caching.** Connection is `st.cache_resource` (process-wide singleton);
  query results are `st.cache_data(ttl=300)`. The sidebar refresh button
  clears the query cache only — connection stays live.

## Operational scope

- Read-only against `<PILOT_SCHEMA>`. No DDL, no DML.
- Credentials live in `services/streamlit-insights/.env` only — gitignored. Never
  hardcoded in `app.py` or `db.py`.
- `app.py` redacts errors into a collapsed `st.expander` showing
  exception type + first 240 chars of message. No password material in
  user-facing error output.
