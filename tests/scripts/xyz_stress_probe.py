#!/usr/bin/env python3
"""Bounded stress probe for <PILOT_NAME> Streamlit and Exasol read paths."""

from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
from typing import Any


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


ROOT = Path(__file__).resolve().parents[2]
WARMUP_PATH = ROOT / "scripts" / "exasol_warmup" / "warmup.py"
# Heavy-analytics targets deliberately scan raw <PILOT_SCHEMA>.FACT_PRIMARY (170 M
# rows) — NOT the sub-second MV_* snapshots — to probe the raw-fact-scan ceiling.
# EXPECTED BEHAVIOUR, NOT A DEFECT: single-user they return correct results in
# ~19-44 s, but under high concurrency (e.g. XYZ_STRESS=15) 15 simultaneous full
# scans saturate the single-node Exasol Personal and each exceeds the 120 s
# --db-timeout → 100% error_rate (timeout_after_120s). These are classified
# blocking=no (advisory), so a 100% error rate here surfaces as an advisory p95
# breach and does NOT fail the swarm gate. See
# docs/10-product-data/XYZ_EXASOL_PERFORMANCE_ANALYSIS.md §6.1.
HEAVY_ANALYTICS_TARGETS = {
    "grafana_region_performance_rank",
    "grafana_top3_brands_sales",
    "grafana_top_brand_sales_by_year",
    "perf_account_penetration",
    "perf_grouping_sets_rollup",
    "perf_rolling_12m_sales",
    # ESL BASE_LAYER probes are UDF-backed <SEMANTIC_LAYER_VENDOR> semantic view paths that can be
    # slow under high concurrency, so they are classed heavy_analytics/advisory
    # like the raw-fact-scan targets above. V_BUSINESS_METRICS is a fast
    # snapshot view and is treated as a fast/core target (NOT listed here).
    "esl_base_layer_year_sales_dash",
    "biz_esl_base_layer_year_sales",
}
BASE_LAYER_MINIMAL_TARGET = "biz_esl_base_layer_minimal_year"


def _is_schema_inventory_target(target: str) -> bool:
    return target.startswith("view_count_")


