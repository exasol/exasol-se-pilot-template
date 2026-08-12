"""Tiny Exasol -> JSON HTTP proxy for the Grafana Infinity datasource.

Exposes one read-only GET endpoint that runs a SQL query against Exasol with
XYZ_DASH credentials and returns the result as a JSON array of records.

Auth: shared bearer token in the PROXY_TOKEN env var. Required on every
request via the Authorization header.

Connection: pyexasol with TLS + skip-cert (self-signed Exasol Personal cert).
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import threading
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyexasol
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("exasol_proxy")

REQUIRED_ENV = ["EXASOL_HOST", "EXASOL_USER", "EXASOL_PASSWORD", "PROXY_TOKEN"]
for var in REQUIRED_ENV:
    if not os.environ.get(var):
        raise RuntimeError(f"missing required env var: {var}")

EXASOL_HOST = os.environ["EXASOL_HOST"]
EXASOL_PORT = os.environ.get("EXASOL_PORT", "8563")
EXASOL_USER = os.environ["EXASOL_USER"]
EXASOL_PASSWORD = os.environ["EXASOL_PASSWORD"]
EXASOL_SCHEMA = os.environ.get("EXASOL_SCHEMA", "<PILOT_SCHEMA>")
PROXY_TOKEN = os.environ["PROXY_TOKEN"]

LICENSE_SCRIPT = Path(os.environ.get("EXASOL_LICENSE_SCRIPT", "/app/exasol-personal-license-introspect.sh"))
LICENSE_NODE = os.environ.get("EXASOL_LICENSE_NODE", "")
LICENSE_SSH_KEY = os.environ.get("EXASOL_LICENSE_SSH_KEY", "")
LICENSE_STATE_DIR = os.environ.get("EXASOL_LICENSE_STATE_DIR", "")
LICENSE_ARTIFACT_DIR = Path(os.environ.get("EXASOL_LICENSE_ARTIFACT_DIR", "/tmp/exasol-license-introspect"))
LICENSE_TIMEOUT_SECONDS = int(os.environ.get("EXASOL_LICENSE_TIMEOUT_SECONDS", "90"))
LICENSE_REFRESH_ON_STARTUP = os.environ.get("EXASOL_LICENSE_REFRESH_ON_STARTUP", "1").lower() not in {
    "0",
    "false",
    "no",
    "off",
}

# pyexasol connections are NOT thread-safe (raises ExaConcurrencyError when two
# requests touch the same connection). FastAPI runs sync endpoints in a thread
# pool, so a Grafana dashboard with 8 panels firing at once would otherwise
# collide. Serialise all queries on one connection via a process-wide lock —
# Exasol queries here are sub-100ms, so the queue is invisible to the user.
_conn: pyexasol.ExaConnection | None = None
_conn_lock = threading.Lock()
_license_lock = threading.Lock()
_license_rows: list[dict[str, str]] = []


def _connection() -> pyexasol.ExaConnection:
    global _conn
    if _conn is None or _conn.is_closed:
        log.info("opening pyexasol connection to %s:%s as %s", EXASOL_HOST, EXASOL_PORT, EXASOL_USER)
        _conn = pyexasol.connect(
            dsn=f"{EXASOL_HOST}:{EXASOL_PORT}",
            user=EXASOL_USER,
            password=EXASOL_PASSWORD,
            schema=EXASOL_SCHEMA,
            encryption=True,
            websocket_sslopt={"cert_reqs": 0},
            compression=True,
        )
    return _conn


def _json_safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime, time)):
        return v.isoformat()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, str):
        # pyexasol returns wide-precision DECIMAL columns as Python str to
        # avoid float-precision loss. Grafana's Infinity datasource then types
        # the field as "string" and Stat panels render "No Data" because they
        # expect numeric values. If the string parses cleanly as a number,
        # return the numeric form so panels render. Otherwise leave it alone.
        try:
            return int(v) if "." not in v and "e" not in v.lower() else float(v)
        except (ValueError, TypeError):
            return v
    return v


def _check_auth(authorization: str | None) -> None:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing Authorization header")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="expected Bearer token")
    if parts[1] != PROXY_TOKEN:
        raise HTTPException(status_code=403, detail="bad token")


def _license_row(section: str, field: str, value: str, key: str = "") -> dict[str, str]:
    row = {"SECTION": section, "FIELD": field, "VALUE": value}
    if key:
        row["KEY"] = key
    return row


def _license_status_rows(status: str, detail: str = "") -> list[dict[str, str]]:
    rows = [
        _license_row("Runtime", "Status", status, "status"),
        _license_row(
            "Runtime",
            "Last checked UTC",
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "last_checked_utc",
        ),
    ]
    if detail:
        rows.append(_license_row("Runtime", "Detail", detail, "detail"))
    return rows


def _configured_for_license_refresh() -> bool:
    return bool(LICENSE_STATE_DIR or (LICENSE_NODE and LICENSE_SSH_KEY))


def _latest_license_artifact() -> Path | None:
    candidates = list(LICENSE_ARTIFACT_DIR.glob("exasol-introspect-*/evidence/confd_license_info.txt"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _parse_confd_license(text: str, artifact: Path) -> list[dict[str, str]]:
    values: dict[str, str] = {}
    section = ""
    block = ""
    current_key = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line == "...":
            continue
        if line.startswith("### "):
            key, _, value = line[4:].partition("=")
            values[key.strip()] = value.strip()
            continue
        if line.startswith("====="):
            section = line.strip("= ").replace("confd_client ", "")
            block = ""
            current_key = ""
            continue
        if section == "license_info" and not raw_line.startswith(" ") and line.endswith(":"):
            block = line[:-1]
            current_key = ""
            continue
        if raw_line.startswith("  ") and ":" in line:
            key, _, value = line.partition(":")
            values[key] = value.strip().strip("'\"")
            values[f"{key}__section"] = block or section
            current_key = key
            continue
        if raw_line.startswith("    ") and current_key:
            values[current_key] = f"{values.get(current_key, '').rstrip()} {line}".strip()
            continue
        if section == "license_run_check":
            values["license_run_check"] = line
            continue
        if section.startswith("db_state"):
            values["db_state"] = line

    field_order = [
        ("Runtime", "Status", "status", "OK"),
        ("Runtime", "Last checked UTC", "timestamp_utc", values.get("timestamp_utc", "")),
        ("ConfD", "Source", "source", "ConfD license_info"),
        ("ConfD", "Source artifact", "source_artifact", str(artifact)),
        ("ConfD", "Play ID", "play_id", values.get("play_id", "")),
        ("ConfD", "Database", "db_name", values.get("db_name", "")),
        ("Contract", "License ID", "license_id", values.get("license_id", "")),
        ("Contract", "Company", "company_name", values.get("company_name", "")),
        ("Contract", "Distributor", "distributor", values.get("distributor", "")),
        ("Contract", "Distributor ID", "distributor_id", values.get("distributor_id", "")),
        ("Contract", "Expiration date", "expiration_date", values.get("expiration_date", "")),
        ("Contract", "Comment / service tag", "comment", values.get("comment", "")),
        ("Limits", "Max session limit", "max_session_limit", values.get("max_session_limit", "")),
        ("Limits", "Max DB memory size", "max_db_mem_size_in_gb", values.get("max_db_mem_size_in_gb", "")),
        ("Limits", "Max DB raw data size", "max_db_raw_data_size_in_gb", values.get("max_db_raw_data_size_in_gb", "")),
        ("Limits", "Max nodes per cluster", "max_nodes_per_cluster", values.get("max_nodes_per_cluster", "")),
        ("Limits", "Max clusters", "max_num_clusters", values.get("max_num_clusters", "")),
        ("Exasol_DB_license", "Schema version", "schema_version", values.get("schema_version", "")),
        ("Check", "License run check", "license_run_check", values.get("license_run_check", "")),
        ("Check", "DB state", "db_state", values.get("db_state", "")),
    ]
    return [
        _license_row(section_name, field, value, key)
        for section_name, field, key, value in field_order
        if value
    ]


def _stale_license_rows(detail: str) -> list[dict[str, str]] | None:
    artifact = _latest_license_artifact()
    if artifact is None:
        return None
    try:
        rows = _parse_confd_license(artifact.read_text(encoding="utf-8"), artifact)
    except OSError:
        log.exception("could not read stale license artifact %s", artifact)
        return None
    stale = [
        _license_row("Runtime", "Status", "stale", "status"),
        _license_row(
            "Runtime",
            "Last refresh attempt UTC",
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "last_refresh_attempt_utc",
        ),
        _license_row("Runtime", "Detail", detail, "detail"),
    ]
    return stale + [row for row in rows if row.get("FIELD") != "Status"]


def _license_error_rows(detail: str) -> list[dict[str, str]]:
    return _stale_license_rows(detail) or _license_status_rows("error", detail)


def _run_license_introspection() -> list[dict[str, str]]:
    if not _configured_for_license_refresh():
        return _stale_license_rows(
            "license refresh is not configured; showing most recent known ConfD details",
        ) or _license_status_rows(
            "not_configured",
            "Set EXASOL_LICENSE_NODE and EXASOL_LICENSE_SSH_KEY_PATH, or EXASOL_LICENSE_STATE_DIR.",
        )

    LICENSE_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    command = [str(LICENSE_SCRIPT), "--non-interactive"]
    if LICENSE_STATE_DIR:
        command.extend(["--state-dir", LICENSE_STATE_DIR])
    if LICENSE_NODE:
        command.extend(["--node", LICENSE_NODE])
    if LICENSE_SSH_KEY:
        command.extend(["--ssh-key", LICENSE_SSH_KEY])

    log.info("refreshing Exasol license details via %s", LICENSE_SCRIPT)
    try:
        completed = subprocess.run(
            command,
            cwd=LICENSE_ARTIFACT_DIR,
            check=False,
            capture_output=True,
            text=True,
            timeout=LICENSE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        log.exception("license introspection timed out after %ss", LICENSE_TIMEOUT_SECONDS)
        return _license_error_rows("license introspection timed out")
    except Exception as exc:
        log.exception("license introspection failed")
        return _license_error_rows(f"license introspection failed: {type(exc).__name__}")

    if completed.returncode != 0:
        log.error("license introspection exited with %s", completed.returncode)
        return _license_error_rows(f"license introspection exited with {completed.returncode}")

    artifact = _latest_license_artifact()
    if artifact is None:
        return _license_error_rows("license introspection produced no confd_license_info.txt")

    return _parse_confd_license(artifact.read_text(encoding="utf-8"), artifact)


def _refresh_license_cache() -> None:
    global _license_rows
    with _license_lock:
        _license_rows = _run_license_introspection()


def _license_export_payload(rows: list[dict[str, str]]) -> dict[str, Any]:
    artifact = _latest_license_artifact()
    payload: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "Grafana exasol_proxy / ConfD license_info",
        "rows": rows,
        "contract": {
            row["KEY"]: row["VALUE"]
            for row in rows
            if row.get("SECTION") == "Contract" and row.get("KEY")
        },
        "limits": {
            row["KEY"]: row["VALUE"]
            for row in rows
            if row.get("SECTION") == "Limits" and row.get("KEY")
        },
    }
    if artifact is not None:
        payload["source_artifact"] = str(artifact)
        try:
            payload["raw_confd_license_info"] = artifact.read_text(encoding="utf-8")
        except OSError:
            log.exception("could not read license artifact %s for export", artifact)
    return payload


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if LICENSE_REFRESH_ON_STARTUP:
        _refresh_license_cache()
    else:
        with _license_lock:
            _license_rows[:] = _license_status_rows("disabled", "EXASOL_LICENSE_REFRESH_ON_STARTUP is disabled")
    yield


app = FastAPI(title="XYZ Exasol Proxy", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with _conn_lock:
            _connection().execute("SELECT 1").fetchall()
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"db: {type(exc).__name__}")


@app.get("/query")
def run_query(
    sql: str = Query(..., min_length=1, max_length=20000),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    _check_auth(authorization)

    stripped = sql.strip().rstrip(";").upper()
    if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
        raise HTTPException(status_code=400, detail="only SELECT / WITH queries allowed")

    with _conn_lock:
        conn = _connection()
        statement = conn.execute(sql)
        columns = list(statement.column_names())
        rows = statement.fetchall()
    payload = [
        {col: _json_safe(val) for col, val in zip(columns, row)}
        for row in rows
    ]
    return JSONResponse(content=payload)


@app.get("/license")
def license_info(
    refresh: bool = Query(False),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    _check_auth(authorization)
    if refresh:
        _refresh_license_cache()
    with _license_lock:
        payload = list(_license_rows) or _license_status_rows("not_checked")
    return JSONResponse(content=payload)


@app.get("/license/export")
def license_export(
    refresh: bool = Query(False),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    # XYZ-PILOT-289 follow-up: Grafana text-panel click-through links hit this
    # endpoint through the datasource proxy path, but some Grafana/proxy paths
    # do not forward datasource auth headers for browser navigation requests.
    # Export payload is non-secret metadata only, so we allow unauthenticated
    # access here and keep strict auth on write/sensitive paths.
    if authorization:
        _check_auth(authorization)
    if refresh:
        _refresh_license_cache()
    with _license_lock:
        rows = list(_license_rows) or _license_status_rows("not_checked")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return JSONResponse(
        content=_license_export_payload(rows),
        headers={"Content-Disposition": f'attachment; filename="xyz-exasol-license-confd-{timestamp}.json"'},
    )


@app.get("/license/import-plan")
def license_import_plan(authorization: str | None = Header(default=None)) -> JSONResponse:
    # Read-only static plan (no secrets); keep auth optional for Grafana
    # browser click-through compatibility.
    if authorization:
        _check_auth(authorization)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return JSONResponse(
        content=[
            _license_row("Boundary", "Direct Grafana import", "disabled", "direct_grafana_import"),
            _license_row(
                "Boundary",
                "Reason",
                "Grafana has no safe operator-approved file upload path for Exasol license rotation.",
                "reason",
            ),
            _license_row(
                "Step",
                "1. Export current license",
                "Use /license/export?refresh=true and scripts/exasol-personal-license-export.sh before any rotation attempt.",
                "export_current_license",
            ),
            _license_row(
                "Step",
                "2. Stage replacement license",
                "Use scripts/exasol-personal-license-rotate.sh with --license-file first; keep the file out of shared logs.",
                "stage_replacement_license",
            ),
            _license_row(
                "Step",
                "3. Validate guarded apply path",
                "Re-import the exported current license with --apply --confirm-apply before rotating to a new license.",
                "apply_approved_path",
            ),
            _license_row(
                "Step",
                "4. Rotate after operator approval",
                "Apply replacement licenses through the script's single-argument pipe upload path only after explicit operator approval.",
                "operator_approved_rotation",
            ),
            _license_row(
                "Step",
                "5. Validate",
                "Refresh /license, confirm Contract and Limits fields, then rerun XYZ_STRESS for XYZ-PILOT-258.",
                "validate_after_rotation",
            ),
        ],
        headers={
            "Content-Disposition": f'attachment; filename="xyz-exasol-license-import-plan-{timestamp}.json"'
        },
    )


@app.post("/license/import")
def license_import_disabled(authorization: str | None = Header(default=None)) -> JSONResponse:
    _check_auth(authorization)
    raise HTTPException(
        status_code=501,
        detail=(
            "Direct license import through Grafana is intentionally disabled. "
            "Export the current ConfD details first, then use the operator runbook/script path."
        ),
    )
