#!/usr/bin/env python3
"""Report-only repository retention candidate scanner.

This script intentionally does not delete anything. It scans tracked Git files,
groups them into conservative retention classes, and prints file/dir sizes so
operators can agree cleanup policy before any destructive workflow exists.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys


DISPOSABLE_NAMES = {".DS_Store"}
DISPOSABLE_SUFFIXES = {".pyc", ".tmp", ".bak", ".orig"}
E2E_RAW_NAMES = {
    "agent.stdout",
    "agent.stderr",
    "notification.stdout",
    "notification.stderr",
}
E2E_RAW_SUFFIXES = {
    ".body",
    ".err",
    ".out",
    ".status",
    ".jsonl",
}
E2E_AUDIT_NAMES = {
    "manifest.md",
    "results.md",
    "report.md",
    "commands.md",
    "skills.md",
    "issue-candidates.md",
    "run.sh",
    "agent.exit",
    "notification.sent.json",
}


@dataclass(frozen=True)
class FileRecord:
    path: str
    size: int


def human_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def tracked_files(repo_root: Path) -> list[FileRecord]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    records: list[FileRecord] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8")
        path = repo_root / rel
        if path.is_file():
            records.append(FileRecord(rel, path.stat().st_size))
    return records


def classify(path: str) -> str:
    p = Path(path)
    name = p.name
    suffix = p.suffix.lower()
    parts = p.parts

    if name in DISPOSABLE_NAMES or suffix in DISPOSABLE_SUFFIXES or "__pycache__" in parts:
        return "disposable_tracked_junk"
    if path.startswith("docs/archive/XYZ_PILOT_ISSUE_TRACKER_"):
        return "tracker_guardrail_archive"
    if path.startswith("docs/archive/"):
        return "docs_archive_other"
    if path.startswith("tests/logs/runs/"):
        if "/candidate-outcomes/" in path:
            return "e2e_candidate_outcome_audit"
        if name in E2E_AUDIT_NAMES:
            return "e2e_run_audit_evidence"
        if name in E2E_RAW_NAMES or suffix in E2E_RAW_SUFFIXES:
            return "e2e_raw_runtime_artifact"
        return "e2e_other_runtime_artifact"
    if path.startswith("docs/70-generated-qa/qa/") or path.startswith("docs/70-generated-qa/architecture/"):
        return "generated_reference_capture"
    if path.startswith("clients/") or path.startswith("archives/"):
        return "client_source_archive"
    return "ordinary_tracked_file"


def retention_policy(category: str) -> str:
    policies = {
        "disposable_tracked_junk": "Cleanup candidate after operator approval; no audit value expected.",
        "tracker_guardrail_archive": "Keep; report growth. Cleanup only after off-platform backup and explicit operator policy.",
        "docs_archive_other": "Keep; report growth. Archive-class content requires owner approval before deletion.",
        "e2e_candidate_outcome_audit": "Keep; candidate decision audit trail.",
        "e2e_run_audit_evidence": "Keep; E2E run review evidence.",
        "e2e_raw_runtime_artifact": "Report-only candidate after retention window; preserve until policy approved.",
        "e2e_other_runtime_artifact": "Report-only candidate after retention window; preserve until policy approved.",
        "generated_reference_capture": "Keep unless superseded by explicit doc owner cleanup.",
        "client_source_archive": "Never auto-delete; source/original input material.",
        "ordinary_tracked_file": "No cleanup action.",
    }
    return policies.get(category, "No cleanup action.")


def directory_rollups(records: list[FileRecord]) -> dict[str, int]:
    rollups: dict[str, int] = {}
    for rec in records:
        parts = Path(rec.path).parts
        if len(parts) >= 3 and parts[0] == "tests" and parts[1] == "logs" and parts[2] == "runs":
            key = "/".join(parts[:4]) if len(parts) >= 4 else "tests/logs/runs"
        elif len(parts) >= 2 and parts[0] == "docs" and parts[1] == "archive":
            key = "docs/archive"
        elif len(parts) == 2:
            key = parts[0]
        elif len(parts) >= 2:
            key = "/".join(parts[:2])
        else:
            key = parts[0] if parts else "."
        rollups[key] = rollups.get(key, 0) + rec.size
    return rollups


def build_report(records: list[FileRecord]) -> dict[str, object]:
    categories: dict[str, dict[str, object]] = {}
    for rec in records:
        category = classify(rec.path)
        bucket = categories.setdefault(
            category,
            {"count": 0, "bytes": 0, "policy": retention_policy(category), "files": []},
        )
        bucket["count"] = int(bucket["count"]) + 1
        bucket["bytes"] = int(bucket["bytes"]) + rec.size
        bucket["files"].append({"path": rec.path, "bytes": rec.size})

    for bucket in categories.values():
        bucket["files"] = sorted(
            bucket["files"],
            key=lambda item: (-int(item["bytes"]), str(item["path"])),
        )

    dirs = [
        {"path": path, "bytes": size}
        for path, size in sorted(directory_rollups(records).items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "mode": "report-only",
        "tracked_file_count": len(records),
        "tracked_bytes": sum(rec.size for rec in records),
        "categories": categories,
        "directories": dirs,
    }


def render_markdown(report: dict[str, object], max_files: int, max_dirs: int) -> str:
    categories = report["categories"]
    assert isinstance(categories, dict)
    directories = report["directories"]
    assert isinstance(directories, list)

    lines = [
        "# Repository Retention Candidate Report",
        "",
        "**Mode:** report-only. No files are deleted, moved, or modified by this scanner.",
        "",
        f"- Tracked files scanned: {report['tracked_file_count']}",
        f"- Tracked bytes scanned: {human_bytes(int(report['tracked_bytes']))}",
        "",
        "## Category Summary",
        "",
        "| Category | Files | Size | Policy |",
        "|---|---:|---:|---|",
    ]

    for category, bucket_obj in sorted(
        categories.items(),
        key=lambda item: (-int(item[1]["bytes"]), item[0]),
    ):
        bucket = bucket_obj
        lines.append(
            f"| `{category}` | {bucket['count']} | {human_bytes(int(bucket['bytes']))} | "
            f"{bucket['policy']} |"
        )

    lines.extend(["", "## Largest Directories", "", "| Directory | Size |", "|---|---:|"])
    for item in directories[:max_dirs]:
        lines.append(f"| `{item['path']}` | {human_bytes(int(item['bytes']))} |")

    lines.extend(["", "## Largest Files By Candidate Class", ""])
    for category, bucket_obj in sorted(
        categories.items(),
        key=lambda item: (-int(item[1]["bytes"]), item[0]),
    ):
        bucket = bucket_obj
        files = bucket["files"]
        if not files:
            continue
        lines.extend([
            f"### `{category}`",
            "",
            f"Policy: {bucket['policy']}",
            "",
            "| File | Size |",
            "|---|---:|",
        ])
        for item in files[:max_files]:
            lines.append(f"| `{item['path']}` | {human_bytes(int(item['bytes']))} |")
        if len(files) > max_files:
            lines.append(f"| … {len(files) - max_files} more | — |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    parser.add_argument("--max-files", type=int, default=8, help="Per-category file detail rows.")
    parser.add_argument("--max-dirs", type=int, default=12, help="Directory rollup rows.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Default: current directory.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    records = tracked_files(repo_root)
    report = build_report(records)
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_markdown(report, args.max_files, args.max_dirs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
