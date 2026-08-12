"""XYZ QBR dashboard — Streamlit BI surface over Exasol.

Backed by <PILOT_SCHEMA>.MV_MONTHLY_METRICS, MV_YOY_METRICS, MV_ACTUAL_VS_TARGET,
MV_STOCK_ANALYTICS on the live Exasol Personal instance.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hmac
import os

import altair as alt
import pandas as pd
import streamlit as st

from db import clear_query_cache, run_query

PALETTE = ["#118DFF", "#12239E", "#E66C37", "#6B007B", "#1AAB40", "#D9B300", "#D64554"]
GOOD = "#1AAB40"
BAD = "#D64554"
GRID = "#E5E7EB"
TEXT = "#111827"
MUTED = "#4B5563"
LAST_DEPLOYMENT_ISSUE = os.environ.get(
    "XYZ_LAST_DEPLOYMENT_ISSUE",
    os.environ.get("XYZ_DEPLOYMENT_TICKET", "XYZ-PILOT-202"),
)
DEPLOYMENT_REF_URL = os.environ.get("XYZ_DEPLOYMENT_REF_URL", "").strip()
BUILD_AT_UTC = os.environ.get("XYZ_BUILD_AT_UTC", "not-stamped")
DEPLOYED_AT_UTC = os.environ.get("XYZ_DEPLOYED_AT_UTC", BUILD_AT_UTC)
APP_STARTED_AT_UTC = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
CONFIDENTIAL_NOTICE = (
    "Confidential - <PILOT_NAME> - "
    "© Exasol AG · ESL © <SEMANTIC_LAYER_VENDOR> · for XYZ evaluation use only."
)

st.set_page_config(
    page_title="XYZ QBR — Exasol",
    # Branded with <CUSTOMER_NAME>'s own favicon (lifted directly
    # from <CUSTOMER_DOMAIN>/wp-content/uploads/2017/05/Favicon_32x32.png).
    # Passed as a URL so the browser fetches it at page load — no bundling
    # needed inside the container; if XYZ's CDN ever moves the asset we
    # fall back to the emoji default automatically.
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _apply_dashboard_chrome() -> None:
    """Apply restrained dashboard polish without changing data contracts."""
    st.markdown(
        f"""
        <style>
          [data-testid="stMetric"] {{
            background: #FFFFFF;
            border: 1px solid {GRID};
            border-radius: 6px;
            padding: 0.65rem 0.75rem;
          }}
          [data-testid="stMetric"] label {{
            color: {MUTED};
            font-weight: 600;
          }}
          [data-testid="stMetricValue"] {{
            color: {TEXT};
            letter-spacing: 0;
          }}
          [data-testid="stCaptionContainer"] {{
            color: {MUTED};
          }}
          .stTabs [data-baseweb="tab-list"] {{
            gap: 0.25rem;
            border-bottom: 1px solid {GRID};
          }}
          .stTabs [data-baseweb="tab"] {{
            padding: 0.5rem 0.75rem;
          }}
          [data-testid="stDataFrame"] {{
            border: 1px solid {GRID};
            border-radius: 6px;
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _check_password() -> bool:
    """Gate the app behind a shared password from the APP_PASSWORD env var.

    If APP_PASSWORD is unset or empty:
      - XYZ_REQUIRE_AUTH=1 (production) → fail closed: refuse to start.
      - XYZ_REQUIRE_AUTH unset/0 (local dev) → bypass gate, log a warning.
    XYZ-PILOT-070 — closes the silent-bypass path that existed when a
    production .env file failed to load.
    """
    expected = os.environ.get("APP_PASSWORD", "")
    if not expected:
        if os.environ.get("XYZ_REQUIRE_AUTH", "") == "1":
            st.error(
                "🚨 Configuration error: APP_PASSWORD env var is empty/unset but "
                "XYZ_REQUIRE_AUTH=1 is set. Refusing to start — check "
                "/opt/xyz-qbr/.env on the host."
            )
            st.stop()
        return True
    if st.session_state.get("auth_ok"):
        return True

    st.title("<CUSTOMER_NAME> — QBR")
    st.caption("Restricted demo · enter access password to continue")
    pw = st.text_input("Password", type="password", key="pw_input")
    if st.button("Sign in"):
        if hmac.compare_digest(pw, expected):
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


if not _check_password():
    st.stop()

_apply_dashboard_chrome()


def _style_qbr_metric_cards() -> None:
    """Apply the XYZ visual metric-card treatment once per rerun."""
    try:
        from streamlit_extras.metric_cards import style_metric_cards  # type: ignore
        style_metric_cards(
            background_color="#FFFFFF",
            border_left_color=PALETTE[0],
            border_color="#E5E7EB",
            border_radius_px=6,
            box_shadow=True,
        )
    except ImportError:
        pass


def _build_info_caption() -> str:
    """Return compact QBR deployment provenance for operators."""
    issue = LAST_DEPLOYMENT_ISSUE.strip() or "XYZ-PILOT-202"
    issue_url = DEPLOYMENT_REF_URL or (
        "https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/"
        f"docs/XYZ_PILOT_ISSUE_TRACKER.md#{issue.lower()}"
    )
    return (
        f"[{issue}]({issue_url}) · build {BUILD_AT_UTC} · "
        f"deploy {DEPLOYED_AT_UTC} · QBR process started {APP_STARTED_AT_UTC}"
    )


def _polish_chart(chart: alt.Chart) -> alt.Chart:
    """Apply consistent BI-style Altair chrome to QBR charts."""
    return (
        chart
        .configure_axis(
            labelColor="#374151",
            titleColor=TEXT,
            gridColor=GRID,
            domainColor="#D1D5DB",
            tickColor="#D1D5DB",
            labelFontSize=12,
            titleFontSize=12,
            titleFontWeight=600,
        )
        .configure_legend(
            labelColor="#374151",
            titleColor=TEXT,
            labelFontSize=12,
            titleFontSize=12,
            titleFontWeight=600,
        )
        .configure_view(strokeWidth=0)
    )


def _render_chart(chart: alt.Chart) -> None:
    """Render a polished chart with stable container width."""
    st.altair_chart(_polish_chart(chart), use_container_width=True)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@st.cache_data(ttl=600)
def filter_options() -> dict[str, list]:
    df = run_query(
        """
        SELECT DISTINCT PERIOD_YEAR, XYZ_REGION_1, SEGMENT
        FROM <PILOT_SCHEMA>.MV_MONTHLY_METRICS
        """
    )
    return {
        "years": sorted(df["PERIOD_YEAR"].dropna().astype(int).unique().tolist()),
        "regions": sorted(df["XYZ_REGION_1"].dropna().unique().tolist()),
        "brands": sorted(df["SEGMENT"].dropna().unique().tolist()),
    }


with st.sidebar:
    st.markdown("### XYZ QBR — Filters")
    try:
        opts = filter_options()
    except Exception as exc:  # noqa: BLE001
        with st.expander("Connection error", expanded=True):
            st.error(f"{type(exc).__name__}: {str(exc)[:240]}")
        st.stop()

    # XYZ-PILOT-135: persist Year / Region / Brand filter selections across
    # sessions via browser cookies (streamlit-cookies-controller). On first
    # render in a new browser session, defaults are "all values"; once the
    # subscriber narrows the filters, those choices survive a tab close +
    # reopen + Streamlit redeploy. Graceful fallback to in-session-only
    # multiselect defaults if streamlit-cookies-controller isn't installed.
    _cookies = None
    try:
        from streamlit_cookies_controller import CookieController  # type: ignore
        _cookies = CookieController()
    except ImportError:
        pass

    _filter_state_keys = {
        "years": "qbr_filter_years",
        "regions": "qbr_filter_regions",
        "brands": "qbr_filter_brands",
    }

    def _load_filter(key: str, all_values: list) -> list:
        if _cookies is None:
            return list(all_values)
        raw = _cookies.get(f"xyz_qbr_{key}")
        if not raw:
            return list(all_values)
        # Cookie stored as comma-separated repr; intersect with current
        # opts to drop any stale values the source has dropped since.
        try:
            saved = [v.strip() for v in str(raw).split("|") if v.strip()]
            # Coerce year cookie back to int
            if key == "years":
                saved = [int(v) for v in saved]
            return [v for v in saved if v in all_values] or list(all_values)
        except (ValueError, TypeError):
            return list(all_values)

    def _coerce_filter_values(key: str, values: list, all_values: list) -> list:
        try:
            selected = list(values or [])
            if key == "years":
                selected = [int(v) for v in selected]
            return [v for v in selected if v in all_values]
        except (ValueError, TypeError):
            return list(all_values)

    def _ensure_filter_state(key: str, all_values: list) -> None:
        state_key = _filter_state_keys[key]
        if st.session_state.get("qbr_reset_filters"):
            st.session_state[state_key] = list(all_values)
            return
        if state_key not in st.session_state:
            st.session_state[state_key] = _load_filter(key, all_values)
            return
        st.session_state[state_key] = _coerce_filter_values(
            key,
            st.session_state[state_key],
            all_values,
        )

    def _save_filter(key: str, values: list) -> None:
        if _cookies is None or values is None:
            return
        try:
            cookie_key = f"xyz_qbr_{key}"
            payload = "|".join(str(v) for v in values)
            if str(_cookies.get(cookie_key) or "") != payload:
                _cookies.set(cookie_key, payload, max_age=86400 * 90)
        except Exception:  # noqa: BLE001
            pass

    def _clear_saved_filters() -> None:
        if _cookies is not None:
            for cookie_key in ("xyz_qbr_years", "xyz_qbr_regions", "xyz_qbr_brands"):
                try:
                    _cookies.remove(cookie_key)
                except Exception:  # noqa: BLE001
                    pass
        st.session_state["qbr_reset_filters"] = True

    _ensure_filter_state("years", opts["years"])
    _ensure_filter_state("regions", opts["regions"])
    _ensure_filter_state("brands", opts["brands"])
    st.session_state.pop("qbr_reset_filters", None)

    # Stable widget keys are required here. Without them, Streamlit can reuse
    # cookie-derived defaults one rerun behind the user's actual click, making
    # each add/remove appear to require a second selection.
    sel_years = st.multiselect("Year", opts["years"], key=_filter_state_keys["years"])
    sel_regions = st.multiselect("Region", opts["regions"], key=_filter_state_keys["regions"])
    sel_brands = st.multiselect("Brand", opts["brands"], key=_filter_state_keys["brands"])

    def _scope_text(label: str, selected: list, all_values: list) -> str:
        if not selected or len(selected) == len(all_values):
            return f"{label}: all {len(all_values)}"
        return f"{label}: {len(selected)} of {len(all_values)}"

    st.caption(
        "Scope · "
        + " · ".join(
            [
                _scope_text("years", sel_years, opts["years"]),
                _scope_text("regions", sel_regions, opts["regions"]),
                _scope_text("brands", sel_brands, opts["brands"]),
            ]
        )
    )
    # Persist on every rerun (cheap — cookie write is one tiny header bump)
    _save_filter("years", sel_years)
    _save_filter("regions", sel_regions)
    _save_filter("brands", sel_brands)

    st.divider()
    if st.button("🔄 Refresh data"):
        clear_query_cache()
        st.rerun()
    if _cookies is not None:
        st.button("🗑 Clear saved filters", on_click=_clear_saved_filters)
    st.caption(
        "Data refreshes every 5 min · live DirectQuery via pyexasol · "
        "filters persist 90 d; first-click filter state hotfix XYZ-PILOT-262"
    )
    st.caption(CONFIDENTIAL_NOTICE)


def _sql_lit(v: object) -> str:
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"


def _in_clause(values: list, col: str) -> str:
    if not values:
        return "1=1"
    return f"{col} IN ({','.join(_sql_lit(v) for v in values)})"


def filtered_where(view_alias: str = "") -> tuple[str, list]:
    p = f"{view_alias}." if view_alias else ""
    parts = [
        _in_clause(sel_years, f"{p}PERIOD_YEAR"),
        _in_clause(sel_regions, f"{p}XYZ_REGION_1"),
        _in_clause(sel_brands, f"{p}SEGMENT"),
    ]
    return " AND ".join(parts), []


def filtered_where_esl(view_alias: str = "") -> tuple[str, list]:
    p = f"{view_alias}." if view_alias else ""
    parts = [
        _in_clause(sel_years, f"{p}CALENDAR_YEAR"),
        _in_clause(sel_regions, f"{p}XYZ_REGION_1"),
        _in_clause(sel_brands, f"{p}SEGMENT"),
    ]
    return " AND ".join(parts), []


def _active_filter_caption(year_label: str = "Year", show_region: bool = True) -> None:
    """Render a small caption summarising the active filters at the top of
    each tab so users can see which selections are in scope (XYZ-PILOT-058).
    Pass show_region=False on tabs where region doesn't apply."""
    parts: list[str] = []
    if sel_years and len(sel_years) < len(opts["years"]):
        parts.append(f"**{year_label}:** {', '.join(str(y) for y in sel_years)}")
    if show_region and sel_regions and len(sel_regions) < len(opts["regions"]):
        parts.append(f"**Region:** {', '.join(sel_regions)}")
    if sel_brands and len(sel_brands) < len(opts["brands"]):
        parts.append(f"**Brand:** {', '.join(sel_brands)}")
    if not parts:
        st.caption("_Active filters: none (showing all data)._")
    else:
        st.caption("Active filters → " + " · ".join(parts))


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

col_title, col_meta = st.columns([4, 1])
with col_title:
    st.title("<CUSTOMER_NAME> — QBR")
    st.caption(
        "Live DirectQuery on Exasol Personal · <PILOT_SCHEMA> schema · "
        "Streamlit BI surface over Exasol"
    )
    st.caption(CONFIDENTIAL_NOTICE)
    st.caption(_build_info_caption())
with col_meta:
    meta = run_query(
        "SELECT COUNT(*) AS N, MIN(INVOICE_DATE) AS MIN_D, MAX(INVOICE_DATE) AS MAX_D "
        "FROM <PILOT_SCHEMA>.FACT_PRIMARY"
    ).iloc[0]
    st.metric("Invoiced sales rows", f"{int(meta['N']):,}")
    st.caption(f"{meta['MIN_D']} → {meta['MAX_D']}")

# XYZ-PILOT-134 / XYZ-PILOT-130 UI/UX follow-up: inject metric-card styling once,
# so KPI rows across every tab share the same visual treatment even when the
# Executive Sales tab has no rows for the current filter scope.
_style_qbr_metric_cards()


# ---------------------------------------------------------------------------
# Tabs (4 QBR pages + ESL helper view + Data inventory)
# ---------------------------------------------------------------------------

tab_exec, tab_target, tab_yoy, tab_inv, tab_esl, tab_data = st.tabs(
    [
        "Executive Sales",
        "Sales vs Target",
        "Year-over-Year",
        "Inventory",
        "ESL Metrics",
        "Data",
    ]
)


def _month_str(df: pd.DataFrame) -> pd.Series:
    return (
        df["PERIOD_YEAR"].astype(int).astype(str)
        + "-"
        + df["PERIOD_MONTH"].astype(int).astype(str).str.zfill(2)
    )


# ---------------------------------------------------------------------------
# Page 1 — Executive Sales
# ---------------------------------------------------------------------------

with tab_exec:
    _active_filter_caption()
    where, args = filtered_where()
    monthly = run_query(
        f"""
        SELECT PERIOD_YEAR, PERIOD_MONTH, XYZ_REGION_1, SEGMENT,
               SUM(MONTHLY_VALUE)        AS SALES,
               SUM(MONTHLY_GROSS_MARGIN) AS GM,
               SUM(MONTHLY_QTY)          AS QTY,
               SUM(YTD_VALUE)            AS YTD
        FROM <PILOT_SCHEMA>.MV_MONTHLY_METRICS
        WHERE {where}
        GROUP BY 1,2,3,4
        """,
        tuple(args),
    )

    if monthly.empty:
        st.info("No data for the current filter selection.")
    else:
        total_sales = float(monthly["SALES"].sum())
        total_gm = float(monthly["GM"].sum())
        gm_pct = total_gm / total_sales if total_sales else 0
        total_qty = float(monthly["QTY"].sum())
        ytd_latest = float(
            monthly.sort_values(["PERIOD_YEAR", "PERIOD_MONTH"], ascending=False)["YTD"].iloc[0]
        )

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total Sales", f"${total_sales:,.0f}")
        k2.metric("Gross Margin %", f"{gm_pct:.1%}")
        k3.metric("Units Shipped", f"{total_qty:,.0f}")
        k4.metric("Latest YTD Sales", f"${ytd_latest:,.0f}")

        st.divider()

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Monthly sales trend")
            trend = monthly.groupby(["PERIOD_YEAR", "PERIOD_MONTH"], as_index=False)["SALES"].sum()
            trend["MONTH"] = _month_str(trend)
            trend = trend.sort_values("MONTH")
            chart = (
                alt.Chart(trend)
                .mark_line(point=True, strokeWidth=3, color=PALETTE[0])
                .encode(
                    x=alt.X("MONTH:N", title="Month", sort=trend["MONTH"].tolist()),
                    y=alt.Y("SALES:Q", title="Sales ($)", axis=alt.Axis(format="~s")),
                    tooltip=["MONTH", alt.Tooltip("SALES:Q", format=",.0f")],
                )
                .properties(height=300)
            )
            _render_chart(chart)

        with c2:
            st.subheader("Brand mix")
            brand_share = monthly.groupby("SEGMENT", as_index=False)["SALES"].sum()
            brand_share["SALES"] = brand_share["SALES"].astype(float)
            brand_total = float(brand_share["SALES"].sum())
            brand_share["SHARE_PCT"] = (
                brand_share["SALES"] / brand_total if brand_total else 0
            )
            brand_share = brand_share.sort_values("SALES", ascending=True)
            chart = (
                alt.Chart(brand_share)
                .mark_bar(color=PALETTE[2])
                .encode(
                    y=alt.Y("SEGMENT:N", sort="-x", title="Brand"),
                    x=alt.X("SALES:Q", title="Sales ($)", axis=alt.Axis(format="~s")),
                    tooltip=[
                        "SEGMENT",
                        alt.Tooltip("SALES:Q", format=",.0f"),
                        alt.Tooltip("SHARE_PCT:Q", title="Share", format=".1%"),
                    ],
                )
                .properties(height=300)
            )
            _render_chart(chart)

        c3, c4 = st.columns(2)
        with c3:
            st.subheader("Sales by region")
            reg = monthly.groupby("XYZ_REGION_1", as_index=False)["SALES"].sum().sort_values(
                "SALES", ascending=True
            )
            chart = (
                alt.Chart(reg)
                .mark_bar(color=PALETTE[2])
                .encode(
                    y=alt.Y("XYZ_REGION_1:N", sort="-x", title="Region"),
                    x=alt.X("SALES:Q", title="Sales ($)", axis=alt.Axis(format="~s")),
                    tooltip=["XYZ_REGION_1", alt.Tooltip("SALES:Q", format=",.0f")],
                )
                .properties(height=300)
            )
            _render_chart(chart)

        with c4:
            st.subheader("YTD sales by year")
            ytd = monthly.copy()
            ytd["MONTH_NUM"] = ytd["PERIOD_MONTH"].astype(int)
            ytd_chart = ytd.groupby(["PERIOD_YEAR", "MONTH_NUM"], as_index=False)["YTD"].sum()
            chart = (
                alt.Chart(ytd_chart)
                .mark_line(point=True, strokeWidth=2)
                .encode(
                    x=alt.X("MONTH_NUM:O", title="Month"),
                    y=alt.Y("YTD:Q", title="YTD Sales ($)", axis=alt.Axis(format="~s")),
                    color=alt.Color(
                        "PERIOD_YEAR:N", scale=alt.Scale(range=PALETTE), title="Year"
                    ),
                    tooltip=["PERIOD_YEAR", "MONTH_NUM", alt.Tooltip("YTD:Q", format=",.0f")],
                )
                .properties(height=300)
            )
            _render_chart(chart)


# ---------------------------------------------------------------------------
# Page 2 — Sales vs Target
# ---------------------------------------------------------------------------

with tab_target:
    _active_filter_caption()
    where, args = filtered_where()
    tgt = run_query(
        f"""
        SELECT PERIOD_YEAR, PERIOD_MONTH, XYZ_REGION_1, SEGMENT, SUBCATEGORY,
               SUM(ACTUAL_VALUE)   AS ACTUAL,
               SUM(YTD_ACTUAL)     AS YTD_ACTUAL,
               SUM(MONTHLY_TARGET) AS TARGET,
               SUM(YTD_TARGET)     AS YTD_TARGET,
               SUM(TARGET_VARIANCE) AS VARIANCE
        FROM <PILOT_SCHEMA>.MV_ACTUAL_VS_TARGET
        WHERE {where}
        GROUP BY 1,2,3,4,5
        """,
        tuple(args),
    )

    if tgt.empty:
        st.info("No data for the current filter selection.")
    else:
        total_actual = float(tgt["ACTUAL"].sum())
        total_target = float(tgt["TARGET"].sum())
        var_pct = (total_actual - total_target) / total_target if total_target else 0
        ytd_a = float(
            tgt.sort_values(["PERIOD_YEAR", "PERIOD_MONTH"], ascending=False)["YTD_ACTUAL"].iloc[0]
        )
        ytd_t = float(
            tgt.sort_values(["PERIOD_YEAR", "PERIOD_MONTH"], ascending=False)["YTD_TARGET"].iloc[0]
        )

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Actual", f"${total_actual:,.0f}")
        k2.metric("Target", f"${total_target:,.0f}")
        k3.metric("Variance %", f"{var_pct:.1%}", delta=f"{var_pct:.1%}")
        k4.metric("Latest YTD A/T", f"${ytd_a:,.0f} / ${ytd_t:,.0f}")

        st.divider()

        c1, c2 = st.columns([3, 2])
        with c1:
            st.subheader("Monthly variance")
            v = tgt.groupby(["PERIOD_YEAR", "PERIOD_MONTH"], as_index=False)["VARIANCE"].sum()
            v["MONTH"] = _month_str(v)
            v = v.sort_values("MONTH")
            v["COLOR"] = v["VARIANCE"].apply(lambda x: GOOD if x >= 0 else BAD)
            chart = (
                alt.Chart(v)
                .mark_bar()
                .encode(
                    x=alt.X("MONTH:N", title="Month", sort=v["MONTH"].tolist()),
                    y=alt.Y("VARIANCE:Q", title="Variance ($)", axis=alt.Axis(format="~s")),
                    color=alt.Color("COLOR:N", scale=None, legend=None),
                    tooltip=["MONTH", alt.Tooltip("VARIANCE:Q", format=",.0f")],
                )
                .properties(height=350)
            )
            _render_chart(chart)

        with c2:
            st.subheader("Variance by brand")
            bv = (
                tgt.groupby("SEGMENT", as_index=False)
                .agg(ACTUAL=("ACTUAL", "sum"), TARGET=("TARGET", "sum"))
                .melt("SEGMENT", var_name="MEASURE", value_name="VALUE")
            )
            chart = (
                alt.Chart(bv)
                .mark_bar()
                .encode(
                    x=alt.X("SEGMENT:N", title=None),
                    y=alt.Y("VALUE:Q", title="$", axis=alt.Axis(format="~s")),
                    color=alt.Color(
                        "MEASURE:N", scale=alt.Scale(range=[PALETTE[0], PALETTE[2]])
                    ),
                    tooltip=["SEGMENT", "MEASURE", alt.Tooltip("VALUE:Q", format=",.0f")],
                )
                .properties(height=350)
            )
            _render_chart(chart)

        st.subheader("Variance detail")
        detail = tgt.assign(
            VARIANCE_PCT=lambda d: (d["ACTUAL"] - d["TARGET"]) / d["TARGET"].where(d["TARGET"] != 0)
        ).sort_values(["PERIOD_YEAR", "PERIOD_MONTH", "SEGMENT"])
        st.dataframe(
            detail,
            use_container_width=True,
            hide_index=True,
            column_config={
                "ACTUAL": st.column_config.NumberColumn(format="$%.0f"),
                "TARGET": st.column_config.NumberColumn(format="$%.0f"),
                "VARIANCE": st.column_config.NumberColumn(format="$%.0f"),
                "YTD_ACTUAL": st.column_config.NumberColumn(format="$%.0f"),
                "YTD_TARGET": st.column_config.NumberColumn(format="$%.0f"),
                "VARIANCE_PCT": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )


# ---------------------------------------------------------------------------
# Page 3 — Year-over-Year
# ---------------------------------------------------------------------------

with tab_yoy:
    _active_filter_caption()
    where, args = filtered_where()
    yoy = run_query(
        f"""
        SELECT PERIOD_YEAR, PERIOD_MONTH, XYZ_REGION_1, SEGMENT, SUBCATEGORY,
               SUM(SALES_CY) AS SALES_CY,
               SUM(SALES_PY) AS SALES_PY,
               SUM(QTY_CY)   AS QTY_CY,
               SUM(QTY_PY)   AS QTY_PY,
               AVG(SALES_YOY_PCT) AS SALES_YOY_PCT,
               AVG(QTY_YOY_PCT)   AS QTY_YOY_PCT
        FROM <PILOT_SCHEMA>.MV_YOY_METRICS
        WHERE {where}
        GROUP BY 1,2,3,4,5
        """,
        tuple(args),
    )

    if yoy.empty:
        st.info("No data for the current filter selection.")
    else:
        total_cy = float(yoy["SALES_CY"].sum())
        total_py = float(yoy["SALES_PY"].sum())
        sales_yoy_pct = (total_cy - total_py) / total_py if total_py else 0
        total_qcy = float(yoy["QTY_CY"].sum())
        total_qpy = float(yoy["QTY_PY"].sum())
        qty_yoy_pct = (total_qcy - total_qpy) / total_qpy if total_qpy else 0

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Sales CY", f"${total_cy:,.0f}", delta=f"{sales_yoy_pct:.1%}")
        k2.metric("Sales PY", f"${total_py:,.0f}")
        k3.metric("Qty CY", f"{total_qcy:,.0f}", delta=f"{qty_yoy_pct:.1%}")
        k4.metric("Qty PY", f"{total_qpy:,.0f}")

        st.divider()

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("CY vs PY by brand")
            bb = (
                yoy.groupby("SEGMENT", as_index=False)
                .agg(CY=("SALES_CY", "sum"), PY=("SALES_PY", "sum"))
                .melt("SEGMENT", var_name="PERIOD", value_name="SALES")
            )
            chart = (
                alt.Chart(bb)
                .mark_bar()
                .encode(
                    x=alt.X("SEGMENT:N", title=None),
                    y=alt.Y("SALES:Q", title="$", axis=alt.Axis(format="~s")),
                    color=alt.Color(
                        "PERIOD:N", scale=alt.Scale(range=[PALETTE[1], PALETTE[2]])
                    ),
                    tooltip=["SEGMENT", "PERIOD", alt.Tooltip("SALES:Q", format=",.0f")],
                )
                .properties(height=350)
            )
            _render_chart(chart)

        with c2:
            st.subheader("YoY % trend")
            tr = yoy.groupby(["PERIOD_YEAR", "PERIOD_MONTH"], as_index=False)["SALES_YOY_PCT"].mean()
            tr["MONTH"] = _month_str(tr)
            tr = tr.sort_values("MONTH")
            chart = (
                alt.Chart(tr)
                .mark_line(point=True, strokeWidth=3, color=PALETTE[3])
                .encode(
                    x=alt.X("MONTH:N", title="Month", sort=tr["MONTH"].tolist()),
                    y=alt.Y("SALES_YOY_PCT:Q", title="YoY %", axis=alt.Axis(format="%")),
                    tooltip=["MONTH", alt.Tooltip("SALES_YOY_PCT:Q", format=".1%")],
                )
                .properties(height=350)
            )
            _render_chart(chart)

        st.subheader("Top movers")
        movers = (
            yoy.groupby(["SEGMENT", "SUBCATEGORY"], as_index=False)
            .agg(SALES_CY=("SALES_CY", "sum"), SALES_PY=("SALES_PY", "sum"))
        )
        movers["SALES_YOY_PCT"] = (movers["SALES_CY"] - movers["SALES_PY"]) / movers["SALES_PY"].where(
            movers["SALES_PY"] != 0
        )
        movers = movers.sort_values("SALES_YOY_PCT", ascending=False)
        st.dataframe(
            movers,
            use_container_width=True,
            hide_index=True,
            column_config={
                "SALES_CY": st.column_config.NumberColumn(format="$%.0f"),
                "SALES_PY": st.column_config.NumberColumn(format="$%.0f"),
                "SALES_YOY_PCT": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )


# ---------------------------------------------------------------------------
# Page 4 — Inventory
# ---------------------------------------------------------------------------

with tab_inv:
    # XYZ-PILOT-058: Inventory previously took ONLY the Brand filter and silently
    # ignored Year + Region. Year is now applied via EXTRACT(YEAR) from the
    # snapshot date; Region genuinely doesn't apply (no region column on
    # MV_STOCK_ANALYTICS — inventory is global, not regional). The
    # caption below tells the user exactly what's in scope.
    _active_filter_caption(year_label="Year (from INVENTORY_DATE)", show_region=False)
    st.caption(
        "_Inventory snapshots are not regional — the Region filter doesn't apply on this tab._"
    )

    inv_parts: list[str] = [_in_clause(sel_brands, "SEGMENT")]
    if sel_years and len(sel_years) < len(opts["years"]):
        years_sql = ",".join(str(int(y)) for y in sel_years)
        inv_parts.append(f"EXTRACT(YEAR FROM INVENTORY_DATE) IN ({years_sql})")
    where_inv = " AND ".join(inv_parts)

    inv = run_query(
        f"""
        SELECT INVENTORY_DATE, SEGMENT, SUBCATEGORY,
               SUM(QTY_ON_HAND)   AS QOH,
               SUM(QTY_AVAILABLE) AS AVAIL,
               SUM(INV_COST)      AS INV_COST,
               SUM(QTY_SOLD)      AS QTY_SOLD,
               AVG(INV_VALUE_TURNS) AS TURNS,
               AVG(COVERAGE_DAYS)   AS COVERAGE
        FROM <PILOT_SCHEMA>.MV_STOCK_ANALYTICS
        WHERE {where_inv}
        GROUP BY 1,2,3
        """
    )

    if inv.empty:
        st.info("No inventory data for the current filter selection.")
    else:
        latest = inv[inv["INVENTORY_DATE"] == inv["INVENTORY_DATE"].max()]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Current QoH", f"{float(latest['QOH'].sum()):,.0f}")
        k2.metric("Current Inv $", f"${float(latest['INV_COST'].sum()):,.0f}")
        k3.metric("Avg Turns", f"{float(latest['TURNS'].mean() or 0):.2f}×")
        k4.metric("Coverage Days", f"{float(latest['COVERAGE'].mean() or 0):.0f}d")

        st.divider()

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("QoH trend by brand")
            qoh = inv.groupby(["INVENTORY_DATE", "SEGMENT"], as_index=False)["QOH"].sum()
            chart = (
                alt.Chart(qoh)
                .mark_area(opacity=0.7)
                .encode(
                    x=alt.X("INVENTORY_DATE:T", title="Date"),
                    y=alt.Y("QOH:Q", title="Qty on hand", axis=alt.Axis(format="~s")),
                    color=alt.Color("SEGMENT:N", scale=alt.Scale(range=PALETTE)),
                    tooltip=["INVENTORY_DATE", "SEGMENT", alt.Tooltip("QOH:Q", format=",.0f")],
                )
                .properties(height=350)
            )
            _render_chart(chart)

        with c2:
            st.subheader("Inventory vs sales (latest)")
            scatter = (
                alt.Chart(latest)
                .mark_circle(size=180, opacity=0.75)
                .encode(
                    x=alt.X("QOH:Q", title="Qty on hand", axis=alt.Axis(format="~s")),
                    y=alt.Y("QTY_SOLD:Q", title="Qty sold (rolling)", axis=alt.Axis(format="~s")),
                    color=alt.Color("SEGMENT:N", scale=alt.Scale(range=PALETTE)),
                    size=alt.Size("INV_COST:Q", legend=None),
                    tooltip=[
                        "SEGMENT",
                        "SUBCATEGORY",
                        alt.Tooltip("QOH:Q", format=",.0f"),
                        alt.Tooltip("QTY_SOLD:Q", format=",.0f"),
                    ],
                )
                .properties(height=350)
            )
            _render_chart(scatter)

        st.subheader("Brand inventory (latest snapshot)")
        st.dataframe(
            latest.sort_values("SEGMENT"),
            use_container_width=True,
            hide_index=True,
            column_config={
                "QOH": st.column_config.NumberColumn(format="%.0f"),
                "AVAIL": st.column_config.NumberColumn(format="%.0f"),
                "INV_COST": st.column_config.NumberColumn(format="$%.0f"),
                "QTY_SOLD": st.column_config.NumberColumn(format="%.0f"),
                "TURNS": st.column_config.NumberColumn(format="%.2f×"),
                "COVERAGE": st.column_config.NumberColumn(format="%.0fd"),
            },
        )


# ---------------------------------------------------------------------------
# Page 5 — ESL helper metrics
# ---------------------------------------------------------------------------

with tab_esl:
    _active_filter_caption(year_label="Calendar Year")
    st.caption(
        "XYZ-owned helper view backed by a refreshed external semantic layer BASE_LAYER snapshot: "
        "`<PILOT_SCHEMA>.V_BUSINESS_METRICS`."
    )
    where, args = filtered_where_esl()
    esl = run_query(
        f"""
        SELECT CALENDAR_YEAR, CALENDAR_MONTH, MONTH_LABEL, QUARTER_LABEL,
               XYZ_REGION_1, XYZ_REGION_2, SEGMENT, SUBCATEGORY, PRODUCT_LINE_NAME,
               MONTHLY_VALUE_USD, MONTHLY_COGS_USD, MONTHLY_GROSS_MARGIN_USD,
               MONTHLY_QTY_SHIPPED, GROSS_MARGIN_PCT, YTD_VALUE_USD,
               YTD_QTY_SHIPPED, ROLLING_3M_VALUE_USD, ROLLING_6M_VALUE_USD,
               SALES_PCT_OF_BRAND, SALES_PCT_OF_REGION,
               SALES_PY_USD, SALES_YOY_VAR_USD, SALES_YOY_PCT
        FROM <PILOT_SCHEMA>.V_BUSINESS_METRICS
        WHERE {where}
        """,
        tuple(args),
    )

    if esl.empty:
        st.info("No ESL helper metric rows for the current filter selection.")
    else:
        esl["MONTH_SORT"] = (
            esl["CALENDAR_YEAR"].astype(int).astype(str)
            + "-"
            + esl["CALENDAR_MONTH"].astype(int).astype(str).str.zfill(2)
        )
        total_sales = float(esl["MONTHLY_VALUE_USD"].sum())
        total_gm = float(esl["MONTHLY_GROSS_MARGIN_USD"].sum())
        gm_pct = total_gm / total_sales if total_sales else 0
        latest_period = esl.sort_values(["CALENDAR_YEAR", "CALENDAR_MONTH"], ascending=False)
        latest_year = int(latest_period["CALENDAR_YEAR"].iloc[0])
        latest_month = int(latest_period["CALENDAR_MONTH"].iloc[0])
        latest = esl[
            (esl["CALENDAR_YEAR"].astype(int) == latest_year)
            & (esl["CALENDAR_MONTH"].astype(int) == latest_month)
        ]
        latest_ytd = float(latest["YTD_VALUE_USD"].sum())
        yoy_base = float(esl["SALES_PY_USD"].fillna(0).sum())
        yoy_pct = (total_sales - yoy_base) / yoy_base if yoy_base else 0

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("ESL Sales", f"${total_sales:,.0f}")
        k2.metric("ESL Gross Margin %", f"{gm_pct:.1%}")
        k3.metric("Latest ESL YTD", f"${latest_ytd:,.0f}")
        k4.metric("ESL YoY %", f"{yoy_pct:.1%}")

        st.divider()

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("ESL monthly sales")
            trend = (
                esl.groupby(["CALENDAR_YEAR", "CALENDAR_MONTH", "MONTH_LABEL"], as_index=False)
                .agg(SALES=("MONTHLY_VALUE_USD", "sum"))
                .sort_values(["CALENDAR_YEAR", "CALENDAR_MONTH"])
            )
            chart = (
                alt.Chart(trend)
                .mark_line(point=True, strokeWidth=3, color=PALETTE[0])
                .encode(
                    x=alt.X("MONTH_LABEL:N", title="Month", sort=trend["MONTH_LABEL"].tolist()),
                    y=alt.Y("SALES:Q", title="Sales ($)", axis=alt.Axis(format="~s")),
                    tooltip=["MONTH_LABEL", alt.Tooltip("SALES:Q", format=",.0f")],
                )
                .properties(height=320)
            )
            _render_chart(chart)

        with c2:
            st.subheader("ESL YoY % trend")
            yoy_trend = (
                esl.groupby(["CALENDAR_YEAR", "CALENDAR_MONTH", "MONTH_LABEL"], as_index=False)
                .agg(SALES=("MONTHLY_VALUE_USD", "sum"), PY=("SALES_PY_USD", "sum"))
                .sort_values(["CALENDAR_YEAR", "CALENDAR_MONTH"])
            )
            yoy_trend["YOY_PCT"] = (
                (yoy_trend["SALES"] - yoy_trend["PY"]) / yoy_trend["PY"].where(yoy_trend["PY"] != 0)
            )
            chart = (
                alt.Chart(yoy_trend)
                .mark_line(point=True, strokeWidth=3, color=PALETTE[3])
                .encode(
                    x=alt.X("MONTH_LABEL:N", title="Month", sort=yoy_trend["MONTH_LABEL"].tolist()),
                    y=alt.Y("YOY_PCT:Q", title="YoY %", axis=alt.Axis(format="%")),
                    tooltip=["MONTH_LABEL", alt.Tooltip("YOY_PCT:Q", format=".1%")],
                )
                .properties(height=320)
            )
            _render_chart(chart)

        c3, c4 = st.columns(2)
        with c3:
            st.subheader("ESL product-line mix")
            product_mix = (
                esl.groupby(["SEGMENT", "SUBCATEGORY"], as_index=False)["MONTHLY_VALUE_USD"]
                .sum()
                .sort_values("MONTHLY_VALUE_USD", ascending=False)
                .head(20)
            )
            chart = (
                alt.Chart(product_mix)
                .mark_bar(color=PALETTE[2])
                .encode(
                    y=alt.Y("SUBCATEGORY:N", sort="-x", title="Product line"),
                    x=alt.X(
                        "MONTHLY_VALUE_USD:Q",
                        title="Sales ($)",
                        axis=alt.Axis(format="~s"),
                    ),
                    color=alt.Color("SEGMENT:N", scale=alt.Scale(range=PALETTE)),
                    tooltip=[
                        "SEGMENT",
                        "SUBCATEGORY",
                        alt.Tooltip("MONTHLY_VALUE_USD:Q", format=",.0f"),
                    ],
                )
                .properties(height=320)
            )
            _render_chart(chart)

        with c4:
            st.subheader("ESL share by region")
            region = (
                esl.groupby("XYZ_REGION_1", as_index=False)["MONTHLY_VALUE_USD"]
                .sum()
                .sort_values("MONTHLY_VALUE_USD", ascending=True)
            )
            chart = (
                alt.Chart(region)
                .mark_bar(color=PALETTE[4])
                .encode(
                    y=alt.Y("XYZ_REGION_1:N", sort="-x", title="Region"),
                    x=alt.X(
                        "MONTHLY_VALUE_USD:Q",
                        title="Sales ($)",
                        axis=alt.Axis(format="~s"),
                    ),
                    tooltip=[
                        "XYZ_REGION_1",
                        alt.Tooltip("MONTHLY_VALUE_USD:Q", format=",.0f"),
                    ],
                )
                .properties(height=320)
            )
            _render_chart(chart)

        st.subheader("ESL helper metric rows")
        detail_cols = [
            "CALENDAR_YEAR",
            "CALENDAR_MONTH",
            "XYZ_REGION_1",
            "SEGMENT",
            "SUBCATEGORY",
            "MONTHLY_VALUE_USD",
            "GROSS_MARGIN_PCT",
            "YTD_VALUE_USD",
            "ROLLING_3M_VALUE_USD",
            "ROLLING_6M_VALUE_USD",
            "SALES_YOY_PCT",
        ]
        st.dataframe(
            esl[detail_cols].sort_values(
                ["CALENDAR_YEAR", "CALENDAR_MONTH", "XYZ_REGION_1", "SEGMENT", "SUBCATEGORY"]
            ),
            use_container_width=True,
            hide_index=True,
            column_config={
                "MONTHLY_VALUE_USD": st.column_config.NumberColumn(format="$%.0f"),
                "GROSS_MARGIN_PCT": st.column_config.NumberColumn(format="%.1f%%"),
                "YTD_VALUE_USD": st.column_config.NumberColumn(format="$%.0f"),
                "ROLLING_3M_VALUE_USD": st.column_config.NumberColumn(format="$%.0f"),
                "ROLLING_6M_VALUE_USD": st.column_config.NumberColumn(format="$%.0f"),
                "SALES_YOY_PCT": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )


# ---------------------------------------------------------------------------
# Page 6 — Data inventory (every uploaded <PILOT_SCHEMA> table + view)
# ---------------------------------------------------------------------------

with tab_data:
    st.subheader("<PILOT_SCHEMA> — schema inventory")
    st.caption(
        "All tables and views in the <PILOT_SCHEMA> schema. Table row counts come "
        "from Exasol metadata; view row counts run only through the explicit "
        "COUNT(*) action below."
    )

    inventory = run_query(
        """
        SELECT OBJECT_NAME, OBJECT_TYPE, ROW_COUNT FROM (
            SELECT TABLE_NAME AS OBJECT_NAME,
                   CAST('TABLE' AS VARCHAR(8)) AS OBJECT_TYPE,
                   TABLE_ROW_COUNT AS ROW_COUNT
            FROM EXA_ALL_TABLES
            WHERE TABLE_SCHEMA = '<PILOT_SCHEMA>'
            UNION ALL
            SELECT VIEW_NAME,
                   CAST('VIEW' AS VARCHAR(8)),
                   CAST(NULL AS DECIMAL(18,0))
            FROM EXA_ALL_VIEWS
            WHERE VIEW_SCHEMA = '<PILOT_SCHEMA>'
        ) inv
        ORDER BY OBJECT_TYPE, OBJECT_NAME
        """
    )

    if inventory.empty:
        st.info("No objects found in <PILOT_SCHEMA> schema.")
    else:
        # Reserved-word table names need quoting; everything else doesn't.
        def _qual(name: str) -> str:
            return f'<PILOT_SCHEMA>."{name}"' if name.upper() in {"DATE", "USER"} else f"<PILOT_SCHEMA>.{name}"

        col_counts = run_query(
            """
            SELECT COLUMN_TABLE AS OBJECT_NAME, COUNT(*) AS COLUMN_COUNT
            FROM EXA_ALL_COLUMNS
            WHERE COLUMN_SCHEMA = '<PILOT_SCHEMA>'
            GROUP BY COLUMN_TABLE
            """
        ).set_index("OBJECT_NAME")["COLUMN_COUNT"]
        inventory["COLUMN_COUNT"] = inventory["OBJECT_NAME"].map(col_counts).fillna(0).astype(int)
        merged = inventory.copy()
        merged["ROW_COUNT_NUMERIC"] = pd.to_numeric(merged["ROW_COUNT"], errors="coerce")
        merged["ROW_COUNT_DISPLAY"] = merged["ROW_COUNT_NUMERIC"].map(
            lambda value: f"{int(value):,}" if pd.notna(value) else "not counted"
        )
        table_rows = int(
            merged.loc[merged["OBJECT_TYPE"] == "TABLE", "ROW_COUNT_NUMERIC"].fillna(0).sum()
        )

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Tables", int((merged["OBJECT_TYPE"] == "TABLE").sum()))
        k2.metric("Views", int((merged["OBJECT_TYPE"] == "VIEW").sum()))
        k3.metric("Table rows", f"{table_rows:,}")
        largest = merged.sort_values("ROW_COUNT_NUMERIC", ascending=False, na_position="last").iloc[0]
        k4.metric("Largest table", f"{largest['OBJECT_NAME']} ({largest['ROW_COUNT_DISPLAY']})")

        st.divider()

        st.dataframe(
            merged.drop(columns=["ROW_COUNT"]).sort_values(
                ["OBJECT_TYPE", "ROW_COUNT_NUMERIC"],
                ascending=[True, False],
                na_position="last",
            ),
            use_container_width=True,
            hide_index=True,
            column_config={
                "OBJECT_NAME": st.column_config.TextColumn("Object", width="medium"),
                "OBJECT_TYPE": st.column_config.TextColumn("Type", width="small"),
                "COLUMN_COUNT": st.column_config.NumberColumn("Cols", format="%d"),
                "ROW_COUNT_NUMERIC": None,
                "ROW_COUNT_DISPLAY": st.column_config.TextColumn("Rows", width="small"),
            },
        )

        st.divider()
        st.subheader("Interactive query — pick an object, COUNT(*) or sample rows")

        choices = merged["OBJECT_NAME"].tolist()
        default_ix = choices.index("FACT_PRIMARY") if "FACT_PRIMARY" in choices else 0

        col_pick, col_action, col_limit = st.columns([2, 1, 1])
        with col_pick:
            chosen = st.selectbox("Object", choices, index=default_ix, key="data_pick")
        with col_action:
            action = st.radio(
                "Action",
                ["COUNT(*)", "Sample rows"],
                horizontal=False,
                key="data_action",
            )
        with col_limit:
            row_limit = st.number_input(
                "Max rows (1-100)",
                min_value=1,
                max_value=100,
                value=10,
                step=1,
                disabled=(action != "Sample rows"),
                key="data_limit",
            )

        where_clause = st.text_input(
            "Optional WHERE clause (no leading WHERE; XYZ_DASH is read-only and locked to <PILOT_SCHEMA>)",
            value="",
            placeholder="e.g.  PERIOD_YEAR = 2025 AND SEGMENT = '<CUSTOMER_NAME>'",
            key="data_where",
        ).strip()

        # Defence-in-depth: XYZ_DASH only has SELECT, but reject obvious abuse
        # patterns in the user-supplied WHERE so a typo doesn't trip the parser
        # in confusing ways.
        forbidden = (";", "--", "/*", "*/")
        where_safe = where_clause and not any(t in where_clause for t in forbidden)
        if where_clause and not where_safe:
            st.warning(
                "WHERE clause contains forbidden tokens (`;`, `--`, `/*`, `*/`). "
                "Provide a single boolean expression only."
            )

        run = st.button("▶ Run", type="primary", key="data_run")

        if run and (not where_clause or where_safe):
            qual = _qual(chosen)
            where_sql = f" WHERE {where_clause}" if where_clause else ""
            try:
                if action == "COUNT(*)":
                    df = run_query(f"SELECT COUNT(*) AS ROW_COUNT FROM {qual}{where_sql}")
                    n = int(df.iloc[0, 0])
                    st.metric(f"COUNT(*) on {chosen}", f"{n:,}")
                    st.caption(f"Executed: `SELECT COUNT(*) FROM {qual}{where_sql}`")
                else:
                    df = run_query(
                        f"SELECT * FROM {qual}{where_sql} LIMIT {int(row_limit)}"
                    )
                    # XYZ-PILOT-140: AgGrid for sortable / filterable / resizable
                    # data table with CSV export. Falls back to st.dataframe if
                    # streamlit-aggrid not installed.
                    try:
                        from st_aggrid import AgGrid, GridOptionsBuilder  # type: ignore
                        gob = GridOptionsBuilder.from_dataframe(df)
                        gob.configure_default_column(
                            sortable=True, filter=True, resizable=True,
                            wrapText=False, autoHeight=False,
                        )
                        gob.configure_pagination(paginationAutoPageSize=False, paginationPageSize=50)
                        gob.configure_side_bar(filters_panel=True, columns_panel=True)
                        gob.configure_selection("multiple", use_checkbox=False)
                        AgGrid(df, gridOptions=gob.build(),
                               allow_unsafe_jscode=False, fit_columns_on_grid_load=True,
                               theme="alpine", height=520,
                               key=f"aggrid_{chosen}_{int(row_limit)}")
                    except ImportError:
                        st.dataframe(df, use_container_width=True, hide_index=True)
                    st.caption(
                        f"Executed: `SELECT * FROM {qual}{where_sql} LIMIT {int(row_limit)}` · "
                        f"{len(df)} rows · {len(df.columns)} cols"
                    )
            except Exception as exc:  # noqa: BLE001
                with st.expander("Query error", expanded=True):
                    st.error(f"{type(exc).__name__}: {str(exc)[:400]}")

st.divider()
st.caption(CONFIDENTIAL_NOTICE)
