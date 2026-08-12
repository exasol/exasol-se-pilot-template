#!/usr/bin/env python3
"""Advisory GitHub Actions usage and storage watch for the <PILOT_NAME> repo."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_REPO = "<GITHUB_REPOSITORY>"
API_ROOT = "https://api.github.com"


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_mb(num_bytes: int | float) -> str:
    return f"{num_bytes / 1024 / 1024:.1f} MB"


def format_minutes(minutes: float) -> str:
    return f"{minutes:.1f}"


def parse_link_header(value: str) -> dict[str, str]:
    links: dict[str, str] = {}
    for part in value.split(","):
        pieces = part.strip().split(";")
        if len(pieces) < 2:
            continue
        url_part = pieces[0].strip()
        rel_part = pieces[1].strip()
        if not url_part.startswith("<") or not url_part.endswith(">"):
            continue
        if not rel_part.startswith('rel="') or not rel_part.endswith('"'):
            continue
        links[rel_part[5:-1]] = url_part[1:-1]
    return links


def api_get_json(url: str, token: str) -> tuple[dict[str, Any], str]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "<PROJECT_SLUG>-actions-usage-watch",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed GitHub API URL.
        body = response.read().decode("utf-8")
        link = response.headers.get("Link", "")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError(f"GitHub API response was not an object: {url}")
    return payload, link


def api_paginated(repo: str, path: str, token: str, array_key: str, query: dict[str, str] | None = None) -> list[dict[str, Any]]:
    query_params = {"per_page": "100"}
    if query:
        query_params.update(query)
    url = f"{API_ROOT}/repos/{repo}{path}?{urlencode(query_params)}"
    rows: list[dict[str, Any]] = []
    while url:
        payload, link_header = api_get_json(url, token)
        page_rows = payload.get(array_key, [])
        if not isinstance(page_rows, list):
            raise ValueError(f"GitHub API response missing list field {array_key!r}: {url}")
        rows.extend(row for row in page_rows if isinstance(row, dict))
        url = parse_link_header(link_header).get("next", "")
    return rows


def run_duration_minutes(run: dict[str, Any]) -> float:
    start_raw = str(run.get("run_started_at") or run.get("created_at") or "")
    end_raw = str(run.get("updated_at") or "")
    if not start_raw or not end_raw:
        return 0.0
    try:
        start = parse_timestamp(start_raw)
        end = parse_timestamp(end_raw)
    except ValueError:
        return 0.0
    seconds = max((end - start).total_seconds(), 0)
    return seconds / 60


def summarize_runs(runs: list[dict[str, Any]], since: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for run in runs:
        created_raw = str(run.get("created_at") or "")
        if not created_raw:
            continue
        try:
            created = parse_timestamp(created_raw)
        except ValueError:
            continue
        if created >= since:
            filtered.append(run)

    total_minutes = sum(run_duration_minutes(run) for run in filtered)
    completed = [run for run in filtered if run.get("status") == "completed"]
    failures = [run for run in completed if run.get("conclusion") in {"failure", "timed_out", "startup_failure"}]
    cancelled = [run for run in completed if run.get("conclusion") in {"cancelled", "skipped"}]
    by_workflow: dict[str, dict[str, Any]] = defaultdict(lambda: {"runs": 0, "minutes": 0.0, "failures": 0})
    by_event: dict[str, int] = defaultdict(int)

    for run in filtered:
        workflow = str(run.get("name") or run.get("workflow_name") or "unknown")
        event = str(run.get("event") or "unknown")
        duration = run_duration_minutes(run)
        by_workflow[workflow]["runs"] += 1
        by_workflow[workflow]["minutes"] += duration
        by_event[event] += 1
        if run.get("conclusion") in {"failure", "timed_out", "startup_failure"}:
            by_workflow[workflow]["failures"] += 1

    top_workflows = sorted(
        (
            {"workflow": workflow, **values}
            for workflow, values in by_workflow.items()
        ),
        key=lambda row: (float(row["minutes"]), int(row["runs"])),
        reverse=True,
    )
    failure_rate = (len(failures) / len(completed) * 100) if completed else 0.0
    return filtered, {
        "total_runs": len(filtered),
        "completed_runs": len(completed),
        "failed_runs": len(failures),
        "cancelled_or_skipped_runs": len(cancelled),
        "failure_rate": failure_rate,
        "estimated_minutes": total_minutes,
        "top_workflows": top_workflows[:8],
        "events": dict(sorted(by_event.items())),
    }


def summarize_storage(
    artifacts: list[dict[str, Any]],
    caches: list[dict[str, Any]],
    since: datetime,
) -> dict[str, Any]:
    active_artifacts = [artifact for artifact in artifacts if not artifact.get("expired")]
    recent_artifacts = []
    for artifact in active_artifacts:
        created_raw = str(artifact.get("created_at") or "")
        try:
            if created_raw and parse_timestamp(created_raw) >= since:
                recent_artifacts.append(artifact)
        except ValueError:
            continue
    artifact_bytes = sum(int(artifact.get("size_in_bytes", 0) or 0) for artifact in active_artifacts)
    recent_artifact_bytes = sum(int(artifact.get("size_in_bytes", 0) or 0) for artifact in recent_artifacts)
    cache_bytes = sum(int(cache.get("size_in_bytes", 0) or 0) for cache in caches)
    largest_artifacts = sorted(
        active_artifacts,
        key=lambda artifact: int(artifact.get("size_in_bytes", 0) or 0),
        reverse=True,
    )[:5]
    largest_caches = sorted(
        caches,
        key=lambda cache: int(cache.get("size_in_bytes", 0) or 0),
        reverse=True,
    )[:5]
    return {
        "active_artifact_count": len(active_artifacts),
        "active_artifact_bytes": artifact_bytes,
        "recent_artifact_count": len(recent_artifacts),
        "recent_artifact_bytes": recent_artifact_bytes,
        "cache_count": len(caches),
        "cache_bytes": cache_bytes,
        "largest_artifacts": largest_artifacts,
        "largest_caches": largest_caches,
    }


def make_warning_lines(
    run_summary: dict[str, Any],
    storage_summary: dict[str, Any],
    warn_minutes: float,
    warn_artifact_mb: float,
    warn_cache_mb: float,
    warn_failure_rate: float,
) -> list[str]:
    warnings: list[str] = []
    if float(run_summary["estimated_minutes"]) > warn_minutes:
        warnings.append(
            f"Estimated workflow wall-clock minutes {float(run_summary['estimated_minutes']):.1f} "
            f"exceed threshold {warn_minutes:.1f}."
        )
    if float(run_summary["failure_rate"]) > warn_failure_rate:
        warnings.append(
            f"Workflow failure rate {float(run_summary['failure_rate']):.1f}% exceeds threshold "
            f"{warn_failure_rate:.1f}%."
        )
    artifact_mb = int(storage_summary["active_artifact_bytes"]) / 1024 / 1024
    if artifact_mb > warn_artifact_mb:
        warnings.append(f"Active artifact storage {artifact_mb:.1f} MB exceeds threshold {warn_artifact_mb:.1f} MB.")
    cache_mb = int(storage_summary["cache_bytes"]) / 1024 / 1024
    if cache_mb > warn_cache_mb:
        warnings.append(f"Actions cache storage {cache_mb:.1f} MB exceeds threshold {warn_cache_mb:.1f} MB.")
    return warnings


def build_summary(
    repo: str,
    lookback_days: int,
    since: datetime,
    run_summary: dict[str, Any],
    storage_summary: dict[str, Any],
    warnings: list[str],
    api_warnings: list[str],
) -> list[str]:
    lines = [
        "## <PILOT_NAME> GitHub Actions Usage Watch",
        "",
        f"- Repository: `{repo}`",
        f"- Lookback window: {lookback_days} days since `{since.isoformat()}`",
        "- Cost note: workflow minutes are wall-clock estimates from run timestamps, not official GitHub billable minutes.",
        f"- Runs: {run_summary['total_runs']} total, {run_summary['completed_runs']} completed",
        f"- Failed/timed-out/startup-failed runs: {run_summary['failed_runs']}",
        f"- Cancelled/skipped runs: {run_summary['cancelled_or_skipped_runs']}",
        f"- Failure rate: {float(run_summary['failure_rate']):.1f}%",
        f"- Estimated wall-clock run minutes: {format_minutes(float(run_summary['estimated_minutes']))}",
        f"- Active artifact storage: {format_mb(int(storage_summary['active_artifact_bytes']))}"
        f" across {storage_summary['active_artifact_count']} artifact(s)",
        f"- Recent artifact storage: {format_mb(int(storage_summary['recent_artifact_bytes']))}"
        f" across {storage_summary['recent_artifact_count']} artifact(s)",
        f"- Actions cache storage: {format_mb(int(storage_summary['cache_bytes']))}"
        f" across {storage_summary['cache_count']} cache entries",
    ]
    events = run_summary.get("events", {})
    if events:
        lines.extend(["", "### Events"])
        for event, count in events.items():
            lines.append(f"- `{event}`: {count}")
    lines.extend(["", "### Top Workflows By Estimated Minutes"])
    if run_summary["top_workflows"]:
        lines.extend(["", "| Workflow | Runs | Estimated minutes | Failures |", "|---|---:|---:|---:|"])
        for row in run_summary["top_workflows"]:
            lines.append(
                f"| {row['workflow']} | {row['runs']} | {format_minutes(float(row['minutes']))} | {row['failures']} |"
            )
    else:
        lines.append("- No runs in window.")

    if storage_summary["largest_artifacts"]:
        lines.extend(["", "### Largest Active Artifacts"])
        for artifact in storage_summary["largest_artifacts"]:
            lines.append(
                f"- `{artifact.get('name', '<unnamed>')}`: "
                f"{format_mb(int(artifact.get('size_in_bytes', 0) or 0))}"
            )
    if storage_summary["largest_caches"]:
        lines.extend(["", "### Largest Caches"])
        for cache in storage_summary["largest_caches"]:
            lines.append(
                f"- `{cache.get('key', '<unnamed>')}`: "
                f"{format_mb(int(cache.get('size_in_bytes', 0) or 0))}"
            )

    if api_warnings:
        lines.extend(["", "### API Warnings"])
        lines.extend(f"- WARNING: {warning}" for warning in api_warnings)
    if warnings:
        lines.extend(["", "### Usage Warnings"])
        lines.extend(f"- WARNING: {warning}" for warning in warnings)
    else:
        lines.extend(["", "Status: usage watch OK"])
    return lines


def emit_summary(lines: list[str], github_summary: bool, github_warnings: bool, warnings: list[str], api_warnings: list[str]) -> None:
    if github_summary:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
                handle.write("\n")
    if github_warnings:
        for warning in [*api_warnings, *warnings]:
            print(f"::warning::{warning}")
    print("\n".join(lines))


def fetch_live_inputs(repo: str, token: str, since: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    api_warnings: list[str] = []
    created_filter = since.isoformat().replace("+00:00", "Z")
    runs = api_paginated(repo, "/actions/runs", token, "workflow_runs", {"created": f">={created_filter}"})
    try:
        artifacts = api_paginated(repo, "/actions/artifacts", token, "artifacts")
    except HTTPError as exc:
        artifacts = []
        api_warnings.append(f"Could not read Actions artifacts API: HTTP {exc.code}")
    try:
        caches = api_paginated(repo, "/actions/caches", token, "actions_caches")
    except HTTPError as exc:
        caches = []
        api_warnings.append(f"Could not read Actions caches API: HTTP {exc.code}")
    # Keep memory bounded for very busy repositories; older runs are not used.
    runs = [
        run for run in runs
        if str(run.get("created_at") or "") and parse_timestamp(str(run.get("created_at"))) >= since
    ]
    return runs, artifacts, caches, api_warnings


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_json_list(path: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path}: expected JSON list")
    return [item for item in payload if isinstance(item, dict)]


def self_test() -> int:
    now = datetime(2026, 5, 29, 10, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory(prefix="xyz-actions-usage-") as raw_tmp:
        tmp = Path(raw_tmp)
        runs = [
            {
                "name": "<PILOT_NAME> Repo Hygiene (Advisory)",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-05-29T09:00:00Z",
                "run_started_at": "2026-05-29T09:00:05Z",
                "updated_at": "2026-05-29T09:02:05Z",
            },
            {
                "name": "<PILOT_NAME> Regression Validation (Advisory)",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-05-29T08:00:00Z",
                "run_started_at": "2026-05-29T08:00:00Z",
                "updated_at": "2026-05-29T08:05:00Z",
            },
        ]
        artifacts = [
            {"name": "small-report", "expired": False, "size_in_bytes": 1024, "created_at": "2026-05-29T09:02:00Z"}
        ]
        caches = [{"key": "uv-cache", "size_in_bytes": 2048}]
        files = {"runs.json": runs, "artifacts.json": artifacts, "caches.json": caches}
        for name, payload in files.items():
            write_json(tmp / name, payload)
        args = argparse.Namespace(
            repo=DEFAULT_REPO,
            lookback_days=14,
            runs_json=str(tmp / "runs.json"),
            artifacts_json=str(tmp / "artifacts.json"),
            caches_json=str(tmp / "caches.json"),
            now=now.isoformat(),
            warn_minutes=500.0,
            warn_artifact_mb=1024.0,
            warn_cache_mb=1024.0,
            warn_failure_rate=75.0,
            github_summary=False,
            github_warnings=False,
        )
        return run(args)


def run(args: argparse.Namespace) -> int:
    now = parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)
    since = now - timedelta(days=args.lookback_days)
    api_warnings: list[str] = []

    if args.runs_json:
        runs = load_json_list(args.runs_json)
        artifacts = load_json_list(args.artifacts_json) if args.artifacts_json else []
        caches = load_json_list(args.caches_json) if args.caches_json else []
    else:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            print("ERROR: GITHUB_TOKEN or GH_TOKEN is required for live GitHub API usage", file=sys.stderr)
            return 1
        runs, artifacts, caches, api_warnings = fetch_live_inputs(args.repo, token, since)

    _filtered_runs, run_summary = summarize_runs(runs, since)
    storage_summary = summarize_storage(artifacts, caches, since)
    warnings = make_warning_lines(
        run_summary,
        storage_summary,
        args.warn_minutes,
        args.warn_artifact_mb,
        args.warn_cache_mb,
        args.warn_failure_rate,
    )
    lines = build_summary(
        args.repo,
        args.lookback_days,
        since,
        run_summary,
        storage_summary,
        warnings,
        api_warnings,
    )
    emit_summary(lines, args.github_summary, args.github_warnings, warnings, api_warnings)
    print(
        "actions_usage "
        f"runs={run_summary['total_runs']} "
        f"minutes={float(run_summary['estimated_minutes']):.1f} "
        f"artifact_mb={int(storage_summary['active_artifact_bytes']) / 1024 / 1024:.1f} "
        f"cache_mb={int(storage_summary['cache_bytes']) / 1024 / 1024:.1f} "
        f"warnings={len(warnings) + len(api_warnings)}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO))
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--runs-json", default="")
    parser.add_argument("--artifacts-json", default="")
    parser.add_argument("--caches-json", default="")
    parser.add_argument("--now", default="")
    parser.add_argument("--warn-minutes", type=float, default=500.0)
    parser.add_argument("--warn-artifact-mb", type=float, default=1024.0)
    parser.add_argument("--warn-cache-mb", type=float, default=1024.0)
    parser.add_argument("--warn-failure-rate", type=float, default=25.0)
    parser.add_argument("--github-summary", action="store_true")
    parser.add_argument("--github-warnings", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
