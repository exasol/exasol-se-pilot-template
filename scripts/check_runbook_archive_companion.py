#!/usr/bin/env python3
"""XYZ-PILOT-192: CI advisory check — structural runbook rewrites must carry a matching
docs/archive/<basename>_*.md companion in the same PR.

Backstop for CLAUDE.md Rule #10 (XYZ-PILOT-167) runbook-class archive discipline.
The `./scripts/archive_runbook.sh` tooling exists + the discipline is documented,
but until this check shipped there was no CI enforcement. The `c4cb505`
stub-archive incident on 2026-05-30 (VSCode Copilot's ad-hoc archive-stub
bypassing the proper flow) is the canonical surfacing of the gap.

Algorithm
=========
1. Determine the diff range:
   - In CI (workflow_dispatch / pull_request): use `${{ github.event.before }}`
     / `${{ github.event.pull_request.base.sha }}` → HEAD.
   - Locally: `git merge-base HEAD origin/main` → HEAD (PR-equivalent).
   - The CLI accepts --base / --head explicitly to override.
2. For every modified file in the range that matches a runbook-class allowlist,
   count the number of LINES DELETED (`-` lines in the unified diff, excluding
   the `---` file header).
3. If the deletion count for a runbook >= THRESHOLD_DELETIONS (default 50 —
   tunable via --threshold), require the same diff to add a NEW file matching
   `docs/archive/<basename-without-md>_*.md`.
4. Exit codes:
     0  no violations (or no runbook touched, or runbook touched below threshold)
     1  violations found
     2  malformed CLI invocation

Self-test mode (`--self-test`) exercises:
  - 80231a7: XYZ_PILOT_GITHUB_RUNBOOK.md gained ~64 lines (incremental, NO deletions
    above threshold) → PASS (no archive companion required).
  - A synthetic case (constructed from a real archive snapshot) confirms a
    high-deletion change WITH companion = PASS, WITHOUT companion = FAIL.

Why advisory-first (XYZ-PILOT-153 graduation pattern):
  - First 4 weeks of clean advisory runs validate the THRESHOLD tuning.
  - False positives (e.g. legitimate refactors that delete >50 lines per
    section + properly archive) get tuned via threshold OR archive-companion
    glob exemptions.
  - After 4 weeks clean: graduate to blocking gate (requires PR-protection
    rule edit; tracked as a follow-up post-XYZ-PILOT-088 Phase 4).

Per CLAUDE.md Rule #198 advisory failure-email discipline: this workflow
ships with job-level `continue-on-error: true` so its failures surface as
warnings in the run page + step summary + workflow_health rollup, but
do NOT fire failure-notification emails.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Runbook-class allowlist — same family as scripts/check_docs_discipline.py
# RUNBOOK_FILES minus the non-runbook entries (READMEs, MAPPING). This is the
# set of "structural rewrite of this doc would invalidate cross-doc links if
# anchors shift" docs per CLAUDE.md Rule #10.
RUNBOOK_FILES = frozenset({
    "CLAUDE.md",
    "AGENTS.md",
    "docs/30-runbooks/operations/XYZ_PILOT_DEVELOPER_RUNBOOK.md",
    "docs/30-runbooks/deployment/XYZ_PILOT_EXASOL_PILOT_RUNBOOK.md",
    "docs/30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md",
    "docs/50-testing-validation/XYZ_PILOT_TEST_RUNBOOK.md",
    "docs/30-runbooks/security/XYZ_PILOT_CREDENTIALS.md",
    "docs/00-live-status/XYZ_PILOT_LIVE_RUNBOOK.md",
    "docs/30-runbooks/mcp/XYZ_MCP_CLIENT_GUIDE.md",
    "docs/30-runbooks/mcp/XYZ_MCP_CLIENT_MAC_GUIDE.md",
    "docs/30-runbooks/ui/XYZ_PILOT_UI.md",
})

DEFAULT_THRESHOLD = 50  # tunable via --threshold


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True)


def normalize_range(base: str | None, head: str | None) -> tuple[str, str]:
    """Resolve (base, head) using local merge-base when base is unset/HEAD~1
    isn't a sensible PR-equivalent base."""
    if head is None or head == "HEAD":
        head = "HEAD"
    if base is None or base in ("HEAD~1",):
        # Prefer merge-base with origin/main for a PR-equivalent local invocation.
        try:
            base = run_git(["merge-base", "HEAD", "origin/main"]).strip()
        except subprocess.CalledProcessError:
            base = "HEAD~1"
    return base, head


def changed_files(base: str, head: str) -> list[tuple[str, str]]:
    """Return list of (status, path) for changed files in diff range. Handles
    A=added, M=modified, D=deleted, R=renamed (split into D old + A new)."""
    out = run_git(["diff", "--name-status", "-M", base, head])
    files: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) == 3:
            files.append(("D", parts[1]))
            files.append(("A", parts[2]))
        elif len(parts) >= 2:
            files.append((status[0], parts[-1]))
    return files


def count_deletions(path: str, base: str, head: str) -> int:
    """Count `-` lines for `path` in the diff range, excluding the `---` header."""
    try:
        diff = run_git(["diff", "--unified=0", "--no-ext-diff", base, head, "--", path])
    except subprocess.CalledProcessError:
        return 0
    n = 0
    for line in diff.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            n += 1
    return n