def load_warmup_queries(
    path: Path = WARMUP_PATH, *, var_name: str = "WARMUP_QUERIES"
) -> list[tuple[str, str]]:
    """Extract a named query pack from warmup.py without importing it.

    `warmup.py` intentionally reads EXA_PASSWORD at runtime. Importing it in a
    GitHub runner would either fail or risk pulling secret-bearing state into
    the stress process. The query packs are static Python data, so AST
    extraction gives the stress harness the same SQL without executing module
    code. `var_name` selects which top-level pack to extract (e.g. the default
    XYZ_DASH ``WARMUP_QUERIES`` or the XYZ_BIZ ``WARMUP_QUERIES_XYZ_BIZ``).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        value_node: ast.AST | None = None
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if var_name in names:
                value_node = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == var_name
        ):
            value_node = node.value

        if value_node is not None:
            value = ast.literal_eval(value_node)
            return [(str(label), str(sql)) for label, sql in value]
    raise ValueError(f"{var_name} not found in {path}")


def _target_kind(row: dict[str, Any]) -> str:
    return str(row.get("kind") or "http")


def _is_db_kind(kind: str) -> bool:
    return kind in {"db", "db_hold"}


def _target_class(target: str) -> str:
    if _is_schema_inventory_target(target):
        return "schema_inventory"
    return "heavy_analytics" if target in HEAVY_ANALYTICS_TARGETS else "core_uat"


def _is_blocking_target(target: str, *, heavy_analytics_mode: str) -> bool:
    target_class = _target_class(target)
    if target_class == "schema_inventory":
        return False
    if target_class == "heavy_analytics":
        return heavy_analytics_mode == "blocking"
    return True


def fetch_http(
    *,
    user_id: int,
    iteration: int,
    target: str,
    url: str,
    expected_status: int,
    timeout: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    status: int | None = None
    error_text = ""
    ok = False
    try:
        completed = subprocess.run(
            [
                "curl",
                "-k",
                "-sS",
                "--connect-timeout",
                "5",
                "--max-time",
                str(timeout),
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "-H",
                f"User-Agent: xyz-stress-probe/1.0 virtual-user/{user_id}",
                "-H",
                "Cache-Control: no-cache",
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        code_text = completed.stdout.strip()
        if code_text.isdigit():
            status = int(code_text)
        if completed.returncode:
            error_text = completed.stderr.strip()[:160] or f"curl_exit:{completed.returncode}"
        ok = completed.returncode == 0 and status == expected_status
    except Exception as exc:  # noqa: BLE001 - summarized and redacted for QA evidence.
        error_text = f"{exc.__class__.__name__}:{str(exc)[:160]}"
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "kind": "http",
        "user": user_id,
        "iteration": iteration,
        "target": target,
        "url": url,
        "expected_status": expected_status,
        "status": status,
        "ok": ok,
        "elapsed_ms": round(elapsed_ms, 2),
        "error": error_text,
    }


def hold_pyexasol_session(
    *,
    user_id: int,
    iteration: int,
    dsn: str,
    user: str,
    password: str,
    schema: str,
    hold_seconds: float,
    barrier: threading.Barrier | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    error_text = ""
    status = 0
    ok = False
    snapshot = ""
    try:
        import pyexasol  # type: ignore[import-not-found]

        conn = pyexasol.connect(
            dsn=dsn,
            user=user,
            password=password,
            schema=schema,
            encryption=True,
            websocket_sslopt={"cert_reqs": 0},
            autocommit=True,
            compression=True,
        )
        try:
            conn.execute("SELECT 1").fetchone()
            if barrier is not None:
                try:
                    barrier.wait(timeout=max(10.0, hold_seconds + 10.0))
                except threading.BrokenBarrierError:
                    error_text = "session_hold_barrier_broken"
            rows = conn.execute(
                "SELECT USER_NAME, STATUS, COUNT(*) AS SESSION_COUNT "
                "FROM EXA_DBA_SESSIONS WHERE USER_NAME='XYZ_DASH' "
                "GROUP BY USER_NAME, STATUS ORDER BY STATUS"
            ).fetchall()
            snapshot = "; ".join(f"{row[0]}:{row[1]}:{row[2]}" for row in rows)[:240]
            if hold_seconds > 0:
                time.sleep(hold_seconds)
            ok = True
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - summarized and redacted for QA evidence.
        status = -2
        error_text = f"{exc.__class__.__name__}:{str(exc)[:180]}"

    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "kind": "db_hold",
        "user": user_id,
        "iteration": iteration,
        "target": "session_hold",
        "dsn": dsn,
        "ok": ok,
        "status": status,
        "elapsed_ms": round(elapsed_ms, 2),
        "error": error_text,
        "session_snapshot": snapshot,
    }


def run_sql(
    *,
    user_id: int,
    iteration: int,
    target: str,
    sql: str,
    profile: str,
    timeout: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    error_text = ""
    status: int | None = None
    ok = False
    stdout_preview = ""
    try:
        completed = subprocess.run(
            ["exapump", "sql", "--profile", profile, sql],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        status = completed.returncode
        ok = completed.returncode == 0
        stdout_preview = " ".join(completed.stdout.split())[:240]
        if completed.returncode:
            error_text = " ".join((completed.stderr or completed.stdout).split())[:240]
    except subprocess.TimeoutExpired:
        status = -1
        error_text = f"timeout_after_{timeout:.0f}s"
    except Exception as exc:  # noqa: BLE001 - summarized and redacted for QA evidence.
        status = -2
        error_text = f"{exc.__class__.__name__}:{str(exc)[:180]}"

    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "kind": "db",
        "user": user_id,
        "iteration": iteration,
        "target": target,
        "profile": profile,
        "ok": ok,
        "status": status,
        "elapsed_ms": round(elapsed_ms, 2),
        "error": error_text,
        "stdout_preview": stdout_preview,
    }


def _qual_view(name: str) -> str:
    return f'<PILOT_SCHEMA>."{name}"' if name.upper() in {"DATE", "USER"} else f"<PILOT_SCHEMA>.{name}"


def _parse_single_column_table(stdout: str, header: str) -> list[str]:
    values: list[str] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.upper() == header.upper():
            continue
        if line.startswith("-") or line.startswith("["):
            continue
        if "statement executed" in line.lower():
            continue
        values.append(line)
    return values


def discover_view_queries(
    profile: str,
    timeout: float,
    *,
    include_union_all: bool = False,
) -> tuple[list[tuple[str, str]], str]:
    sql = "SELECT VIEW_NAME FROM EXA_ALL_VIEWS WHERE VIEW_SCHEMA='<PILOT_SCHEMA>' ORDER BY VIEW_NAME"
    try:
        completed = subprocess.run(
            ["exapump", "sql", "--profile", profile, sql],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {str(exc)[:160]}"
    if completed.returncode:
        return [], " ".join((completed.stderr or completed.stdout).split())[:240]

    names = _parse_single_column_table(completed.stdout, "VIEW_NAME")
    queries = [
        (f"view_count_{name}", f"SELECT COUNT(*) FROM {_qual_view(name)}")
        for name in names
    ]
    if names and include_union_all:
        union_sql = " UNION ALL ".join(
            f"SELECT '{name}' AS OBJECT_NAME, COUNT(*) AS RC FROM {_qual_view(name)}"
            for name in names
        )
        queries.append(("view_count_union_all", union_sql))
    return queries, ""


def build_db_targets(
    *,
    profile: str,
    warmup_path: Path,
    query_limit: int,
    include_dynamic_views: bool,
    discovery_timeout: float,
    include_view_count_union_all: bool = False,
    include_heavy_analytics: bool = True,
    biz_profile: str = "",
    biz_warmup_var: str = "WARMUP_QUERIES_XYZ_BIZ",
) -> tuple[list[tuple[str, str, str]], dict[str, Any]]:
    """Build per-target DB probe specs as ``(label, sql, profile)`` triples.

    The XYZ_DASH pack (and the dynamic view-count discovery) run under
    ``profile`` (default ``--exasol-profile``/``xyz-pilot-ro``). When ``biz_profile``
    is set, the separate XYZ_BIZ pack (``biz_warmup_var``) is appended and run
    under that profile, giving the probe a distinct per-profile dimension. BIZ
    targets keep their ``biz_`` labels and are classed via ``_target_class`` so
    BASE_LAYER (UDF-backed) is advisory/heavy_analytics while
    V_BUSINESS_METRICS stays fast/core.
    """
    targets: list[tuple[str, str, str]] = [(
        "session_concurrency_marker",
        (
            "SELECT USER_NAME, STATUS, COUNT(*) AS SESSION_COUNT "
            "FROM EXA_DBA_SESSIONS WHERE USER_NAME='XYZ_DASH' "
            "GROUP BY USER_NAME, STATUS ORDER BY STATUS"
        ),
        profile,
    )]
    warmup_queries = load_warmup_queries(warmup_path)
    heavy_query_count = len([target for target, _sql in warmup_queries if _target_class(target) == "heavy_analytics"])
    if not include_heavy_analytics:
        warmup_queries = [
            (target, sql)
            for target, sql in warmup_queries
            if _target_class(target) != "heavy_analytics"
        ]
    if query_limit > 0:
        warmup_queries = warmup_queries[:query_limit]
    targets.extend((target, sql, profile) for target, sql in warmup_queries)

    dynamic_error = ""
    dynamic_queries: list[tuple[str, str]] = []
    if include_dynamic_views:
        dynamic_queries, dynamic_error = discover_view_queries(
            profile,
            discovery_timeout,
            include_union_all=include_view_count_union_all,
        )
        targets.extend((target, sql, profile) for target, sql in dynamic_queries)

    biz_queries: list[tuple[str, str]] = []
    if biz_profile:
        biz_queries = load_warmup_queries(warmup_path, var_name=biz_warmup_var)
        if not include_heavy_analytics:
            biz_queries = [
                (target, sql)
                for target, sql in biz_queries
                if _target_class(target) != "heavy_analytics"
            ]
        if query_limit > 0:
            biz_queries = biz_queries[:query_limit]
        targets.extend((target, sql, biz_profile) for target, sql in biz_queries)

    return targets, {
        "warmup_query_count": len(warmup_queries),
        "dynamic_view_query_count": len(dynamic_queries),
        "dynamic_view_discovery_error": dynamic_error,
        "dynamic_view_union_all_included": include_view_count_union_all,
        "heavy_analytics_query_count": heavy_query_count if include_heavy_analytics else 0,
        "heavy_analytics_mode": "included" if include_heavy_analytics else "skipped",
        "query_limit": query_limit,
        "biz_query_count": len(biz_queries),
        "biz_profile": biz_profile,
    }


def run_user(
    *,
    user_id: int,
    iterations: int,
    http_targets: list[tuple[str, str, int]],
    timeout: float,
    think_ms: int,
    db_targets: list[tuple[str, str, str]],
    db_profile: str,
    db_iterations: int,
    db_timeout: float,
    session_hold_enabled: bool,
    session_hold_dsn: str,
    session_hold_user: str,
    session_hold_password: str,
    session_hold_schema: str,
    session_hold_seconds: float,
    db_barrier: threading.Barrier | None = None,
    session_hold_barrier: threading.Barrier | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for iteration in range(1, iterations + 1):
        for target, url, expected_status in http_targets:
            rows.append(
                fetch_http(
                    user_id=user_id,
                    iteration=iteration,
                    target=target,
                    url=url,
                    expected_status=expected_status,
                    timeout=timeout,
                )
            )
            if think_ms > 0:
                time.sleep(think_ms / 1000)
    if session_hold_enabled:
        rows.append(
            hold_pyexasol_session(
                user_id=user_id,
                iteration=1,
                dsn=session_hold_dsn,
                user=session_hold_user,
                password=session_hold_password,
                schema=session_hold_schema,
                hold_seconds=session_hold_seconds,
                barrier=session_hold_barrier,
            )
        )
    for iteration in range(1, db_iterations + 1):
        if db_barrier is not None:
            try:
                db_barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
        for target, sql, target_profile in db_targets:
            rows.append(
                run_sql(
                    user_id=user_id,
                    iteration=iteration,
                    target=target,
                    sql=sql,
                    profile=target_profile or db_profile,
                    timeout=db_timeout,
                )
            )
            if think_ms > 0:
                time.sleep(think_ms / 1000)
    return rows


def summarize(
    rows: list[dict[str, Any]],
    *,
    max_p95_ms: float,
    max_db_p95_ms: float,
    max_error_rate: float,
    heavy_analytics_mode: str = "advisory",
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["target"]), []).append(row)

    target_summaries: dict[str, dict[str, Any]] = {}
    total_failures = 0
    blocking_failures = 0
    advisory_failures = 0
    for target, target_rows in grouped.items():
        elapsed = [float(row["elapsed_ms"]) for row in target_rows]
        failures = [row for row in target_rows if not row["ok"]]
        total_failures += len(failures)
        status_counts: dict[str, int] = {}
        for row in target_rows:
            status_counts[str(row["status"])] = status_counts.get(str(row["status"]), 0) + 1
        kind = _target_kind(target_rows[0])
        threshold = max_db_p95_ms if _is_db_kind(kind) else max_p95_ms
        target_class = _target_class(target)
        is_blocking = _is_blocking_target(target, heavy_analytics_mode=heavy_analytics_mode)
        if is_blocking:
            blocking_failures += len(failures)
        else:
            advisory_failures += len(failures)
        target_summaries[target] = {
            "kind": kind,
            "class": target_class,
            "blocking": is_blocking,
            "requests": len(target_rows),
            "failures": len(failures),
            "error_rate": round(len(failures) / len(target_rows), 4),
            "p50_ms": round(statistics.median(elapsed), 2),
            "p95_ms": round(percentile(elapsed, 0.95), 2),
            "max_ms": round(max(elapsed), 2),
            "max_allowed_p95_ms": threshold,
            "status_counts": status_counts,
        }

    total_requests = len(rows)
    total_error_rate = total_failures / total_requests if total_requests else 1.0
    blocking_rows = [
        row
        for row in rows
        if _is_blocking_target(str(row["target"]), heavy_analytics_mode=heavy_analytics_mode)
    ]
    advisory_rows = [
        row
        for row in rows
        if not _is_blocking_target(str(row["target"]), heavy_analytics_mode=heavy_analytics_mode)
    ]
    blocking_error_rate = blocking_failures / len(blocking_rows) if blocking_rows else 1.0
    advisory_error_rate = advisory_failures / len(advisory_rows) if advisory_rows else 0.0
    p95_breaches = [
        target
        for target, data in target_summaries.items()
        if float(data["p95_ms"]) > float(data["max_allowed_p95_ms"])
    ]
    blocking_p95_breaches = [
        target
        for target in p95_breaches
        if bool(target_summaries[target].get("blocking"))
    ]
    advisory_p95_breaches = [
        target
        for target in p95_breaches
        if not bool(target_summaries[target].get("blocking"))
    ]
    passed = blocking_error_rate <= max_error_rate and not blocking_p95_breaches
    db_rows = [row for row in rows if _is_db_kind(_target_kind(row))]
    http_rows = [row for row in rows if _target_kind(row) == "http"]
    session_hold_rows = [row for row in rows if _target_kind(row) == "db_hold"]
    minimal = target_summaries.get(BASE_LAYER_MINIMAL_TARGET)
    base_layer_minimal_probe = {
        "target": BASE_LAYER_MINIMAL_TARGET,
        "present": bool(minimal),
        "requests": int(minimal["requests"]) if minimal else 0,
        "failures": int(minimal["failures"]) if minimal else 0,
        "error_rate": float(minimal["error_rate"]) if minimal else 0.0,
        "p95_ms": float(minimal["p95_ms"]) if minimal else 0.0,
        "blocking": bool(minimal["blocking"]) if minimal else False,
        "class": str(minimal.get("class", "core_uat")) if minimal else "n/a",
    }

    return {
        "passed": passed,
        "total_requests": total_requests,
        "total_failures": total_failures,
        "total_error_rate": round(total_error_rate, 4),
        "blocking_requests": len(blocking_rows),
        "blocking_failures": blocking_failures,
        "blocking_error_rate": round(blocking_error_rate, 4),
        "advisory_requests": len(advisory_rows),
        "advisory_failures": advisory_failures,
        "advisory_error_rate": round(advisory_error_rate, 4),
        "http_requests": len(http_rows),
        "http_failures": len([row for row in http_rows if not row["ok"]]),
        "db_requests": len(db_rows),
        "db_failures": len([row for row in db_rows if not row["ok"]]),
        "session_hold_requests": len(session_hold_rows),
        "session_hold_failures": len([row for row in session_hold_rows if not row["ok"]]),
        "max_error_rate": max_error_rate,
        "max_p95_ms": max_p95_ms,
        "max_db_p95_ms": max_db_p95_ms,
        "heavy_analytics_mode": heavy_analytics_mode,
        "p95_breaches": p95_breaches,
        "blocking_p95_breaches": blocking_p95_breaches,
        "advisory_p95_breaches": advisory_p95_breaches,
        "base_layer_minimal_probe": base_layer_minimal_probe,
        "targets": target_summaries,
    }


def write_markdown_summary(path: Path, summary: dict[str, Any]) -> None:
    minimal = summary.get("base_layer_minimal_probe") or {}
    minimal_status = "present" if minimal.get("present") else "absent"
    minimal_line = (
        f"- BASE_LAYER_MINIMAL probe ({minimal.get('target', BASE_LAYER_MINIMAL_TARGET)}): "
        f"`{minimal_status}` · requests `{minimal.get('requests', 0)}` · failures `{minimal.get('failures', 0)}` · "
        f"error rate `{minimal.get('error_rate', 0.0)}` · p95 ms `{minimal.get('p95_ms', 0.0)}`"
    )
    lines = [
        "# XYZ_STRESS Probe Summary",
        "",
        f"- Passed: `{summary['passed']}`",
        f"- Total requests: `{summary['total_requests']}`",
        f"- Total failures: `{summary['total_failures']}`",
        f"- Total error rate: `{summary['total_error_rate']}`",
        f"- Blocking requests/failures: `{summary.get('blocking_requests', 0)}` / `{summary.get('blocking_failures', 0)}`",
        f"- Blocking error rate: `{summary.get('blocking_error_rate', 0)}`",
        f"- Advisory requests/failures: `{summary.get('advisory_requests', 0)}` / `{summary.get('advisory_failures', 0)}`",
        f"- Advisory error rate: `{summary.get('advisory_error_rate', 0)}`",
        f"- HTTP requests/failures: `{summary['http_requests']}` / `{summary['http_failures']}`",
        f"- DB requests/failures: `{summary['db_requests']}` / `{summary['db_failures']}`",
        f"- Session-hold requests/failures: `{summary.get('session_hold_requests', 0)}` / `{summary.get('session_hold_failures', 0)}`",
        f"- Max allowed error rate: `{summary['max_error_rate']}`",
        f"- Max allowed HTTP p95 ms: `{summary['max_p95_ms']}`",
        f"- Max allowed DB p95 ms: `{summary['max_db_p95_ms']}`",
        f"- DB warm-up static queries per user iteration: `{summary.get('db_warmup_query_count', 0)}`",
        f"- DB dynamic view queries per user iteration: `{summary.get('db_dynamic_view_query_count', 0)}`",
        f"- Dynamic view_count_union_all diagnostic: `{summary.get('db_dynamic_view_union_all_included', False)}`",
        f"- Heavy analytics mode: `{summary.get('heavy_analytics_mode', 'advisory')}`",
        f"- Heavy analytics static queries per user iteration: `{summary.get('db_heavy_analytics_query_count', 0)}`",
        f"- BIZ profile: `{summary.get('biz_profile', '') or 'none'}`",
        f"- BIZ ({summary.get('biz_profile', '') or 'none'}) static queries per user iteration: `{summary.get('db_biz_query_count', 0)}`",
        minimal_line,
        f"- Blocking p95 breaches: `{', '.join(summary.get('blocking_p95_breaches', [])) or 'none'}`",
        f"- Advisory p95 breaches: `{', '.join(summary.get('advisory_p95_breaches', [])) or 'none'}`",
        f"- Session hold enabled: `{summary.get('session_hold_enabled', False)}`",
        f"- Session hold seconds: `{summary.get('session_hold_seconds', 0)}`",
        "",
        "| Kind | Class | Blocking | Target | Requests | Failures | Error rate | p50 ms | p95 ms | max ms | P95 limit | Status counts |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for target, data in sorted(summary["targets"].items()):
        lines.append(
            "| {kind} | {target_class} | {blocking} | {target} | {requests} | {failures} | {error_rate} | {p50_ms} | "
            "{p95_ms} | {max_ms} | {max_allowed_p95_ms} | {status_counts} |".format(
                kind=data["kind"],
                target_class=data.get("class", "core_uat"),
                blocking="yes" if data.get("blocking") else "no",
                target=target,
                requests=data["requests"],
                failures=data["failures"],
                error_rate=data["error_rate"],
                p50_ms=data["p50_ms"],
                p95_ms=data["p95_ms"],
                max_ms=data["max_ms"],
                max_allowed_p95_ms=data["max_allowed_p95_ms"],
                status_counts=json.dumps(data["status_counts"], sort_keys=True),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qbr-url", required=True)
    parser.add_argument("--chat-url", required=True)
    parser.add_argument("--users", type=int, default=5)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--think-ms", type=int, default=250)
    parser.add_argument("--max-p95-ms", type=float, default=5000.0)
    parser.add_argument("--max-db-p95-ms", type=float, default=45000.0)
    parser.add_argument("--max-error-rate", type=float, default=0.05)
    parser.add_argument("--exasol-profile", default="")
    parser.add_argument(
        "--biz-profile",
        default="xyz-pilot-biz",
        help=(
            "exapump profile for the XYZ_BIZ pack. When set (default "
            "xyz-pilot-biz), the probe additionally runs WARMUP_QUERIES_XYZ_BIZ "
            "under this profile as a separate per-profile dimension. Pass an "
            "empty string to disable the BIZ dimension."
        ),
    )
    parser.add_argument("--biz-warmup-var", default="WARMUP_QUERIES_XYZ_BIZ")
    parser.add_argument("--db-iterations", type=int, default=1)
    parser.add_argument("--db-timeout", type=float, default=120.0)
    parser.add_argument("--db-query-limit", type=int, default=0)
    parser.add_argument(
        "--heavy-analytics-mode",
        choices=("advisory", "blocking", "skip"),
        default="advisory",
        help=(
            "How to treat known heavy analytical SQL targets. advisory keeps "
            "evidence but excludes them from pass/fail, blocking includes them "
            "in pass/fail, and skip omits them from the run."
        ),
    )
    parser.add_argument("--warmup-path", type=Path, default=WARMUP_PATH)
    parser.add_argument("--skip-db", action="store_true")
    parser.add_argument("--skip-dynamic-views", action="store_true")
    parser.add_argument(
        "--include-view-count-union-all",
        action="store_true",
        help=(
            "Opt in to the combined dynamic view_count_union_all diagnostic. "
            "The standard UAT Swarm keeps individual view_count_* probes but "
            "omits this combined UNION ALL count because it is noisy under "
            "concurrent load and does not represent a user-facing app path."
        ),
    )
    parser.add_argument("--exasol-dsn", default="")
    parser.add_argument("--exasol-user", default="XYZ_DASH")
    parser.add_argument("--exasol-schema", default="<PILOT_SCHEMA>")
    parser.add_argument("--exasol-password-env", default="XYZ_EXASOL_PASSWORD")
    parser.add_argument("--session-hold-seconds", type=float, default=15.0)
    parser.add_argument("--skip-session-hold", action="store_true")
    parser.add_argument("--require-session-hold", action="store_true")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()

    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    qbr = args.qbr_url.rstrip("/")
    chat = args.chat_url.rstrip("/")
    http_targets = [
        ("qbr_health", f"{qbr}/_stcore/health", 200),
        ("qbr_shell", f"{qbr}/", 200),
        ("chat_health", f"{chat}/_stcore/health", 200),
        ("chat_shell", f"{chat}/", 200),
    ]
    db_targets: list[tuple[str, str, str]] = []
    db_meta: dict[str, Any] = {
        "warmup_query_count": 0,
        "dynamic_view_query_count": 0,
        "dynamic_view_discovery_error": "",
        "dynamic_view_union_all_included": False,
        "query_limit": args.db_query_limit,
        "biz_query_count": 0,
        "biz_profile": "",
    }
    if not args.skip_db:
        if not args.exasol_profile:
            raise SystemExit("--exasol-profile is required unless --skip-db is set")
        db_targets, db_meta = build_db_targets(
            profile=args.exasol_profile,
            warmup_path=args.warmup_path,
            query_limit=args.db_query_limit,
            include_dynamic_views=not args.skip_dynamic_views,
            include_view_count_union_all=args.include_view_count_union_all,
            discovery_timeout=args.db_timeout,
            include_heavy_analytics=args.heavy_analytics_mode != "skip",
            biz_profile=args.biz_profile,
            biz_warmup_var=args.biz_warmup_var,
        )

    session_hold_password = ""
    session_hold_enabled = False
    session_hold_skip_reason = ""
    if db_targets and not args.skip_session_hold:
        session_hold_password = os.environ.get(args.exasol_password_env, "")
        if args.exasol_dsn and session_hold_password:
            session_hold_enabled = True
        else:
            missing = []
            if not args.exasol_dsn:
                missing.append("--exasol-dsn")
            if not session_hold_password:
                missing.append(args.exasol_password_env)
            session_hold_skip_reason = "missing " + ", ".join(missing)
            if args.require_session_hold:
                raise SystemExit(f"session hold required but {session_hold_skip_reason}")

    all_rows: list[dict[str, Any]] = []
    started = time.time()
    barrier = threading.Barrier(args.users) if db_targets and args.users > 1 else None
    session_hold_barrier = threading.Barrier(args.users) if session_hold_enabled and args.users > 1 else None
    with ThreadPoolExecutor(max_workers=args.users) as executor:
        futures = [
            executor.submit(
                run_user,
                user_id=user_id,
                iterations=args.iterations,
                http_targets=http_targets,
                timeout=args.timeout,
                think_ms=args.think_ms,
                db_targets=db_targets,
                db_profile=args.exasol_profile,
                db_iterations=args.db_iterations if db_targets else 0,
                db_timeout=args.db_timeout,
                session_hold_enabled=session_hold_enabled,
                session_hold_dsn=args.exasol_dsn,
                session_hold_user=args.exasol_user,
                session_hold_password=session_hold_password,
                session_hold_schema=args.exasol_schema,
                session_hold_seconds=args.session_hold_seconds,
                db_barrier=barrier,
                session_hold_barrier=session_hold_barrier,
            )
            for user_id in range(1, args.users + 1)
        ]
        for future in as_completed(futures):
            all_rows.extend(future.result())

    summary = summarize(
        all_rows,
        max_p95_ms=args.max_p95_ms,
        max_db_p95_ms=args.max_db_p95_ms,
        max_error_rate=args.max_error_rate,
        heavy_analytics_mode=args.heavy_analytics_mode,
    )
    summary.update(
        {
            "users": args.users,
            "iterations": args.iterations,
            "db_enabled": bool(db_targets),
            "db_iterations": args.db_iterations if db_targets else 0,
            "db_profile": args.exasol_profile if db_targets else "",
            "db_query_limit": args.db_query_limit,
            "db_warmup_query_count": db_meta["warmup_query_count"],
            "db_dynamic_view_query_count": db_meta["dynamic_view_query_count"],
            "db_dynamic_view_discovery_error": db_meta["dynamic_view_discovery_error"],
            "db_dynamic_view_union_all_included": db_meta.get("dynamic_view_union_all_included", False),
            "db_heavy_analytics_query_count": db_meta.get("heavy_analytics_query_count", 0),
            "heavy_analytics_mode": args.heavy_analytics_mode,
            "db_queries_per_user_iteration": len(db_targets),
            "biz_profile": db_meta.get("biz_profile", "") if db_targets else "",
            "db_biz_query_count": db_meta.get("biz_query_count", 0),
            "session_hold_enabled": session_hold_enabled,
            "session_hold_seconds": args.session_hold_seconds if session_hold_enabled else 0,
            "session_hold_skip_reason": session_hold_skip_reason,
            "duration_seconds": round(time.time() - started, 2),
        }
    )

    results_path = args.artifact_dir / "stress_results.jsonl"
    with results_path.open("w", encoding="utf-8") as handle:
        for row in all_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    (args.artifact_dir / "stress_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_markdown_summary(args.artifact_dir / "stress_summary.md", summary)

    print(
        "passed={passed} users={users} iterations={iterations} "
        "db_enabled={db_enabled} db_iterations={db_iterations} "
        "heavy_analytics_mode={heavy_analytics_mode} "
        "session_hold_enabled={session_hold_enabled} "
        "requests={requests} failures={failures} "
        "blocking_requests={blocking_requests} blocking_failures={blocking_failures} "
        "db_requests={db_requests} db_failures={db_failures} "
        "error_rate={error_rate} blocking_error_rate={blocking_error_rate} "
        "duration_seconds={duration}".format(
            passed=summary["passed"],
            users=args.users,
            iterations=args.iterations,
            db_enabled=summary["db_enabled"],
            db_iterations=summary["db_iterations"],
            heavy_analytics_mode=summary["heavy_analytics_mode"],
            session_hold_enabled=summary["session_hold_enabled"],
            requests=summary["total_requests"],
            failures=summary["total_failures"],
            blocking_requests=summary["blocking_requests"],
            blocking_failures=summary["blocking_failures"],
            db_requests=summary["db_requests"],
            db_failures=summary["db_failures"],
            error_rate=summary["total_error_rate"],
            blocking_error_rate=summary["blocking_error_rate"],
            duration=summary["duration_seconds"],
        )
    )
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
