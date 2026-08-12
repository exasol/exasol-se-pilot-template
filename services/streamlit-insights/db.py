"""Cached Exasol connection helpers for the QBR Streamlit app."""

from __future__ import annotations

import os
import threading
from typing import Any

import pandas as pd
import pyexasol
import streamlit as st

_LOCK = threading.Lock()


def _config() -> dict[str, Any]:
    host = os.environ["EXASOL_HOST"]
    port = os.environ.get("EXASOL_PORT", "8563")
    encryption = os.environ.get("EXASOL_TLS", "true").lower() == "true"
    cfg: dict[str, Any] = {
        "dsn": f"{host}:{port}",
        "user": os.environ["EXASOL_USER"],
        "password": os.environ["EXASOL_PASSWORD"],
        "schema": os.environ.get("EXASOL_SCHEMA", "<PILOT_SCHEMA>"),
        "encryption": encryption,
        "compression": True,
    }
    if encryption:
        # Exasol Personal ships a self-signed cert; skip CA verification.
        cfg["websocket_sslopt"] = {"cert_reqs": 0}
    return cfg


@st.cache_resource(show_spinner="Connecting to Exasol…")
def get_connection() -> pyexasol.ExaConnection:
    cfg = _config()
    return pyexasol.connect(**cfg)


@st.cache_data(ttl=1800, show_spinner="Querying Exasol…")
def run_query(sql: str, params: tuple | None = None) -> pd.DataFrame:
    """Run a SQL query and return a pandas DataFrame.

    Uses pyexasol's plain WebSocket fetch path (`execute().fetchall()`) rather
    than `export_to_pandas`, which uses an EXPORT-over-HTTP transport with
    ~1.2 s of fixed overhead per call to set up a transient HTTP listener.
    The dashboard queries hit MV_* snapshots returning < 1 500 rows; the
    overhead dominates the actual query time and stalls the UI on every
    filter change. The plain-fetch path is ~50× faster at this scale.
    See XYZ-PILOT-029 in docs/XYZ_PILOT_ISSUE_TRACKER.md.

    `params` was historically a tuple of values for ?-placeholders, but the
    SQL above is now built with inline IN-list values (vetted against the DB
    catalog before substitution), so params is unused — kept in the signature
    so the cache key doesn't change for already-cached calls.
    """
    del params
    conn = get_connection()
    with _LOCK:
        statement = conn.execute(sql)
        columns = list(statement.column_names())
        rows = statement.fetchall()
    df = pd.DataFrame(rows, columns=columns)
    # pyexasol returns wide-precision DECIMAL as Python str (to avoid float
    # precision loss). Without coercion, str-valued columns end up with a
    # non-numeric dtype and `.sum()` concatenates them as strings instead of
    # summing numbers. Same root cause as XYZ-PILOT-015 in the Grafana proxy
    # (where _json_safe gained string→numeric coercion); XYZ-PILOT-030 here.
    #
    # We don't filter on dtype because newer pandas reports
    # Python-string columns variously as 'object', 'str', or 'string[python]'
    # depending on extension-types configuration. Cheapest correct approach:
    # try numeric coercion on every column; pd.to_numeric raises for
    # genuinely non-numeric content (SEGMENT, REGION names, etc.) — caught and
    # ignored, leaving the column as-is.
    for col in df.columns:
        try:
            df[col] = pd.to_numeric(df[col])
        except (ValueError, TypeError):
            pass
    return df


def clear_query_cache() -> None:
    run_query.clear()
