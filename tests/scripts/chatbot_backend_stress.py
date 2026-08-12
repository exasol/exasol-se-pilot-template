#!/usr/bin/env python3
"""XYZ-PILOT-204 Phase 2b first slice — chatbot BACKEND concurrent-prompt stress probe.

Simulates the load N concurrent UAT testers generate against the chatbot's
backend (Bedrock Converse API + Exasol via pyexasol), bypassing the
Streamlit UI + Cognito OAuth (which requires Playwright to drive). Each
"virtual user" thread runs the SAME shape of Bedrock-tool-loop the live
chatbot runs: Converse → tool_use(exasol_query) → tool_result → Converse →
final text.

This isolates the actual scaling concern (Bedrock account quota + Exasol
concurrent-session ceiling) from the cheap front-end shell.

Usage:
  python3 tests/scripts/chatbot_backend_stress.py \
    --users 12 --iterations 1 --artifact-dir tests/logs/stress-runs/$RUN_ID

Reads XYZ_DASH credentials from ~/.exapump/config.toml [xyz-pilot-ro].
Bedrock region/model from XYZ_BEDROCK_REGION / XYZ_BEDROCK_MODEL_ID env vars
(defaults: <AWS_REGION> / us.anthropic.claude-sonnet-4-6).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import median
from typing import Any

import boto3
import pyexasol

EXAPUMP_PROFILE = "xyz-pilot-ro"
AWS_REGION = os.environ.get("XYZ_BEDROCK_REGION", "<AWS_REGION>")
BEDROCK_MODEL_ID = os.environ.get("XYZ_BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
MAX_TOOL_ITERATIONS = int(os.environ.get("XYZ_CHATBOT_MAX_TOOL_ITERATIONS", "14"))
SYSTEM_PROMPT = """You are a SQL agent for <PILOT_NAME>'s <PILOT_SCHEMA> schema on Exasol.
For every question about the data, call the exasol_query tool with a SELECT.
Schema: <PILOT_SCHEMA>. Tables: FACT_PRIMARY, FACT_TARGET, FACT_STOCK,
DIM_CATEGORY, DIM_ACCOUNT, DIM_REGION, SEGMENT, DATE_DIM, MV_MONTHLY_METRICS,
MV_YOY_METRICS, MV_ACTUAL_VS_TARGET, MV_STOCK_ANALYTICS.
Use MV_* views for time-series/region/brand aggregates (faster).
Always include LIMIT 100 in SELECTs. Respond with the SQL result. Keep
responses short — one paragraph max."""

PROMPT_PACK = [
    "How many rows are in <PILOT_SCHEMA>.FACT_PRIMARY?",
    "What are the top 5 brands by total sales amount in 2025?",
    "Show total sales by XYZ_REGION_1 for January 2026.",
    "How many distinct product groups exist in DIM_CATEGORY?",
    "What is the maximum sale amount in FACT_PRIMARY?",
]

TOOL_SPEC = {
    "toolSpec": {
        "name": "exasol_query",
        "description": (
            "Run a read-only SELECT against the <PILOT_SCHEMA> schema on Exasol. "
            "Returns formatted rows. Results capped at 200 rows / 12K chars."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A complete SELECT statement.",
                    },
                },
                "required": ["sql"],
            }
        },
    }
}


def _load_exapump_profile(profile_name: str) -> dict[str, Any]:
    """Read exapump's TOML config for the named profile."""
    cfg = Path.home() / ".exapump" / "config.toml"
    with cfg.open("rb") as f:
        data = tomllib.load(f)
    if profile_name not in data:
        raise SystemExit(f"profile {profile_name!r} not found in {cfg}")
    return data[profile_name]


def _connect_exasol(profile: dict[str, Any]) -> pyexasol.ExaConnection:
    dsn = f"{profile['host']}:{profile['port']}"
    return pyexasol.connect(
        dsn=dsn,
        user=profile["user"],
        password=profile["password"],
        schema=profile.get("schema", "<PILOT_SCHEMA>"),
        encryption=bool(profile.get("tls", True)),
        websocket_sslopt={"cert_reqs": 0},
        autocommit=True,
        fetch_size_bytes=2_000_000,
    )


def _exec_exasol(conn: pyexasol.ExaConnection, sql: str, row_cap: int = 50) -> str:
    """Execute a SELECT and return a compact text rendering (cap rows + chars)."""
    sql_clean = sql.strip().rstrip(";")
    upper = sql_clean.lstrip().upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return f"refused: only SELECT/WITH allowed; got {upper.split()[0]!r}"
    stmt = conn.execute(sql_clean)
    rows = stmt.fetchall()[:row_cap]
    cols = [d[0] for d in stmt.columns().items()] if hasattr(stmt, "columns") else []
    if not rows:
        return "(no rows)"
    header = " | ".join(str(c) for c in cols) if cols else ""
    body = "\n".join(" | ".join("" if v is None else str(v) for v in r) for r in rows)
    out = f"{header}\n{body}" if header else body
    return out[:6000]


