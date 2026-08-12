#!/usr/bin/env python3
"""Find undecided XYZ_MASTER candidate files that no longer need filing.

This is a report-first helper for XYZ-PILOT-228 / XYZ-PILOT-215. It scans
committed candidate files, skips candidates with outcome markers, and classifies
the rest so operators can clear stale backlog noise without re-running the
background filing workflow.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import subprocess
import sys

import xyz_master_background_filing as filing


TRACKER_PATH = Path("docs/XYZ_PILOT_ISSUE_TRACKER.md")
DEFAULT_MIN_RUN_ID = "2026-06-03T000000Z"
DETAIL_TITLE_RE = re.compile(
    r"^###\s+(XYZ-PILOT-\d{3})\s+(?:—|--?|[-–])\s+(.+)$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class OrphanCandidate:
    candidate_file: str
    candidate_index: int
    run_id: str
    source: str
    classification: str
    title: str
    duplicate_of: str
    reason: str
    marker_path: str = ""


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def tracker_title_index(tracker_text: str) -> dict[str, str]:
    index: dict[str, str] = {}
    for match in DETAIL_TITLE_RE.finditer(tracker_text):
        index[normalize_title(match.group(2))] = match.group(1)
    return index


def run_and_source(candidate_file: Path, repo_root: Path) -> tuple[str, str]:
    rel_parts = candidate_file.relative_to(repo_root).parts
    try:
        idx = rel_parts.index("runs")
        return rel_parts[idx + 1], rel_parts[idx + 2]
    except (ValueError, IndexError):
        return "unknown-run", candidate_file.parent.name


def candidate_marker_paths(candidate_file: Path, candidate_index: int) -> list[Path]:
    marker_dir = candidate_file.parent / "candidate-outcomes"
    if not marker_dir.is_dir():
        return []
    return sorted(marker_dir.glob(f"candidate-{candidate_index:03d}.*.md"))


def candidate_title(candidate: filing.Candidate) -> str:
    try:
        if filing.is_status_transition_candidate(candidate) or filing.is_severity_transition_candidate(candidate):
            return candidate.title
        payload = filing.extract_payload(candidate)
        return payload.detail_title_template
    except Exception:
        return candidate.title


def classify_candidate(
    candidate: filing.Candidate,
    tracker_titles: dict[str, str],
    repo_root: Path,
) -> OrphanCandidate:
    run_id, source = run_and_source(candidate.file_path, repo_root)
    rel_candidate = candidate.file_path.relative_to(repo_root).as_posix()

    try:
        if filing.is_status_transition_candidate(candidate) or filing.is_severity_transition_candidate(candidate):
            return OrphanCandidate(
                rel_candidate,
                candidate.local_index,
                run_id,
                source,
                "legitimately-pending",
                candidate.title,
                "",
                "status/severity transition candidates require explicit operator action",
            )
        payload = filing.extract_payload(candidate)
        title = payload.detail_title_template
    except Exception as exc:  # noqa: BLE001 - report parser failure as operator evidence.
        return OrphanCandidate(
            rel_candidate,
            candidate.local_index,
            run_id,
            source,
            "structurally-malformed",
            candidate.title,
            "",
            f"{type(exc).__name__}: {exc}",
        )

    duplicate_of = tracker_titles.get(normalize_title(title), "")
    if duplicate_of:
        return OrphanCandidate(
            rel_candidate,
            candidate.local_index,
            run_id,
            source,
            "already-landed",
            title,
            duplicate_of,
            "candidate title already exists in the tracker full register",
        )

    return OrphanCandidate(
        rel_candidate,
        candidate.local_index,
        run_id,
        source,
        "legitimately-pending",
        title,
        "",
        "no outcome marker and no matching tracker title",
    )


def iter_candidate_files(repo_root: Path, min_run_id: str) -> list[Path]:
    root = repo_root / "tests" / "logs" / "runs"
    if not root.is_dir():
        return []
    files = []
    for candidate_file in sorted(root.glob("*/*/issue-candidates.md")):
        run_id, _source = run_and_source(candidate_file, repo_root)
        if run_id < min_run_id:
            continue
        files.append(candidate_file)
    return files


def scan(repo_root: Path, min_run_id: str) -> list[OrphanCandidate]:
    tracker_text = (repo_root / TRACKER_PATH).read_text(encoding="utf-8")
    titles = tracker_title_index(tracker_text)
    findings: list[OrphanCandidate] = []
    for candidate_file in iter_candidate_files(repo_root, min_run_id):
        candidates = filing.parse_candidates(candidate_file)
        if not candidates:
            continue
        for candidate in candidates:
            if candidate_marker_paths(candidate_file, candidate.local_index):
                continue
            findings.append(classify_candidate(candidate, titles, repo_root))
    return findings


def write_denial_marker(
    repo_root: Path,
    finding: OrphanCandidate,
    *,
    dry_run: bool,
    decision_by: str,
) -> str:
    if finding.classification == "already-landed":
        denial_reason = "duplicate"
        duplicate_args = ["--duplicate-of", finding.duplicate_of]
    elif finding.classification == "structurally-malformed":
        denial_reason = "invalid"
        duplicate_args = []
    else:
        raise ValueError(f"{finding.classification} is not safe for automatic denial")

    cmd = [
        sys.executable,
        "scripts/write_candidate_outcome.py",
        "--candidate-file",
        finding.candidate_file,
        "--candidate-index",
        str(finding.candidate_index),
        "--outcome",
        "denied",
        "--denial-reason",
        denial_reason,
        "--decision-by",
        decision_by,
        "--title",
        finding.title,
        "--notes",
        f"XYZ-PILOT-228 orphan-candidate detector classified this as {finding.classification}: {finding.reason}",
        *duplicate_args,
    ]
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, cwd=repo_root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"write_candidate_outcome failed for {finding.candidate_file}: {detail}")
    return proc.stdout.splitlines()[0] if proc.stdout.strip() else ""


def render_text(findings: list[OrphanCandidate]) -> str:
    counts: dict[str, int] = {}
    for item in findings:
        counts[item.classification] = counts.get(item.classification, 0) + 1
    lines = [
        "# XYZ_MASTER Orphan Candidate Report",
        "",
        f"- Findings: `{len(findings)}`",
        f"- already-landed: `{counts.get('already-landed', 0)}`",
        f"- structurally-malformed: `{counts.get('structurally-malformed', 0)}`",
        f"- legitimately-pending: `{counts.get('legitimately-pending', 0)}`",
        "",
        "| Classification | Candidate | Title | Reason |",
        "|---|---|---|---|",
    ]
    for item in findings:
        duplicate = f" duplicate of `{item.duplicate_of}`;" if item.duplicate_of else ""
        lines.append(
            "| {classification} | `{candidate_file}` #{candidate_index} | {title} |{duplicate} {reason} |".format(
                classification=item.classification,
                candidate_file=item.candidate_file,
                candidate_index=item.candidate_index,
                title=item.title.replace("|", r"\|"),
                duplicate=duplicate,
                reason=item.reason.replace("|", r"\|"),
            )
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Find undecided XYZ_MASTER orphan candidates.")
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1].as_posix())
    parser.add_argument("--min-run-id", default=DEFAULT_MIN_RUN_ID)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--auto-wontfix", action="store_true", help="Write denial markers for safe orphan classes.")
    parser.add_argument("--dry-run", action="store_true", help="Preview marker writes when --auto-wontfix is set.")
    parser.add_argument("--decision-by", default="XYZ_MASTER_ORPHAN_DETECTOR")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    findings = scan(repo_root, args.min_run_id)

    marker_paths: dict[tuple[str, int], str] = {}
    if args.auto_wontfix:
        for item in findings:
            if item.classification not in {"already-landed", "structurally-malformed"}:
                continue
            marker_paths[(item.candidate_file, item.candidate_index)] = write_denial_marker(
                repo_root,
                item,
                dry_run=args.dry_run,
                decision_by=args.decision_by,
            )

    if marker_paths:
        findings = [
            OrphanCandidate(
                **{
                    **asdict(item),
                    "marker_path": marker_paths.get((item.candidate_file, item.candidate_index), item.marker_path),
                }
            )
            for item in findings
        ]

    if args.format == "json":
        print(json.dumps([asdict(item) for item in findings], indent=2, sort_keys=True))
    else:
        print(render_text(findings), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
