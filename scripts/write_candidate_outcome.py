#!/usr/bin/env python3
"""Write an XYZ_MASTER candidate-outcome marker.

Markers live next to `issue-candidates.md` under `candidate-outcomes/`.
They are committed evidence that one candidate was accepted into the tracker
or denied as duplicate / wontfix / invalid. The original candidate file stays
unchanged.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import sys


VALID_OUTCOMES = {"accepted", "denied"}
VALID_CLASSIFICATIONS = {
    "critical",
    "major",
    "minor",
    "feature",
    "external",
    "hygiene",
    "status",
}
VALID_DENIAL_REASONS = {"duplicate", "wontfix", "invalid", "merged", "out-of-scope"}
TRACKER_ID_RE = re.compile(r"^XYZ-PILOT-\d{3}$")


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def yaml_value(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    if value is None:
        return "null"
    return json.dumps(str(value), ensure_ascii=False)


def normalize_tracker_id(value: str | None, field_name: str) -> str | None:
    if not value:
        return None
    out = value.strip().upper()
    if not TRACKER_ID_RE.fullmatch(out):
        raise ValueError(f"{field_name} must look like XYZ-PILOT-NNN, got {value!r}")
    return out


def outcome_filename(
    candidate_index: int,
    outcome: str,
    classification: str | None,
    tracker_id: str | None,
    denial_reason: str | None,
    duplicate_of: str | None,
) -> str:
    parts = [f"candidate-{candidate_index:03d}", outcome.upper()]
    if outcome == "accepted":
        if classification:
            parts.append(classification.upper().replace("-", "_"))
        if tracker_id:
            parts.append(tracker_id)
    else:
        if denial_reason:
            parts.append(denial_reason.upper().replace("-", "_"))
        if duplicate_of:
            parts.append(duplicate_of)
    return ".".join(parts) + ".md"


def build_marker(args: argparse.Namespace) -> tuple[Path, str]:
    candidate_file = Path(args.candidate_file)
    if candidate_file.name != "issue-candidates.md":
        raise ValueError("candidate file must be named issue-candidates.md")
    if args.candidate_index < 1:
        raise ValueError("--candidate-index must be >= 1")

    outcome = args.outcome.lower()
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"--outcome must be one of {sorted(VALID_OUTCOMES)}")

    classification = args.classification.lower() if args.classification else None
    if classification and classification not in VALID_CLASSIFICATIONS:
        raise ValueError(f"--classification must be one of {sorted(VALID_CLASSIFICATIONS)}")

    tracker_id = normalize_tracker_id(args.tracker_id, "--tracker-id")
    duplicate_of = normalize_tracker_id(args.duplicate_of, "--duplicate-of")
    denial_reason = args.denial_reason.lower() if args.denial_reason else None
    if denial_reason and denial_reason not in VALID_DENIAL_REASONS:
        raise ValueError(f"--denial-reason must be one of {sorted(VALID_DENIAL_REASONS)}")

    if outcome == "accepted":
        if not tracker_id:
            raise ValueError("--tracker-id is required for accepted outcomes")
        if not classification:
            raise ValueError("--classification is required for accepted outcomes")
    else:
        if not denial_reason:
            raise ValueError("--denial-reason is required for denied outcomes")
        if denial_reason == "duplicate" and not duplicate_of:
            raise ValueError("--duplicate-of is required when --denial-reason duplicate")

    decision_at = args.decision_at_utc or utc_now()
    marker_dir = candidate_file.parent / "candidate-outcomes"
    marker_path = marker_dir / outcome_filename(
        args.candidate_index,
        outcome,
        classification,
        tracker_id,
        denial_reason,
        duplicate_of,
    )

    fields: dict[str, object] = {
        "candidate_index": args.candidate_index,
        "source_file": candidate_file.as_posix(),
        "outcome": outcome,
        "decision_by": args.decision_by,
        "decision_at_utc": decision_at,
        "commit": args.commit,
    }
    if args.title:
        fields["title"] = args.title
    if outcome == "accepted":
        fields["classification"] = classification
        fields["tracker_id"] = tracker_id
        fields["tracker_status"] = args.tracker_status
    else:
        fields["denial_reason"] = denial_reason
        if duplicate_of:
            fields["duplicate_of"] = duplicate_of

    frontmatter = ["---"]
    frontmatter.extend(f"{key}: {yaml_value(value)}" for key, value in fields.items() if value is not None)
    frontmatter.append("---")

    if outcome == "accepted":
        heading = f"# Candidate outcome: ACCEPTED -> {tracker_id}"
        body_lines = [
            heading,
            "",
            f"Candidate {args.candidate_index} from `{candidate_file.as_posix()}` was accepted by `{args.decision_by}`.",
            f"Tracker entry: `{tracker_id}`.",
        ]
        if args.tracker_status:
            body_lines.append(f"Tracker status: `{args.tracker_status}`.")
    else:
        heading = f"# Candidate outcome: DENIED -> {denial_reason}"
        body_lines = [
            heading,
            "",
            f"Candidate {args.candidate_index} from `{candidate_file.as_posix()}` was denied by `{args.decision_by}`.",
            f"Reason: `{denial_reason}`.",
        ]
        if duplicate_of:
            body_lines.append(f"Duplicate of: `{duplicate_of}`.")
    if args.notes:
        body_lines.extend(["", "## Notes", "", args.notes])

    return marker_path, "\n".join(frontmatter + [""] + body_lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Write an XYZ_MASTER candidate outcome marker.")
    parser.add_argument("--candidate-file", required=True)
    parser.add_argument("--candidate-index", required=True, type=int)
    parser.add_argument("--outcome", required=True, choices=sorted(VALID_OUTCOMES))
    parser.add_argument("--classification", choices=sorted(VALID_CLASSIFICATIONS))
    parser.add_argument("--tracker-id")
    parser.add_argument("--tracker-status")
    parser.add_argument("--denial-reason", choices=sorted(VALID_DENIAL_REASONS))
    parser.add_argument("--duplicate-of")
    parser.add_argument("--decision-by", default="xyz_master")
    parser.add_argument("--decision-at-utc")
    parser.add_argument("--commit", default="same-commit")
    parser.add_argument("--title")
    parser.add_argument("--notes")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    try:
        marker_path, content = build_marker(args)
        if args.dry_run:
            print(marker_path)
            print(content)
            return 0
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        if marker_path.exists() and not args.overwrite:
            print(f"ERROR: marker already exists: {marker_path}", file=sys.stderr)
            return 1
        marker_path.write_text(content, encoding="utf-8")
        print(marker_path)
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI should show the validation reason.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
