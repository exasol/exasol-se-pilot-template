#!/usr/bin/env python3
"""Run ANALYZE TABLE through pyexasol for large tables.

Use this when `exapump sql` reaches its client timeout on large fact tables.
The database password must be supplied through EXA_PASSWORD; this script never
prints it.
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def parse_table(value: str) -> tuple[str, str]:
    if "." not in value:
        raise argparse.ArgumentTypeError("--table must use SCHEMA.TABLE format")
    schema, table = value.split(".", 1)
    if not schema or not table:
        raise argparse.ArgumentTypeError("--table must use SCHEMA.TABLE format")
    return schema, table


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True, help="Exasol DSN, for example host:8563")
    parser.add_argument("--user", default="sys")
    parser.add_argument("--schema", default="<PILOT_SCHEMA>")
    parser.add_argument(
        "--table",
        action="append",
        type=parse_table,
        required=True,
        help="Table to analyze in SCHEMA.TABLE form. Repeat for multiple tables.",
    )
    args = parser.parse_args()

    password = os.environ.get("EXA_PASSWORD")
    if not password:
        print("ERROR: EXA_PASSWORD is required", file=sys.stderr)
        return 2

    import pyexasol

    conn = pyexasol.connect(
        dsn=args.dsn,
        user=args.user,
        password=password,
        schema=args.schema,
        encryption=True,
        websocket_sslopt={"cert_reqs": 0},
        autocommit=True,
    )
    failures = 0
    try:
        for schema, table in args.table:
            statement = f"ANALYZE TABLE {quote_ident(schema)}.{quote_ident(table)}"
            started = time.time()
            print(f"RUN {statement}", flush=True)
            try:
                conn.execute(statement)
            except Exception as exc:  # noqa: BLE001 - operator evidence.
                failures += 1
                elapsed = time.time() - started
                print(
                    f"FAIL {schema}.{table} elapsed_s={elapsed:.1f} "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                elapsed = time.time() - started
                print(f"OK {schema}.{table} elapsed_s={elapsed:.1f}", flush=True)
    finally:
        conn.close()

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
