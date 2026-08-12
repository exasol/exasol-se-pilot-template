#!/usr/bin/env python3
"""Verify the <PILOT_NAME> UI provenance contract across all user-facing UIs."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _require(rel: str, pattern: str, description: str, *, flags: int = re.MULTILINE) -> str | None:
    content = _text(rel)
    if re.search(pattern, content, flags):
        return None
    return f"{rel}: missing {description}"


def main() -> int:
    checks = [
        # Operator Dashboard: static S3 page, manual BUILD_INFO stamp.
        (
            "scheduler/status_dashboard/dashboard.html",
            r"const BUILD_INFO\s*=\s*\{[\s\S]*lastDeploymentIssue:",
            "BUILD_INFO.lastDeploymentIssue",
        ),
        (
            "scheduler/status_dashboard/dashboard.html",
            r"const BUILD_INFO\s*=\s*\{[\s\S]*trackerUrl:",
            "BUILD_INFO.trackerUrl",
        ),
        (
            "scheduler/status_dashboard/dashboard.html",
            r"const BUILD_INFO\s*=\s*\{[\s\S]*builtAtUtc:",
            "BUILD_INFO.builtAtUtc",
        ),
        (
            "scheduler/status_dashboard/dashboard.html",
            r"dashboardSessionStartedAtUtc:\s*new Date\(\)\.toISOString\(\)",
            "dashboard session start timestamp",
        ),
        (
            "scheduler/status_dashboard/dashboard.html",
            r"document\.lastModified",
            "S3 Last-Modified deployment timestamp",
        ),
        # Chatbot: Docker-stamped Streamlit container.
        ("services/chatbot/app.py", r"XYZ_LAST_DEPLOYMENT_ISSUE", "chatbot issue env"),
        ("services/chatbot/app.py", r"XYZ_BUILD_AT_UTC", "chatbot build env"),
        ("services/chatbot/app.py", r"XYZ_DEPLOYED_AT_UTC", "chatbot deploy env"),
        ("services/chatbot/app.py", r"APP_STARTED_AT_UTC", "chatbot process start timestamp"),
        ("services/chatbot/app.py", r"chatbot process started", "chatbot rendered process-start label"),
        ("services/chatbot/Dockerfile", r"ARG XYZ_LAST_DEPLOYMENT_ISSUE=", "chatbot Docker build issue arg"),
        ("services/chatbot/Dockerfile", r"ENV XYZ_LAST_DEPLOYMENT_ISSUE=", "chatbot Docker runtime issue env"),
        (
            "services/chatbot/docker-compose.yml",
            r"XYZ_LAST_DEPLOYMENT_ISSUE:\s*\$\{XYZ_LAST_DEPLOYMENT_ISSUE:-XYZ-PILOT-\d{3}\}",
            "chatbot compose issue stamp",
        ),
        (
            "services/chatbot/docker-compose.yml",
            r"XYZ_DEPLOYED_AT_UTC:\s*\$\{XYZ_DEPLOYED_AT_UTC:-not-stamped\}",
            "chatbot compose deploy stamp",
        ),
        # QBR: Docker-stamped Streamlit container.
        ("services/streamlit-insights/app.py", r"XYZ_LAST_DEPLOYMENT_ISSUE", "QBR issue env"),
        ("services/streamlit-insights/app.py", r"XYZ_DEPLOYMENT_REF_URL", "QBR candidate evidence URL env"),
        ("services/streamlit-insights/app.py", r"XYZ_BUILD_AT_UTC", "QBR build env"),
        ("services/streamlit-insights/app.py", r"XYZ_DEPLOYED_AT_UTC", "QBR deploy env"),
        ("services/streamlit-insights/app.py", r"APP_STARTED_AT_UTC", "QBR process start timestamp"),
        ("services/streamlit-insights/app.py", r"QBR process started", "QBR rendered process-start label"),
        ("services/streamlit-insights/Dockerfile", r"ARG XYZ_LAST_DEPLOYMENT_ISSUE=", "QBR Docker build issue arg"),
        ("services/streamlit-insights/Dockerfile", r"ARG XYZ_DEPLOYMENT_REF_URL=", "QBR Docker build candidate URL arg"),
        ("services/streamlit-insights/Dockerfile", r"ENV XYZ_LAST_DEPLOYMENT_ISSUE=", "QBR Docker runtime issue env"),
        ("services/streamlit-insights/Dockerfile", r"ENV XYZ_DEPLOYMENT_REF_URL=", "QBR Docker runtime candidate URL env"),
        (
            "services/streamlit-insights/docker-compose.yml",
            r"XYZ_LAST_DEPLOYMENT_ISSUE:\s*\$\{XYZ_LAST_DEPLOYMENT_ISSUE:-XYZ-PILOT-\d{3}\}",
            "QBR compose issue stamp",
        ),
        (
            "services/streamlit-insights/docker-compose.yml",
            r"XYZ_DEPLOYMENT_REF_URL:\s*\$\{XYZ_DEPLOYMENT_REF_URL:-\}",
            "QBR compose candidate evidence URL",
        ),
        (
            "services/streamlit-insights/docker-compose.yml",
            r"XYZ_DEPLOYED_AT_UTC:\s*\$\{XYZ_DEPLOYED_AT_UTC:-not-stamped\}",
            "QBR compose deploy stamp",
        ),
        # Central discipline documentation.
        (
            "docs/30-runbooks/ui/XYZ_PILOT_UI_RUNBOOK.md",
            r"single last deployment-cause issue",
            "central UI provenance single-issue rule",
        ),
        (
            "docs/30-runbooks/ui/XYZ_PILOT_UI_RUNBOOK.md",
            r"XYZ_LAST_DEPLOYMENT_ISSUE",
            "central Docker-stamped UI env contract",
        ),
    ]

    errors = [
        msg
        for rel, pattern, description in checks
        if (msg := _require(rel, pattern, description))
    ]
    dashboard = _text("scheduler/status_dashboard/dashboard.html")
    issue_match = re.search(r"lastDeploymentIssue:\s*\"(XYZ-PILOT-\d{3})\"", dashboard)
    url_match = re.search(r"trackerUrl:\s*\"([^\"]+)\"", dashboard)
    if not issue_match or not url_match:
        errors.append("scheduler/status_dashboard/dashboard.html: missing comparable issue/link pair")
    else:
        issue = issue_match.group(1).lower()
        url = url_match.group(1).lower()
        if f"#{issue}" not in url:
            errors.append("scheduler/status_dashboard/dashboard.html: trackerUrl anchor does not match lastDeploymentIssue")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"ui_provenance_errors={len(errors)}", file=sys.stderr)
        return 1

    print(f"ui_provenance_checks={len(checks)} errors=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