def _bedrock_client() -> Any:
    """Per-thread Bedrock client — boto3 clients are not thread-safe."""
    session = boto3.Session(region_name=AWS_REGION)
    return session.client("bedrock-runtime")


def _run_one_turn(
    *,
    user_id: int,
    prompt_idx: int,
    prompt: str,
    bedrock: Any,
    exasol_conn: pyexasol.ExaConnection,
) -> dict[str, Any]:
    """Execute one chatbot-equivalent turn: prompt → tool-loop → final text."""
    started = time.perf_counter()
    bedrock_calls = 0
    tool_calls = 0
    tool_errors = 0
    last_error = ""
    final_text = ""

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"text": prompt}]},
    ]

    try:
        for _ in range(MAX_TOOL_ITERATIONS):
            bedrock_calls += 1
            resp = bedrock.converse(
                modelId=BEDROCK_MODEL_ID,
                system=[{"text": SYSTEM_PROMPT}],
                messages=messages,
                toolConfig={"tools": [TOOL_SPEC]},
                inferenceConfig={"maxTokens": 1500, "temperature": 0.2},
            )
            stop_reason = resp.get("stopReason", "")
            assistant_msg = resp.get("output", {}).get("message", {})
            messages.append(assistant_msg)

            if stop_reason != "tool_use":
                # Extract final text from assistant message
                for block in assistant_msg.get("content", []):
                    if "text" in block:
                        final_text = block["text"]
                        break
                break

            # Execute every tool_use block; build a single user message of tool_result blocks
            tool_results: list[dict[str, Any]] = []
            for block in assistant_msg.get("content", []):
                tu = block.get("toolUse")
                if not tu:
                    continue
                tool_calls += 1
                tool_name = tu.get("name", "")
                tool_input = tu.get("input", {})
                tool_use_id = tu.get("toolUseId", "")
                if tool_name != "exasol_query":
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": f"tool {tool_name!r} unsupported in stress probe"}],
                            "status": "error",
                        }
                    })
                    tool_errors += 1
                    continue
                try:
                    result_text = _exec_exasol(exasol_conn, tool_input.get("sql", ""))
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": result_text}],
                            "status": "success",
                        }
                    })
                except Exception as exc:  # noqa: BLE001 — stress capture
                    tool_errors += 1
                    last_error = f"{type(exc).__name__}: {exc}"
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": f"sql error: {exc}"}],
                            "status": "error",
                        }
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            last_error = f"tool-loop hit {MAX_TOOL_ITERATIONS}-iteration ceiling"
    except Exception as exc:  # noqa: BLE001 — stress capture
        last_error = f"{type(exc).__name__}: {exc}"

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    ok = bool(final_text) and not last_error.startswith(("ThrottlingException", "ModelError", "AccessDenied"))
    return {
        "user_id": user_id,
        "prompt_idx": prompt_idx,
        "prompt": prompt[:80],
        "ok": ok,
        "elapsed_ms": round(elapsed_ms, 2),
        "bedrock_calls": bedrock_calls,
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
        "final_text_len": len(final_text),
        "last_error": last_error,
    }


