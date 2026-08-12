#!/usr/bin/env python3
"""Lightweight GitHub Actions workflow policy checks."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


WORKFLOWS = Path(".github/workflows")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
USES_RE = re.compile(r"^\s*uses:\s*([^#\s]+)")
XYZ_MASTER_BACKGROUND_FILING = "xyz-master-background-filing.yml"
XYZ_MASTER_CONCURRENCY_GROUP = "xyz-master-background-filing"
TRACKER_FORCE_PUSH_GUARD_WORKFLOWS = {
    "tracker-major-defect-heads-up.yml": ("SES", "ses-output-dir"),
    "tracker-major-defect-resolved-sns.yml": ("SNS", "sns-output-dir"),
}


def emit_warning(message: str, github_warnings: bool) -> None:
    if github_warnings:
        print(f"::warning::{message}")
    else:
        print(f"WARNING: {message}")


def top_level_block_values(text: str, block_name: str) -> dict[str, str]:
    """Extract simple key/value entries from a top-level YAML block.

    This intentionally stays lightweight; the repository policy checker should
    not depend on PyYAML just to enforce a few first-level workflow properties.
    """

    lines = text.splitlines()
    values: dict[str, str] = {}
    in_block = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            if in_block:
                break
            in_block = line.strip() == f"{block_name}:"
            continue
        if not in_block:
            continue
        match = re.match(r"^\s+([A-Za-z0-9_-]+):\s*(.*?)\s*$", line)
        if match:
            values[match.group(1)] = match.group(2).strip('"').strip("'")
    return values


def check_xyz_master_background_filing_policy(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    if path.name != XYZ_MASTER_BACKGROUND_FILING:
        return errors
    concurrency = top_level_block_values(text, "concurrency")
    group = concurrency.get("group")
    cancel = concurrency.get("cancel-in-progress")
    if group != XYZ_MASTER_CONCURRENCY_GROUP:
        errors.append(
            f"{path}: XYZ_MASTER background filing must use global concurrency group "
            f"{XYZ_MASTER_CONCURRENCY_GROUP!r}"
        )
    if cancel != "false":
        errors.append(
            f"{path}: XYZ_MASTER background filing must keep cancel-in-progress: false"
        )
    return errors


def check_tracker_notifier_force_push_guard(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    config = TRACKER_FORCE_PUSH_GUARD_WORKFLOWS.get(path.name)
    if not config:
        return errors
    channel, output_flag = config
    required_snippets = {
        "forced push skip": '${{ github.event.forced }}" = "true"',
        "all-zero base skip": '0000000000000000000000000000000000000000',
        "missing base skip": 'git cat-file -e "${base}^{commit}"',
        "non-ancestor base skip": 'git merge-base --is-ancestor "${base}" "${head}"',
        "skip output": 'echo "skip=${skip}" >> "${GITHUB_OUTPUT}"',
        "send gate": "steps.range.outputs.skip != 'true'",
        "push-only notification": "github.event_name == 'push'",
        "notification output disabled on skip": output_flag,
    }
    for label, snippet in required_snippets.items():
        if snippet not in text:
            errors.append(
                f"{path}: tracker {channel} notifier missing {label} guard for XYZ-PILOT-252"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-warnings", action="store_true")
    args = parser.parse_args()

    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    errors: list[str] = []
    warnings: list[str] = []

    for path in files:
        text = path.read_text(encoding="utf-8")
        if "permissions:" not in text:
            errors.append(f"{path}: missing explicit permissions block")
        if "timeout-minutes:" not in text:
            errors.append(f"{path}: missing job timeout-minutes")
        if "concurrency:" not in text:
            errors.append(f"{path}: missing concurrency block")
        if re.search(r"^\s*pull_request_target\s*:", text, re.MULTILINE):
            errors.append(f"{path}: pull_request_target is not allowed without explicit security review")
        if re.search(r"contents:\s*write", text):
            warnings.append(f"{path}: contents: write detected; verify this is intentional")
        errors.extend(check_xyz_master_background_filing_policy(path, text))
        errors.extend(check_tracker_notifier_force_push_guard(path, text))

        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = USES_RE.match(line)
            if not match:
                continue
            uses = match.group(1).strip('"').strip("'")
            if uses.startswith(("./", "docker://")):
                continue
            if "@" not in uses:
                errors.append(f"{path}:{lineno}: action reference missing @ref: {uses}")
                continue
            _action, ref = uses.rsplit("@", 1)
            if not SHA_RE.fullmatch(ref):
                errors.append(
                    f"{path}:{lineno}: action is not pinned to a full SHA: {uses}"
                )

    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    for warning in warnings:
        emit_warning(warning, args.github_warnings)

    print(f"workflow_policy_files={len(files)} errors={len(errors)} warnings={len(warnings)}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
