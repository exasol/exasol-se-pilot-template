#!/usr/bin/env python3
"""Deterministic background filing helper for XYZ_MASTER candidates.

This is the XYZ-PILOT-215 implementation path: consume one committed
`issue-candidates.md` entry, generate exactly one tracker mutation, and either
preview it (`dry-run`) or apply it to the working tree for a PR branch.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import difflib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


TRACKER_PATH = Path("docs/XYZ_PILOT_ISSUE_TRACKER.md")
VALID_CLASSIFICATIONS = {"critical", "major", "minor", "feature", "external", "hygiene", "status"}
HIGH_SEVERITY = {"critical", "major"}
ISSUE_HEADING_RE = re.compile(r"^### XYZ-PILOT-(\d{3})\b", re.MULTILINE)
CANDIDATE_HEADING_RE = re.compile(r"^##\s+Candidate\b(.*)$", re.MULTILINE)
ACTIONABLE_FIELD_RE = re.compile(
    r"^-\s+\*\*(?:"
    r"Agent|Run|Type|Severity|Proposed severity|Recommended severity|"
    r"Requested severity|Requested classification|Recommended classification|"
    r"Area|Proposed area|Recommended area|Observed|Expected|Evidence|"
    r"Existing ID|Existing issue|Existing ticket|Tracker ID|Requested status|Recommended status|"
    r"Proposed status|Status|Evidence date"
    r"):\*\*",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Candidate:
    local_index: int
    title: str
    body: str
    file_preamble: str
    file_path: Path


@dataclass(frozen=True)
class Payload:
    classification: str
    status: str
    area: str
    top_row_template: str
    detail_template: str
    detail_title_template: str


@dataclass(frozen=True)
class Mutation:
    mutation_kind: str
    tracker_id: str
    classification: str
    status: str
    title: str
    top_row: str
    detail_section: str
    tracker_text: str


class FilingError(RuntimeError):
    """User-facing validation error."""


_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def business_days_since(start: dt.date, end: dt.date) -> int:
    days = 0
    cur = start
    while cur < end:
        cur = cur + dt.timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def active_issues_cutoff(today: dt.date) -> dt.date:
    cutoff = today
    while business_days_since(cutoff - dt.timedelta(days=1), today) <= 1:
        cutoff -= dt.timedelta(days=1)
    return cutoff


def refresh_active_issues_today_line(tracker_text: str, today: dt.date | None = None) -> str:
    today = today or dt.datetime.now(dt.UTC).date()
    cutoff = active_issues_cutoff(today)
    replacement = (
        f"> *Today is {today.isoformat()} ({_DOW[today.weekday()]}) 06:00 CEST: "
        f"cut-off date for the struck-through window is therefore {cutoff.isoformat()} "
        f"({_DOW[cutoff.weekday()]}). Resolved-on-or-after-that-date items remain visible "
        "with ID, Severity, and Area struck-through; older ones are pruned.*"
    )
    refreshed, count = re.subn(
        r"^>?\s*\*Today is \d{4}-\d{2}-\d{2}\s+\(\w{3}\)[^\n]*\*$",
        replacement,
        tracker_text,
        count=1,
        flags=re.MULTILINE,
    )
    return refreshed if count == 1 else tracker_text


def run_cmd(
    args: list[str],
    repo_root: Path,
    *,
    env: dict[str, str] | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        args,
        cwd=repo_root,
        env=full_env,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )


def require_cmd(args: list[str], repo_root: Path, *, env: dict[str, str] | None = None) -> str:
    proc = run_cmd(args, repo_root, env=env, capture=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise FilingError(f"command failed ({proc.returncode}): {' '.join(args)}\n{detail}")
    return proc.stdout


def discover_candidate_file(repo_root: Path, run_id: str | None, source: str | None, candidate_file: str | None) -> Path:
    if candidate_file:
        path = Path(candidate_file)
        return path if path.is_absolute() else repo_root / path

    if not run_id:
        raise FilingError("either --candidate-file or --run-id is required")
    run_dir = repo_root / "tests" / "logs" / "runs" / run_id
    if not run_dir.is_dir():
        raise FilingError(f"run folder not found: {run_dir}")

    if source:
        path = run_dir / source / "issue-candidates.md"
        if not path.is_file():
            raise FilingError(f"candidate file not found: {path}")
        return path

    matches = [
        path
        for path in sorted(run_dir.glob("*/issue-candidates.md"))
        if parse_candidates(path)
    ]
    if not matches:
        raise FilingError(f"no actionable issue-candidates.md files found under {run_dir}")
    if len(matches) > 1:
        rels = ", ".join(path.relative_to(repo_root).as_posix() for path in matches)
        raise FilingError(f"multiple candidate files found; pass --source. Candidates: {rels}")
    return matches[0]


def implicit_candidate_parts(src: str) -> tuple[str, str, str]:
    sep = src.find("\n---\n")
    if sep == -1:
        preamble = ""
        body = src.strip()
        title_src = src
    else:
        preamble = src[:sep]
        body = src[sep + len("\n---\n"):].strip()
        title_src = preamble
    first_h = re.search(r"^#\s+(.+?)$", title_src, re.MULTILINE)
    title = first_h.group(1).strip() if first_h else "(implicit candidate)"
    return title, body, preamble


def has_actionable_candidate_fields(body: str) -> bool:
    return bool(
        ACTIONABLE_FIELD_RE.search(body)
        or "### Top-table row" in body
        or "### Full-register detail section" in body
    )


def parse_candidates(candidate_file: Path) -> list[Candidate]:
    src = candidate_file.read_text(encoding="utf-8")
    matches = list(CANDIDATE_HEADING_RE.finditer(src))
    if not matches:
        title, body, preamble = implicit_candidate_parts(src)
        if not has_actionable_candidate_fields(body):
            return []
        return [Candidate(1, title, body, preamble, candidate_file)]

    preamble = src[: matches[0].start()]
    candidates: list[Candidate] = []
    for idx, match in enumerate(matches, start=1):
        end = matches[idx].start() if idx < len(matches) else len(src)
        title = match.group(1).strip(" -:").strip() or f"(candidate {idx})"
        candidates.append(Candidate(idx, title, src[match.end() : end].strip(), preamble, candidate_file))
    return candidates


def outcome_markers(candidate_file: Path, candidate_index: int) -> list[Path]:
    marker_dir = candidate_file.parent / "candidate-outcomes"
    if not marker_dir.is_dir():
        return []
    return sorted(marker_dir.glob(f"candidate-{candidate_index:03d}.*.md"))


# XYZ-PILOT-286: the Operator Dashboard dispatch action writes a non-terminal
# "dispatch requested" marker (`candidate-NNN.APPROVED_DISPATCHED.<mode>.md`)
# BEFORE the filer runs. It must NOT trip the double-file guard (that left the
# candidate approved-but-unfiled), and it is replaced by the terminal ACCEPTED
# marker on a successful apply. Terminal markers (ACCEPTED / DENIED) still block.
_DISPATCH_OUTCOME_TOKENS = {"APPROVED_DISPATCHED"}


def _marker_outcome_token(marker: Path) -> str:
    # candidate-NNN.<TOKEN>.<...>.md -> <TOKEN>
    parts = marker.name.split(".")
    return parts[1] if len(parts) > 1 else ""


def terminal_outcome_markers(candidate_file: Path, candidate_index: int) -> list[Path]:
    return [
        m
        for m in outcome_markers(candidate_file, candidate_index)
        if _marker_outcome_token(m) not in _DISPATCH_OUTCOME_TOKENS
    ]


def pending_dispatch_markers(candidate_file: Path, candidate_index: int) -> list[Path]:
    return [
        m
        for m in outcome_markers(candidate_file, candidate_index)
        if _marker_outcome_token(m) in _DISPATCH_OUTCOME_TOKENS
    ]


def field(body: str, name: str, *, required: bool = True) -> str:
    pat = re.compile(rf"^-\s+\*\*{re.escape(name)}:\*\*\s*`?([^`\n]+)`?\s*$", re.MULTILINE)
    match = pat.search(body)
    if not match:
        if required:
            raise FilingError(f"candidate is missing field: {name}")
        return ""
    return match.group(1).strip()


def field_any(body: str, names: tuple[str, ...], *, required: bool = True, default: str = "") -> str:
    for name in names:
        value = field(body, name, required=False)
        if value:
            return value
    if required:
        raise FilingError(f"candidate is missing one of fields: {', '.join(names)}")
    return default


def normalize_classification(value: str) -> str:
    match = re.search(r"\b(critical|major|minor|feature|external|hygiene|status)\b", value.lower())
    if not match:
        raise FilingError(f"unsupported proposed severity/classification: {value}")
    return match.group(1)


def existing_tracker_id(body: str) -> str:
    value = field_any(
        body,
        # XYZ-PILOT-259: "Existing issue" is the label OpenAI Codex candidates use;
        # accept it alongside the canonical labels so a Codex-authored status
        # transition is filed against the right ticket instead of erroring.
        ("Existing ID", "Existing issue", "Existing ticket", "Tracker ID"),
        required=True,
    )
    match = re.search(r"\bXYZ-PILOT-\d{3}\b", value.upper())
    if not match:
        raise FilingError(f"existing ticket field does not contain an XYZ-PILOT-NNN ID: {value}")
    return match.group(0)


def is_status_transition_candidate(candidate: Candidate) -> bool:
    if "### Top-table row" in candidate.body and "### Full-register detail section" in candidate.body:
        return False
    try:
        existing_tracker_id(candidate.body)
        field_any(candidate.body, ("Requested status", "Recommended status", "Proposed status", "Status"))
        return True
    except FilingError:
        return False


def is_severity_transition_candidate(candidate: Candidate) -> bool:
    if "### Top-table row" in candidate.body and "### Full-register detail section" in candidate.body:
        return False
    try:
        existing_tracker_id(candidate.body)
        field_any(
            candidate.body,
            ("Requested severity", "Requested classification", "Recommended classification"),
        )
        return True
    except FilingError:
        return False


def is_evidence_update_candidate(candidate: Candidate) -> bool:
    if "### Top-table row" in candidate.body and "### Full-register detail section" in candidate.body:
        return False
    try:
        existing_tracker_id(candidate.body)
    except FilingError:
        return False
    if is_status_transition_candidate(candidate) or is_severity_transition_candidate(candidate):
        return False
    return bool(
        re.search(
            r"^###\s+(Evidence update|Additional evidence|Fresh evidence|Validation evidence|Investigation evidence)\s*$",
            candidate.body,
            re.MULTILINE,
        )
        or field(candidate.body, "Evidence", required=False)
    )


def extract_top_row(body: str) -> str:
    top_section = re.search(r"^### Top-table row\s*(.*?)(?:^### |\Z)", body, re.MULTILINE | re.DOTALL)
    if not top_section:
        raise FilingError("candidate is missing a '### Top-table row' section")
    for line in top_section.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            stripped = stripped[1:].strip()
        if stripped.startswith("|") and "XYZ-PILOT-NNN" in stripped:
            return stripped
        if stripped.startswith("[XYZ-PILOT-NNN]") and "|" in stripped:
            return "| " + stripped.rstrip("|").strip() + " |"
    raise FilingError("top-table row section does not contain a placeholder row")


def extract_detail_template(body: str) -> str:
    detail_section = re.search(
        r"^### Full-register detail section\s*```(?:markdown)?\s*\n(.*?)\n```",
        body,
        re.MULTILINE | re.DOTALL,
    )
    if not detail_section:
        raise FilingError("candidate is missing a fenced full-register detail section")
    detail = detail_section.group(1).strip() + "\n"
    if "XYZ-PILOT-NNN" not in detail or 'id="xyz-pilot-NNN"' not in detail:
        raise FilingError("full-register detail section must contain XYZ-PILOT-NNN placeholders")
    return detail


def extract_payload(candidate: Candidate) -> Payload:
    if "### Top-table row" not in candidate.body or "### Full-register detail section" not in candidate.body:
        return synthesize_lightweight_payload(candidate)

    classification = normalize_classification(
        field_any(candidate.body, ("Proposed severity", "Recommended severity", "Severity"))
    )
    status = field_any(
        candidate.body,
        ("Proposed status", "Recommended status", "Status"),
        required=False,
        default="Open",
    )
    area = field_any(
        candidate.body,
        ("Proposed area", "Recommended area", "Area"),
        required=False,
        default="Unclassified",
    )
    top_row = extract_top_row(candidate.body)
    detail = extract_detail_template(candidate.body)
    title_match = re.search(r"^### XYZ-PILOT-NNN\s+--?\s+(.+)$", detail, re.MULTILINE)
    if not title_match:
        title_match = re.search(r"^### XYZ-PILOT-NNN\s+.\s+(.+)$", detail, re.MULTILINE)
    if not title_match:
        raise FilingError("full-register detail section must have a '### XYZ-PILOT-NNN - title' heading")
    return Payload(classification, status, area, top_row, detail, title_match.group(1).strip())


def candidate_source_context(candidate: Candidate) -> tuple[str, str, str]:
    parts = candidate.file_path.parts
    try:
        rel = "/".join(parts[parts.index("tests"):])
    except ValueError:
        rel = candidate.file_path.as_posix()
    run_id = field(candidate.body, "Run", required=False)
    source = field(candidate.body, "Agent", required=False)
    try:
        idx = parts.index("runs")
        run_id = run_id or parts[idx + 1]
        source = source or parts[idx + 2]
    except (ValueError, IndexError):
        pass
    return run_id or "unknown-run", source or candidate.file_path.parent.name, rel


def table_cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).replace("|", r"\|").strip()


def lightweight_status(candidate: Candidate) -> str:
    explicit = field_any(
        candidate.body,
        ("Proposed status", "Recommended status"),
        required=False,
        default="",
    )
    if explicit:
        return explicit
    status_note = field(candidate.body, "Status", required=False)
    today = dt.datetime.now(dt.UTC).date().isoformat()
    if re.search(r"\bresolved\b", status_note, re.IGNORECASE):
        return f"Resolved {today}"
    if re.search(r"\bwontfix\b", status_note, re.IGNORECASE):
        return f"Wontfix {today}"
    return "Open"


def synthesize_lightweight_payload(candidate: Candidate) -> Payload:
    classification = normalize_classification(
        field_any(
            candidate.body,
            ("Proposed severity", "Recommended severity", "Severity"),
            required=False,
            default="minor",
        )
    )
    run_id, source, rel_path = candidate_source_context(candidate)
    status = lightweight_status(candidate)
    area = field_any(
        candidate.body,
        ("Proposed area", "Recommended area", "Area"),
        required=False,
        default=source,
    ) or source
    title = table_cell(candidate.title or "XYZ_MASTER candidate")
    summary = table_cell(f"{title} (from {source} run {run_id}).")
    top_row = f"| [XYZ-PILOT-NNN](#xyz-pilot-NNN) | {classification} | {table_cell(area)} | {summary} |"
    detail = "\n".join(
        [
            '<a id="xyz-pilot-NNN"></a>',
            "",
            f"### XYZ-PILOT-NNN - {title}",
            f"- **Status:** {status}",
            f"- **Severity:** {classification}",
            f"- **Area:** {area}",
            f"- **Surfaced:** {source} candidate from run `{run_id}`; source `{rel_path}` candidate `{candidate.local_index}`.",
            "",
            "#### Problem",
            "",
            "A lightweight runtime candidate was approved for tracker filing through the XYZ_MASTER Operator Approval flow.",
            "",
            "#### Candidate details",
            "",
            candidate.body.strip(),
            "",
            "#### Filing notes",
            "",
            "XYZ_MASTER generated this tracker template from the submitted candidate because the source did not include a full top-table row and full-register detail template.",
            "",
        ]
    )
    return Payload(classification, status, area, top_row, detail, title)


def override_classification(payload: Payload, classification: str | None) -> Payload:
    if not classification:
        return payload
    normalized = classification.strip().lower()
    if normalized not in VALID_CLASSIFICATIONS:
        raise FilingError(f"unsupported classification override: {classification}")

    row = payload.top_row_template
    if row.startswith("|"):
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if len(cells) >= 4:
            cells[1] = normalized
            row = "| " + " | ".join(cells) + " |"
        else:
            row = re.sub(
                r"\|\s*" + re.escape(payload.classification) + r"\s*\|",
                f"| {normalized} |",
                row,
                count=1,
            )
    else:
        row = re.sub(
            r"\|\s*" + re.escape(payload.classification) + r"\s*\|",
            f"| {normalized} |",
            row,
            count=1,
        )

    detail, count = re.subn(
        r"^-\s+\*\*Severity:\*\*\s*[^\n]+$",
        f"- **Severity:** {normalized}",
        payload.detail_template,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise FilingError("could not apply classification override to detail severity line")
    return Payload(normalized, payload.status, payload.area, row, detail, payload.detail_title_template)


def next_tracker_id(tracker_text: str) -> tuple[int, str]:
    ids = [int(m.group(1)) for m in ISSUE_HEADING_RE.finditer(tracker_text)]
    if not ids:
        raise FilingError("tracker has no XYZ-PILOT detail headings")
    nxt = max(ids) + 1
    return nxt, f"XYZ-PILOT-{nxt:03d}"


def replace_placeholders(text: str, tracker_id: str) -> str:
    anchor = tracker_id.lower()
    return text.replace("XYZ-PILOT-NNN", tracker_id).replace("xyz-pilot-NNN", anchor)


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


def duplicate_tracker_id(tracker_text: str, title: str) -> str | None:
    # Only an OPEN ticket with the same title is a live duplicate. A same-title
    # match that is Resolved/Wontfix means this candidate is a fresh occurrence
    # (regression / distinct bug) and must mint a NEW id, not fold into the closed
    # ticket. Matching any status masked regressions of closed incident classes —
    # e.g. the 2026-07-02 chatbot 'object SALES_CY not found' failure vs Resolved
    # XYZ-PILOT-230 (same auto-title 'Chatbot visual incident · … · exasol_query failed').
    normalized = normalize_title(title)
    headings = list(re.finditer(r"^### XYZ-PILOT-\d{3}\s+(?:--?|—)\s+(.+)$", tracker_text, re.MULTILINE))
    for idx, match in enumerate(headings):
        if normalize_title(match.group(1)) != normalized:
            continue
        section_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(tracker_text)
        section = tracker_text[match.start():section_end]
        status_match = re.search(r"\*\*Status:\*\*\s*([^\n]+)", section)
        if status_match and status_terminal(status_match.group(1)):
            continue  # closed ticket — not a live duplicate, allow a new id
        id_match = re.search(r"XYZ-PILOT-\d{3}", match.group(0))
        return id_match.group(0) if id_match else None
    return None


def build_mutation(tracker_text: str, payload: Payload) -> Mutation:
    _nxt, tracker_id = next_tracker_id(tracker_text)
    title = replace_placeholders(payload.detail_title_template, tracker_id)
    duplicate_id = duplicate_tracker_id(tracker_text, title)
    if duplicate_id:
        return Mutation(
            "already-filed",
            duplicate_id,
            payload.classification,
            payload.status,
            title,
            "",
            "",
            tracker_text,
        )
    top_row = replace_placeholders(payload.top_row_template, tracker_id)
    if status_terminal(payload.status):
        top_row = terminalize_new_top_row(top_row, payload.status)
    detail = replace_placeholders(payload.detail_template, tracker_id).rstrip() + "\n\n"

    table_marker = "| ID | Severity | Area | Summary |\n|---|---|---|---|\n"
    if table_marker not in tracker_text:
        raise FilingError("active-issues table marker not found")
    mutated = tracker_text.replace(table_marker, table_marker + top_row + "\n", 1)

    register_marker = "## Full register (newest first)\n\n"
    if register_marker not in mutated:
        raise FilingError("full-register marker not found")
    mutated = mutated.replace(register_marker, register_marker + detail, 1)

    today = dt.datetime.now(dt.UTC).date().isoformat()
    refresh = (
        f"**Last refresh:** {today} ({tracker_id} background XYZ_MASTER tracker mutation "
        f"service filed {payload.status}; verifier clean)."
    )
    mutated, count = re.subn(r"^\*\*Last refresh:\*\* .*$", refresh, mutated, count=1, flags=re.MULTILINE)
    if count != 1:
        raise FilingError("last-refresh line not found or not uniquely replaceable")
    mutated = refresh_active_issues_today_line(mutated)

    return Mutation("new-ticket", tracker_id, payload.classification, payload.status, title, top_row, detail, mutated)


def split_md_row(line: str) -> list[str]:
    if not line.startswith("|"):
        return []
    body = line[1:-1] if line.endswith("|") else line[1:]
    cells: list[str] = []
    cur: list[str] = []
    escaped = False
    for ch in body:
        if ch == "\\" and not escaped:
            escaped = True
            cur.append(ch)
            continue
        if ch == "|" and not escaped:
            cells.append("".join(cur).strip())
            cur = []
            escaped = False
            continue
        cur.append(ch)
        escaped = False
    cells.append("".join(cur).strip())
    return cells


def strike_cell(cell: str) -> str:
    stripped = cell.strip()
    if stripped.startswith("~~") and stripped.endswith("~~"):
        return stripped
    return f"~~{stripped}~~"


def status_terminal(status: str) -> bool:
    return bool(re.search(r"\b(Resolved|Wontfix)\b", status, re.IGNORECASE))


def terminal_status_label_and_date(status: str) -> tuple[str, str | None]:
    match = re.search(r"\b(Resolved|Wontfix)\s+\(?(\d{4}-\d{2}-\d{2})", status, re.IGNORECASE)
    if not match:
        label_match = re.search(r"\b(Resolved|Wontfix)\b", status, re.IGNORECASE)
        return (label_match.group(1).capitalize() if label_match else "", None)
    return match.group(1).capitalize(), match.group(2)


def terminal_status_subheading(status: str) -> str:
    label, status_date = terminal_status_label_and_date(status)
    rdate = status_date or dt.datetime.now(dt.UTC).date().isoformat()
    if label == "Resolved":
        return f"#### Resolution - shipped {rdate}"
    if label == "Wontfix":
        return f"#### Wontfix - decision {rdate}"
    raise FilingError(f"unsupported terminal status label: {status}")


def terminalize_new_top_row(row: str, status: str) -> str:
    cells = split_md_row(row)
    if len(cells) != 4:
        return row
    cells[0] = strike_cell(cells[0])
    cells[1] = strike_cell(cells[1])
    cells[2] = strike_cell(cells[2])
    summary = re.sub(r"\s*\((?:Resolved|Wontfix)\s+\d{4}-\d{2}-\d{2}\)\s*$", "", cells[3]).strip()
    label, tdate = terminal_status_label_and_date(status)
    cells[3] = f"{summary} ({label} {tdate})" if label and tdate else summary
    return "| " + " | ".join(cells) + " |"


def update_top_table_for_status(tracker_text: str, tracker_id: str, status: str) -> str:
    lines = tracker_text.splitlines()
    target_re = re.compile(rf"\|\s*~?~?\[{re.escape(tracker_id)}\]")
    changed = False
    for idx, line in enumerate(lines):
        if not target_re.search(line):
            continue
        cells = split_md_row(line)
        if len(cells) != 4:
            raise FilingError(f"top-table row for {tracker_id} is malformed")
        if status_terminal(status):
            label, tdate = terminal_status_label_and_date(status)
            # XYZ-PILOT-338 follow-up (backdated-terminal enhancement): a terminal
            # transition whose date is already PAST the one-business-day visibility
            # window is PRUNED outright (the row moves down to the Full register)
            # rather than struck-and-left. A backdated strike would be instantly
            # stale and the verifier's (D) Resolved/Wontfix window check (XYZ-PILOT-337)
            # would rightly reject it. In-window terminals are struck as before and
            # the daily-status Lambda prunes them when they age out.
            today = dt.datetime.now(dt.UTC).date()
            past_window = False
            if tdate:
                try:
                    past_window = business_days_since(dt.date.fromisoformat(tdate), today) > 1
                except ValueError:
                    past_window = False
            if past_window:
                del lines[idx]
                changed = True
                break
            cells[0] = strike_cell(cells[0])
            cells[1] = strike_cell(cells[1])
            cells[2] = strike_cell(cells[2])
            summary = re.sub(r"\s*\((?:Resolved|Wontfix)\s+\d{4}-\d{2}-\d{2}\)\s*$", "", cells[3]).strip()
            cells[3] = f"{summary} ({label} {tdate})" if label and tdate else summary
        else:
            cells[:3] = [cell.strip().strip("~").strip() for cell in cells[:3]]
        lines[idx] = "| " + " | ".join(cells) + " |"
        changed = True
        break
    if not changed:
        raise FilingError(f"top-table row for {tracker_id} not found")
    return "\n".join(lines) + ("\n" if tracker_text.endswith("\n") else "")


def update_top_table_for_severity(tracker_text: str, tracker_id: str, severity: str) -> str:
    lines = tracker_text.splitlines()
    target_re = re.compile(rf"\|\s*~?~?\[{re.escape(tracker_id)}\]")
    changed = False
    for idx, line in enumerate(lines):
        if not target_re.search(line):
            continue
        cells = split_md_row(line)
        if len(cells) != 4:
            raise FilingError(f"top-table row for {tracker_id} is malformed")
        # Preserve the resolved-row strike-through: if the ID cell is struck
        # (a Resolved/Wontfix row still in its 1-business-day visibility window),
        # the new severity cell must be struck too, else the row goes
        # partial-struck and fails the verifier's (E) semantic-parity check.
        id_cell = cells[0].strip()
        id_struck = id_cell.startswith("~~") and id_cell.endswith("~~")
        cells[1] = f"~~{severity}~~" if id_struck else severity
        lines[idx] = "| " + " | ".join(cells) + " |"
        changed = True
        break
    if not changed:
        raise FilingError(f"top-table row for {tracker_id} not found")
    return "\n".join(lines) + ("\n" if tracker_text.endswith("\n") else "")


def detail_section_bounds(tracker_text: str, tracker_id: str) -> tuple[int, int, str]:
    anchor = f'<a id="{tracker_id.lower()}"></a>'
    start = tracker_text.find(anchor)
    if start == -1:
        raise FilingError(f"detail anchor for {tracker_id} not found")
    next_match = re.search(r'\n<a id="xyz-pilot-\d{3}"></a>', tracker_text[start + len(anchor):])
    end = start + len(anchor) + next_match.start() if next_match else len(tracker_text)
    return start, end, tracker_text[start:end]


def candidate_resolution_evidence(body: str) -> str:
    # Canonical headings first, then the section names OpenAI Codex status-
    # transition candidates use (XYZ-PILOT-259). Accept ### or #### heading levels
    # so a Codex-authored resolution files through the backlog instead of
    # erroring on the evidence gate. Canonical labels stay first, so the existing
    # Claude Code / template behaviour is unchanged.
    headings = (
        "Resolution evidence",
        "Resolution details for the tracker",
        "Resolution details",
        "Implementation and validation",
        "Summary",
        "Implementation",
        "Live AWS evidence",
        "Validation",
    )
    for heading in headings:
        match = re.search(rf"^#{{3,4}}\s+{re.escape(heading)}\s*$", body, re.MULTILINE)
        if not match:
            continue
        rest = body[match.end():]
        stop = re.search(r"^#{3,4}\s+", rest, re.MULTILINE)
        evidence = rest[: stop.start() if stop else len(rest)].strip()
        if evidence:
            return evidence
    raise FilingError("status-transition candidate is missing a resolution evidence/details section")


def candidate_evidence_update(body: str) -> str:
    headings = (
        "Evidence update",
        "Additional evidence",
        "Fresh evidence",
        "Validation evidence",
        "Investigation evidence",
    )
    for heading in headings:
        match = re.search(rf"^###\s+{re.escape(heading)}\s*$", body, re.MULTILINE)
        if not match:
            continue
        rest = body[match.end():]
        stop = re.search(r"^###\s+", rest, re.MULTILINE)
        evidence = rest[: stop.start() if stop else len(rest)].strip()
        if evidence:
            return evidence
    inline = field(body, "Evidence", required=False)
    if inline:
        return f"- {inline}"
    raise FilingError("evidence-update candidate is missing an evidence section or Evidence field")


def detail_title(section: str, tracker_id: str) -> str:
    match = re.search(rf"^###\s+{re.escape(tracker_id)}\s+(?:—|--?|[-–])\s+(.+)$", section, re.MULTILINE)
    if not match:
        match = re.search(rf"^###\s+{re.escape(tracker_id)}\b\s*(.*)$", section, re.MULTILINE)
    return match.group(1).strip() if match else tracker_id


def build_evidence_update_mutation(tracker_text: str, candidate: Candidate, classification_override: str | None) -> Mutation:
    tracker_id = existing_tracker_id(candidate.body)
    classification = normalize_classification(
        classification_override
        or field_any(
            candidate.body,
            ("Proposed severity", "Recommended severity", "Severity"),
            required=False,
            default="status",
        )
    )

    start, end, section = detail_section_bounds(tracker_text, tracker_id)
    evidence = candidate_evidence_update(candidate.body)
    evidence_date = field(candidate.body, "Evidence date", required=False) or dt.datetime.now(dt.UTC).date().isoformat()
    subheading = f"#### Evidence update - {evidence_date}"
    if evidence in section:
        raise FilingError(f"{tracker_id} already contains the supplied evidence update")
    addition = f"\n\n{subheading}\n\n{evidence}\n"
    section = section.rstrip() + addition
    mutated = tracker_text[:start] + section + tracker_text[end:]

    refresh = (
        f"**Last refresh:** {dt.datetime.now(dt.UTC).date().isoformat()} "
        f"({tracker_id} background XYZ_MASTER evidence update; verifier clean)."
    )
    mutated, refresh_count = re.subn(r"^\*\*Last refresh:\*\* .*$", refresh, mutated, count=1, flags=re.MULTILINE)
    if refresh_count != 1:
        raise FilingError("last-refresh line not found or not uniquely replaceable")
    mutated = refresh_active_issues_today_line(mutated)

    status = field(section, "Status", required=False) or "Open"
    return Mutation(
        "evidence-update",
        tracker_id,
        classification,
        status,
        detail_title(section, tracker_id),
        "",
        addition.strip() + "\n",
        mutated,
    )


def build_status_transition_mutation(tracker_text: str, candidate: Candidate, classification_override: str | None) -> Mutation:
    tracker_id = existing_tracker_id(candidate.body)
    classification = normalize_classification(
        classification_override
        or field_any(
            candidate.body,
            ("Proposed severity", "Recommended severity", "Severity"),
            required=False,
            default="status",
        )
    )
    if classification_override and classification_override.strip().lower() not in VALID_CLASSIFICATIONS:
        raise FilingError(f"unsupported classification override: {classification_override}")
    status = field_any(candidate.body, ("Requested status", "Recommended status", "Proposed status", "Status"))
    if not status_terminal(status):
        raise FilingError("status-transition background filing currently supports terminal Resolved/Wontfix statuses only")

    start, end, section = detail_section_bounds(tracker_text, tracker_id)
    old_status_re = re.compile(r"^(?P<prefix>-\s+)?\*\*Status:\*\*\s*[^\n]+$", re.MULTILINE)
    section, count = old_status_re.subn(
        lambda match: f"{match.group('prefix') or ''}**Status:** {status}",
        section,
        count=1,
    )
    if count != 1:
        raise FilingError(f"detail status line for {tracker_id} not found or not unique")

    subheading = terminal_status_subheading(status)
    if re.search(rf"^{re.escape(subheading)}\s*$", section, re.MULTILINE):
        raise FilingError(f"{tracker_id} already contains `{subheading}`")
    evidence = candidate_resolution_evidence(candidate.body)
    addition = f"\n\n{subheading}\n\n{evidence}\n"
    section = section.rstrip() + addition
    mutated = tracker_text[:start] + section + tracker_text[end:]
    # The Active top-table is a curated subset (recent/active tickets); older
    # Open tickets live only in the Full register. Only strike a top-table row
    # when one exists — a Full-register-only ticket just needs its detail-section
    # **Status:** updated (done above). Guards the FilingError that would
    # otherwise block closing any ticket absent from the Active table.
    _row_re = re.compile(rf"\|\s*~?~?\[{re.escape(tracker_id)}\]")
    if any(_row_re.search(ln) for ln in mutated.splitlines()):
        mutated = update_top_table_for_status(mutated, tracker_id, status)

    refresh = (
        f"**Last refresh:** {dt.datetime.now(dt.UTC).date().isoformat()} "
        f"({tracker_id} background XYZ_MASTER status transition to {status}; verifier clean)."
    )
    mutated, refresh_count = re.subn(r"^\*\*Last refresh:\*\* .*$", refresh, mutated, count=1, flags=re.MULTILINE)
    if refresh_count != 1:
        raise FilingError("last-refresh line not found or not uniquely replaceable")
    mutated = refresh_active_issues_today_line(mutated)

    return Mutation(
        "status-transition",
        tracker_id,
        classification,
        status,
        detail_title(section, tracker_id),
        "",
        addition.strip() + "\n",
        mutated,
    )


def build_severity_transition_mutation(tracker_text: str, candidate: Candidate, classification_override: str | None) -> Mutation:
    tracker_id = existing_tracker_id(candidate.body)
    requested = normalize_classification(
        classification_override
        or field_any(
            candidate.body,
            ("Requested severity", "Requested classification", "Recommended classification"),
        )
    )
    if classification_override and classification_override.strip().lower() not in VALID_CLASSIFICATIONS:
        raise FilingError(f"unsupported classification override: {classification_override}")

    start, end, section = detail_section_bounds(tracker_text, tracker_id)
    old_severity = field(section, "Severity", required=False)
    if old_severity and normalize_classification(old_severity) == requested:
        raise FilingError(f"{tracker_id} is already classified as {requested}")

    section, count = re.subn(
        r"^-\s+\*\*Severity:\*\*\s*[^\n]+$",
        f"- **Severity:** {requested}",
        section,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise FilingError(f"detail severity line for {tracker_id} not found or not unique")

    rationale = field_any(
        candidate.body,
        ("Elevation rationale", "Severity rationale", "Reason", "Observed"),
        required=False,
        default="Operator approved severity elevation through the XYZ_MASTER approval flow.",
    )
    today = dt.datetime.now(dt.UTC).date().isoformat()
    addition = (
        f"\n\n#### Severity transition - approved {today}\n\n"
        f"- **Previous severity:** {old_severity or 'unknown'}\n"
        f"- **New severity:** {requested}\n"
        f"- **Rationale:** {rationale}\n"
    )
    section = section.rstrip() + addition
    mutated = tracker_text[:start] + section + tracker_text[end:]
    mutated = update_top_table_for_severity(mutated, tracker_id, requested)

    refresh = (
        f"**Last refresh:** {today} "
        f"({tracker_id} background XYZ_MASTER severity transition to {requested}; verifier clean)."
    )
    mutated, refresh_count = re.subn(r"^\*\*Last refresh:\*\* .*$", refresh, mutated, count=1, flags=re.MULTILINE)
    if refresh_count != 1:
        raise FilingError("last-refresh line not found or not uniquely replaceable")
    mutated = refresh_active_issues_today_line(mutated)

    status = field(section, "Status", required=False) or "Open"
    return Mutation(
        "severity-transition",
        tracker_id,
        requested,
        status,
        detail_title(section, tracker_id),
        "",
        addition.strip() + "\n",
        mutated,
    )


def verify_tracker(repo_root: Path, tracker_path: Path | None = None) -> str:
    env = {"TRACKER_PATH": str(tracker_path)} if tracker_path else None
    return require_cmd(["./scripts/verify_tracker_completeness.sh"], repo_root, env=env).strip()


def archive_tracker(repo_root: Path, run_id: str | None) -> Path:
    suffix = "xyz-master-background-filing"
    if run_id:
        suffix += "-" + run_id.lower().replace(":", "-")
    before = set((repo_root / "docs" / "archive").glob("XYZ_PILOT_ISSUE_TRACKER_*"))
    require_cmd(["./scripts/archive_tracker.sh", suffix], repo_root)
    after = set((repo_root / "docs" / "archive").glob("XYZ_PILOT_ISSUE_TRACKER_*"))
    created = sorted(after - before)
    if not created:
        raise FilingError("archive_tracker.sh did not create a new archive")
    return created[-1]


def write_outcome(
    repo_root: Path,
    candidate_file: Path,
    candidate_index: int,
    mutation: Mutation,
    *,
    dry_run: bool,
) -> tuple[Path, str]:
    rel_candidate = candidate_file.relative_to(repo_root).as_posix()
    cmd = [
        sys.executable,
        "scripts/write_candidate_outcome.py",
        "--candidate-file",
        rel_candidate,
        "--candidate-index",
        str(candidate_index),
        "--outcome",
        "accepted",
        "--classification",
        mutation.classification,
        "--tracker-id",
        mutation.tracker_id,
        "--tracker-status",
        mutation.status,
        "--commit",
        "same-commit",
        "--title",
        mutation.title,
    ]
    if dry_run:
        cmd.append("--dry-run")
    output = require_cmd(cmd, repo_root)
    marker = Path(output.splitlines()[0])
    new_marker = (repo_root / marker).resolve()
    if not dry_run:
        # XYZ-PILOT-286: replace the dashboard's non-terminal APPROVED_DISPATCHED
        # marker with this terminal ACCEPTED marker so exactly one outcome marker
        # remains (verify_xyz_master_candidate_outcomes.py requires exactly one).
        # `git rm` stages the deletion so the workflow's commit drops it.
        for stale in pending_dispatch_markers(candidate_file, candidate_index):
            if stale.resolve() == new_marker:
                continue
            rel = stale.relative_to(repo_root).as_posix()
            try:
                require_cmd(["git", "rm", "-q", "--", rel], repo_root)
            except Exception:
                stale.unlink(missing_ok=True)
    return repo_root / marker, output


def write_summary(path: Path | None, text: str) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def write_outputs(path: Path | None, values: dict[str, str]) -> None:
    if not path:
        return
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def selected_candidate(args: argparse.Namespace, repo_root: Path) -> Candidate:
    candidate_file = discover_candidate_file(repo_root, args.run_id, args.source, args.candidate_file)
    candidates = parse_candidates(candidate_file)
    if args.candidate_index < 1 or args.candidate_index > len(candidates):
        raise FilingError(f"--candidate-index out of range 1..{len(candidates)}")
    candidate = candidates[args.candidate_index - 1]
    markers = terminal_outcome_markers(candidate.file_path, candidate.local_index)
    if markers:
        rels = ", ".join(marker.relative_to(repo_root).as_posix() for marker in markers)
        raise FilingError(f"candidate already has a terminal outcome marker: {rels}")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare one deterministic XYZ_MASTER tracker filing.")
    parser.add_argument("--mode", choices=["dry-run", "apply"], default="dry-run")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--run-id")
    parser.add_argument("--source")
    parser.add_argument("--candidate-file")
    parser.add_argument("--candidate-index", type=int, default=1)
    parser.add_argument("--classification-override")
    parser.add_argument("--operator-approved-high-severity", action="store_true")
    parser.add_argument("--diff-file")
    parser.add_argument("--summary-file")
    parser.add_argument("--github-output")
    args = parser.parse_args()

    try:
        repo_root = Path(args.repo_root).resolve()
        candidate = selected_candidate(args, repo_root)
        if is_status_transition_candidate(candidate):
            mutation_kind = "status-transition"
        elif is_severity_transition_candidate(candidate):
            mutation_kind = "severity-transition"
        elif is_evidence_update_candidate(candidate):
            mutation_kind = "evidence-update"
        else:
            mutation_kind = "new-ticket"
        payload = None if mutation_kind in {"status-transition", "severity-transition", "evidence-update"} else override_classification(
            extract_payload(candidate),
            args.classification_override,
        )
        if (
            args.mode == "apply"
            and (
                (payload.classification if payload else normalize_classification(
                    args.classification_override
                    or field_any(
                        candidate.body,
                        (
                            "Requested severity",
                            "Requested classification",
                            "Recommended classification",
                            "Proposed severity",
                            "Recommended severity",
                            "Severity",
                        ),
                        required=False,
                        default="status",
                    )
                ))
                in HIGH_SEVERITY
            )
            and not args.operator_approved_high_severity
        ):
            raise FilingError(
                "major/critical candidates require --operator-approved-high-severity"
            )

        tracker_path = repo_root / TRACKER_PATH
        tracker_text = tracker_path.read_text(encoding="utf-8")
        mutation = (
            build_status_transition_mutation(tracker_text, candidate, args.classification_override)
            if mutation_kind == "status-transition"
            else build_severity_transition_mutation(tracker_text, candidate, args.classification_override)
            if mutation_kind == "severity-transition"
            else build_evidence_update_mutation(tracker_text, candidate, args.classification_override)
            if mutation_kind == "evidence-update"
            else build_mutation(tracker_text, payload)
        )
        diff_text = "".join(
            difflib.unified_diff(
                tracker_text.splitlines(keepends=True),
                mutation.tracker_text.splitlines(keepends=True),
                fromfile=str(TRACKER_PATH),
                tofile=str(TRACKER_PATH),
            )
        )

        pre_verify_error: FilingError | None = None
        try:
            pre_verify = verify_tracker(repo_root)
        except FilingError as exc:
            pre_verify_error = exc
            pre_verify = f"FAILED before mutation: {exc}"
        archive_path: Path | None = None

        if mutation.mutation_kind == "already-filed":
            if pre_verify_error:
                raise pre_verify_error
            post_verify = pre_verify
            outcome_path, outcome_preview = write_outcome(
                repo_root,
                candidate.file_path,
                candidate.local_index,
                mutation,
                dry_run=args.mode == "dry-run",
            )
        elif args.mode == "dry-run":
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
                tmp.write(mutation.tracker_text)
                tmp_path = Path(tmp.name)
            try:
                post_verify = verify_tracker(repo_root, tmp_path)
            finally:
                tmp_path.unlink(missing_ok=True)
            outcome_path, outcome_preview = write_outcome(
                repo_root, candidate.file_path, candidate.local_index, mutation, dry_run=True
            )
        else:
            archive_path = archive_tracker(repo_root, args.run_id)
            tracker_path.write_text(mutation.tracker_text, encoding="utf-8")
            post_verify = verify_tracker(repo_root)
            outcome_path, outcome_preview = write_outcome(
                repo_root, candidate.file_path, candidate.local_index, mutation, dry_run=False
            )

        if args.diff_file:
            diff_path = Path(args.diff_file)
            diff_path.parent.mkdir(parents=True, exist_ok=True)
            diff_path.write_text(diff_text, encoding="utf-8")

        rel_candidate = candidate.file_path.relative_to(repo_root).as_posix()
        rel_outcome = outcome_path.relative_to(repo_root).as_posix()
        rel_archive = archive_path.relative_to(repo_root).as_posix() if archive_path else ""
        summary = "\n".join(
            [
                f"# XYZ_MASTER background filing {args.mode}",
                "",
                f"- Mutation kind: `{mutation.mutation_kind}`",
                f"- Candidate: `{rel_candidate}` candidate `{candidate.local_index}`",
                f"- Assigned ID: `{mutation.tracker_id}`",
                f"- Classification: `{mutation.classification}`",
                f"- Status: `{mutation.status}`",
                f"- Title: `{mutation.title}`",
                f"- Pre-verifier: `{pre_verify}`",
                f"- Post-verifier: `{post_verify}`",
                f"- Archive: `{rel_archive or '(dry-run only)'}`",
                f"- Outcome marker: `{rel_outcome}`",
                "",
                "## Diff stat",
                "",
                f"- Tracker diff lines: `{len(diff_text.splitlines())}`",
                "",
                "## Outcome preview",
                "",
                "```",
                outcome_preview.strip(),
                "```",
            ]
        ) + "\n"
        write_summary(Path(args.summary_file) if args.summary_file else None, summary)
        write_outputs(
            Path(args.github_output) if args.github_output else None,
            {
                "tracker_id": mutation.tracker_id,
                "mutation_kind": mutation.mutation_kind,
                "classification": mutation.classification,
                "tracker_status": mutation.status,
                "candidate_file": rel_candidate,
                "candidate_index": str(candidate.local_index),
                "archive_path": rel_archive,
                "outcome_path": rel_outcome,
                "title": mutation.title,
            },
        )
        print(summary)
        return 0
    except FilingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
