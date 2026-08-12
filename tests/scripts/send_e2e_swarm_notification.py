#!/usr/bin/env python3
"""XYZ-PILOT-207: post-E2E-swarm SES notification.

After a Tester Swarm E2E run completes (`tests/scripts/run_e2e_swarm.sh`),
this script aggregates the results across every agent's `results.md` +
`issue-candidates.md`, composes an HTML email with health icons, severity
counts, agent-by-agent results, and a GitHub deep-link to the run
directory, then sends it via SES to the configured recipients.

Wired into `run_e2e_swarm.sh` as the standard post-completion step
("Persist for every future Tester Swarm E2E this notification as standard
execution textbook" — operator directive 2026-06-02).

Recipients are resolved from Cognito group `OPS_ONLY` only, then filtered
through SES identity verification (`VerificationStatus == Success`) so no
unverified address is targeted.

Subject format: `[<health icon>] <PILOT_NAME> E2E Swarm · <run-id> · <PASS|N FAILED>`

Usage:
  AWS_PROFILE=default python3 tests/scripts/send_e2e_swarm_notification.py \\
      --run-id 2026-06-02T201137Z \\
      --github-ref <archive-commit-sha> \\
      [--recipients-override <OPERATOR_EMAIL>]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import boto3
except ModuleNotFoundError:  # pragma: no cover - exercised by local dry-run smoke.
    boto3 = None  # type: ignore[assignment]

REGION = (
    os.environ.get("XYZ_E2E_NOTIFY_SES_REGION")
    or os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or "<AWS_REGION>"
)
SES_FROM = os.environ.get("XYZ_E2E_NOTIFY_SES_FROM", "<OPERATOR_EMAIL>")
# Operator-codified 2026-06-08 (pass 5 = final): ALL automated outbound
# <PILOT_NAME> notifications share the SAME 3-person OPS-ONLY list — <OPS_USER_2> +
# <OPS_USER_3> + <OPERATOR_NAME> No more separate engineering-signal carve-out. The E2E
# swarm completion summary is one of those automated outbound channels, so
# it ships to the OPS-ONLY 3. <OPS_USER_3> B. is no longer on this list.
DEFAULT_RECIPIENTS = [
    "<OPERATOR_EMAIL>",
    "<OPS_USER_2_EMAIL>",
    "<OPS_USER_3_EMAIL>",
]
COGNITO_USER_POOL_ID = os.environ.get(
    "COGNITO_USER_POOL_ID",
    "<COGNITO_USER_POOL_ID>",
)
NOTIFY_GROUP = "OPS_ONLY"
GITHUB_REPO_TREE_BASE = "https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/tree"

# Severity bucket emojis (BMP escapes to avoid copy-paste fragility)
SEV_ICON = {
    "critical": "\U0001F534",  # red circle
    "major":    "\U0001F7E0",  # orange circle
    "minor":    "\U0001F7E1",  # yellow circle
    "feature":  "\U0001F535",  # blue circle
    "external": "⚪",      # white circle
}

# Health overall icons
HEALTH_ICON = {
    "green":  "\U0001F7E2",  # green circle — all green
    "yellow": "\U0001F7E1",  # yellow circle — partial / warnings only
    "red":    "\U0001F534",  # red circle — failures present
}


# ---- Parsing -----------------------------------------------------------------

_SUMMARY_FAILURES_RE = re.compile(r"^-\s*Failures:\s*`(\d+)`", re.MULTILINE)
_SUMMARY_WARNINGS_RE = re.compile(r"^-\s*Warnings/candidates:\s*`(\d+)`", re.MULTILINE)
_SUMMARY_FINISHED_RE = re.compile(r"^-\s*Finished UTC:\s*`([^`]+)`", re.MULTILINE)

_CANDIDATE_SEV_RE = re.compile(
    r"^-\s*\*\*Severity:\*\*\s*([A-Za-z]+)", re.MULTILINE
)
_CANDIDATE_HEADING_RE = re.compile(r"^##\s+Candidate\b(.*)$", re.MULTILINE)


def _candidate_blocks(body: str) -> list[tuple[str, str]]:
    matches = list(_CANDIDATE_HEADING_RE.finditer(body))
    blocks: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        title = match.group(1).strip(" -:").strip() or f"candidate {idx + 1}"
        blocks.append((title, body[match.end():end].strip()))
    return blocks


def _split_results_row(line: str) -> list[str]:
    """Split a simple Markdown table row written by tests/scripts/common.sh."""
    if not line.startswith("|"):
        return []
    cells = line.strip().strip("|").split("|")
    return [cell.replace("\\|", "|").strip() for cell in cells]


def _interesting_rows(results_path: Path) -> list[dict[str, str]]:
    if not results_path.exists():
        return []
    rows: list[dict[str, str]] = []
    for line in results_path.read_text(errors="replace").splitlines():
        cells = _split_results_row(line)
        if len(cells) != 5:
            continue
        probe, expected, actual, result, notes = cells
        upper = result.upper()
        if not (upper.startswith("FAIL") or upper.startswith("WARN") or upper.startswith("CANDIDATE")):
            continue
        rows.append({
            "probe": probe,
            "expected": expected,
            "actual": actual,
            "result": result,
            "notes": notes,
        })
    return rows


def parse_agent_results(agent_dir: Path) -> dict[str, Any]:
    """Read results.md + agent.exit + issue-candidates.md for one agent."""
    agent_name = agent_dir.name
    results_path = agent_dir / "results.md"
    exit_path = agent_dir / "agent.exit"
    candidates_path = agent_dir / "issue-candidates.md"

    exit_code = -1
    if exit_path.exists():
        try:
            exit_code = int(exit_path.read_text().strip())
        except ValueError:
            exit_code = -1

    failures = 0
    warnings = 0
    finished_utc = ""
    if results_path.exists():
        body = results_path.read_text(errors="replace")
        m = _SUMMARY_FAILURES_RE.search(body)
        if m:
            failures = int(m.group(1))
        m = _SUMMARY_WARNINGS_RE.search(body)
        if m:
            warnings = int(m.group(1))
        m = _SUMMARY_FINISHED_RE.search(body)
        if m:
            finished_utc = m.group(1)

    # Candidates: count explicit `## Candidate...` sections.
    candidates_by_sev: dict[str, int] = {sev: 0 for sev in SEV_ICON}
    candidate_titles: list[str] = []
    if candidates_path.exists():
        body = candidates_path.read_text(errors="replace")
        for title, block in _candidate_blocks(body):
            candidate_titles.append(title)
            sev_match = _CANDIDATE_SEV_RE.search(block)
            sev = (sev_match.group(1).lower() if sev_match else "minor")
            # Strip any "pending operator approval" suffix
            sev = sev.split()[0]
            if sev in candidates_by_sev:
                candidates_by_sev[sev] += 1

    return {
        "agent": agent_name,
        "exit_code": exit_code,
        "failures": failures,
        "warnings": warnings,
        "finished_utc": finished_utc,
        "interesting_rows": _interesting_rows(results_path),
        "candidates_by_sev": candidates_by_sev,
        "candidate_titles": candidate_titles,
    }


def parse_stress_summary(agent_dir: Path) -> dict[str, Any] | None:
    """XYZ_STRESS agent emits a JSON summary — parse it if present."""
    path = agent_dir / "artifacts" / "stress_summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ---- Aggregation -------------------------------------------------------------

def aggregate(run_dir: Path) -> dict[str, Any]:
    agents = []
    total_failures = 0
    total_warnings = 0
    total_candidates_by_sev: dict[str, int] = {sev: 0 for sev in SEV_ICON}
    stress_summary: dict[str, Any] | None = None

    for child in sorted(run_dir.iterdir()):
        if not child.is_dir():
            continue
        if child.name == "XYZ_MASTER":
            # XYZ_MASTER is the consolidator (post-tester) — include for visibility
            # but don't count its failures separately
            pass
        info = parse_agent_results(child)
        agents.append(info)
        total_failures += info["failures"]
        total_warnings += info["warnings"]
        for sev, n in info["candidates_by_sev"].items():
            total_candidates_by_sev[sev] += n
        if child.name == "XYZ_STRESS":
            stress_summary = parse_stress_summary(child)

    # Health classification:
    # Red is reserved for probe/agent failures. Queued candidates, even major
    # ones, are backlog/review signal; otherwise a successful run can render as
    # "Red - failures present" with zero failures.
    if total_failures > 0:
        health = "red"
    elif total_warnings > 0 or any(total_candidates_by_sev[s] > 0 for s in total_candidates_by_sev):
        health = "yellow"
    else:
        health = "green"

    return {
        "agents": agents,
        "total_failures": total_failures,
        "total_warnings": total_warnings,
        "total_candidates_by_sev": total_candidates_by_sev,
        "stress_summary": stress_summary,
        "health": health,
    }


# ---- HTML rendering ----------------------------------------------------------

def _esc(s: str) -> str:
    return html.escape(str(s), quote=False)


def _clip(s: str, limit: int = 260) -> str:
    value = str(s)
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _github_run_url(run_id: str, github_ref: str, agent: str | None = None) -> str:
    suffix = f"/{agent}" if agent else ""
    return f"{GITHUB_REPO_TREE_BASE}/{github_ref}/tests/logs/runs/{run_id}{suffix}/"


def render_email_html(
    run_id: str,
    summary: dict[str, Any],
    github_ref: str = "main",
    *,
    archive_failed: bool = False,
    actions_url: str = "",
) -> str:
    health = summary["health"]
    health_icon = HEALTH_ICON[health]
    health_label = {"green": "All green", "yellow": "Yellow — review", "red": "Red — failures present"}[health]
    total_failures = summary["total_failures"]
    total_warnings = summary["total_warnings"]
    by_sev = summary["total_candidates_by_sev"]
    total_candidates = sum(by_sev.values())

    # ---- Headline block ----
    if archive_failed:
        run_ref = (
            f'<span style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{_esc(run_id)}</span>'
            f'<br><b style="color:#b00020;">Run archive failed:</b> GitHub artefact links are intentionally omitted to avoid 404s.'
            + (
                f'<br><b>Actions run:</b> <a href="{_esc(actions_url)}">{_esc(actions_url)}</a>'
                if actions_url else ""
            )
        )
    else:
        run_ref = (
            f'<a href="{_github_run_url(_esc(run_id), _esc(github_ref))}" '
            f'style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{_esc(run_id)}</a>'
        )

    headline = (
        f'<h2 style="margin-bottom:.2em;">{health_icon} <PILOT_NAME> E2E Tester Swarm</h2>'
        f'<p style="color:#666;margin-top:0;"><b>Run ID:</b> {run_ref}</p>'
        f'<p style="font-size:1.05em;"><b>Overall health:</b> {health_icon} {_esc(health_label)} &nbsp;|&nbsp; '
        f'<b>Probe failures:</b> {total_failures} &nbsp;|&nbsp; '
        f'<b>Warnings:</b> {total_warnings} &nbsp;|&nbsp; '
        f'<b>Candidate defects queued:</b> {total_candidates}</p>'
    )

    # ---- Defects by severity ----
    sev_rows = ""
    for sev in ("critical", "major", "minor", "feature", "external"):
        n = by_sev[sev]
        if n == 0:
            continue
        sev_rows += (
            f'<tr><td style="padding:4px 12px;font-size:1.1em;">{SEV_ICON[sev]}</td>'
            f'<td style="padding:4px 12px;text-transform:capitalize;"><b>{sev}</b></td>'
            f'<td style="padding:4px 12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{n}</td></tr>'
        )
    if not sev_rows:
        defects_block = (
            '<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Defects detected</h3>'
            '<p style="color:#444;"><i>No candidate defects queued by any agent. \U0001F389 Clean run.</i></p>'
        )
    else:
        defects_block = (
            '<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Defects detected (queued for XYZ_MASTER drain)</h3>'
            '<table style="border-collapse:collapse;margin:.4em 0;">'
            '<thead><tr style="background:#f6f7f8;">'
            '<th style="padding:6px 10px;text-align:left;">Icon</th>'
            '<th style="padding:6px 10px;text-align:left;">Severity</th>'
            '<th style="padding:6px 10px;text-align:left;">Count</th>'
            f'</tr></thead><tbody>{sev_rows}</tbody></table>'
        )

    # ---- Agents table ----
    agent_rows = ""
    for info in summary["agents"]:
        agent = info["agent"]
        exit_code = info["exit_code"]
        status_icon = "✅" if exit_code == 0 else "❌" if exit_code > 0 else "⚠️"
        status_text = "PASS" if exit_code == 0 else f"FAIL (exit {exit_code})" if exit_code > 0 else "?"
        agent_label = (
            _esc(agent)
            if archive_failed
            else f'<a href="{_github_run_url(_esc(run_id), _esc(github_ref), _esc(agent))}">{_esc(agent)}</a>'
        )
        cand_n = sum(info["candidates_by_sev"].values())
        agent_rows += (
            f'<tr>'
            f'<td style="padding:4px 10px;">{status_icon}</td>'
            f'<td style="padding:4px 10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">'
            f'{agent_label}</td>'
            f'<td style="padding:4px 10px;">{_esc(status_text)}</td>'
            f'<td style="padding:4px 10px;text-align:right;">{info["failures"]}</td>'
            f'<td style="padding:4px 10px;text-align:right;">{info["warnings"]}</td>'
            f'<td style="padding:4px 10px;text-align:right;">{cand_n}</td>'
            f'</tr>'
        )
    agents_block = (
        '<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Participating agents</h3>'
        '<table style="border-collapse:collapse;margin:.4em 0;">'
        '<thead><tr style="background:#f6f7f8;">'
        '<th style="padding:6px 10px;text-align:left;"></th>'
        '<th style="padding:6px 10px;text-align:left;">Agent</th>'
        '<th style="padding:6px 10px;text-align:left;">Status</th>'
        '<th style="padding:6px 10px;text-align:right;">Failures</th>'
        '<th style="padding:6px 10px;text-align:right;">Warnings</th>'
        '<th style="padding:6px 10px;text-align:right;">Candidates</th>'
        f'</tr></thead><tbody>{agent_rows}</tbody></table>'
    )

    # ---- XYZ_STRESS detail block (concurrent-user metrics) ----
    stress_block = ""
    if summary["stress_summary"]:
        ss = summary["stress_summary"]
        passed = ss.get("passed", False)
        passed_icon = "✅" if passed else "❌"
        # `targets` is a dict keyed by label; aggregate top-level totals from it.
        targets_dict = ss.get("targets", {}) or {}
        total_requests = sum(t.get("requests", 0) for t in targets_dict.values())
        total_failures = sum(t.get("failures", 0) for t in targets_dict.values())
        http_requests = ss.get("http_requests", 0)
        http_failures = ss.get("http_failures", 0)
        db_requests = ss.get("db_requests", 0)
        db_failures = ss.get("db_failures", 0)
        session_hold_requests = ss.get("session_hold_requests", 0)
        session_hold_failures = ss.get("session_hold_failures", 0)
        iterations = max(1, ss.get("iterations", 1))
        inferred_users = ss.get("users", 0)
        db_enabled = bool(ss.get("db_enabled", False))
        target_rows = ""
        for label, t in targets_dict.items():
            error_rate_pct = t.get("error_rate", 0.0) * 100
            target_rows += (
                f'<tr>'
                f'<td style="padding:4px 10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{_esc(t.get("kind", "http"))}</td>'
                f'<td style="padding:4px 10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{_esc(label)}</td>'
                f'<td style="padding:4px 10px;text-align:right;">{t.get("requests", 0)}</td>'
                f'<td style="padding:4px 10px;text-align:right;">{t.get("failures", 0)}</td>'
                f'<td style="padding:4px 10px;text-align:right;">{error_rate_pct:.2f}%</td>'
                f'<td style="padding:4px 10px;text-align:right;">{t.get("p50_ms", 0):.0f}</td>'
                f'<td style="padding:4px 10px;text-align:right;">{t.get("p95_ms", 0):.0f}</td>'
                f'<td style="padding:4px 10px;text-align:right;">{t.get("max_ms", 0):.0f}</td>'
                f'<td style="padding:4px 10px;text-align:right;">{t.get("max_allowed_p95_ms", 0):.0f}</td>'
                f'</tr>'
            )
        db_note = ""
        if db_enabled:
            db_note = (
                f'<br>DB lane: <b>{db_requests}</b> read-only Exasol probes '
                f'(<b>{db_failures}</b> failures) · '
                f'session-hold probes <b>{session_hold_requests}</b> '
                f'(<b>{session_hold_failures}</b> failures, '
                f'{ss.get("session_hold_seconds", 0):.0f}s hold) · '
                f'{ss.get("db_warmup_query_count", 0)} static warm-up queries + '
                f'{ss.get("db_dynamic_view_query_count", 0)} dynamic view queries per DB iteration · '
                f'DB p95 threshold {ss.get("max_db_p95_ms", 45000):.0f} ms'
            )
        stress_block = (
            f'<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">XYZ_STRESS · concurrent UI + Exasol regression load <span style="color:#888;font-weight:normal;font-size:.85em;">{passed_icon}</span></h3>'
            f'<p style="color:#444;margin:.4em 0;"><b>{inferred_users}</b> concurrent users · '
            f'<b>{iterations}</b> iterations · '
            f'duration <b>{ss.get("duration_seconds", 0):.1f}s</b> · '
            f'total probes <b>{total_requests}</b> · '
            f'total failures <b>{total_failures}</b> · '
            f'HTTP probes <b>{http_requests}</b> (<b>{http_failures}</b> failures) · '
            f'error-rate threshold {ss.get("max_error_rate", 0.05) * 100:.1f}% · '
            f'HTTP p95 threshold {ss.get("max_p95_ms", 5000):.0f} ms'
            f'{db_note}</p>'
            '<table style="border-collapse:collapse;margin:.4em 0;">'
            '<thead><tr style="background:#f6f7f8;">'
            '<th style="padding:6px 10px;text-align:left;">Kind</th>'
            '<th style="padding:6px 10px;text-align:left;">Target</th>'
            '<th style="padding:6px 10px;text-align:right;">Reqs</th>'
            '<th style="padding:6px 10px;text-align:right;">Fails</th>'
            '<th style="padding:6px 10px;text-align:right;">Err rate</th>'
            '<th style="padding:6px 10px;text-align:right;">p50 ms</th>'
            '<th style="padding:6px 10px;text-align:right;">p95 ms</th>'
            '<th style="padding:6px 10px;text-align:right;">max ms</th>'
            '<th style="padding:6px 10px;text-align:right;">p95 limit</th>'
            f'</tr></thead><tbody>{target_rows}</tbody></table>'
        )

    # ---- Summary footer ----
    detected_summary = ""
    if total_failures > 0 or total_warnings > 0 or total_candidates > 0:
        bullets = []
        if total_failures > 0:
            bullets.append(f"⚠️ <b>{total_failures}</b> probe failure(s) across the swarm — see per-agent rows above for triage.")
        if total_warnings > 0:
            bullets.append(f"⚡ <b>{total_warnings}</b> warning(s) flagged — non-fatal but worth review.")
        if total_candidates > 0:
            cand_breakdown = ", ".join(
                f"{by_sev[s]} {s}" for s in ("critical", "major", "minor", "feature", "external") if by_sev[s] > 0
            )
            bullets.append(f"\U0001F4DD <b>{total_candidates}</b> candidate defect(s) queued for XYZ_MASTER drain ({cand_breakdown}).")
        detail_rows = ""
        for info in summary["agents"]:
            for row in info.get("interesting_rows", []):
                detail_rows += (
                    "<tr>"
                    f'<td style="padding:4px 10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{_esc(info["agent"])}</td>'
                    f'<td style="padding:4px 10px;">{_esc(row["result"])}</td>'
                    f'<td style="padding:4px 10px;">{_esc(row["probe"])}</td>'
                    f'<td style="padding:4px 10px;">{_esc(_clip(row["actual"]))}</td>'
                    f'<td style="padding:4px 10px;">{_esc(_clip(row["notes"], 180))}</td>'
                    "</tr>"
                )
        details_table = ""
        if detail_rows:
            details_table = (
                '<table style="border-collapse:collapse;margin:.4em 0;">'
                '<thead><tr style="background:#f6f7f8;">'
                '<th style="padding:6px 10px;text-align:left;">Agent</th>'
                '<th style="padding:6px 10px;text-align:left;">Result</th>'
                '<th style="padding:6px 10px;text-align:left;">Probe</th>'
                '<th style="padding:6px 10px;text-align:left;">Actual</th>'
                '<th style="padding:6px 10px;text-align:left;">Notes</th>'
                f'</tr></thead><tbody>{detail_rows}</tbody></table>'
            )
        detected_summary = (
            '<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">What was detected</h3>'
            '<ul style="line-height:1.6;">' + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>"
            + details_table
        )
    else:
        detected_summary = (
            '<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">What was detected</h3>'
            '<p style="color:#444;"><i>\U0001F31F Nothing to flag. All probes green, no warnings, no candidates queued.</i></p>'
        )

    # ---- Footer ----
    if archive_failed:
        footer_detail = (
            "Run artefacts were generated in the GitHub runner but were not pushed to the repository. "
            "Treat this as an operational archive/notification-path defect; inspect the Actions run log first."
        )
    else:
        footer_detail = (
            f'Run artefacts: <a href="{_github_run_url(_esc(run_id), _esc(github_ref))}">'
            f'tests/logs/runs/{_esc(run_id)}/</a>. Archive ref: <code>{_esc(github_ref)}</code>.'
        )
    footer = (
        f'<p style="color:#888;font-size:.85em;margin-top:2em;">'
        f'Auto-generated by <code>tests/scripts/send_e2e_swarm_notification.py</code> on completion of '
        f'<code>tests/scripts/run_e2e_swarm.sh</code>. {footer_detail} '
        f'To stop receiving these, reply with "unsubscribe E2E swarm" to <OPERATOR_EMAIL>.'
        f'</p>'
    )

    return (
        f'<html><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;'
        f'max-width:840px;line-height:1.5;color:#222;text-align:left;">'
        f'{headline}{detected_summary}{defects_block}{stress_block}{agents_block}{footer}'
        f'</body></html>'
    )


def render_subject(run_id: str, summary: dict[str, Any], *, archive_failed: bool = False) -> str:
    health_icon = HEALTH_ICON[summary["health"]]
    total_failures = summary["total_failures"]
    total_warnings = summary["total_warnings"]
    total_candidates = sum(summary["total_candidates_by_sev"].values())
    if archive_failed:
        verdict = "ARCHIVE FAILED"
        health_icon = HEALTH_ICON["red"]
    elif total_failures > 0:
        verdict = f"{total_failures} FAILED"
    elif total_candidates > 0:
        verdict = f"PASS · {total_candidates} candidate(s) queued"
    elif total_warnings > 0:
        verdict = f"PASS · {total_warnings} warning(s)"
    else:
        verdict = "PASS · clean"
    return f"{health_icon} <PILOT_NAME> E2E Swarm · {run_id} · {verdict}"


# ---- Send --------------------------------------------------------------------

def send_email(subject: str, html_body: str, recipients: list[str], dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return {"dry_run": True, "would_send_to": recipients, "subject": subject, "html_len": len(html_body)}
    if boto3 is None:
        raise RuntimeError("boto3 is required to send SES email; install boto3 or use --dry-run")
    ses = boto3.client("ses", region_name=REGION)
    resp = ses.send_email(
        Source=SES_FROM,
        Destination={"ToAddresses": recipients},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}},
        },
    )
    return {"sent_to": recipients, "subject": subject, "message_id": resp.get("MessageId")}


def _ops_only_recipients() -> list[str]:
    """Return Cognito OPS_ONLY emails (fallback to static ops list on errors)."""
    if boto3 is None:
        return list(DEFAULT_RECIPIENTS)
    out: dict[str, str] = {}
    try:
        cognito = boto3.client("cognito-idp", region_name=REGION)
        paginator = cognito.get_paginator("list_users_in_group")
        for page in paginator.paginate(UserPoolId=COGNITO_USER_POOL_ID, GroupName=NOTIFY_GROUP, Limit=60):
            for user in page.get("Users", []):
                email = next(
                    (a.get("Value", "") for a in user.get("Attributes", []) if a.get("Name") == "email"),
                    "",
                ).strip()
                if email and "@" in email:
                    out.setdefault(email.lower(), email)
    except Exception as exc:  # noqa: BLE001
        print(
            f"WARN: Cognito OPS_ONLY lookup failed ({type(exc).__name__}); "
            "falling back to static OPS_ONLY defaults.",
            file=sys.stderr,
        )
        return list(DEFAULT_RECIPIENTS)
    if not out:
        return list(DEFAULT_RECIPIENTS)
    return sorted(out.values(), key=str.lower)


def _ses_verified_recipients(addresses: list[str]) -> list[str]:
    """Keep only SES-verified addresses (sandbox-safe delivery filter)."""
    if boto3 is None:
        return addresses
    ses = boto3.client("ses", region_name=REGION)
    verified: list[str] = []
    for idx in range(0, len(addresses), 100):
        chunk = addresses[idx : idx + 100]
        try:
            attrs = ses.get_identity_verification_attributes(Identities=chunk).get(
                "VerificationAttributes", {}
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"WARN: SES verification lookup failed ({type(exc).__name__}); "
                "proceeding with unfiltered OPS_ONLY list.",
                file=sys.stderr,
            )
            return addresses
        for addr in chunk:
            status = str(attrs.get(addr, {}).get("VerificationStatus", "")).lower()
            if status == "success":
                verified.append(addr)
    return verified


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--run-id", required=True, help="UTC run-id, e.g. 2026-06-02T201137Z")
    parser.add_argument("--run-dir", default=None,
                        help="Override run directory path (default: tests/logs/runs/<run-id>)")
    parser.add_argument("--recipients-override", default=None,
                        help="Comma-separated explicit override (e.g. for <OPS_USER_1>-only preview)")
    parser.add_argument("--github-ref", default="main",
                        help="GitHub ref used in run artefact links (prefer the pushed archive commit SHA)")
    parser.add_argument("--archive-failed", action="store_true",
                        help="Send an operational failure email without GitHub artefact links")
    parser.add_argument("--actions-url", default="",
                        help="GitHub Actions run URL to include when --archive-failed is set")
    parser.add_argument("--sent-marker", default=None,
                        help="Path to idempotency marker; default: <run-dir>/notification.sent.json")
    parser.add_argument("--force", action="store_true",
                        help="Send even when the sent-marker already exists")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render the HTML + subject + recipients but do NOT call SES")
    parser.add_argument("--write-html", default=None,
                        help="Optional path to also write the rendered HTML for visual inspection")
    args = parser.parse_args()

    run_id = args.run_id
    if args.run_dir:
        run_dir = Path(args.run_dir)
    else:
        repo_root = Path(__file__).resolve().parents[2]
        run_dir = repo_root / "tests" / "logs" / "runs" / run_id
    if not run_dir.is_dir():
        print(f"ERROR: run directory not found: {run_dir}", file=sys.stderr)
        return 2
    marker_path = Path(args.sent_marker) if args.sent_marker else run_dir / "notification.sent.json"
    if marker_path.exists() and not args.force and not args.dry_run:
        try:
            marker = json.loads(marker_path.read_text())
        except (json.JSONDecodeError, OSError):
            marker = {"path": str(marker_path)}
        print(
            "Notification already sent for this run; "
            f"marker={marker_path} message_id={marker.get('message_id', '?')}"
        )
        return 0

    summary = aggregate(run_dir)
    subject = render_subject(run_id, summary, archive_failed=args.archive_failed)
    html_body = render_email_html(
        run_id,
        summary,
        github_ref=args.github_ref,
        archive_failed=args.archive_failed,
        actions_url=args.actions_url,
    )

    if args.write_html:
        Path(args.write_html).write_text(html_body)
        print(f"wrote HTML preview to {args.write_html} ({len(html_body)} bytes)")

    if args.recipients_override or os.environ.get("XYZ_E2E_NOTIFY_RECIPIENTS"):
        print(
            "INFO: recipients override ignored; Swarm notifications are strictly "
            "OPS_ONLY with SES verification filtering.",
            file=sys.stderr,
        )
    recipients = _ops_only_recipients()
    recipients = _ses_verified_recipients(recipients)
    if not recipients:
        print("WARN: no SES-verified OPS_ONLY recipients found; skipping send.")
        if not args.dry_run:
            marker_path.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "sent_utc": subprocess.check_output(
                            ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], text=True
                        ).strip(),
                        "github_ref": args.github_ref,
                        "archive_failed": args.archive_failed,
                        "actions_url": args.actions_url,
                        "recipients": [],
                        "subject": subject,
                        "ses_region": REGION,
                        "ses_source": SES_FROM,
                        "message_id": None,
                        "skipped_reason": "no_ses_verified_ops_only_recipients",
                        "script": "tests/scripts/send_e2e_swarm_notification.py",
                    },
                    indent=2,
                )
                + "\n"
            )
        return 0

    print(f"Subject: {subject}")
    print(f"Recipients: {recipients}")
    print(f"SES: region={REGION} source={SES_FROM}")
    print(f"Health: {summary['health']} · failures={summary['total_failures']} "
          f"warnings={summary['total_warnings']} "
          f"candidates={sum(summary['total_candidates_by_sev'].values())}")
    print(f"Agents: {[a['agent'] for a in summary['agents']]}")

    resp = send_email(subject, html_body, recipients, dry_run=args.dry_run)
    print(json.dumps(resp, indent=2))
    if not args.dry_run:
        marker_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "sent_utc": subprocess.check_output(
                        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], text=True
                    ).strip(),
                    "github_ref": args.github_ref,
                    "archive_failed": args.archive_failed,
                    "actions_url": args.actions_url,
                    "recipients": recipients,
                    "subject": subject,
                    "ses_region": REGION,
                    "ses_source": SES_FROM,
                    "message_id": resp.get("message_id"),
                    "script": "tests/scripts/send_e2e_swarm_notification.py",
                },
                indent=2,
            )
            + "\n"
        )
        print(f"Wrote sent marker: {marker_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
