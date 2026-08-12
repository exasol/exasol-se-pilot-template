#!/usr/bin/env python3
"""Validate durable XYZ_MASTER candidate outcome coverage.

This catches the class where an operator approves several candidates from one
`issue-candidates.md` file, one workflow run fails before writing its accepted
marker, and later successful runs make the tracker look healthy while one
approved candidate silently remains undecided.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import re
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
TRACKER_PATH = Path("docs/XYZ_PILOT_ISSUE_TRACKER.md")

SPEC = importlib.util.spec_from_file_location(
    "xyz_master_background_filing",
    ROOT / "scripts" / "xyz_master_background_filing.py",
)
assert SPEC and SPEC.loader
xyz_master = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = xyz_master
SPEC.loader.exec_module(xyz_master)


@dataclass(frozen=True)
class OutcomeMarker:
    path: Path
    candidate_index: int
    outcome: str
    tracker_id: str
    tracker_status: str


@dataclass(frozen=True)
class OutcomeValidation:
    candidate_file: Path
    checked_indexes: tuple[int, ...]
    missing_indexes: tuple[int, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _front_matter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValueError(f"{path}: missing YAML-like front matter")
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    return data


def parse_outcome_marker(path: Path) -> OutcomeMarker:
    data = _front_matter(path)
    try:
        candidate_index = int(data.get("candidate_index", "0"))
    except ValueError as exc:
        raise ValueError(f"{path}: invalid candidate_index") from exc
    return OutcomeMarker(
        path=path,
        candidate_index=candidate_index,
        outcome=data.get("outcome", "").lower(),
        tracker_id=data.get("tracker_id", ""),
        tracker_status=data.get("tracker_status", ""),
    )


def _detail_status(tracker_text: str, tracker_id: str) -> str:
    _, _, section = xyz_master.detail_section_bounds(tracker_text, tracker_id)
    match = re.search(r"^(?:-\s+)?\*\*Status:\*\*\s*([^\n]+)$", section, re.MULTILINE)
    if not match:
        raise ValueError(f"{tracker_id}: detail status line not found")
    return match.group(1).strip()


def _candidate_by_index(candidates: Iterable[object]) -> dict[int, object]:
    return {candidate.local_index: candidate for candidate in candidates}


def validate_outcomes(
    candidate_file: Path,
    tracker_path: Path,
    *,
    candidate_index: int | None = None,
    require_all: bool = False,
    warn_missing_siblings: bool = False,
) -> OutcomeValidation:
    candidates = xyz_master.parse_candidates(candidate_file)
    by_index = _candidate_by_index(candidates)
    if not candidates:
        raise ValueError(f"{candidate_file}: no actionable candidates found")

    if candidate_index is not None and candidate_index not in by_index:
        raise ValueError(f"{candidate_file}: candidate index {candidate_index} not found")

    checked = [candidate_index] if candidate_index is not None else sorted(by_index)
    if require_all:
        checked = sorted(by_index)

    tracker_text = tracker_path.read_text(encoding="utf-8")
    errors: list[str] = []
    warnings: list[str] = []

    marker_by_index: dict[int, list[Path]] = {
        index: xyz_master.outcome_markers(candidate_file, index)
        for index in sorted(by_index)
    }
    missing = tuple(index for index, markers in marker_by_index.items() if not markers)

    if warn_missing_siblings:
        for index in missing:
            if candidate_index is None or index != candidate_index:
                warnings.append(
                    f"{candidate_file}: candidate {index:03d} has no durable outcome marker"
                )

    for index in checked:
        markers = marker_by_index[index]
        if len(markers) != 1:
            errors.append(
                f"{candidate_file}: candidate {index:03d} expected exactly one outcome marker, found {len(markers)}"
            )
            continue

        try:
            marker = parse_outcome_marker(markers[0])
        except ValueError as exc:
            errors.append(str(exc))
            continue

        if marker.candidate_index != index:
            errors.append(f"{marker.path}: front matter candidate_index={marker.candidate_index}, expected {index}")
        if marker.outcome not in {"accepted", "denied"}:
            errors.append(f"{marker.path}: unsupported outcome `{marker.outcome or '<missing>'}`")
        if marker.outcome == "accepted" and marker.tracker_id:
            try:
                current_status = _detail_status(tracker_text, marker.tracker_id)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            candidate = by_index[index]
            if xyz_master.is_status_transition_candidate(candidate):
                if not marker.tracker_status:
                    errors.append(f"{marker.path}: accepted status-transition marker missing tracker_status")
                elif current_status != marker.tracker_status:
                    errors.append(
                        f"{marker.path}: tracker status mismatch for {marker.tracker_id}: "
                        f"marker={marker.tracker_status!r}, current={current_status!r}"
                    )

    return OutcomeValidation(
        candidate_file=candidate_file,
        checked_indexes=tuple(checked),
        missing_indexes=missing,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def write_summary(result: OutcomeValidation, path: Path | None) -> None:
    lines = [
        "# XYZ_MASTER Candidate Outcome Validation",
        "",
        f"- Candidate file: `{result.candidate_file.as_posix()}`",
        f"- Checked candidate indexes: `{', '.join(f'{idx:03d}' for idx in result.checked_indexes)}`",
        f"- Missing durable outcomes: `{', '.join(f'{idx:03d}' for idx in result.missing_indexes) or 'none'}`",
        f"- Errors: `{len(result.errors)}`",
        f"- Warnings: `{len(result.warnings)}`",
    ]
    if result.errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {error}" for error in result.errors)
    if result.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result.warnings)
    content = "\n".join(lines) + "\n"
    if path:
        path.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--source", default="")
    parser.add_argument("--candidate-file")
    parser.add_argument("--candidate-index", type=int)
    parser.add_argument("--tracker", default=str(TRACKER_PATH))
    parser.add_argument("--require-all", action="store_true")
    parser.add_argument("--warn-missing-siblings", action="store_true")
    parser.add_argument("--summary-file")
    args = parser.parse_args()

    try:
        candidate_file = xyz_master.discover_candidate_file(
            ROOT,
            args.run_id,
            args.source or None,
            args.candidate_file,
        )
        result = validate_outcomes(
            candidate_file,
            ROOT / args.tracker,
            candidate_index=args.candidate_index,
            require_all=args.require_all,
            warn_missing_siblings=args.warn_missing_siblings,
        )
        write_summary(result, Path(args.summary_file) if args.summary_file else None)
        for warning in result.warnings:
            print(f"::warning::{warning}")
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1 if result.errors else 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
