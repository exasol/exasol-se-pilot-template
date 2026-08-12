#!/usr/bin/env python3
"""XYZ-PILOT-171 — repo-hygiene check for prohibited VS Code Copilot auto-approve entries.

The Layer 4 guardrail for XYZ-PILOT-168's commit-attribution + Rule #5
single-writer disciplines. The overnight 2026-05-30/31 VSCode Copilot
session bypassed xyz_master by auto-approving `git add` of the tracker
via `.vscode/settings.json::chat.tools.terminal.autoApprove`. XYZ-PILOT-168
Layers 1-3 (CLAUDE.md / AGENTS.md / .github/copilot-instructions.md +
optional commit-msg hook) were defeated by that session-local IDE config.

This script catches the recurrence vector at PR / push time. It parses
`.vscode/settings.json` if present and fails if any auto-approve entry
matches a pattern from the FORBIDDEN_PATTERNS list below.

Exit codes:
  0   no .vscode/settings.json OR all auto-approve entries are safe
  1   .vscode/settings.json present and at least one prohibited
      auto-approve entry found — fails the workflow
  2   .vscode/settings.json present but malformed JSON — fails

The check is intentionally narrow: it does NOT block VS Code Copilot
auto-approvals of read-only scripts (verifier / archive_tracker /
archive_runbook). It only blocks commands that mutate the tracker, the
repo's main branch, deployed Lambdas, the EC2 instance, or S3 deploy
artifacts — i.e. the path classes that need explicit operator review.

Usage:
  python3 scripts/check_vscode_autoapprove_safety.py

CI usage:
  Invoked by .github/workflows/vscode-autoapprove-safety.yml on push +
  pull_request to main.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Patterns of auto-approve KEYS that this check will fail on. Each entry is
# either a literal command-line prefix (matched via startswith on the key
# string with whitespace + quoting normalised) OR a regex when the key
# starts/ends with `/` (the VS Code Copilot config syntax for regex auto-
# approvals).
#
# Format: tuples of (description, matcher). matcher is one of:
#   ("startswith", "<prefix>")
#   ("regex_contains", "<inner-regex-substring>")
#
# When extending: cite the relevant XYZ-PILOT-NNN in the description so the
# rationale stays auditable.
FORBIDDEN_PATTERNS = [
    # Tracker single-writer (Rule #5 / XYZ-PILOT-167)
    ("git add docs/XYZ_PILOT_ISSUE_TRACKER.md — Rule #5 single-writer gatekeeper",
     "startswith", "git add docs/XYZ_PILOT_ISSUE_TRACKER.md"),
    ("git add docs/XYZ_PILOT_ISSUE_TRACKER (regex variant) — Rule #5",
     "regex_contains", r"git\s+add\s+docs/XYZ_PILOT_ISSUE_TRACKER"),

    # Generic git mutations
    ("git commit — direct commit bypasses xyz_master review path",
     "startswith", "git commit"),
    ("git push — pushing to main without review",
     "startswith", "git push"),
    ("git push (regex variant)",
     "regex_contains", r"git\s+push"),
    ("git rebase / merge / reset --hard — destructive history operations",
     "regex_contains", r"git\s+(rebase|merge|reset\s+--hard)"),
    ("git tag -d / --delete — tag deletion may affect off-platform backup recovery",
     "regex_contains", r"git\s+tag\s+(-d|--delete)"),

    # AWS Lambda deploy (CLAUDE.md Rule #8)
    ("aws lambda update-function-code — bypasses Rule #8 deploy-time review",
     "startswith", "aws lambda update-function-code"),
    ("aws lambda update-function-configuration — env var / IAM changes",
     "startswith", "aws lambda update-function-configuration"),
    ("aws lambda invoke — uncontrolled Lambda invocation",
     "startswith", "aws lambda invoke"),
    ("aws lambda delete-function — destructive",
     "startswith", "aws lambda delete-function"),

    # AWS EC2 lifecycle (CLAUDE.md Rule #6)
    ("aws ec2 start-instances / stop-instances — schedule discipline",
     "regex_contains", r"aws\s+ec2\s+(start|stop|reboot|terminate)-instances"),

    # AWS S3 deploy artifact mutations
    ("aws s3 cp / sync to <PROJECT_SLUG>-status-* bucket — dashboard.html deploy",
     "regex_contains", r"aws\s+s3\s+(cp|sync).*<PROJECT_SLUG>-status"),

    # GitHub ruleset / branch protection
    ("gh api .*rulesets — XYZ-PILOT-088 Phase 3 activation gate",
     "regex_contains", r"gh\s+api.*rulesets"),
    ("gh ruleset — XYZ-PILOT-088 Phase 3 activation gate",
     "regex_contains", r"gh\s+ruleset"),

    # AWS IAM / Secrets Manager mutations
    ("aws iam delete- / detach- — permission revocation",
     "regex_contains", r"aws\s+iam\s+(delete|detach|remove)-"),
    ("aws secretsmanager / aws ssm put-parameter — secret write",
     "regex_contains", r"aws\s+(secretsmanager\s+(put|delete|update)|ssm\s+put-parameter)"),
    ("aws cognito-idp update-user-pool-client — rotation operation",
     "startswith", "aws cognito-idp update-user-pool-client"),
]

# Patterns that are EXPLICITLY safe and worth allowlisting for clarity.
# (Not used by the check itself — purely documentation in error output.)
SAFE_HINTS = [
    "./scripts/verify_tracker_completeness.sh",
    "./scripts/archive_tracker.sh",
    "./scripts/archive_runbook.sh",
    "git status",
    "git diff",
    "git log",
    "git fetch",
]


def _normalize_key(k: str) -> str:
    """Strip surrounding whitespace + leading/trailing slashes (VS Code's
    regex syntax) for easier matching."""
    k = k.strip()
    if k.startswith("/") and k.endswith("/") and len(k) > 1:
        # VS Code regex-style key — strip the slashes for regex matching,
        # but keep the regex-flag in mind for the matcher dispatch.
        return k[1:-1]
    return k


def _key_matches_pattern(raw_key: str, matcher_type: str, pattern: str) -> bool:
    key = _normalize_key(raw_key)
    # VS Code regex keys: `^...$` anchors are common; strip them for
    # contains-matching against our regex_contains patterns.
    key_unanchored = re.sub(r"^\^|\$$", "", key).replace("\\.", ".").replace("\\\\", "\\")
    if matcher_type == "startswith":
        return key.startswith(pattern) or key_unanchored.startswith(pattern)
    elif matcher_type == "regex_contains":
        try:
            return bool(re.search(pattern, key_unanchored, flags=re.IGNORECASE))
        except re.error:
            return False
    return False


def check(settings_path: Path) -> int:
    if not settings_path.is_file():
        print(f"OK: {settings_path} not present — no auto-approve entries to check.")
        return 0

    try:
        with settings_path.open() as f:
            settings = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"FAIL: {settings_path} is not valid JSON: {exc}", file=sys.stderr)
        return 2

    auto_approve = (
        settings.get("chat.tools.terminal.autoApprove")
        or settings.get("chat.tools.autoApprove")
        or {}
    )
    if not isinstance(auto_approve, dict):
        print(f"OK: {settings_path} has no chat.tools.terminal.autoApprove dict — nothing to check.")
        return 0

    violations: list[tuple[str, str]] = []
    for raw_key, value in auto_approve.items():
        # The auto-approval value can be: bool True, or an object with
        # `approve: true/false`. Skip entries that are explicitly disabled
        # (approve=false or value=false).
        approved = True
        if isinstance(value, dict):
            approved = bool(value.get("approve", True))
        elif isinstance(value, bool):
            approved = value
        if not approved:
            continue

        for description, matcher_type, pattern in FORBIDDEN_PATTERNS:
            if _key_matches_pattern(raw_key, matcher_type, pattern):
                violations.append((raw_key, description))
                break  # one violation per key is enough

    if violations:
        print(f"FAIL: {settings_path} contains {len(violations)} prohibited auto-approve entries:",
              file=sys.stderr)
        for key, desc in violations:
            print(f"  - {key!r}", file=sys.stderr)
            print(f"      reason: {desc}", file=sys.stderr)
        print("", file=sys.stderr)
        print("Per XYZ-PILOT-171 (Layer 4 IDE auto-approval guardrail), these patterns auto-approve",
              file=sys.stderr)
        print("commands that mutate the tracker, the repo's main branch, deployed Lambdas, the EC2",
              file=sys.stderr)
        print("instance, or S3 deploy artifacts — all of which need explicit operator review per",
              file=sys.stderr)
        print("CLAUDE.md Rule #5 (single-writer), Rule #6 (AWS_PROFILE discipline), Rule #8 (daily",
              file=sys.stderr)
        print("doc discipline), and the parallel-session guardrail.",
              file=sys.stderr)
        print("", file=sys.stderr)
        print("To fix:", file=sys.stderr)
        print("  1. Edit .vscode/settings.json and REMOVE the offending key(s), OR set the entry's",
              file=sys.stderr)
        print("     `approve: false`.", file=sys.stderr)
        print("  2. Keep only read-only auto-approvals like:", file=sys.stderr)
        for hint in SAFE_HINTS:
            print(f"       {hint}", file=sys.stderr)
        print("  3. For commands that need operator review, do NOT add them to auto-approve.",
              file=sys.stderr)
        print("     Run them manually + accept the prompt in VS Code Copilot's chat.", file=sys.stderr)
        return 1

    n_entries = sum(1 for v in auto_approve.values()
                    if (isinstance(v, bool) and v) or (isinstance(v, dict) and v.get("approve", True)))
    print(f"OK: {settings_path} — {n_entries} approved auto-approve entries, none on the forbidden list.")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Resolution order for the settings file:
    #   1. Explicit path argument: `python check_vscode_autoapprove_safety.py <path>`
    #   2. CWD-relative `.vscode/settings.json` (matches CI checkout layout
    #      where the workflow `cd`s to the repo root before invoking us).
    #   3. Fallback: script-location-relative (legacy behaviour).
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        return check(Path(argv[0]))
    cwd_path = Path(".vscode") / "settings.json"
    if cwd_path.exists() or not (Path(__file__).resolve().parent.parent / ".vscode" / "settings.json").exists():
        # If neither exists, prefer the CWD message — it's clearer.
        return check(cwd_path)
    return check(Path(__file__).resolve().parent.parent / ".vscode" / "settings.json")


if __name__ == "__main__":
    sys.exit(main())
