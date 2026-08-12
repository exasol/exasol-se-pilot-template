#!/usr/bin/env python3
"""XYZ-PILOT-220 follow-through: extract live external semantic layer deployment DDLs to git.

Runs against the live Pilot Exasol Personal instance via `exapump --profile
xyz-pilot` (SYS user, read-only inventory) and dumps the canonical
deployment state under `external_semantic_layer/` so the Pilot is not one EC2
termination away from losing 221 hand-modeled objects.

Extracts:
  - Views: full CREATE VIEW text per view, one file per schema under
    `external_semantic_layer/ddl/<schema>.sql` (concatenated by schema for readability)
  - Scripts (UDFs): full CREATE SCRIPT text per script, one file per
    script under `external_semantic_layer/udfs/<schema>/<script>.sql`
  - Tables: column-list manifest per schema under
    `external_semantic_layer/ddl/<schema>_tables.csv` (full CREATE TABLE reconstruction
    is a follow-up; column lists are sufficient to identify drift)
  - Role grants: `external_semantic_layer/grants/roles_and_grants.sql`
  - Virtual schema definitions: `external_semantic_layer/ddl/virtual_schemas.sql`
  - Config seed data (small tables only, ≤500 rows): CSV per table under
    `external_semantic_layer/config_seeds/<schema>/<table>.csv`
  - Inventory manifest: `external_semantic_layer/FACT_STOCK.json` — machine-readable
    summary the discovery doc cites

Usage:
  python3 scripts/extract_external_semantic_layer.py

Safe to re-run — overwrites existing files. Diff before commit to see
what changed since last extraction.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "external_semantic_layer"
PROFILE = "xyz-pilot"
SCHEMAS = [
    "ESL_MODEL",
    "ESL_PILOT_BUSINESS_VIEW",
    "ESL_PILOT_LOGICAL_MODEL",
    "ESL_PILOT_PHYSICAL_MODEL",
    "ESL_PILOT_RELATIONAL_MODEL",
    "ESL_PILOT_SEMANTIC_LAYER_HOME",
    "ESL_PILOT_SEMANTIC_LAYER_VIRTUAL_SCHEMA",
    "ESL_PILOT_SEMANTIC_LAYER_VIRTUAL_SCHEMA_TST",
    "ESL_SCRIPTS",
]
SEED_ROW_CAP = 500  # don't dump >500-row tables as config seeds


def q(sql: str) -> list[dict[str, Any]]:
    """Run query via exapump and return list of result dicts."""
    res = subprocess.run(
        ["exapump", "sql", "--profile", PROFILE, "--format", "json", sql],
        capture_output=True, text=True, check=True,
    )
    # exapump prefixes log lines like `[1/1] SELECT ... N rows`; the actual
    # JSON is the last non-empty line.
    lines = [ln for ln in res.stdout.strip().split("\n") if ln.strip()]
    if not lines:
        return []
    # Walk back from the end to find the JSON array line
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            return json.loads(line)
    return []


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("(empty)\n", encoding="utf-8")
        return
    # Union of all keys across all rows — exapump's JSON output may omit
    # keys for NULL-only columns in some rows.
    keys: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                keys.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, restval="")
        w.writeheader()
        w.writerows(rows)


def extract_views(schema: str) -> tuple[int, list[str]]:
    """Return (count, list-of-view-DDL-chunks) for the schema."""
    rows = q(
        f"SELECT VIEW_NAME, VIEW_TEXT, VIEW_OWNER FROM SYS.EXA_DBA_VIEWS "
        f"WHERE VIEW_SCHEMA='{schema}' ORDER BY VIEW_NAME"
    )
    chunks = []
    for r in rows:
        chunks.append(f"-- VIEW {schema}.{r['VIEW_NAME']} (owner: {r['VIEW_OWNER']})")
        chunks.append(r['VIEW_TEXT'].rstrip())
        chunks.append("\n;\n")
    return len(rows), chunks


def extract_tables_manifest(schema: str) -> tuple[int, list[dict[str, Any]]]:
    """Return (count, column-manifest-rows) for tables in the schema."""
    rows = q(
        f"SELECT t.TABLE_NAME, t.TABLE_ROW_COUNT, t.TABLE_OWNER, "
        f"c.COLUMN_NAME, c.COLUMN_TYPE, c.COLUMN_IS_NULLABLE, c.COLUMN_DEFAULT "
        f"FROM SYS.EXA_DBA_TABLES t "
        f"LEFT JOIN SYS.EXA_DBA_COLUMNS c "
        f"  ON c.COLUMN_SCHEMA=t.TABLE_SCHEMA AND c.COLUMN_TABLE=t.TABLE_NAME "
        f"WHERE t.TABLE_SCHEMA='{schema}' "
        f"ORDER BY t.TABLE_NAME, c.COLUMN_ORDINAL_POSITION"
    )
    # Distinct table count
    table_names = sorted({r['TABLE_NAME'] for r in rows})
    return len(table_names), rows


def extract_scripts(schema: str) -> int:
    """Write one CREATE SCRIPT file per script under udfs/<schema>/<name>.sql"""
    rows = q(
        f"SELECT SCRIPT_NAME, SCRIPT_TEXT, SCRIPT_TYPE, SCRIPT_LANGUAGE, SCRIPT_OWNER "
        f"FROM SYS.EXA_DBA_SCRIPTS WHERE SCRIPT_SCHEMA='{schema}' ORDER BY SCRIPT_NAME"
    )
    for r in rows:
        path = ROOT / "udfs" / schema / f"{r['SCRIPT_NAME']}.sql"
        header = (
            f"-- SCRIPT {schema}.{r['SCRIPT_NAME']}\n"
            f"-- TYPE     : {r['SCRIPT_TYPE']}\n"
            f"-- LANGUAGE : {r['SCRIPT_LANGUAGE']}\n"
            f"-- OWNER    : {r['SCRIPT_OWNER']}\n\n"
        )
        body = (r['SCRIPT_TEXT'] or "").rstrip() + "\n"
        write_text(path, header + body)
    return len(rows)


def extract_grants() -> int:
    """Write all ESL_* role + grant info to grants/roles_and_grants.sql."""
    roles = q(
        "SELECT ROLE_NAME, CREATED, ROLE_CONSUMER_GROUP, ROLE_COMMENT "
        "FROM SYS.EXA_DBA_ROLES WHERE ROLE_NAME LIKE 'ESL%' ORDER BY ROLE_NAME"
    )
    role_privs = q(
        "SELECT GRANTEE, GRANTED_ROLE, ADMIN_OPTION FROM SYS.EXA_DBA_ROLE_PRIVS "
        "WHERE GRANTED_ROLE LIKE 'ESL%' OR GRANTEE LIKE 'ESL%' "
        "ORDER BY GRANTED_ROLE, GRANTEE"
    )
    obj_privs = q(
        "SELECT GRANTEE, OBJECT_SCHEMA, OBJECT_NAME, OBJECT_TYPE, PRIVILEGE, GRANTOR, OWNER "
        "FROM SYS.EXA_DBA_OBJ_PRIVS WHERE OBJECT_SCHEMA LIKE 'ESL%' "
        "OR GRANTEE LIKE 'ESL%' ORDER BY OBJECT_SCHEMA, OBJECT_NAME, GRANTEE"
    )
    sys_privs = q(
        "SELECT GRANTEE, PRIVILEGE, ADMIN_OPTION FROM SYS.EXA_DBA_SYS_PRIVS "
        "WHERE GRANTEE LIKE 'ESL%' ORDER BY GRANTEE, PRIVILEGE"
    )
    lines: list[str] = ["-- XYZ-PILOT-220 — external semantic layer role + grant inventory\n"]
    lines.append(f"-- Extracted: {datetime.now(timezone.utc).isoformat()}\n\n")
    lines.append("-- ============================================================\n")
    lines.append("-- 1. Roles\n")
    lines.append("-- ============================================================\n")
    for r in roles:
        lines.append(
            f"CREATE ROLE \"{r['ROLE_NAME']}\";  "
            f"-- consumer_group={r.get('ROLE_CONSUMER_GROUP') or 'default'} "
            f"created={r['CREATED']}\n"
        )
    lines.append("\n-- ============================================================\n")
    lines.append("-- 2. Role grants (GRANT <role> TO <grantee>)\n")
    lines.append("-- ============================================================\n")
    for r in role_privs:
        admin = " WITH ADMIN OPTION" if r['ADMIN_OPTION'] else ""
        lines.append(
            f"GRANT \"{r['GRANTED_ROLE']}\" TO \"{r['GRANTEE']}\"{admin};\n"
        )
    lines.append("\n-- ============================================================\n")
    lines.append("-- 3. Object privileges on ESL_* objects\n")
    lines.append("-- ============================================================\n")
    for r in obj_privs:
        sch = r.get('OBJECT_SCHEMA') or ''
        name = r.get('OBJECT_NAME') or ''
        obj_type = r.get('OBJECT_TYPE') or 'OBJECT'
        priv = r.get('PRIVILEGE') or 'UNKNOWN'
        grantee = r.get('GRANTEE') or '?'
        if sch and name:
            target = f"{obj_type} \"{sch}\".\"{name}\""
        elif name:
            target = f"{obj_type} \"{name}\""
        else:
            target = f"{obj_type}"
        lines.append(
            f"GRANT {priv} ON {target} TO \"{grantee}\";  "
            f"-- grantor={r.get('GRANTOR') or '?'} owner={r.get('OWNER') or '?'}\n"
        )
    lines.append("\n-- ============================================================\n")
    lines.append("-- 4. System privileges granted to ESL_* roles\n")
    lines.append("-- ============================================================\n")
    for r in sys_privs:
        admin = " WITH ADMIN OPTION" if r['ADMIN_OPTION'] else ""
        lines.append(f"GRANT {r['PRIVILEGE']} TO \"{r['GRANTEE']}\"{admin};\n")
    write_text(ROOT / "grants" / "roles_and_grants.sql", "".join(lines))
    return len(roles) + len(role_privs) + len(obj_privs) + len(sys_privs)


def extract_virtual_schemas() -> int:
    """Write CREATE VIRTUAL SCHEMA statements."""
    schemas = q(
        "SELECT SCHEMA_NAME, SCHEMA_OWNER, ADAPTER_SCRIPT_SCHEMA, ADAPTER_SCRIPT_NAME, "
        "LAST_REFRESH, LAST_REFRESH_BY, ADAPTER_NOTES FROM SYS.EXA_DBA_VIRTUAL_SCHEMAS "
        "WHERE SCHEMA_NAME LIKE 'ESL%' ORDER BY SCHEMA_NAME"
    )
    properties = q(
        "SELECT SCHEMA_NAME, PROPERTY_NAME, PROPERTY_VALUE "
        "FROM SYS.EXA_DBA_VIRTUAL_SCHEMA_PROPERTIES "
        "WHERE SCHEMA_NAME LIKE 'ESL%' ORDER BY SCHEMA_NAME, PROPERTY_NAME"
    )
    lines: list[str] = ["-- XYZ-PILOT-220 — external semantic layer virtual schema definitions\n"]
    lines.append(f"-- Extracted: {datetime.now(timezone.utc).isoformat()}\n\n")
    by_schema: dict[str, list[dict[str, Any]]] = {}
    for p in properties:
        by_schema.setdefault(p['SCHEMA_NAME'], []).append(p)
    for s in schemas:
        lines.append(f"\n-- ============================================================\n")
        lines.append(f"-- VIRTUAL SCHEMA {s['SCHEMA_NAME']}\n")
        lines.append(f"-- Owner          : {s['SCHEMA_OWNER']}\n")
        lines.append(f"-- Adapter script : {s['ADAPTER_SCRIPT_SCHEMA']}.{s['ADAPTER_SCRIPT_NAME']}\n")
        lines.append(f"-- Last refresh   : {s['LAST_REFRESH']} (by {s['LAST_REFRESH_BY']})\n")
        if s.get('ADAPTER_NOTES'):
            lines.append(f"-- Adapter notes  : {s['ADAPTER_NOTES']}\n")
        lines.append("-- ============================================================\n")
        lines.append(f"CREATE VIRTUAL SCHEMA \"{s['SCHEMA_NAME']}\"\n")
        lines.append(f"  USING \"{s['ADAPTER_SCRIPT_SCHEMA']}\".\"{s['ADAPTER_SCRIPT_NAME']}\"\n")
        props = by_schema.get(s['SCHEMA_NAME'], [])
        if props:
            lines.append("  WITH\n")
            for i, p in enumerate(props):
                comma = "," if i < len(props) - 1 else ""
                v = (p['PROPERTY_VALUE'] or "").replace("'", "''")
                lines.append(f"    \"{p['PROPERTY_NAME']}\" = '{v}'{comma}\n")
        lines.append(";\n")
    write_text(ROOT / "ddl" / "virtual_schemas.sql", "".join(lines))
    return len(schemas)


def extract_config_seeds() -> dict[str, int]:
    """Dump config-table seed data for SEMANTIC_LAYER_HOME tables ≤ SEED_ROW_CAP."""
    candidates = q(
        f"SELECT TABLE_NAME, TABLE_ROW_COUNT FROM SYS.EXA_DBA_TABLES "
        f"WHERE TABLE_SCHEMA='ESL_PILOT_SEMANTIC_LAYER_HOME' "
        f"AND TABLE_ROW_COUNT <= {SEED_ROW_CAP} "
        f"ORDER BY TABLE_NAME"
    )
    counts: dict[str, int] = {}
    for c in candidates:
        name = c['TABLE_NAME']
        rows = q(f"SELECT * FROM ESL_PILOT_SEMANTIC_LAYER_HOME.\"{name}\"")
        path = ROOT / "config_seeds" / "ESL_PILOT_SEMANTIC_LAYER_HOME" / f"{name}.csv"
        write_csv(path, rows)
        counts[name] = len(rows)
    return counts


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    inventory: dict[str, Any] = {
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
        "schemas": {},
    }

    for sch in SCHEMAS:
        sch_inv: dict[str, Any] = {}
        # Views
        n_views, view_chunks = extract_views(sch)
        sch_inv["views"] = n_views
        # Tables
        n_tables, table_rows = extract_tables_manifest(sch)
        sch_inv["tables"] = n_tables
        # Write the schema DDL bundle
        out = ROOT / "ddl" / f"{sch}.sql"
        body = (
            f"-- XYZ-PILOT-220 — schema: {sch}\n"
            f"-- Extracted: {datetime.now(timezone.utc).isoformat()}\n"
            f"-- Tables: {n_tables} (see {sch}_tables.csv for column manifest)\n"
            f"-- Views:  {n_views}\n\n"
            f"CREATE SCHEMA IF NOT EXISTS \"{sch}\";\n\n"
            f"-- ============================================================\n"
            f"-- VIEWS ({n_views})\n"
            f"-- ============================================================\n\n"
            + "\n".join(view_chunks)
        )
        write_text(out, body)
        # Tables column manifest
        write_csv(ROOT / "ddl" / f"{sch}_tables.csv", table_rows)
        # Scripts
        sch_inv["scripts"] = extract_scripts(sch)
        inventory["schemas"][sch] = sch_inv
        print(f"  {sch}: views={n_views} tables={n_tables} scripts={sch_inv['scripts']}")

    # Cross-schema: grants + virtual schemas + config seeds
    n_grants = extract_grants()
    inventory["grants_lines"] = n_grants
    print(f"  grants: {n_grants} lines (roles + grants + obj_privs + sys_privs)")

    n_vs = extract_virtual_schemas()
    inventory["virtual_schemas"] = n_vs
    print(f"  virtual_schemas: {n_vs}")

    seeds = extract_config_seeds()
    inventory["config_seeds"] = seeds
    print(f"  config_seeds: {sum(seeds.values())} rows across {len(seeds)} tables")

    write_text(ROOT / "FACT_STOCK.json", json.dumps(inventory, indent=2, default=str) + "\n")
    print(f"\nWrote inventory manifest: {ROOT / 'FACT_STOCK.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
