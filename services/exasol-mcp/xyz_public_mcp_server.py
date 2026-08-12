"""<PILOT_NAME> public Exasol MCP entrypoint.

Extends the official Exasol MCP server with guarded read-only SQL tools for
external MCP clients. The base server still owns OAuth, metadata tools, and the
pyexasol connection factory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import threading
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from mcp.types import ToolAnnotations
from pydantic import Field
from typing_extensions import Annotated

from exasol.ai.mcp.server.main import mcp_server
from exasol.ai.mcp.server.tools.mcp_server import verify_query


logger = logging.getLogger(__name__)
CONFIDENTIALITY_NOTE = (
    "Confidential <PILOT_NAME> endpoint — © Exasol AG; "
    "ESL ownership remains with <SEMANTIC_LAYER_VENDOR>; evaluation use only."
)

DEFAULT_MAX_ROWS = 200
DEFAULT_MAX_CHARS = 12_000
SQL_EXECUTION_LOCK = threading.Lock()
FORBIDDEN_SQL_RE = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE|MERGE|GRANT|REVOKE|"
    r"IMPORT|EXPORT|CALL|EXECUTE|REPLACE|COMMIT|ROLLBACK"
    r")\b",
    re.IGNORECASE,
)
LEADING_COMMENT_RE = re.compile(r"^\s*(?:--[^\n]*(?:\n|$)|/\*.*?\*/\s*)", re.DOTALL)

SqlArg = Annotated[
    str,
    Field(
        description=(
            "A single read-only Exasol SQL statement. SELECT and WITH are parsed "
            "as SELECT-only SQL; DESCRIBE, EXPLAIN, and SHOW are allowed for "
            "read-only diagnostics. Do not include multiple statements."
        )
    ),
]
MaxRowsArg = Annotated[
    int,
    Field(
        default=100,
        description=(
            "Maximum rows to return. The server enforces an upper bound through "
            "XYZ_MCP_SQL_MAX_ROWS."
        ),
    ),
]


def _configured_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        logger.warning("Invalid %s value; using default %s", name, default)
        return default


def _strip_leading_comments(sql: str) -> str:
    s = sql
    while True:
        next_s = LEADING_COMMENT_RE.sub("", s, count=1)
        if next_s == s:
            return s.lstrip()
        s = next_s


def _normalise_sql(sql: str) -> str:
    s = (sql or "").strip()
    if not s:
        raise ValueError("sql is empty")
    if s.endswith(";"):
        s = s[:-1].strip()
    if ";" in s:
        raise ValueError("Only one SQL statement is accepted.")
    return s


def _first_token(sql: str) -> str:
    s = _strip_leading_comments(sql)
    match = re.match(r"([A-Za-z_]+)", s)
    return match.group(1).upper() if match else ""


def _validate_read_only_sql(sql: str) -> str:
    s = _normalise_sql(sql)
    token = _first_token(s)
    if token not in {"SELECT", "WITH", "DESCRIBE", "EXPLAIN", "SHOW"}:
        raise ValueError(
            "Only SELECT / WITH / DESCRIBE / EXPLAIN / SHOW statements are accepted."
        )
    if FORBIDDEN_SQL_RE.search(s):
        raise ValueError("DDL, DML, transaction, import/export, and procedure calls are refused.")
    if token in {"SELECT", "WITH"} and not verify_query(s):
        raise ValueError("The SQL is not a valid SELECT/WITH read query.")
    return s


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        try:
            if value == value.to_integral_value():
                return int(value)
            return float(value)
        except (OverflowError, ValueError):
            return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _safe_row(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return {str(k): _json_safe(v) for k, v in row.items()}
    if isinstance(row, (list, tuple)):
        return {f"COL_{idx + 1}": _json_safe(v) for idx, v in enumerate(row)}
    return {"VALUE": _json_safe(row)}


def _columns(statement: Any, rows: list[dict[str, Any]]) -> list[str]:
    try:
        return [column[0] for column in statement.columns().items()]
    except Exception:  # noqa: BLE001 - best-effort metadata for client UX
        return list(rows[0].keys()) if rows else []


def _apply_char_cap(payload: dict[str, Any], max_chars: int) -> dict[str, Any]:
    payload["truncated_chars"] = False
    while len(json.dumps(payload, default=str, ensure_ascii=False)) > max_chars:
        rows = payload.get("rows") or []
        if rows:
            rows.pop()
            payload["truncated_chars"] = True
            payload["truncated_rows"] = True
            payload["returned_rows"] = len(rows)
            continue
        payload["note"] = f"Result metadata exceeded {max_chars} chars after row truncation."
        break
    return payload


def register_xyz_sql_tools(server: Any) -> None:
    max_rows_limit = max(1, _configured_int("XYZ_MCP_SQL_MAX_ROWS", DEFAULT_MAX_ROWS))
    max_chars = max(1000, _configured_int("XYZ_MCP_SQL_MAX_CHARS", DEFAULT_MAX_CHARS))
    db_user = os.environ.get("EXA_USER", "")
    if db_user.upper() == "SYS":
        raise RuntimeError("Refusing to expose public SQL tools with EXA_USER=sys.")

    def _execute(sql: str, max_rows: int = 100) -> dict[str, Any]:
        s = _validate_read_only_sql(sql)
        row_limit = min(max(1, int(max_rows or 100)), max_rows_limit)
        sql_hash = hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
        logger.info(
            "xyz_public_sql_tool_start sql_hash=%s verb=%s max_rows=%s db_user=%s",
            sql_hash,
            _first_token(s),
            row_limit,
            db_user,
        )
        with SQL_EXECUTION_LOCK:
            statement = server.connection.execute_query(s, snapshot=False)
            raw_rows = statement.fetchmany(row_limit + 1)
            rows = [_safe_row(row) for row in raw_rows[:row_limit]]
            columns = _columns(statement, rows)
        payload: dict[str, Any] = {
            "columns": columns,
            "rows": rows,
            "returned_rows": len(rows),
            "max_rows": row_limit,
            "truncated_rows": len(raw_rows) > row_limit,
            "sql_hash": sql_hash,
            "db_user": db_user,
        }
        _apply_char_cap(payload, max_chars)
        logger.info(
            "xyz_public_sql_tool_ok sql_hash=%s returned_rows=%s truncated_rows=%s truncated_chars=%s",
            sql_hash,
            payload["returned_rows"],
            payload["truncated_rows"],
            payload["truncated_chars"],
        )
        return payload

    def exasol_query(sql: SqlArg, max_rows: MaxRowsArg = 100) -> dict[str, Any]:
        """Run guarded read-only SQL against the live <PILOT_NAME> <PILOT_SCHEMA> database."""
        return _execute(sql=sql, max_rows=max_rows)

    def execute_read_only_sql(sql: SqlArg, max_rows: MaxRowsArg = 100) -> dict[str, Any]:
        """Alias for exasol_query for MCP clients that look for SQL tool names."""
        return _execute(sql=sql, max_rows=max_rows)

    def run_exasol_query(sql: SqlArg, max_rows: MaxRowsArg = 100) -> dict[str, Any]:
        """Alias for exasol_query for MCP clients that look for run/query naming."""
        return _execute(sql=sql, max_rows=max_rows)

    annotations = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
    server.tool(
        exasol_query,
        name="exasol_query",
        description=(
            "Run a guarded read-only SQL statement against the live <PILOT_NAME> "
            "Exasol Personal database as XYZ_DASH. Accepts SELECT/WITH plus "
            "DESCRIBE/EXPLAIN/SHOW diagnostics. Rejects DDL/DML, multi-statement "
            "input, import/export, procedures, and sys-user exposure. Results are "
            f"capped at {max_rows_limit} rows / {max_chars} JSON chars. "
            f"{CONFIDENTIALITY_NOTE}"
        ),
        annotations=annotations,
    )
    server.tool(
        execute_read_only_sql,
        name="execute_read_only_sql",
        description=f"Alias for exasol_query with explicit read-only naming. {CONFIDENTIALITY_NOTE}",
        annotations=annotations,
    )
    server.tool(
        run_exasol_query,
        name="run_exasol_query",
        description=f"Alias for exasol_query with run/query naming. {CONFIDENTIALITY_NOTE}",
        annotations=annotations,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="<PILOT_NAME> public Exasol MCP server")
    parser.add_argument("--transport", default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Accepted for CLI compatibility. Public deployment uses OAuth env vars.",
    )
    args = parser.parse_args()

    server = mcp_server()
    register_xyz_sql_tools(server)
    server.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
