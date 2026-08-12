#!/usr/bin/env python3
"""Advisory documentation-discipline check for runtime-impacting changes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys


xyz_PILOT_REF_RE = re.compile(r"\bXYZ-PILOT-\d{3}\b")
ZERO_SHA_RE = re.compile(r"^0{40}$")

# XYZ-PILOT-197: Dependabot-bump heuristic — skip docs-discipline check for
# exclusive dependency-version bumps where there is no behavioral change.
# Triggered by:
#   - Commit subject matching `deps(<ecosystem>):` (Dependabot's default
#     conventional-commit prefix per .github/dependabot.yml's
#     `commit-message: prefix: "deps"`); covers all 4 ecosystems —
#     github_actions, docker, pip, npm.
#   - Diff hunks containing ONLY additions of `uses: <action>@<SHA>  # vX.Y.Z`
#     lines (GitHub Actions bumps) OR `FROM <image>:<tag>` lines (Docker
#     base-image bumps) OR `<package>==X.Y.Z`/`<package>~=X.Y.Z` lines
#     (Python requirements bumps).
# Such commits have zero runtime behavioral change — only the pinned
# version moves. Requiring a runbook update for these is over-eager;
# it produces a false-positive failure email per Dependabot merge.
DEPENDABOT_SUBJECT_RE = re.compile(r"^deps\(")
# Patterns for valid Dependabot-bump diff additions (after the `+` marker
# is stripped + leading whitespace trimmed). Any added line that doesn't
# match one of these patterns disqualifies the commit from the bump-only
# heuristic.
DEPENDABOT_BUMP_ADD_PATTERNS = (
    re.compile(r"^uses:\s+\S+@[a-f0-9]{7,40}(?:\s+#\s*v?[\d.]+(?:[-+][\w.-]+)?)?$"),
    re.compile(r"^FROM\s+\S+:\S+(?:\s+AS\s+\S+)?$"),
    re.compile(r"^\S+(?:==|~=|>=|<=|!=|<|>)[\d.][\w.\-+!]*(?:,\S*)?\s*$"),
)

RUNBOOK_HINTS = {
    "GUIDE",
    "RUNBOOK",
    "CREDENTIALS",
    "MAPPING",
    "STATUS",
    "TESTCASES",
}

RUNBOOK_FILES = {
    "README.md",
    "CLAUDE.md",
    "docs/30-runbooks/operations/XYZ_PILOT_DEVELOPER_RUNBOOK.md",
    "docs/30-runbooks/deployment/XYZ_PILOT_EXASOL_PILOT_RUNBOOK.md",
    "docs/30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md",
    "docs/50-testing-validation/XYZ_PILOT_TEST_RUNBOOK.md",
    "docs/30-runbooks/security/XYZ_PILOT_CREDENTIALS.md",
    "docs/00-live-status/XYZ_PILOT_LIVE_RUNBOOK.md",
    "docs/30-runbooks/mcp/XYZ_MCP_CLIENT_GUIDE.md",
    "docs/30-runbooks/mcp/XYZ_MCP_CLIENT_MAC_GUIDE.md",
    "docs/30-runbooks/ui/XYZ_PILOT_UI.md",
    "tests/README.md",
    "scheduler/README.md",
    "pilot_mcp_stack/README.md",
    "services/chatbot/README.md",
    "services/streamlit-insights/README.md",
    "scripts/exasol_warmup/README.md",
}

IMPACT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("github-actions-governance", (".github/workflows/", ".github/dependabot.yml", ".github/rulesets/", ".github/CODEOWNERS")),
    ("aws-scheduler-lambda", ("scheduler/",)),
    ("chatbot-service", ("services/chatbot/",)),
    ("public-mcp-service", ("pilot_mcp_stack/",)),
    ("streamlit-qbr-service", ("services/streamlit-insights/",)),
    ("grafana-observability", ("services/grafana/",)),
    ("exasol-personal-infrastructure", ("exasol-deployment/", "deployment/infrastructure/")),
    ("exasol-sql-model", ("sample_schema_assets/", "sample_source_model/")),
    ("operator-scripts", ("scripts/",)),
    ("tester-swarm-runtime", ("tests/scripts/", "tests/agents/", "tests/cases/")),
)

IGNORED_IMPACT_PATHS = {
    "docs/XYZ_PILOT_ISSUE_TRACKER.md",
}


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True)


def ref_exists(ref: str) -> bool:
    if ZERO_SHA_RE.fullmatch(ref):
        return False
    result = subprocess.run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], capture_output=True, text=True)
    return result.returncode == 0


def normalize_range(base: str, head: str) -> tuple[str, str]:
    if not ref_exists(head):
        raise ValueError(f"head ref is not available locally: {head}")

    if ref_exists(base):
        return base, head

    fallback = f"{head}~1"
    if ref_exists(fallback):
        print(f"WARNING: base ref {base!r} is not available; falling back to {fallback}", file=sys.stderr)
        return fallback, head

    root = run_git(["rev-list", "--max-parents=0", head]).splitlines()[0]
    print(f"WARNING: base ref {base!r} is not available; falling back to repository root {root}", file=sys.stderr)
    return root, head


def changed_paths(base: str, head: str) -> list[str]:
    output = run_git(["diff", "--name-status", "-M", base, head])
    paths: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        file_parts = parts[1:]
        if not file_parts:
            continue
        if status.startswith("R") and len(file_parts) == 2:
            paths.extend(file_parts)
        else:
            paths.append(file_parts[-1])
    return sorted(set(paths))


def impacted_categories(paths: list[str]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {}
    for path in paths:
        if path in IGNORED_IMPACT_PATHS:
            continue
        for category, prefixes in IMPACT_RULES:
            if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes):
                categories.setdefault(category, []).append(path)
                break
    return categories


def is_runbook_doc(path: str) -> bool:
    if path in RUNBOOK_FILES:
        return True
    if path.endswith("/README.md"):
        return True
    if path.startswith("docs/") and path.endswith(".md"):
        name = Path(path).name.upper()
        return any(hint in name for hint in RUNBOOK_HINTS)
    return False


def added_pilot_refs(base: str, head: str) -> list[str]:
    output = run_git(
        [
            "diff",
            "--unified=0",
            "--no-ext-diff",
            base,
            head,
            "--",
            ".",
            ":(exclude)docs/archive/**",
            ":(exclude)docs/70-generated-qa/qa/**",
            ":(exclude)docs/70-generated-qa/architecture/**",
        ]
    )
    refs: set[str] = set()
    for line in output.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        refs.update(xyz_PILOT_REF_RE.findall(line))
    return sorted(refs)


def _git_show_subject(ref: str) -> str:
    """Return the commit subject for `ref` (the `%s` format)."""
    return run_git(["show", "--no-patch", "--format=%s", ref]).strip()


def is_dependabot_dep_bump(base: str, head: str) -> bool:
    """XYZ-PILOT-197: detect Dependabot dependency-version-only bump commits.

    Returns True iff the HEAD commit:
      - Has a Dependabot-style subject (matches `^deps\\(`), AND
      - Every added line in the diff matches one of the
        DEPENDABOT_BUMP_ADD_PATTERNS regexes (pure version pin changes).

    A commit that ALSO has manual edits (e.g. a Dependabot PR that the
    operator amended with a runbook tweak) will NOT match — non-matching
    added lines disqualify it.

    Used to short-circuit the docs-discipline check: pure dep bumps do
    not require a paired runbook update.
    """
    try:
        subject = _git_show_subject(head)
    except subprocess.CalledProcessError:
        return False
    if not DEPENDABOT_SUBJECT_RE.match(subject):
        return False

    # Walk every added line in the diff. Empty/blank/comment additions
    # are allowed (commit-message-style trailers etc); only NON-pattern
    # additions disqualify.
    try:
        diff = run_git(["diff", "--unified=0", "--no-ext-diff", base, head])
    except subprocess.CalledProcessError:
        return False
    for raw in diff.splitlines():
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        stripped = raw[1:].lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if not any(p.match(stripped) for p in DEPENDABOT_BUMP_ADD_PATTERNS):
            return False
    return True


def emit_summary(lines: list[str], enabled: bool) -> None:
    if enabled:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
                handle.write("\n")
    print("\n".join(lines))


def _self_test() -> int:
    """XYZ-PILOT-197: self-test — exercise both is_dependabot_dep_bump paths
    against known commits. Returns 0 on success, 1 on any mismatch.

    Expected behaviour:
      e8ad8b0 — Dependabot setup-python bump      → True  (skip)
      c580fc1 — chatbot UI text update (code)     → False (must check)
      c87377c — runbook section addition (doc)    → False (must check)
      3b0d816 — xyz_master tracker mutation       → False (must check)
    """
    cases = [
        ("e8ad8b0", True,  "Dependabot setup-python bump"),
        ("c580fc1", False, "chatbot UI text update (real code change)"),
        ("c87377c", False, "runbook section addition (real doc change)"),
        ("3b0d816", False, "xyz_master tracker mutation"),
    ]
    failures: list[str] = []
    for sha, expected, label in cases:
        try:
            actual = is_dependabot_dep_bump(f"{sha}~1", sha)
        except (subprocess.CalledProcessError, ValueError) as exc:
            failures.append(f"  {sha} ({label}): could not evaluate — {exc}")
            continue
        verdict = "PASS" if actual == expected else "FAIL"
        print(f"  {verdict}: {sha} → is_dependabot_dep_bump={actual} (expected {expected}) — {label}")
        if actual != expected:
            failures.append(f"  {sha} ({label}): expected {expected}, got {actual}")
    if failures:
        print(f"\nself-test FAILED — {len(failures)} mismatch(es):", file=sys.stderr)
        for f in failures:
            print(f, file=sys.stderr)
        return 1
    print(f"\nself-test OK — all {len(cases)} cases match expected.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD~1", help="Base git ref for the documentation-discipline diff")
    parser.add_argument("--head", default="HEAD", help="Head git ref for the documentation-discipline diff")
    parser.add_argument("--github-summary", action="store_true", help="Write a compact report to GITHUB_STEP_SUMMARY")
    parser.add_argument("--self-test", action="store_true",
                        help="XYZ-PILOT-197: exercise is_dependabot_dep_bump against known commits + exit")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()

    base, head = normalize_range(args.base, args.head)

    # XYZ-PILOT-197: short-circuit on Dependabot dependency-version bumps.
    # These have zero runtime behavioral change + don't need a runbook
    # update. Skipping here prevents the false-positive failure email
    # that fires on every future Dependabot merge.
    if is_dependabot_dep_bump(base, head):
        try:
            subject = _git_show_subject(head)
        except subprocess.CalledProcessError:
            subject = "<unavailable>"
        print(
            f"docs_discipline: skipped — Dependabot dependency-version bump detected "
            f"(commit subject: {subject!r}) — no runbook update required per XYZ-PILOT-197."
        )
        return 0

    paths = changed_paths(base, head)
    impacts = impacted_categories(paths)
    runbook_changes = sorted(path for path in paths if is_runbook_doc(path))
    refs = added_pilot_refs(base, head)

    errors: list[str] = []
    if impacts and not runbook_changes:
        errors.append("runtime-impacting changes require at least one runbook/guide/test catalog update")
    if impacts and not refs:
        errors.append("runtime-impacting changes require at least one added XYZ-PILOT-NNN tracker reference")

    summary_lines = [
        "## <PILOT_NAME> Documentation Discipline",
        "",
        f"- Diff range: `{base}` → `{head}`",
        f"- Changed paths: {len(paths)}",
        f"- Runtime-impacting categories: {len(impacts)}",
        f"- Runbook/guide/test catalog changes: {len(runbook_changes)}",
        f"- Added XYZ-PILOT references: {', '.join(refs) if refs else 'none'}",
    ]
    if impacts:
        summary_lines.extend(["", "### Impacted Categories"])
        for category, category_paths in sorted(impacts.items()):
            summary_lines.append(f"- `{category}`: {', '.join(f'`{p}`' for p in category_paths[:5])}")
    if runbook_changes:
        summary_lines.extend(["", "### Documentation Updates"])
        for path in runbook_changes:
            summary_lines.append(f"- `{path}`")
    if errors:
        summary_lines.extend(["", "### Advisory Failures"])
        for error in errors:
            summary_lines.append(f"- ERROR: {error}")

    emit_summary(summary_lines, args.github_summary)

    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    print(
        f"docs_discipline impacts={len(impacts)} runbook_changes={len(runbook_changes)} "
        f"pilot_refs={len(refs)} errors={len(errors)}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
