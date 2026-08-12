#!/usr/bin/env python3
"""E2E checks for GitHub Actions workflow safety and template rendering."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


BASE_WORKFLOWS = (
    "actions-usage-watch.yml",
    "backup-health-summary.yml",
    "codeowners-ruleset-drift.yml",
    "docs-discipline.yml",
    "e2e-swarm-nightly.yml",
    "github-actions-e2e.yml",
    "markdown-link-anchor-check.yml",
    "notify-on-tracker-mutation.yml",
    "regression-validation.yml",
    "repo-backup.yml",
    "repo-hygiene.yml",
    "runbook-archive-companion.yml",
    "secret-scan.yml",
    "tracker-major-defect-heads-up.yml",
    "tracker-major-defect-resolved-sns.yml",
    "vscode-autoapprove-safety.yml",
    "xyz-master-background-filing.yml",
)

SAFE_SCHEDULED = {
    "actions-usage-watch.yml",
    "secret-scan.yml",
}

SAFE_PUSH = {
    "secret-scan.yml",
    "vscode-autoapprove-safety.yml",
}

MANUAL_ONLY_TEMPLATE = {
    "backup-health-summary.yml",
    "e2e-swarm-nightly.yml",
    "notify-on-tracker-mutation.yml",
    "repo-backup.yml",
    "tracker-major-defect-heads-up.yml",
    "tracker-major-defect-resolved-sns.yml",
    "xyz-master-background-filing.yml",
}

ALLOWED_CONTENTS_WRITE = {
    "e2e-swarm-nightly.yml",
    "xyz-master-background-filing.yml",
}

AWS_PLACEHOLDER_MARKERS = (
    "<AWS_ACCOUNT_ID>",
    "ROLE_TO_ASSUME:",
    "SNS_TOPIC_ARN:",
    "SES_SOURCE:",
)

SOURCE_PILOT_MARKERS = (
    "<EC2_INSTANCE_ID>",
    "<EXASOL_HOSTNAME>",
    "<SOURCE_AWS_ACCOUNT_ID>",
    "<AWS_ACCOUNT_ID>",
)


def workflow_name(filename: str, project_id: str) -> str:
    if project_id == "xyz":
        return filename
    return filename.replace("xyz-", f"{project_id}-")


def expected_workflows(project_id: str) -> list[str]:
    return sorted(workflow_name(name, project_id) for name in BASE_WORKFLOWS)


def canonical_name(filename: str, project_id: str) -> str:
    if project_id != "xyz" and filename == f"{project_id}-master-background-filing.yml":
        return "xyz-master-background-filing.yml"
    return filename


def extract_top_level_block(text: str, key: str) -> str:
    lines = text.splitlines()
    in_block = False
    block: list[str] = []
    for line in lines:
        if line == f"{key}:":
            in_block = True
            continue
        if in_block:
            if line and not line.startswith((" ", "#")):
                break
            block.append(line)
    return "\n".join(block)


def has_event(text: str, event_name: str) -> bool:
    on_block = extract_top_level_block(text, "on")
    return re.search(rf"^\s{{2}}{re.escape(event_name)}\s*:", on_block, re.MULTILINE) is not None


def has_workflow_dispatch(text: str) -> bool:
    return has_event(text, "workflow_dispatch")


def path_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def append_command_result(
    root: Path,
    command: list[str],
    label: str,
    errors: list[str],
    notes: list[str],
) -> None:
    result = subprocess.run(command, cwd=root, text=True, capture_output=True)
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if result.returncode:
        errors.append(f"{label} failed with exit {result.returncode}:\n{output}")
    else:
        notes.append(f"{label}: OK")
        if output:
            notes.append(output)


def check_workflow_inventory(root: Path, project_id: str, errors: list[str]) -> list[Path]:
    workflows_dir = root / ".github" / "workflows"
    paths = sorted(workflows_dir.glob("*.yml"))
    actual = sorted(path.name for path in paths)
    expected = expected_workflows(project_id)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        if missing:
            errors.append(f"missing workflow files: {', '.join(missing)}")
        if unexpected:
            errors.append(f"unexpected workflow files: {', '.join(unexpected)}")
    return paths


def check_catalog(root: Path, project_id: str, workflow_paths: list[Path], errors: list[str]) -> None:
    candidates = sorted((root / "docs" / "30-runbooks" / "github").glob("*_PILOT_GITHUB_RUNBOOK.md"))
    if not candidates:
        errors.append("missing GitHub runbook under docs/30-runbooks/github")
        return
    text = "\n".join(path_text(path) for path in candidates)
    expected_count = len(workflow_paths)
    if f"There are {expected_count} GitHub Actions workflows" not in text:
        errors.append(f"GitHub runbook must state the current workflow count: {expected_count}")
    if "There are 16 GitHub Actions workflows" in text and expected_count != 16:
        errors.append("GitHub runbook still contains stale 16-workflow wording")
    for path in workflow_paths:
        if f"`{path.name}`" not in text:
            errors.append(f"{path.name}: missing from GitHub Actions workflow catalog")
    if "XYZ-PILOT-001" not in text and project_id == "xyz":
        errors.append("GitHub runbook must link workflow activation posture to XYZ-PILOT-001")


def check_triggers_and_permissions(
    workflow_paths: list[Path],
    project_id: str,
    errors: list[str],
    notes: list[str],
) -> None:
    for path in workflow_paths:
        text = path_text(path)
        name = canonical_name(path.name, project_id)
        if not has_workflow_dispatch(text):
            errors.append(f"{path.name}: missing workflow_dispatch trigger")
        has_schedule = has_event(text, "schedule")
        has_push = has_event(text, "push")
        has_pull_request = has_event(text, "pull_request")
        if has_schedule and name not in SAFE_SCHEDULED:
            errors.append(f"{path.name}: schedule trigger is not allowed in the neutral template")
        if has_push and name not in SAFE_PUSH:
            errors.append(f"{path.name}: push trigger is not allowed in the neutral template")
        if name in MANUAL_ONLY_TEMPLATE and (has_schedule or has_push or has_pull_request):
            errors.append(f"{path.name}: must remain workflow_dispatch-only in the neutral template")
        if "contents: write" in text and name not in ALLOWED_CONTENTS_WRITE:
            errors.append(f"{path.name}: unexpected contents: write permission")
        if "id-token: write" in text and not any(marker in text for marker in AWS_PLACEHOLDER_MARKERS):
            errors.append(f"{path.name}: id-token: write without visible AWS/OIDC dependency")
        if any(marker in text for marker in AWS_PLACEHOLDER_MARKERS) and (has_schedule or has_push):
            errors.append(f"{path.name}: AWS placeholder workflow must not run on schedule or push")
        notes.append(
            f"{path.name}: dispatch={has_workflow_dispatch(text)} "
            f"pr={has_pull_request} push={has_push} schedule={has_schedule}"
        )


def check_template_leakage(root: Path, project_id: str, workflow_paths: list[Path], errors: list[str]) -> None:
    workflow_text = "\n".join(path_text(path) for path in workflow_paths)
    for marker in SOURCE_PILOT_MARKERS:
        if marker in workflow_text:
            errors.append(f"source-pilot marker leaked into workflow definitions: {marker}")
    if project_id != "xyz":
        generated_forbidden = ("<PILOT_NAME>", "XYZ-PILOT", "XYZ_", "xyz-pilot", "xyz_master")
        for marker in generated_forbidden:
            if marker in workflow_text:
                errors.append(f"generated workflow still contains template marker: {marker}")
        expected_master = f"{project_id}-master-background-filing.yml"
        if not (root / ".github" / "workflows" / expected_master).exists():
            errors.append(f"generated repository missing rendered master workflow: {expected_master}")


def check_generated_repository(root: Path, args: argparse.Namespace, errors: list[str], notes: list[str]) -> None:
    if args.skip_generate:
        return
    generate = root / "generate"
    if not generate.exists():
        errors.append("generate wrapper missing; cannot run generated-repository workflow smoke")
        return
    with tempfile.TemporaryDirectory(prefix="exasol-template-actions-e2e-") as tmp:
        target_root = Path(tmp)
        command = [
            str(generate),
            "-project",
            args.generated_project,
            "--project-id",
            args.generated_project_id,
            "-pilot-name",
            args.generated_pilot_name,
            "-customer-name",
            args.generated_customer_name,
            "-awsprofile",
            "default",
            "-awsregion",
            "eu-central-1",
            "-awsaz",
            "eu-central-1a",
            "--target-root",
            str(target_root),
        ]
        result = subprocess.run(command, cwd=root, text=True, capture_output=True)
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        if result.returncode:
            errors.append(f"generate smoke failed with exit {result.returncode}:\n{output}")
            return
        generated = target_root / args.generated_project
        if not generated.is_dir():
            errors.append(f"generated repository missing: {generated}")
            return
        notes.append(f"generated repository: {generated}")
        generated_script = generated / "scripts" / "check_github_actions_e2e.py"
        command = [
            sys.executable,
            str(generated_script),
            "--repo-root",
            str(generated),
            "--project-id",
            args.generated_project_id,
            "--skip-generate",
        ]
        result = subprocess.run(command, cwd=generated, text=True, capture_output=True)
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        if result.returncode:
            errors.append(f"generated workflow E2E failed with exit {result.returncode}:\n{output}")
        else:
            notes.append("generated workflow E2E: OK")
            if output:
                notes.append(output)


def write_github_summary(errors: list[str], notes: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = ["## GitHub Actions E2E", ""]
    lines.append(f"- Errors: `{len(errors)}`")
    lines.append(f"- Notes: `{len(notes)}`")
    if errors:
        lines.extend(["", "### Errors"])
        lines.extend(f"- {error.splitlines()[0]}" for error in errors)
    if notes:
        lines.extend(["", "### Notes"])
        lines.extend(f"- {note.splitlines()[0]}" for note in notes[:30])
    Path(summary_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--project-id", default="xyz")
    parser.add_argument("--skip-generate", action="store_true")
    parser.add_argument("--generated-project", default="gcme-analytics-pilot")
    parser.add_argument("--generated-project-id", default="gcm")
    parser.add_argument("--generated-pilot-name", default="GCME Analytics Pilot")
    parser.add_argument("--generated-customer-name", default="GCME - German Companies Make Everything")
    parser.add_argument("--github-summary", action="store_true")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    project_id = args.project_id.lower()
    errors: list[str] = []
    notes: list[str] = []

    if not re.fullmatch(r"[a-z]{3}", project_id):
        errors.append(f"--project-id must be exactly three lowercase letters: {args.project_id}")
    workflow_paths = check_workflow_inventory(root, project_id, errors)
    check_catalog(root, project_id, workflow_paths, errors)
    check_triggers_and_permissions(workflow_paths, project_id, errors, notes)
    check_template_leakage(root, project_id, workflow_paths, errors)
    append_command_result(
        root,
        [sys.executable, "scripts/check_github_workflow_policy.py", "--github-warnings"],
        "workflow policy check",
        errors,
        notes,
    )
    check_generated_repository(root, args, errors, notes)

    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    for note in notes:
        print(note)
    print(f"github_actions_e2e workflows={len(workflow_paths)} errors={len(errors)}")
    if args.github_summary:
        write_github_summary(errors, notes)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
