#!/usr/bin/env python3
"""Advisory health check for the <PILOT_NAME> GitHub repository backups."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile


EXPECTED_REPOSITORY = "<GITHUB_REPOSITORY>"
EXPECTED_ARCHIVE_KEY = "latest/<PROJECT_SLUG>.git.tar.gz"
EXPECTED_CHECKSUM_KEY = f"{EXPECTED_ARCHIVE_KEY}.sha256"
EXPECTED_MANIFEST_KEY = "latest/latest.json"


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def age_hours(stamp: datetime, now: datetime) -> float:
    return (now - stamp).total_seconds() / 3600


def object_summary(head: dict[str, object], key: str, min_bytes: int, errors: list[str]) -> tuple[str, int]:
    last_modified_raw = str(head.get("LastModified", ""))
    content_length = int(head.get("ContentLength", 0) or 0)
    if not last_modified_raw:
        errors.append(f"{key}: missing LastModified in S3 head-object output")
    if content_length < min_bytes:
        errors.append(f"{key}: content length {content_length} is below expected minimum {min_bytes}")
    return last_modified_raw, content_length


def validate_manifest(
    manifest: dict[str, object],
    bucket: str,
    max_age_hours: float,
    now: datetime,
    errors: list[str],
) -> tuple[str, str, datetime]:
    repository = str(manifest.get("repository", ""))
    commit_sha = str(manifest.get("commit_sha", ""))
    workflow_run_id = str(manifest.get("workflow_run_id", ""))
    backup_uri = str(manifest.get("backup_uri", ""))
    created_at_raw = str(manifest.get("created_at_utc", ""))

    if repository != EXPECTED_REPOSITORY:
        errors.append(f"latest manifest repository {repository!r} does not match {EXPECTED_REPOSITORY!r}")
    if not commit_sha or len(commit_sha) != 40:
        errors.append("latest manifest commit_sha is missing or not a full 40-character SHA")
    if not workflow_run_id.isdigit():
        errors.append("latest manifest workflow_run_id is missing or not numeric")
    expected_prefix = f"s3://{bucket}/daily/"
    if not backup_uri.startswith(expected_prefix) or not backup_uri.endswith(".git.tar.gz"):
        errors.append(f"latest manifest backup_uri {backup_uri!r} does not look like a daily backup URI")

    try:
        created_at = parse_timestamp(created_at_raw)
        manifest_age = age_hours(created_at, now)
        if manifest_age < -0.25:
            errors.append(f"latest manifest created_at_utc is in the future: {created_at_raw}")
        if manifest_age > max_age_hours:
            errors.append(
                f"latest manifest age {manifest_age:.1f}h exceeds allowed {max_age_hours:.1f}h"
            )
    except Exception as exc:  # noqa: BLE001 - report malformed operator data clearly.
        errors.append(f"latest manifest created_at_utc is invalid: {created_at_raw!r}: {exc}")
        created_at = now

    return commit_sha, workflow_run_id, created_at


def validate_object_metadata(
    label: str,
    head: dict[str, object],
    expected_commit_sha: str,
    expected_workflow_run_id: str,
    errors: list[str],
) -> None:
    metadata = head.get("Metadata", {})
    if not isinstance(metadata, dict):
        errors.append(f"{label}: S3 metadata is not a JSON object")
        return
    commit_sha = str(metadata.get("commit-sha", ""))
    workflow_run_id = str(metadata.get("workflow-run-id", ""))
    if expected_commit_sha and commit_sha and commit_sha != expected_commit_sha:
        errors.append(f"{label}: metadata commit-sha {commit_sha} does not match manifest {expected_commit_sha}")
    if expected_workflow_run_id and workflow_run_id and workflow_run_id != expected_workflow_run_id:
        errors.append(
            f"{label}: metadata workflow-run-id {workflow_run_id} does not match manifest {expected_workflow_run_id}"
        )


def validate_runs(
    runs: list[object],
    workflow_run_id: str,
    commit_sha: str,
    created_at: datetime,
    max_age_hours: float,
    now: datetime,
    errors: list[str],
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    normalized_runs = [run for run in runs if isinstance(run, dict)]
    latest_run = normalized_runs[0] if normalized_runs else None
    matching_run = next((run for run in normalized_runs if str(run.get("databaseId", "")) == workflow_run_id), None)

    if not latest_run:
        errors.append("no repo-backup.yml workflow runs were returned by GitHub")
        return None, None

    if latest_run.get("status") != "completed" or latest_run.get("conclusion") != "success":
        errors.append(
            "latest repo-backup.yml run is not successful: "
            f"status={latest_run.get('status')} conclusion={latest_run.get('conclusion')}"
        )

    if not matching_run:
        errors.append(f"manifest workflow_run_id {workflow_run_id} was not found in recent repo-backup.yml runs")
        return latest_run, None

    if matching_run.get("status") != "completed" or matching_run.get("conclusion") != "success":
        errors.append(
            f"manifest workflow run {workflow_run_id} is not successful: "
            f"status={matching_run.get('status')} conclusion={matching_run.get('conclusion')}"
        )

    head_sha = str(matching_run.get("headSha", ""))
    if commit_sha and head_sha and head_sha != commit_sha:
        errors.append(f"manifest run headSha {head_sha} does not match manifest commit {commit_sha}")

    updated_at_raw = str(matching_run.get("updatedAt", ""))
    try:
        updated_at = parse_timestamp(updated_at_raw)
        validation_age = age_hours(updated_at, now)
        if validation_age < -0.25:
            errors.append(f"manifest workflow run updatedAt is in the future: {updated_at_raw}")
        if validation_age > max_age_hours:
            errors.append(
                f"last successful restore-validation run age {validation_age:.1f}h exceeds allowed {max_age_hours:.1f}h"
            )
        if updated_at < created_at:
            errors.append(
                f"manifest workflow run updatedAt {updated_at_raw} is older than manifest creation "
                f"{created_at.isoformat()}"
            )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"manifest workflow run updatedAt is invalid: {updated_at_raw!r}: {exc}")

    return latest_run, matching_run


def emit_summary(lines: list[str], enabled: bool) -> None:
    if enabled:
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
                handle.write("\n")
    print("\n".join(lines))


def run_check(args: argparse.Namespace) -> int:
    errors: list[str] = []
    now = parse_timestamp(args.now) if args.now else datetime.now(timezone.utc)

    manifest_obj = load_json(Path(args.manifest_json))
    manifest_head = load_json(Path(args.manifest_head_json))
    archive_head = load_json(Path(args.archive_head_json))
    checksum_head = load_json(Path(args.checksum_head_json))
    runs_obj = load_json(Path(args.workflow_runs_json))

    if not isinstance(manifest_obj, dict):
        raise ValueError("manifest JSON must be an object")
    if not isinstance(manifest_head, dict) or not isinstance(archive_head, dict) or not isinstance(checksum_head, dict):
        raise ValueError("S3 head-object JSON files must be objects")
    if not isinstance(runs_obj, list):
        raise ValueError("workflow runs JSON must be a list")

    commit_sha, workflow_run_id, created_at = validate_manifest(
        manifest_obj,
        args.bucket,
        args.max_age_hours,
        now,
        errors,
    )
    object_summary(manifest_head, EXPECTED_MANIFEST_KEY, 80, errors)
    archive_last_modified, archive_bytes = object_summary(archive_head, EXPECTED_ARCHIVE_KEY, 1024, errors)
    checksum_last_modified, checksum_bytes = object_summary(checksum_head, EXPECTED_CHECKSUM_KEY, 32, errors)
    validate_object_metadata("latest archive", archive_head, commit_sha, workflow_run_id, errors)
    validate_object_metadata("latest checksum", checksum_head, commit_sha, workflow_run_id, errors)
    latest_run, matching_run = validate_runs(
        runs_obj,
        workflow_run_id,
        commit_sha,
        created_at,
        args.max_age_hours,
        now,
        errors,
    )

    summary_lines = [
        "## <PILOT_NAME> Repo Backup Health",
        "",
        f"- Bucket: `{args.bucket}`",
        f"- Manifest age: {age_hours(created_at, now):.1f}h",
        f"- Manifest commit: `{commit_sha or '<missing>'}`",
        f"- Manifest workflow run: `{workflow_run_id or '<missing>'}`",
        f"- Latest archive bytes: {archive_bytes}",
        f"- Latest archive last modified: `{archive_last_modified or '<missing>'}`",
        f"- Latest checksum bytes: {checksum_bytes}",
        f"- Latest checksum last modified: `{checksum_last_modified or '<missing>'}`",
        f"- Latest backup workflow conclusion: `{latest_run.get('conclusion') if latest_run else '<missing>'}`",
        f"- Manifest run conclusion: `{matching_run.get('conclusion') if matching_run else '<missing>'}`",
        f"- Allowed max age: {args.max_age_hours:.1f}h",
    ]
    if matching_run and matching_run.get("url"):
        summary_lines.append(f"- Manifest workflow URL: {matching_run.get('url')}")
    if errors:
        summary_lines.extend(["", "### Health Failures"])
        summary_lines.extend(f"- ERROR: {error}" for error in errors)
    else:
        summary_lines.extend(["", "Status: backup health OK"])

    emit_summary(summary_lines, args.github_summary)

    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    print(f"backup_health errors={len(errors)} max_age_hours={args.max_age_hours:.1f}")
    return 1 if errors else 0


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def self_test() -> int:
    now = datetime(2026, 5, 29, 9, 30, tzinfo=timezone.utc)
    commit_sha = "a" * 40
    run_id = "26600000000"
    with tempfile.TemporaryDirectory(prefix="xyz-backup-health-") as raw_tmp:
        tmp = Path(raw_tmp)
        manifest = {
            "repository": EXPECTED_REPOSITORY,
            "commit_sha": commit_sha,
            "workflow_run_id": run_id,
            "backup_uri": "s3://<PROJECT_SLUG>-repo-backups-<AWS_ACCOUNT_ID>/daily/2026/05/29/"
            "<PROJECT_SLUG>_20260529T090000Z_aaaaaaaaaaaa.git.tar.gz",
            "created_at_utc": "2026-05-29T09:00:00Z",
        }
        head_base = {
            "LastModified": "2026-05-29T09:00:10Z",
            "ContentLength": 2048,
            "Metadata": {
                "commit-sha": commit_sha,
                "workflow-run-id": run_id,
            },
        }
        runs = [
            {
                "databaseId": int(run_id),
                "status": "completed",
                "conclusion": "success",
                "headSha": commit_sha,
                "createdAt": "2026-05-29T09:00:00Z",
                "updatedAt": "2026-05-29T09:03:00Z",
                "url": "https://github.com/<GITHUB_REPOSITORY>/actions/runs/26600000000",
            }
        ]
        files = {
            "manifest.json": manifest,
            "manifest-head.json": {**head_base, "ContentLength": 180},
            "archive-head.json": head_base,
            "checksum-head.json": {**head_base, "ContentLength": 96},
            "runs.json": runs,
        }
        for name, payload in files.items():
            write_json(tmp / name, payload)
        args = argparse.Namespace(
            bucket="<PROJECT_SLUG>-repo-backups-<AWS_ACCOUNT_ID>",
            manifest_json=str(tmp / "manifest.json"),
            manifest_head_json=str(tmp / "manifest-head.json"),
            archive_head_json=str(tmp / "archive-head.json"),
            checksum_head_json=str(tmp / "checksum-head.json"),
            workflow_runs_json=str(tmp / "runs.json"),
            max_age_hours=30.0,
            now=now.isoformat(),
            github_summary=False,
        )
        return run_check(args)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", default="<PROJECT_SLUG>-repo-backups-<AWS_ACCOUNT_ID>")
    parser.add_argument("--manifest-json", default="/tmp/xyz-backup-health/latest.json")
    parser.add_argument("--manifest-head-json", default="/tmp/xyz-backup-health/latest-head.json")
    parser.add_argument("--archive-head-json", default="/tmp/xyz-backup-health/archive-head.json")
    parser.add_argument("--checksum-head-json", default="/tmp/xyz-backup-health/checksum-head.json")
    parser.add_argument("--workflow-runs-json", default="/tmp/xyz-backup-health/backup-runs.json")
    parser.add_argument("--max-age-hours", type=float, default=30.0)
    parser.add_argument("--now", default="")
    parser.add_argument("--github-summary", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    return run_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
