#!/usr/bin/env python3
"""XYZ-PILOT-189 — chatbot submission acceptance/denial SES notification.

Triggered by the `.github/workflows/notify-on-tracker-mutation.yml` workflow
on push to `main`. Detects two commit-subject patterns and fires an SES
notification to the chatbot submitter with the operator triumvirate
(<OPERATOR_NAME> / <OPS_USER_2> / <OPS_USER_3>) on Cc.

Acceptance pattern (xyz_master drained a chatbot candidate):
    `xyz_master: file XYZ-PILOT-NNN ...`

Denial pattern (chatbot pre-flight Bedrock dup-check rejected — Phase 2):
    `chatbot: file xyz_master candidate (denied) — ...` — DEFERRED to
    Phase 2 once the chatbot UI is updated to commit denial markers.

Today this script ships only the ACCEPTANCE path so the immediate
"black hole" UX problem (XYZ-PILOT-189 user-credibility framing) is
closed without waiting on the chatbot denial-marker change.

The script is invoked from CI with the commit SHA + the GitHub-server-side
repo path; it shells out to `git` to read the commit subject + consumed
candidate file paths. No AWS API call other than `ses:SendEmail` is made.

Exit codes:
    0  email sent (or no-op when commit doesn't match any pattern)
    1  match found but email send failed
    2  malformed CLI invocation
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ACCEPTANCE_SUBJECT_RE = re.compile(
    r"^xyz_master:\s+file\s+XYZ-PILOT-(?P<id>\d{3})"
    r"(?:\s+\((?P<title_paren>.+)\)|(?:\s+(?P<title_text>.+))?)$"
)
DENIAL_SUBJECT_RE = re.compile(
    r"^chatbot: file xyz_master candidate \(denied\) — (?P<title>.+)$"
)
TRACKER_URL_BASE = (
    "https://github.com/<GITHUB_REPOSITORY>/"
    "blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md"
)
CANDIDATE_AUTHOR_RE = re.compile(
    r"^- \*\*Author:\*\*\s+(?P<email>[\w.+-]+@[\w-]+\.[\w.-]+)"
)
CANDIDATE_TITLE_RE = re.compile(r"^# Candidate handoff to xyz_master — .*?: (?P<title>.+)$")
CANDIDATE_SEVERITY_RE = re.compile(r"^- \*\*Proposed severity:\*\*\s+`?(?P<sev>[a-zA-Z]+)`?")

# Cc on every acceptance/denial email — the unified OPS-ONLY 3-person list
# per operator directive 2026-06-08 (pass 5). All automated outbound <CUSTOMER_NAME>
# Pilot notifications share this same list. <OPS_USER_3> was previously on
# the engineering-signal triumvirate (XYZ-PILOT-173 SES_TO_TEST + XYZ-PILOT-189);
# replaced by <OPS_USER_3> when the engineering vs strategic split collapsed
# into one unified strategic-ops list.
OPERATOR_CC = (
    "<OPERATOR_EMAIL>",
    "<OPS_USER_2_EMAIL>",
    "<OPS_USER_3_EMAIL>",
)

SES_REGION = os.environ.get("AWS_REGION", "eu-central-1")
SES_FROM = os.environ.get("SES_SOURCE", "<OPERATOR_EMAIL>")


def git_show(sha: str, fmt: str) -> str:
    """Return `git show --format=<fmt> --no-patch <sha>` stripped."""
    return subprocess.check_output(
        ["git", "show", "--no-patch", f"--format={fmt}", sha],
        text=True,
    ).strip()


CHATBOT_CANDIDATE_PATH_RE = r"(?P<path>tests/logs/runs/[^\s]+/xyz_PILOT_CHATBOT/issue-candidates\.md)"
CANDIDATE_SOURCE_PATTERNS = (
    re.compile(r"Candidate source:\s+" + CHATBOT_CANDIDATE_PATH_RE),
    re.compile(r"candidate handoff from\s+" + CHATBOT_CANDIDATE_PATH_RE, re.IGNORECASE),
)


def parse_acceptance_subject(subject: str) -> tuple[str, str] | None:
    """Return `(<CUSTOMER_NAME> id, title)` for an accepted xyz_master file commit."""
    m = ACCEPTANCE_SUBJECT_RE.match(subject)
    if not m:
        return None
    title = (m.group("title_paren") or m.group("title_text") or "").strip()
    return m.group("id"), title


def extract_chatbot_candidate_path(body: str) -> Path | None:
    """Extract the chatbot candidate file path cited by an xyz_master commit."""
    for pattern in CANDIDATE_SOURCE_PATTERNS:
        if m := pattern.search(body):
            return Path(m.group("path"))
    return None


def find_chatbot_candidate(sha: str) -> Path | None:
    """Locate the chatbot-emitted candidate file the xyz_master commit drained.

    xyz_master's commit message body includes a `Candidate source: <path>`
    line citing the consumed candidate file. The chatbot committed that
    file in a PRIOR commit (xyz_master itself doesn't re-stage it), so we
    parse the path from the commit message rather than the added-files
    list of this commit.

    Returns the Path if (a) the message cites a candidate, (b) the path
    is under xyz_PILOT_CHATBOT/, AND (c) the file exists in the working
    tree. Else None.
    """
    body = git_show(sha, "%B")
    path = extract_chatbot_candidate_path(body)
    if path is None:
        return None
    if not path.is_file():
        return None
    return path


def parse_candidate(path: Path) -> dict[str, str]:
    """Pull submitter email + title + severity out of the candidate file."""
    out: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if m := CANDIDATE_TITLE_RE.match(line):
            out.setdefault("title", m.group("title").strip())
        if m := CANDIDATE_AUTHOR_RE.match(line):
            out.setdefault("email", m.group("email").strip())
        if m := CANDIDATE_SEVERITY_RE.match(line):
            out.setdefault("severity", m.group("sev").strip())
    return out


def first_name_from_email(email: str) -> str:
    """Best-effort greeting name from a verified submitter email address."""
    local = email.split("@", 1)[0].strip()
    first_token = re.split(r"[._+-]+", local, maxsplit=1)[0].strip()
    if not first_token or not re.fullmatch(r"[A-Za-z][A-Za-z'-]{0,39}", first_token):
        return ""
    return first_token[:1].upper() + first_token[1:].lower()


def build_acceptance_email(pilot_id: str, title: str, severity: str, submitter: str) -> dict:
    anchor = f"XYZ-PILOT-{int(pilot_id):03d}"
    tracker_url = f"{TRACKER_URL_BASE}#{anchor}"
    subject = f"[<PILOT_NAME>] Your submission accepted as XYZ-PILOT-{pilot_id}"
    greeting_name = first_name_from_email(submitter)
    greeting = f"Hi {greeting_name}," if greeting_name else "Hi,"
    body_text = (
        f"{greeting}\n\n"
        f"Your submission to the <PILOT_NAME> Chatbot has been accepted into\n"
        f"the central issue tracker by xyz_master.\n\n"
        f"  ID:       XYZ-PILOT-{pilot_id}\n"
        f"  Title:    {title}\n"
        f"  Severity: {severity or 'minor (chatbot default)'}\n"
        f"  Status:   Open\n"
        f"  Tracker:  {tracker_url}\n\n"
        f"This email is your persistent record of the filing — searchable\n"
        f"by 'XYZ-PILOT-{pilot_id}' in your inbox. <OPERATOR_NAME> + <OPS_USER_2> + <OPS_USER_3>\n"
        f"are on Cc per the XYZ-PILOT-189 operator-confirmed design.\n\n"
        f"If you spot anything missing in the filed detail section, reply\n"
        f"to this thread and we'll route the correction through xyz_master.\n\n"
        f"— <PILOT_NAME> Chatbot submission discipline (XYZ-PILOT-189)\n"
    )
    body_html = f"""<html><body style="font-family:system-ui,-apple-system,sans-serif;line-height:1.45;">
<p>{greeting}</p>
<p>Your submission to the <PILOT_NAME> Chatbot has been accepted into the central issue tracker by xyz_master.</p>
<table style="border-collapse:collapse;margin:1em 0;">
  <tr><td style="padding:.2em 1em .2em 0;color:#666;">ID:</td><td><b>XYZ-PILOT-{pilot_id}</b></td></tr>
  <tr><td style="padding:.2em 1em .2em 0;color:#666;">Title:</td><td>{title}</td></tr>
  <tr><td style="padding:.2em 1em .2em 0;color:#666;">Severity:</td><td>{severity or "minor (chatbot default)"}</td></tr>
  <tr><td style="padding:.2em 1em .2em 0;color:#666;">Status:</td><td>Open</td></tr>
  <tr><td style="padding:.2em 1em .2em 0;color:#666;">Tracker:</td><td><a href="{tracker_url}">{tracker_url}</a></td></tr>
</table>
<p>This email is your persistent record of the filing — searchable by <code>XYZ-PILOT-{pilot_id}</code> in your inbox.
<OPERATOR_NAME> + <OPS_USER_2> + <OPS_USER_3> are on Cc per the XYZ-PILOT-189 operator-confirmed design.</p>
<p>If you spot anything missing in the filed detail section, reply to this thread and we'll route the correction through xyz_master.</p>
<p style="color:#888;font-size:.85em;">— <PILOT_NAME> Chatbot submission discipline (XYZ-PILOT-189)</p>
</body></html>"""
    return {
        "Subject": subject,
        "BodyText": body_text,
        "BodyHtml": body_html,
        "To": [submitter],
        "Cc": [a for a in OPERATOR_CC if a.lower() != submitter.lower()],
    }


def send_email(payload: dict) -> str:
    """Send via SES SendEmail. Returns the MessageId on success."""
    import boto3

    client = boto3.client("ses", region_name=SES_REGION)
    resp = client.send_email(
        Source=SES_FROM,
        Destination={
            "ToAddresses": payload["To"],
            "CcAddresses": payload["Cc"],
        },
        Message={
            "Subject": {"Data": payload["Subject"], "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": payload["BodyText"], "Charset": "UTF-8"},
                "Html": {"Data": payload["BodyHtml"], "Charset": "UTF-8"},
            },
        },
    )
    return resp["MessageId"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--sha", required=True, help="commit SHA to classify")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the email payload without sending")
    args = parser.parse_args()

    subject = git_show(args.sha, "%s")
    print(f"commit subject: {subject!r}", file=sys.stderr)

    acceptance = parse_acceptance_subject(subject)
    if not acceptance:
        if DENIAL_SUBJECT_RE.match(subject):
            print("denial path is Phase 2 — not handled yet", file=sys.stderr)
        else:
            print("no acceptance / denial pattern match — no-op", file=sys.stderr)
        return 0

    pilot_id, commit_title = acceptance
    print(f"acceptance match: XYZ-PILOT-{pilot_id}", file=sys.stderr)

    candidate_path = find_chatbot_candidate(args.sha)
    if candidate_path is None:
        print(
            "no chatbot candidate file consumed in this commit — "
            "this xyz_master pass drained a non-chatbot candidate (e.g. CLAUDE_CODE-filed). "
            "Skipping email — chatbot-discipline notification only applies to xyz_PILOT_CHATBOT-source candidates.",
            file=sys.stderr,
        )
        return 0

    print(f"candidate file: {candidate_path}", file=sys.stderr)
    parsed = parse_candidate(candidate_path)
    submitter = parsed.get("email")
    if not submitter:
        print(
            f"could not extract `Author:` email from {candidate_path} — "
            "candidate file may be malformed. Skipping.",
            file=sys.stderr,
        )
        return 1

    title = parsed.get("title") or commit_title
    severity = parsed.get("severity", "minor")

    payload = build_acceptance_email(pilot_id, title, severity, submitter)
    print(f"recipients: To={payload['To']} Cc={payload['Cc']}", file=sys.stderr)

    if args.dry_run:
        print("=== DRY RUN — would send: ===")
        print(f"Subject: {payload['Subject']}")
        print(f"To: {', '.join(payload['To'])}")
        print(f"Cc: {', '.join(payload['Cc'])}")
        print(f"BodyText:\n{payload['BodyText']}")
        return 0

    try:
        message_id = send_email(payload)
        print(f"sent — MessageId={message_id}", file=sys.stderr)
        return 0
    except Exception as exc:  # noqa: BLE001 — we want to log every SES error
        print(f"SES send failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