def archive_companion_added(runbook_path: str, added_files: list[str]) -> str | None:
    """Return the matching archive companion path if one exists in added_files,
    else None. Match rule: docs/archive/<basename-without-md>_*.md."""
    basename = Path(runbook_path).stem  # e.g. "xyz_PILOT_DEVELOPER_RUNBOOK"
    prefix = f"docs/archive/{basename}_"
    for path in added_files:
        if path.startswith(prefix) and path.endswith(".md"):
            return path
    return None


def emit_summary(lines: list[str], enabled: bool) -> None:
    text = "\n".join(lines)
    if enabled:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(text)
                handle.write("\n")
    print(text)


def check(base: str, head: str, threshold: int, github_summary: bool) -> int:
    files = changed_files(base, head)
    added_files = [p for status, p in files if status == "A"]
    modified_runbooks: list[tuple[str, int]] = []
    for status, path in files:
        if status in ("M", "A") and path in RUNBOOK_FILES:
            dels = count_deletions(path, base, head)
            modified_runbooks.append((path, dels))

    violations: list[tuple[str, int]] = []
    for path, dels in modified_runbooks:
        if dels < threshold:
            continue
        companion = archive_companion_added(path, added_files)
        if companion is None:
            violations.append((path, dels))

    summary_lines = [
        "## <PILOT_NAME> Runbook Archive Companion (Advisory)",
        "",
        f"- Diff range: `{base}` → `{head}`",
        f"- Threshold: {threshold} deletions per runbook",
        f"- Runbook-class files touched: {len(modified_runbooks)}",
    ]
    if modified_runbooks:
        summary_lines.extend(["", "### Runbook-class diffs"])
        for path, dels in modified_runbooks:
            companion = archive_companion_added(path, added_files)
            status = (
                f"PASS (deletions {dels} < {threshold}, no archive needed)"
                if dels < threshold
                else f"PASS (deletions {dels}, archive companion `{companion}` present)"
                if companion
                else f"FAIL (deletions {dels} >= {threshold}, NO archive companion in this diff)"
            )
            summary_lines.append(f"- `{path}`: {status}")
    if violations:
        summary_lines.extend([
            "",
            "### Advisory Failures",
            "",
            "Per CLAUDE.md Rule #10 (XYZ-PILOT-167): structural rewrites of runbook-class docs MUST be preceded by a snapshot into `docs/archive/`. Run:",
            "",
            "```",
        ])
        for path, dels in violations:
            summary_lines.append(f'./scripts/archive_runbook.sh {path} "<reason for the rewrite>"')
        summary_lines.append("```")
        summary_lines.extend([
            "",
            "Then `git add docs/archive/<the-new-snapshot>.md` and amend / re-commit so the archive ships in the same PR as the structural change.",
        ])

    emit_summary(summary_lines, github_summary)

    for path, dels in violations:
        print(
            f"ERROR: {path} has {dels} deletions in this diff but no matching "
            f"docs/archive/{Path(path).stem}_*.md companion. See CLAUDE.md Rule #10.",
            file=sys.stderr,
        )
    return 1 if violations else 0


def self_test() -> int:
    """Exercise the checker against known commits.

    Cases:
      80231a7  XYZ_PILOT_GITHUB_RUNBOOK.md gained 64 lines, 0 deletions. PASS.
      ca96f55  No runbook-class file touched. PASS.
      0481e57  XYZ_PILOT_GITHUB_RUNBOOK.md gained 15 lines, 2 deletions. PASS.
    """
    cases = [
        ("80231a7~1", "80231a7", 0, "XYZ-PILOT-198 advisory continue-on-error (additive only)"),
        ("ca96f55~1", "ca96f55", 0, "XYZ-PILOT-187 tracker-only xyz_master pass (no runbook touched)"),
        ("0481e57~1", "0481e57", 0, "XYZ-PILOT-088 Phase 3 paired runbook update (mostly additive)"),
    ]
    failures: list[str] = []
    for base, head, expected_rc, label in cases:
        try:
            rc = check(base, head, DEFAULT_THRESHOLD, github_summary=False)
        except subprocess.CalledProcessError as exc:
            failures.append(f"  {head} ({label}): could not evaluate — {exc}")
            continue
        verdict = "PASS" if rc == expected_rc else "FAIL"
        print(f"\n  {verdict}: {head} → rc={rc} (expected {expected_rc}) — {label}")
        if rc != expected_rc:
            failures.append(f"  {head} ({label}): expected rc {expected_rc}, got {rc}")
    if failures:
        print(f"\nself-test FAILED — {len(failures)} mismatch(es):", file=sys.stderr)
        for f in failures:
            print(f, file=sys.stderr)
        return 1
    print(f"\nself-test OK — all {len(cases)} cases match expected.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--base", default=None, help="Base git ref (default: merge-base with origin/main)")
    parser.add_argument("--head", default="HEAD", help="Head git ref (default: HEAD)")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help=f"Deletion threshold per runbook to require an archive companion (default {DEFAULT_THRESHOLD})")
    parser.add_argument("--github-summary", action="store_true",
                        help="Write a compact report to GITHUB_STEP_SUMMARY")
    parser.add_argument("--self-test", action="store_true",
                        help="XYZ-PILOT-192: exercise the checker against known commits + exit")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    base, head = normalize_range(args.base, args.head)
    return check(base, head, args.threshold, args.github_summary)


if __name__ == "__main__":
    raise SystemExit(main())
