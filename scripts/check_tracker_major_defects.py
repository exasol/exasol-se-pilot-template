#!/usr/bin/env python3
"""Classifier for critical/major tracker heads-up notifications."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import html
import json
from pathlib import Path
import re
import subprocess
import sys


TRACKER = "docs/XYZ_PILOT_ISSUE_TRACKER.md"
NOTIFICATION_RECIPIENTS = ("<OPERATOR_NAME>", "<OPS_USER_2>", "<OPS_USER_3>")
TERMINAL_STATUSES = ("resolved", "wontfix")
DEFECT_TERMS = (
    "defect",
    "bug",
    "failure",
    "fails",
    "failed",
    "error",
    "exception",
    "broken",
    "regression",
    "misconfiguration",
    "auth",
    "credential",
    "security",
    "risk",
    "data loss",
    "stale",
    "missing",
    "drift",
    "corrupt",
    "incorrect",
    "invalid",
    "blocked",
    "blocker",
)
SECRETISH_RE = re.compile(
    r"(?i)(token|secret|password|passwd|pwd|key|credential)=([^\s,)]+)"
)


@dataclass
class Issue:
    issue_id: str
    title: str
    status: str
    severity: str
    area: str
    summary: str
    body: str


def git_show(ref: str, path: str) -> str:
    if Path(ref).is_file():
        return Path(ref).read_text(encoding="utf-8")
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], text=True)


def strip_md(text: str) -> str:
    return (
        text.replace("~~", "")
        .replace("**", "")
        .replace("`", "")
        .strip()
    )


def redact(text: str) -> str:
    return SECRETISH_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)


def split_md_row(line: str) -> list[str]:
    body = line[1:-1] if line.startswith("|") and line.endswith("|") else line.strip("|")
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in body:
        if char == "\\" and not escaped:
            escaped = True
            current.append(char)
            continue
        if char == "|" and not escaped:
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(char)
        escaped = False
    cells.append("".join(current).strip())
    return cells


def parse_top_rows(src: str) -> dict[str, dict[str, str]]:
    top_rows: dict[str, dict[str, str]] = {}
    match = re.search(r"^## Active issues[^\n]*\n(?P<body>.*?)(?=^---\s*$)", src, re.S | re.M)
    if not match:
        return top_rows
    for line in match.group("body").splitlines():
        if not line.startswith("|"):
            continue
        cells = split_md_row(line)
        if len(cells) < 4 or cells[0] in {"ID", "---"}:
            continue
        id_match = re.search(r"XYZ-PILOT-(\d{3})", cells[0])
        if not id_match:
            continue
        issue_id = f"XYZ-PILOT-{id_match.group(1)}"
        top_rows[issue_id] = {
            "severity": strip_md(cells[1]),
            "area": strip_md(cells[2]),
            "summary": strip_md(cells[3]),
        }
    return top_rows


def field(body: str, name: str) -> str:
    match = re.search(rf"^-\s+\*\*{re.escape(name)}:\*\*\s*(.+)$", body, re.M)
    return strip_md(match.group(1)) if match else ""


def parse_issues(src: str) -> dict[str, Issue]:
    top_rows = parse_top_rows(src)
    issues: dict[str, Issue] = {}
    pattern = re.compile(
        r'<a id="XYZ-PILOT-(\d{3})"></a>\s*\n(?P<section>### XYZ-PILOT-\d{3}\b.*?)(?=\n<a id="XYZ-PILOT-\d{3}"></a>|\n## Issue lifecycle|\Z)',
        re.S,
    )
    for match in pattern.finditer(src):
        issue_id = f"XYZ-PILOT-{match.group(1)}"
        section = match.group("section")
        heading = section.splitlines()[0].strip()
        title = strip_md(heading.replace(f"### {issue_id}", "").lstrip(" -"))
        top = top_rows.get(issue_id, {})
        severity = field(section, "Severity") or top.get("severity", "")
        area = field(section, "Area") or top.get("area", "")
        status = field(section, "Status")
        summary = top.get("summary", title)
        issues[issue_id] = Issue(
            issue_id=issue_id,
            title=title,
            status=status,
            severity=severity,
            area=area,
            summary=summary,
            body=section,
        )
    return issues


def is_candidate(issue: Issue) -> bool:
    severity = issue.severity.lower().strip()
    if not (severity.startswith("critical") or severity.startswith("major")):
        return False
    status = issue.status.lower().strip()
    if status.startswith(TERMINAL_STATUSES):
        return False
    text = " ".join([issue.title, issue.area, issue.summary, issue.body]).lower()
    if severity.startswith("critical"):
        return True
    return any(term in text for term in DEFECT_TERMS)


def severity_level(value: str) -> int:
    severity = value.lower().strip()
    if severity.startswith("critical"):
        return 3
    if severity.startswith("major"):
        return 2
    return 1


def is_elevation_candidate(before: Issue, after: Issue) -> bool:
    if before.status.lower().strip().startswith(TERMINAL_STATUSES):
        return False
    if after.status.lower().strip().startswith(TERMINAL_STATUSES):
        return False
    if severity_level(before.severity) >= severity_level(after.severity):
        return False
    return is_candidate(after)


def is_resolution_candidate(before: Issue, after: Issue) -> bool:
    severity = after.severity.lower().strip()
    if not (severity.startswith("critical") or severity.startswith("major")):
        return False
    if before.status.lower().strip().startswith("resolved"):
        return False
    if not after.status.lower().strip().startswith("resolved"):
        return False
    text = " ".join([after.title, after.area, after.summary, after.body]).lower()
    if severity.startswith("critical"):
        return True
    return any(term in text for term in DEFECT_TERMS)


def tracker_url(issue_id: str) -> str:
    server = subprocess.getoutput("git config --get remote.origin.url").strip()
    if server.startswith("https://github.com/"):
        repo_url = server[:-4] if server.endswith(".git") else server
    else:
        repo_url = "https://github.com/<GITHUB_REPOSITORY>"
    return f"{repo_url}/blob/main/{TRACKER}#{issue_id}"


def issue_severity_count(issue: Issue) -> str:
    severity = issue.severity.lower().strip()
    if severity.startswith("critical"):
        return "1 critical"
    if severity.startswith("major"):
        return "1 major"
    return "1 issue"


def issue_severity_word(issue: Issue) -> str:
    severity = issue.severity.lower().strip()
    if severity.startswith("critical"):
        return "critical"
    if severity.startswith("major"):
        return "major"
    return "qualifying"


def subject_summary(issue: Issue, max_len: int = 40) -> str:
    summary = redact(issue.summary or issue.title)
    summary = " ".join(summary.split())
    if len(summary) > max_len:
        summary = summary[: max_len - 3].rstrip() + "..."
    return summary


def proposed_subject(issue: Issue) -> str:
    return (
        f'<PILOT_NAME> Heads-Up: {issue_severity_count(issue)} defect filed/elevated : '
        f'{issue.issue_id} - "{subject_summary(issue)}"'
    )


def proposed_resolved_subject(issue: Issue) -> str:
    prefix = f"<PILOT_NAME> Resolved: {issue_severity_count(issue)} defect shipped : {issue.issue_id} - "
    max_subject_len = 100
    max_summary_len = max(12, max_subject_len - len(prefix) - 2)
    return f'{prefix}"{subject_summary(issue, max_len=max_summary_len)}"'


def render_notification_summary(issue: Issue) -> str:
    return "\n".join(
        [
            f"## Heads-Up Notification For {issue.issue_id}",
            "",
            f"- Subject: `{proposed_subject(issue)}`",
            f"- Recipients: `{', '.join(NOTIFICATION_RECIPIENTS)}` only",
            f"- Tracker: {tracker_url(issue.issue_id)}",
            f"- Severity: `{redact(issue.severity)}`",
            f"- Area: `{redact(issue.area)}`",
            f"- Status: `{redact(issue.status)}`",
            f"- Summary: {redact(issue.summary)}",
            "",
            "Full issue details stay in the email body / tracker link, never in the subject.",
            "",
        ]
    )


def render_email_text(issue: Issue) -> str:
    severity_word = issue_severity_word(issue)
    return "\n".join(
        [
            "<PILOT_NAME> Heads-Up",
            "",
            f"A {severity_word} defect was filed or elevated in the <PILOT_NAME> tracker.",
            "",
            f"Issue: {issue.issue_id}",
            f"Severity: {redact(issue.severity)}",
            f"Area: {redact(issue.area)}",
            f"Status: {redact(issue.status)}",
            f"Summary: {redact(issue.summary)}",
            f"Tracker: {tracker_url(issue.issue_id)}",
            "",
            "This email is intentionally scoped to one issue only so it can be forwarded directly to an assigned developer.",
            "Generic feature requests and non-major tracker updates are excluded by policy.",
            "",
        ]
    )


def render_email_html(issue: Issue) -> str:
    severity_word = html.escape(issue_severity_word(issue))
    issue_id = html.escape(issue.issue_id)
    severity = html.escape(redact(issue.severity))
    area = html.escape(redact(issue.area))
    status = html.escape(redact(issue.status))
    summary = html.escape(redact(issue.summary))
    url = html.escape(tracker_url(issue.issue_id), quote=True)
    return f"""<html><body style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.45;color:#222;">
