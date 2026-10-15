#!/usr/bin/env python3
"""Render project placeholders in a generated pilot repository."""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
import re


_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _biz_days_since(then: dt.date, today: dt.date) -> int:
    """Business days between two dates, mirroring verify_tracker_completeness.sh."""
    n, cur = 0, then
    while cur < today:
        cur += dt.timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def _tracker_freshness(today: dt.date) -> tuple[str, str, str]:
    """Seed the tracker's '*Today is … cut-off …*' line at generate time.

    A hardcoded date in the template is stale the day after the template is cut,
    and check (J) fails every generated repo on arrival. Stamping it here means a
    fresh pilot is green on day 1; the daily-status Lambda refreshes it after that.
    """
    cutoff = today
    while _biz_days_since(cutoff - dt.timedelta(days=1), today) <= 1:
        cutoff -= dt.timedelta(days=1)
    fmt = lambda d: f"{d.isoformat()} ({_DOW[d.weekday()]})"
    return fmt(today), fmt(cutoff), today.isoformat()


TEXT_SUFFIXES = {
    ".md", ".py", ".sh", ".yml", ".yaml", ".json", ".toml", ".ini",
    ".txt", ".env", ".example", ".service", ".csv", ".html", ".css", ".js",
    ".lock",
    # .sql / .zsh / .ps1 / .timer / .conf / .upstream were missing here, so
    # those files were silently shipped unrendered. That is fatal now that the
    # analytical schema name is a placeholder: every generated .sql would still
    # say <PILOT_SCHEMA>.
    ".sql", ".zsh", ".ps1", ".timer", ".conf", ".upstream", ".gitignore",
}


def is_text_path(path: Path) -> bool:
    if path.name in {"render_template_placeholders.py", "validate_template_neutrality.sh"}:
        return False
    if path.name in {"generate", "generate.sh"}:
        return True
    return path.suffix in TEXT_SUFFIXES or path.suffix == "" or ".env" in path.name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--pilot-name", required=True)
    parser.add_argument("--customer-name", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--aws-profile", required=True)
    parser.add_argument("--aws-region", required=True)
    parser.add_argument("--aws-az", default="")
    parser.add_argument("--domain", default="")
    parser.add_argument("--deploy-aws", default="0")
    parser.add_argument("--pilot-schema", default="PILOT_DW")
    parser.add_argument("--github-owner", default="")
    parser.add_argument("--customer-domain", default="")
    parser.add_argument("--operator-email", default="")
    parser.add_argument("--cognito-domain-prefix", default="")
    args = parser.parse_args()

    root = Path(args.root)
    project_id = args.project_id.lower()
    if not re.fullmatch(r"[a-z]{3}", project_id):
        raise SystemExit("--project-id must be exactly three ASCII letters, e.g. abc or gcm")

    pilot_schema = (args.pilot_schema or "PILOT_DW").upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", pilot_schema):
        raise SystemExit(
            "--pilot-schema must be a valid Exasol identifier: a letter followed by "
            "letters, digits or underscores, e.g. PILOT_DW or MISSION_DW"
        )
    # exa:Owner tags and commit trailers want the local part, not the address.
    operator_user = args.operator_email.split("@", 1)[0] if "@" in args.operator_email else ""
    tracker_today, tracker_cutoff, tracker_today_date = _tracker_freshness(dt.date.today())

    tracker_file = f"{project_id.upper()}_PILOT_ISSUE_TRACKER.md"
    tracker_stem = tracker_file.removesuffix(".md")
    tracker_id_prefix = f"{project_id.upper()}-PILOT"
    runtime_id_prefix = f"{project_id}-pilot"
    replacements = [
        ("clients/XYZ", f"clients/{project_id.upper()}"),
        ("XYZ_PILOT_ISSUE_TRACKER.md", tracker_file),
        ("XYZ_PILOT_ISSUE_TRACKER", tracker_stem),
        ("XYZ-PILOT", tracker_id_prefix),
        ("XYZ_", f"{project_id.upper()}_"),
        ("xyz-pilot", runtime_id_prefix),
        ("xyz_pilot", runtime_id_prefix.replace("-", "_")),
        ("xyz", project_id),
        ("<PROJECT_ID>", project_id),
        ("<PROJECT_ID_UPPER>", project_id.upper()),
        ("<TRACKER_PREFIX>", project_id),
        ("<TRACKER_FILE>", tracker_file),
        ("<TRACKER_ID_PREFIX>", tracker_id_prefix),
        ("<PROJECT_SLUG>", args.project),
        ("<PILOT_NAME>", args.pilot_name),
        ("<CUSTOMER_NAME>", args.customer_name),
        ("<AWS_PROFILE>", args.aws_profile),
        ("<AWS_REGION>", args.aws_region),
        ("<AWS_AZ>", args.aws_az),
        ("<PILOT_DOMAIN>", args.domain),
        ("<DEPLOY_AWS>", str(args.deploy_aws)),
        ("<PILOT_SCHEMA>", pilot_schema),
        ("<TRACKER_TODAY>", tracker_today),
        ("<TRACKER_CUTOFF>", tracker_cutoff),
        ("<TRACKER_TODAY_DATE>", tracker_today_date),
    ]

    # Optional identity values. Leaving the placeholder in place when a value is
    # not supplied is deliberate: rendering an empty string would silently
    # produce `Co-Authored-By: Agent <>` trailers and untagged exa:Owner values.
    for token, value in (
        ("<GITHUB_OWNER>", args.github_owner),
        ("<CUSTOMER_DOMAIN>", args.customer_domain),
        ("<OPERATOR_EMAIL>", args.operator_email),
        ("<OPERATOR_USER>", operator_user),
        # Cognito domain prefix defaults to the project slug: the Hosted-UI
        # domain is chosen at pool-creation time and the slug is a safe,
        # unique default a pilot can override.
        ("<COGNITO_DOMAIN_PREFIX>", args.cognito_domain_prefix or args.project),
        # owner/repo, as GitHub Actions and IAM trust policies express it
        ("<GITHUB_REPOSITORY>", f"{args.github_owner}/{args.project}" if args.github_owner else ""),
    ):
        if value:
            replacements.append((token, value))

    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if not is_text_path(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new_text = text
        # NOTE: an earlier rule uppercased `xyz-pilot-NNN` into `<PREFIX>-PILOT-NNN`.
        # That was safe only while anchors used a different spelling. Lowercase
        # `xyz-pilot-NNN` is now the anchor/link form itself (the filer derives it
        # as tracker_id.lower()), so uppercasing it broke every `<a id=...>` in the
        # generated tracker and failed verifier check (B). The ordinary
        # ("xyz-pilot" -> "<id>-pilot") replacement below handles these correctly.
        for old, new in replacements:
            new_text = new_text.replace(old, new)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")

    # Rename files/directories after content rendering. Walk deepest-first so
    # child paths are handled before their parents.
    paths = sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    for path in paths:
        if ".git" in path.parts:
            continue
        new_name = path.name
        for old, new in replacements:
            new_name = new_name.replace(old, new)
        if new_name != path.name:
            target = path.with_name(new_name)
            if target.exists():
                raise FileExistsError(f"Cannot rename {path} to existing {target}")
            os.replace(path, target)

    # Bare "XYZ" is not a general placeholder, so the customer-material folder
    # needs its own rename.
    clients_dir = root / "clients" / "XYZ"
    if clients_dir.is_dir():
        os.replace(clients_dir, clients_dir.with_name(project_id.upper()))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
