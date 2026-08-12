#!/usr/bin/env python3
"""Advisory CODEOWNERS and ruleset drift checks for <PILOT_NAME> governance."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
from pathlib import Path
import sys
import tempfile


RUNBOOK = Path("docs/30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md")
CODEOWNERS = Path(".github/CODEOWNERS")
RULESET = Path(".github/rulesets/XYZ-PILOT-088-main-governance.json")
REQUIRED_OWNER = "@<GITHUB_OWNER>"
EXPECTED_RULESET_NAME = "XYZ-PILOT-088 main branch governance"
REQUIRED_RULE_TYPES = {"deletion", "non_fast_forward", "pull_request"}
EXPECTED_BYPASS_ACTORS = [
    {
        "actor_id": 5,
        "actor_type": "RepositoryRole",
        "bypass_mode": "always",
    }
]
EXPECTED_DIRECT_WRITE_PATHS = {
    "docs/XYZ_PILOT_ISSUE_TRACKER.md",
    "docs/archive/XYZ_PILOT_ISSUE_TRACKER_*.md",
    "docs/70-generated-qa/qa/*",
    "docs/70-generated-qa/architecture/*",
}


def read_section_bullets(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    in_section = False
    bullets: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == heading:
            in_section = True
            continue
        if in_section and stripped.startswith("#"):
            break
        if in_section and stripped.startswith("- `") and stripped.endswith("`"):
            bullets.append(stripped[3:-1])
    return bullets


def parse_codeowners(path: Path) -> list[tuple[str, list[str], int]]:
    entries: list[tuple[str, list[str], int]] = []
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"{path}:{lineno}: CODEOWNERS entry needs a pattern and at least one owner")
        entries.append((parts[0], parts[1:], lineno))
    return entries


def normalize_pattern(pattern: str) -> str:
    return pattern.lstrip("/")


def codeowners_match(pattern: str, candidate: str) -> bool:
    normalized_pattern = normalize_pattern(pattern)
    normalized_candidate = candidate.lstrip("/")

    if normalized_pattern == "*":
        return True
    if normalized_pattern.endswith("/"):
        return normalized_candidate.startswith(normalized_pattern)
    if normalized_pattern.endswith("/*"):
        return normalized_candidate.startswith(normalized_pattern[:-1])
    if "*" in normalized_pattern or "?" in normalized_pattern or "[" in normalized_pattern:
        return fnmatch.fnmatch(normalized_candidate, normalized_pattern)
    if normalized_candidate == normalized_pattern:
        return True
    return normalized_candidate.startswith(f"{normalized_pattern}/")


def owner_for_path(entries: list[tuple[str, list[str], int]], candidate: str) -> tuple[list[str], int] | None:
    # GitHub CODEOWNERS uses the last matching pattern.
    match: tuple[list[str], int] | None = None
    for pattern, owners, lineno in entries:
        if codeowners_match(pattern, candidate):
            match = (owners, lineno)
    return match


def validate_codeowners(
    runbook_text: str,
    codeowners_path: Path,
    errors: list[str],
) -> tuple[list[str], list[str], list[tuple[str, list[str], int]]]:
    if not codeowners_path.is_file():
        errors.append(f"{codeowners_path}: missing CODEOWNERS file")
        return [], [], []

    codeowners_text = codeowners_path.read_text(encoding="utf-8")
    entries = parse_codeowners(codeowners_path)
    if not entries:
        errors.append(f"{codeowners_path}: no active CODEOWNERS entries")
    if "XYZ-PILOT-088" not in codeowners_text:
        errors.append(f"{codeowners_path}: missing XYZ-PILOT-088 governance context comment")

    critical_paths = read_section_bullets(runbook_text, "### Critical Paths Prepared For Review Gating")
    direct_write_paths = read_section_bullets(runbook_text, "### Direct-Write Automation Paths To Preserve")
    if len(critical_paths) < 10:
        errors.append("docs/30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md: critical review-gating path list is unexpectedly short")
    if EXPECTED_DIRECT_WRITE_PATHS.difference(direct_write_paths):
        missing = ", ".join(sorted(EXPECTED_DIRECT_WRITE_PATHS.difference(direct_write_paths)))
        errors.append(f"docs/30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md: direct-write automation path list is missing {missing}")

    for critical_path in critical_paths:
        match = owner_for_path(entries, critical_path)
        if not match:
            errors.append(f"{codeowners_path}: critical path {critical_path!r} is not covered by CODEOWNERS")
            continue
        owners, lineno = match
        if REQUIRED_OWNER not in owners:
            errors.append(
                f"{codeowners_path}:{lineno}: critical path {critical_path!r} is owned by {owners}, "
                f"not {REQUIRED_OWNER}"
            )

    return critical_paths, direct_write_paths, entries


def validate_ruleset(path: Path, errors: list[str]) -> dict[str, object] | None:
    if not path.is_file():
        errors.append(f"{path}: missing active ruleset payload")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{path}: invalid JSON: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{path}: ruleset JSON must be an object")
        return None

    if payload.get("name") != EXPECTED_RULESET_NAME:
        errors.append(f"{path}: unexpected ruleset name {payload.get('name')!r}")
    if payload.get("target") != "branch":
        errors.append(f"{path}: target must remain 'branch'")
    if payload.get("enforcement") != "active":
        errors.append(f"{path}: Phase 3 checked-in payload must remain enforcement='active'")

    conditions = payload.get("conditions")
    if not isinstance(conditions, dict):
        errors.append(f"{path}: missing conditions object")
    else:
        ref_name = conditions.get("ref_name")
        if not isinstance(ref_name, dict) or ref_name.get("include") != ["refs/heads/main"]:
            errors.append(f"{path}: ruleset must target refs/heads/main only")
        if isinstance(ref_name, dict) and ref_name.get("exclude") not in ([], None):
            errors.append(f"{path}: ruleset should not exclude main governance refs in the active payload")

    rules = payload.get("rules")
    if not isinstance(rules, list):
        errors.append(f"{path}: missing rules array")
    else:
        rule_types = {rule.get("type") for rule in rules if isinstance(rule, dict)}
        missing_rule_types = REQUIRED_RULE_TYPES.difference(rule_types)
        if missing_rule_types:
            errors.append(f"{path}: missing rule type(s): {', '.join(sorted(missing_rule_types))}")
        pull_request_rules = [rule for rule in rules if isinstance(rule, dict) and rule.get("type") == "pull_request"]
        if len(pull_request_rules) != 1:
            errors.append(f"{path}: expected exactly one pull_request rule")
        elif isinstance(pull_request_rules[0].get("parameters"), dict):
            params = pull_request_rules[0]["parameters"]
            if params.get("required_approving_review_count", 0) < 1:
                errors.append(f"{path}: pull_request rule must require at least one approving review")
            if params.get("require_code_owner_review") is not True:
                errors.append(f"{path}: pull_request rule must require code owner review")
            if params.get("required_review_thread_resolution") is not True:
                errors.append(f"{path}: pull_request rule must require review-thread resolution")
        else:
            errors.append(f"{path}: pull_request rule missing parameters object")

    bypass_actors = payload.get("bypass_actors")
    if bypass_actors != EXPECTED_BYPASS_ACTORS:
        errors.append(
            f"{path}: active Phase 3 payload must define the expected Admin RepositoryRole bypass actor"
        )

    return payload


def emit_summary(lines: list[str], enabled: bool) -> None:
    if enabled:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
                handle.write("\n")
    print("\n".join(lines))


def run_check(runbook: Path, codeowners: Path, ruleset: Path, github_summary: bool) -> int:
    errors: list[str] = []
    if not runbook.is_file():
        errors.append(f"{runbook}: missing GitHub runbook")
        runbook_text = ""
    else:
        runbook_text = runbook.read_text(encoding="utf-8")

    critical_paths, direct_write_paths, entries = validate_codeowners(runbook_text, codeowners, errors)
    ruleset_payload = validate_ruleset(ruleset, errors)
    rule_types = []
    if isinstance(ruleset_payload, dict) and isinstance(ruleset_payload.get("rules"), list):
        rule_types = [
            str(rule.get("type"))
            for rule in ruleset_payload["rules"]
            if isinstance(rule, dict) and rule.get("type")
        ]

    wildcard_owner = any(pattern == "*" and REQUIRED_OWNER in owners for pattern, owners, _lineno in entries)
    summary_lines = [
        "## <PILOT_NAME> CODEOWNERS / Ruleset Drift",
        "",
        f"- CODEOWNERS entries: {len(entries)}",
        f"- Wildcard owner `{REQUIRED_OWNER}` present: `{str(wildcard_owner).lower()}`",
        f"- Critical review-gating paths documented: {len(critical_paths)}",
        f"- Direct-write automation paths documented: {len(direct_write_paths)}",
        f"- Active ruleset file: `{ruleset}`",
        f"- Ruleset enforcement: `{ruleset_payload.get('enforcement') if isinstance(ruleset_payload, dict) else '<missing>'}`",
        f"- Ruleset rule types: {', '.join(rule_types) if rule_types else '<missing>'}",
    ]
    if errors:
        summary_lines.extend(["", "### Drift Failures"])
        summary_lines.extend(f"- ERROR: {error}" for error in errors)
    else:
        summary_lines.extend(["", "Status: CODEOWNERS / ruleset drift OK"])

    emit_summary(summary_lines, github_summary)

    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    print(
        f"codeowners_ruleset_drift entries={len(entries)} critical_paths={len(critical_paths)} "
        f"direct_write_paths={len(direct_write_paths)} errors={len(errors)}"
    )
    return 1 if errors else 0


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="xyz-codeowners-drift-") as raw_tmp:
        tmp = Path(raw_tmp)
        runbook = tmp / "XYZ_PILOT_GITHUB_RUNBOOK.md"
        codeowners = tmp / "CODEOWNERS"
        ruleset = tmp / "ruleset.json"
        critical = "\n".join(f"- `path{i}/`" for i in range(1, 11))
        direct = "\n".join(f"- `{path}`" for path in sorted(EXPECTED_DIRECT_WRITE_PATHS))
        runbook.write_text(
            f"### Critical Paths Prepared For Review Gating\n\n{critical}\n\n"
            f"### Direct-Write Automation Paths To Preserve\n\n{direct}\n\n## Next\n",
            encoding="utf-8",
        )
        codeowners.write_text("# XYZ-PILOT-088 Phase 3 active governance.\n* @<GITHUB_OWNER>\n", encoding="utf-8")
        ruleset.write_text(
            json.dumps(
                {
                    "name": EXPECTED_RULESET_NAME,
                    "target": "branch",
                    "enforcement": "active",
                    "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
                    "rules": [
                        {"type": "deletion"},
                        {"type": "non_fast_forward"},
                        {
                            "type": "pull_request",
                            "parameters": {
                                "required_approving_review_count": 1,
                                "require_code_owner_review": True,
                                "required_review_thread_resolution": True,
                            },
                        },
                    ],
                    "bypass_actors": EXPECTED_BYPASS_ACTORS,
                }
            ),
            encoding="utf-8",
        )
        return run_check(runbook, codeowners, ruleset, False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runbook", type=Path, default=RUNBOOK)
    parser.add_argument("--codeowners", type=Path, default=CODEOWNERS)
    parser.add_argument("--ruleset", type=Path, default=RULESET)
    parser.add_argument("--github-summary", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    return run_check(args.runbook, args.codeowners, args.ruleset, args.github_summary)


if __name__ == "__main__":
    raise SystemExit(main())