<h2 style="margin:0 0 12px 0;"><PILOT_NAME> Heads-Up</h2>
<p>A <strong>{severity_word}</strong> defect was filed or elevated in the <PILOT_NAME> tracker.</p>
<table style="border-collapse:collapse;margin:12px 0;">
  <tr><td style="padding:4px 14px 4px 0;color:#666;">Issue:</td><td><a href="{url}"><strong>{issue_id}</strong></a></td></tr>
  <tr><td style="padding:4px 14px 4px 0;color:#666;">Severity:</td><td>{severity}</td></tr>
  <tr><td style="padding:4px 14px 4px 0;color:#666;">Area:</td><td>{area}</td></tr>
  <tr><td style="padding:4px 14px 4px 0;color:#666;">Status:</td><td>{status}</td></tr>
  <tr><td style="padding:4px 14px 4px 0;color:#666;">Summary:</td><td>{summary}</td></tr>
  <tr><td style="padding:4px 14px 4px 0;color:#666;">Tracker:</td><td><a href="{url}">{url}</a></td></tr>
</table>
<p>This email is intentionally scoped to one issue only so it can be forwarded directly to an assigned developer.</p>
<p style="color:#666;">Generic feature requests and non-major tracker updates are excluded by policy.</p>
</body></html>"""


def render_resolved_text(issue: Issue) -> str:
    severity_word = issue_severity_word(issue)
    return "\n".join(
        [
            "<PILOT_NAME> Resolved / Shipped",
            "",
            f"A {severity_word} defect was marked Resolved + Shipped in the <PILOT_NAME> tracker.",
            "",
            f"Issue: {issue.issue_id}",
            f"Severity: {redact(issue.severity)}",
            f"Area: {redact(issue.area)}",
            f"Status: {redact(issue.status)}",
            f"Summary: {redact(issue.summary)}",
            f"Tracker: {tracker_url(issue.issue_id)}",
            "",
            "This SNS notification is intentionally scoped to one resolved/shipped issue only.",
            "Generic feature requests and non-major tracker updates are excluded by policy.",
            "",
        ]
    )


def write_ses_messages(
    candidates: list[Issue],
    output_dir: Path,
    source: str,
    recipients: list[str],
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    for issue in candidates:
        payload = {
            "Source": source,
            "Destination": {"ToAddresses": recipients},
            "Message": {
                "Subject": {
                    "Data": proposed_subject(issue),
                    "Charset": "UTF-8",
                },
                "Body": {
                    "Text": {
                        "Data": render_email_text(issue),
                        "Charset": "UTF-8",
                    },
                    "Html": {
                        "Data": render_email_html(issue),
                        "Charset": "UTF-8",
                    },
                },
            },
        }
        path = output_dir / f"{issue.issue_id}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return len(candidates)


def write_sns_messages(
    candidates: list[Issue],
    output_dir: Path,
    topic_arn: str,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    for issue in candidates:
        payload = {
            "TopicArn": topic_arn,
            "Subject": proposed_resolved_subject(issue),
            "Message": render_resolved_text(issue),
        }
        path = output_dir / f"{issue.issue_id}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return len(candidates)


def render_report(
    base: str,
    head: str,
    candidates: list[Issue],
    email_enabled: bool,
    mode: str,
) -> str:
    channel = "SNS" if mode == "resolved" else "SES"
    event_name = "resolved/shipped" if mode == "resolved" else "filed"
    email_state = (
        "enabled for push candidates through AWS SES"
        if email_enabled and mode == "filed"
        else "enabled for push candidates through AWS SNS"
        if email_enabled and mode == "resolved"
        else "disabled for this dry-run/manual classification"
    )
    lines = [
        "# <PILOT_NAME> Tracker Heads-Up Classification",
        "",
        f"- Base: `{base}`",
        f"- Head: `{head}`",
        f"- Policy: `critical` / `major` defect-like issues only, event=`{event_name}`",
        f"- Delivery model: exactly one {channel} notification per single issue when enabled",
        "- Aggregation rule: never combine multiple issues in one delivered subject or body",
        f"- Approved email recipients: `{', '.join(NOTIFICATION_RECIPIENTS)}` only",
        f"- Notification: {email_state}",
        f"- Candidate notification count: `{len(candidates)}`",
        "",
    ]
    if not candidates:
        subject = (
            "<PILOT_NAME> Resolved: no critical/major tracker resolutions detected"
            if mode == "resolved"
            else "<PILOT_NAME> Heads-Up: no critical/major tracker defects detected"
        )
        lines.append(f"- Proposed subject: `{subject}`")
        lines.append("")
        lines.append("No matching heads-up candidates were detected.")
        return "\n".join(lines) + "\n"

    for index, issue in enumerate(candidates, start=1):
        subject = proposed_resolved_subject(issue) if mode == "resolved" else proposed_subject(issue)
        lines.append(f"### Notification {index} Of {len(candidates)}")
        lines.append("")
        lines.append(f"## Heads-Up Notification For {issue.issue_id}")
        lines.append("")
        lines.append(f"- Subject: `{subject}`")
        lines.append(f"- Recipients: `{', '.join(NOTIFICATION_RECIPIENTS)}` only")
        lines.append(f"- Tracker: {tracker_url(issue.issue_id)}")
        lines.append(f"- Severity: `{redact(issue.severity)}`")
        lines.append(f"- Area: `{redact(issue.area)}`")
        lines.append(f"- Status: `{redact(issue.status)}`")
        lines.append(f"- Summary: {redact(issue.summary)}")
        lines.append("")
    lines.append("Each section above is a separate notification candidate; do not concatenate them.")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD~1")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--mode", choices=("filed", "resolved"), default="filed")
    parser.add_argument("--github-summary", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--ses-output-dir")
    parser.add_argument("--ses-source")
    parser.add_argument("--ses-recipient", action="append", default=[])
    parser.add_argument("--sns-output-dir")
    parser.add_argument("--sns-topic-arn")
    args = parser.parse_args()

    if args.self_test:
        sample = [
            Issue("XYZ-PILOT-999", "Critical auth failure", "Open", "critical", "Auth", "Critical auth failure", ""),
            Issue(
                "XYZ-PILOT-998",
                "Major runtime regression",
                "Open",
                "major",
                "Runtime",
                "Major runtime regression needs more than forty letters",
                "",
            ),
        ]
        expected = [
            '<PILOT_NAME> Heads-Up: 1 critical defect filed/elevated : XYZ-PILOT-999 - "Critical auth failure"',
            '<PILOT_NAME> Heads-Up: 1 major defect filed/elevated : XYZ-PILOT-998 - "Major runtime regression needs more t..."',
        ]
        actual = [proposed_subject(issue) for issue in sample]
        if actual != expected:
            print(f"ERROR: subject mismatch\nexpected={expected!r}\nactual={actual!r}", file=sys.stderr)
            return 1
        resolved_subject = proposed_resolved_subject(sample[1])
        if not resolved_subject.startswith("<PILOT_NAME> Resolved: 1 major defect shipped : XYZ-PILOT-998"):
            print(f"ERROR: resolved subject mismatch: {resolved_subject!r}", file=sys.stderr)
            return 1
        if len(resolved_subject) > 100:
            print(f"ERROR: SNS subject too long: {len(resolved_subject)}", file=sys.stderr)
            return 1
        report = render_report("BASE", "HEAD", sample, email_enabled=True, mode="filed")
        if "Notification 1 Of 2" not in report or "Notification 2 Of 2" not in report:
            print("ERROR: self-test report does not render one proposed email per issue", file=sys.stderr)
            return 1
        print("subject self-test OK")
        for subject in actual:
            print(subject)
        print(resolved_subject)
        return 0

    if args.ses_output_dir and (not args.ses_source or not args.ses_recipient):
        print("ERROR: --ses-output-dir requires --ses-source and at least one --ses-recipient", file=sys.stderr)
        return 2
    if args.sns_output_dir and not args.sns_topic_arn:
        print("ERROR: --sns-output-dir requires --sns-topic-arn", file=sys.stderr)
        return 2

    try:
        before = parse_issues(git_show(args.base, TRACKER))
    except subprocess.CalledProcessError:
        before = {}
    after = parse_issues(git_show(args.head, TRACKER))

    if args.mode == "resolved":
        common_ids = sorted(set(before) & set(after), reverse=True)
        candidates = [
            after[issue_id]
            for issue_id in common_ids
            if is_resolution_candidate(before[issue_id], after[issue_id])
        ]
    else:
        new_ids = sorted(set(after) - set(before), reverse=True)
        elevated_ids = sorted(set(before) & set(after), reverse=True)
        candidates = [after[issue_id] for issue_id in new_ids if is_candidate(after[issue_id])]
        candidates.extend(
            after[issue_id]
            for issue_id in elevated_ids
            if is_elevation_candidate(before[issue_id], after[issue_id])
        )
    email_enabled = bool(args.ses_output_dir or args.sns_output_dir)
    if args.ses_output_dir:
        write_ses_messages(candidates, Path(args.ses_output_dir), args.ses_source, args.ses_recipient)
    if args.sns_output_dir:
        write_sns_messages(candidates, Path(args.sns_output_dir), args.sns_topic_arn)

    report = render_report(args.base, args.head, candidates, email_enabled=email_enabled, mode=args.mode)
    print(report)

    if args.github_summary:
        summary_path = Path(__import__("os").environ.get("GITHUB_STEP_SUMMARY", ""))
        if summary_path:
            with summary_path.open("a", encoding="utf-8") as fh:
                fh.write(report)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