def _run_user(*, user_id: int, iterations: int, profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-thread entry point — own Bedrock client, own pyexasol connection."""
    bedrock = _bedrock_client()
    conn = _connect_exasol(profile)
    rows: list[dict[str, Any]] = []
    try:
        for it in range(iterations):
            for idx, prompt in enumerate(PROMPT_PACK):
                row = _run_one_turn(
                    user_id=user_id,
                    prompt_idx=it * len(PROMPT_PACK) + idx,
                    prompt=prompt,
                    bedrock=bedrock,
                    exasol_conn=conn,
                )
                rows.append(row)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return rows


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    k = (len(vs) - 1) * p
    f = int(k)
    c = min(f + 1, len(vs) - 1)
    if f == c:
        return vs[f]
    return vs[f] + (vs[c] - vs[f]) * (k - f)


def _write_summary(artifact_dir: Path, results: list[dict[str, Any]], duration_s: float, args: argparse.Namespace) -> dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "stress_results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in results) + "\n"
    )

    by_prompt: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_prompt.setdefault(r["prompt"], []).append(r)

    rows_md = []
    for prompt, rs in by_prompt.items():
        lats = [r["elapsed_ms"] for r in rs]
        failures = sum(1 for r in rs if not r["ok"])
        rows_md.append((
            prompt[:60],
            len(rs),
            failures,
            round(failures / len(rs), 3) if rs else 0.0,
            round(median(lats), 1),
            round(_pct(lats, 0.95), 1),
            round(max(lats), 1),
            sum(r["bedrock_calls"] for r in rs),
            sum(r["tool_calls"] for r in rs),
            sum(r["tool_errors"] for r in rs),
        ))

    all_lats = [r["elapsed_ms"] for r in results]
    total_failures = sum(1 for r in results if not r["ok"])
    error_rate = total_failures / len(results) if results else 0.0
    passed = error_rate <= args.max_error_rate and _pct(all_lats, 0.95) <= args.max_p95_ms

    summary = {
        "passed": passed,
        "users": args.users,
        "iterations": args.iterations,
        "prompts_per_iteration": len(PROMPT_PACK),
        "total_turns": len(results),
        "total_failures": total_failures,
        "error_rate": round(error_rate, 4),
        "max_allowed_error_rate": args.max_error_rate,
        "p50_ms": round(median(all_lats), 1) if all_lats else 0,
        "p95_ms": round(_pct(all_lats, 0.95), 1) if all_lats else 0,
        "max_ms": round(max(all_lats), 1) if all_lats else 0,
        "max_allowed_p95_ms": args.max_p95_ms,
        "total_bedrock_calls": sum(r["bedrock_calls"] for r in results),
        "total_tool_calls": sum(r["tool_calls"] for r in results),
        "total_tool_errors": sum(r["tool_errors"] for r in results),
        "duration_seconds": round(duration_s, 2),
        "bedrock_model": BEDROCK_MODEL_ID,
        "aws_region": AWS_REGION,
    }
    (artifact_dir / "stress_summary.json").write_text(json.dumps(summary, indent=2))

    md_lines = [
        "# XYZ-PILOT-204 Phase 2b — Chatbot Backend Stress Summary",
        "",
        f"- Passed: `{passed}`",
        f"- Concurrent virtual users: `{args.users}`",
        f"- Iterations per user: `{args.iterations}`",
        f"- Prompts per iteration: `{len(PROMPT_PACK)}`",
        f"- Total turns: `{len(results)}`",
        f"- Total failures: `{total_failures}` (error rate {round(error_rate*100, 2)}%, budget {round(args.max_error_rate*100, 2)}%)",
        f"- p50 / p95 / max latency: `{round(median(all_lats),0) if all_lats else 0} ms` / `{round(_pct(all_lats,0.95),0) if all_lats else 0} ms` / `{round(max(all_lats),0) if all_lats else 0} ms` (p95 budget {int(args.max_p95_ms)} ms)",
        f"- Total Bedrock Converse calls: `{summary['total_bedrock_calls']}`",
        f"- Total `exasol_query` tool calls: `{summary['total_tool_calls']}` (errors: {summary['total_tool_errors']})",
        f"- Wall-clock duration: `{round(duration_s, 1)} s`",
        f"- Bedrock model: `{BEDROCK_MODEL_ID}` in `{AWS_REGION}`",
        "",
        "## Per-prompt breakdown",
        "",
        "| Prompt | Turns | Fails | Err rate | p50 ms | p95 ms | max ms | Bedrock | Tools | Tool errs |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows_md:
        md_lines.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} | {r[8]} | {r[9]} |")
    (artifact_dir / "stress_summary.md").write_text("\n".join(md_lines) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=12, help="Concurrent virtual users (default 12 = UAT cap)")
    parser.add_argument("--iterations", type=int, default=1, help="Prompt-pack iterations per user (default 1)")
    parser.add_argument("--max-p95-ms", type=float, default=30000.0, help="p95 latency budget (Bedrock+Exasol round-trip; default 30s)")
    parser.add_argument("--max-error-rate", type=float, default=0.05, help="Max error rate (default 0.05)")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--exapump-profile", default=EXAPUMP_PROFILE)
    args = parser.parse_args()

    profile = _load_exapump_profile(args.exapump_profile)
    print(f"Exasol DSN: {profile['host']}:{profile['port']} user={profile['user']}", file=sys.stderr)
    print(f"Bedrock model: {BEDROCK_MODEL_ID} region: {AWS_REGION}", file=sys.stderr)
    print(f"Running {args.users} concurrent users × {args.iterations} iter × {len(PROMPT_PACK)} prompts = {args.users * args.iterations * len(PROMPT_PACK)} total turns", file=sys.stderr)

    started = time.perf_counter()
    all_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.users) as pool:
        futures = [
            pool.submit(_run_user, user_id=i, iterations=args.iterations, profile=profile)
            for i in range(args.users)
        ]
        for fut in as_completed(futures):
            all_results.extend(fut.result())
    duration = time.perf_counter() - started

    summary = _write_summary(args.artifact_dir, all_results, duration, args)
    print(json.dumps(summary), file=sys.stderr)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
