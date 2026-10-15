"""Hourly drain for XYZ Pilot issue-candidate feeds.

XYZ-PILOT-208 phase 1 scanned committed candidate handoff files and sent an
operator digest. The XYZ-PILOT-215 extension keeps that digest path for
high-severity or ambiguous candidates, but can dispatch deterministic
background filing for low-severity candidates without an interactive
XYZ_MASTER session.
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import boto3

AWS_REGION = os.environ.get("AWS_REGION", "<AWS_REGION>")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "<GITHUB_OWNER>/<PROJECT_SLUG>")
GITHUB_REF = os.environ.get("GITHUB_REF", "main")
GITHUB_TOKEN_SECRET_ID = os.environ.get("GITHUB_TOKEN_SECRET_ID", "xyz-pilot/github/service-token")
GITHUB_TOKEN_PARAM = os.environ.get("GITHUB_TOKEN_PARAM", "/xyz-pilot/github/service-token")
STATE_BUCKET = os.environ.get("STATE_BUCKET", "xyz-pilot-status-<SOURCE_AWS_ACCOUNT_ID>")
STATE_KEY = os.environ.get("STATE_KEY", "candidate-drain/state.json")
SES_FROM = os.environ.get("SES_FROM", "<OPERATOR_EMAIL>")
SES_TO = [
    item.strip()
    for item in os.environ.get(
        "SES_TO",
        "<OPERATOR_EMAIL>,<OPS_USER_2_EMAIL>",
    ).split(",")
    if item.strip()
]
MAX_CANDIDATES = int(os.environ.get("MAX_CANDIDATES", "25"))
MAX_AUTO_DISPATCH_PER_FILE = int(os.environ.get("MAX_AUTO_DISPATCH_PER_FILE", "10"))
AUTO_FILE_LOW_SEVERITY = os.environ.get("AUTO_FILE_LOW_SEVERITY", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
AUTO_FILE_WORKFLOW_ID = os.environ.get(
    "AUTO_FILE_WORKFLOW_ID",
    "xyz-master-background-filing.yml",
)
AUTO_FILE_MODE = os.environ.get("AUTO_FILE_MODE", "direct-main")
LOW_SEVERITY_AUTO_FILE = {"minor", "feature"}
VALID_AUTO_FILE_MODES = {"dry-run", "direct-main", "pr"}
MANUAL_REVIEW_SOURCES = {"XYZ_PILOT_CHATBOT"}

CANDIDATE_PATH_RE = re.compile(
    r"^tests/logs/runs/(?P<run_id>[^/]+)/(?P<source>[^/]+)/issue-candidates\.md$"
)
OUTCOME_PATH_RE = re.compile(
    r"^tests/logs/runs/(?P<run_id>[^/]+)/(?P<source>[^/]+)/candidate-outcomes/"
    r"candidate-(?P<idx>\d{3})\..+\.md$"
)
SEVERITY_RE = re.compile(
    r"^\s*-\s+\*\*(?:Proposed\s+)?Severity:\*\*\s*(?P<value>[^;\n]+)",
    re.IGNORECASE | re.MULTILINE,
)
TYPE_RE = re.compile(
    r"^\s*-\s+\*\*(?:Proposed\s+)?Type:\*\*\s*(?P<value>[^;\n]+)",
    re.IGNORECASE | re.MULTILINE,
)
CANDIDATE_HEADING_RE = re.compile(r"^##\s+Candidate\b.*$", re.MULTILINE)
ACTIONABLE_CANDIDATE_FIELD_RE = re.compile(
    r"^-\s+\*\*(?:"
    r"Agent|Run|Type|Severity|Proposed severity|Recommended severity|"
    r"Area|Proposed area|Recommended area|Observed|Expected|Evidence|"
    r"Existing ID|Existing ticket|Tracker ID|Requested status|Recommended status|"
    r"Proposed status|Status"
    r"):\*\*",
    re.MULTILINE,
)
CLASSIFICATION_TOKEN_RE = re.compile(r"\b(critical|major|minor|feature|external|hygiene|status)\b")
STATUS_TRANSITION_RE = re.compile(
    r"^-\s+\*\*(?:Existing ID|Existing ticket|Tracker ID|Requested status|Recommended status):\*\*",
    re.IGNORECASE | re.MULTILINE,
)
PRUNE_HOUSEKEEPING_RE = re.compile(
    r"\bPrune top-table row XYZ-PILOT-\d{3}\b|Remove the single struck-through",
    re.IGNORECASE,
)

s3 = boto3.client("s3", region_name=AWS_REGION)
ses = boto3.client("ses", region_name=AWS_REGION)
secretsmanager = boto3.client("secretsmanager", region_name=AWS_REGION)
ssm = boto3.client("ssm", region_name=AWS_REGION)
_GITHUB_TOKEN_CACHE: str | None = None
_GITHUB_TOKEN_WARNED: set[str] = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _warn_github_token_once(key: str, message: str) -> None:
    if key in _GITHUB_TOKEN_WARNED:
        return
    print(message)
    _GITHUB_TOKEN_WARNED.add(key)


def _github_token_from_secret() -> str:
    """Return the GitHub PAT from Secrets Manager without logging the value."""
    if not GITHUB_TOKEN_SECRET_ID:
        return ""
    try:
        resp = secretsmanager.get_secret_value(SecretId=GITHUB_TOKEN_SECRET_ID)
        raw = resp.get("SecretString")
        if raw is None and resp.get("SecretBinary"):
            raw = base64.b64decode(resp["SecretBinary"]).decode("utf-8", "replace")
        value = str(raw or "").strip()
    except Exception as exc:  # noqa: BLE001 - fallback to SSM.
        _warn_github_token_once(
            "secret_lookup",
            f"WARN: GitHub token Secrets Manager lookup failed for {GITHUB_TOKEN_SECRET_ID}: "
            f"{type(exc).__name__}; falling back to SSM",
        )
        return ""
    if not value:
        _warn_github_token_once(
            "secret_empty",
            f"WARN: GitHub token Secrets Manager secret {GITHUB_TOKEN_SECRET_ID} returned an empty value; "
            "falling back to SSM",
        )
        return ""
    return value


def _github_token_from_ssm() -> str:
    """Return the GitHub PAT from SSM fallback without logging the value."""
    if _GITHUB_TOKEN_CACHE is not None:
        return _GITHUB_TOKEN_CACHE
    try:
        resp = ssm.get_parameter(Name=GITHUB_TOKEN_PARAM, WithDecryption=True)
        value = str(resp.get("Parameter", {}).get("Value") or "").strip()
    except Exception as exc:  # noqa: BLE001 - surface as GitHub API auth failure.
        _warn_github_token_once(
            "ssm_lookup",
            f"WARN: GitHub token SSM lookup failed for {GITHUB_TOKEN_PARAM}: {type(exc).__name__}",
        )
        return ""
    if not value:
        _warn_github_token_once(
            "ssm_empty",
            f"WARN: GitHub token SSM parameter {GITHUB_TOKEN_PARAM} returned an empty value",
        )
        return ""
    return value


def _github_token() -> str:
    """Return the GitHub PAT from Secrets Manager, with SSM fallback."""
    global _GITHUB_TOKEN_CACHE
    if _GITHUB_TOKEN_CACHE is not None:
        return _GITHUB_TOKEN_CACHE
    value = _github_token_from_secret() or _github_token_from_ssm()
    if value:
        _GITHUB_TOKEN_CACHE = value
    return value


def _github_api_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    expected_status: set[int] | None = None,
) -> Any:
    url = f"https://api.github.com/repos/{GITHUB_REPO}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "xyz-pilot-candidate-drain",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if expected_status and response.status not in expected_status:
                raise RuntimeError(f"GitHub API request returned unexpected status {response.status}")
            raw = response.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"GitHub API request failed: {exc.code} {exc.reason}; {body}") from exc


def _github_api_json(path: str) -> dict[str, Any]:
    return _github_api_request("GET", path, expected_status={200})


def _dispatch_background_filing(
    candidate: dict[str, Any],
    candidate_index: int,
    *,
    mode: str,
    operator_approved_high_severity: bool = False,
) -> dict[str, Any]:
    if mode not in VALID_AUTO_FILE_MODES:
        raise RuntimeError(f"Unsupported auto-file mode: {mode}")
    payload = {
        "ref": GITHUB_REF,
        "inputs": {
            "run_id": candidate["run_id"],
            "source": candidate["source"],
            "candidate_index": str(candidate_index),
            "classification_override": "",
            "mode": mode,
            "operator_approved_high_severity": "true" if operator_approved_high_severity else "false",
        },
    }
    workflow = urllib.parse.quote(AUTO_FILE_WORKFLOW_ID, safe="")
    _github_api_request(
        "POST",
        f"/actions/workflows/{workflow}/dispatches",
        payload,
        expected_status={204},
    )
    return {
        "run_id": candidate["run_id"],
        "source": candidate["source"],
        "candidate_index": candidate_index,
        "mode": mode,
        "operator_approved_high_severity": operator_approved_high_severity,
        "workflow": AUTO_FILE_WORKFLOW_ID,
    }


def _handle_targeted_dispatch(request: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    """Operator-approved single-candidate dispatch for the XYZ_MASTER approval panel.

    The approval panel invokes this Lambda (the single GitHub-writer principal, so the
    dashboard never holds a PAT) with an explicit candidate + mode. Unlike the hourly
    cron scan -- which only auto-files low-severity, non-transition candidates in
    direct-main -- this path can dispatch ``pr`` mode with
    ``operator_approved_high_severity=True`` so a NEW major/critical ticket goes through
    the guarded PR flow. Idempotency still comes from the ``candidate-outcomes/`` marker
    the background-filing workflow writes, so a panel approve racing the cron drain of
    the same candidate collapses to a no-op.
    """
    run_id = str(request.get("run_id") or "").strip()
    source = str(request.get("source") or "").strip()
    mode = str(request.get("mode") or AUTO_FILE_MODE).strip()
    approved = bool(request.get("operator_approved_high_severity"))
    try:
        candidate_index = int(request.get("candidate_index"))
    except (TypeError, ValueError):
        raise RuntimeError("file_candidate requires an integer candidate_index")
    if not run_id or not source or candidate_index < 1:
        raise RuntimeError("file_candidate requires run_id, source, and candidate_index >= 1")
    if mode not in VALID_AUTO_FILE_MODES:
        raise RuntimeError(f"Unsupported file_candidate mode: {mode}")
    request_summary = {
        "run_id": run_id,
        "source": source,
        "candidate_index": candidate_index,
        "mode": mode,
        "operator_approved_high_severity": approved,
    }
    if dry_run:
        return {"status": "dry-run", "file_candidate": request_summary}
    result = _dispatch_background_filing(
        {"run_id": run_id, "source": source},
        candidate_index,
        mode=mode,
        operator_approved_high_severity=approved,
    )
    return {"status": "dispatched", "file_candidate": result}


def _repo_tree_entries() -> list[dict[str, Any]]:
    tree = _github_api_json(f"/git/trees/{urllib.parse.quote(GITHUB_REF, safe='')}?recursive=1")
    entries = []
    for item in tree.get("tree", []):
        if item.get("type") == "blob":
            entries.append(item)
    entries.sort(key=lambda item: item["path"])
    return entries


def _candidate_tree_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in entries if CANDIDATE_PATH_RE.match(item.get("path", ""))]


def _outcome_index(entries: list[dict[str, Any]]) -> dict[str, set[int]]:
    index: dict[str, set[int]] = {}
    for item in entries:
        path = item.get("path", "")
        match = OUTCOME_PATH_RE.match(path)
        if not match:
            continue
        candidate_path = (
            f"tests/logs/runs/{match.group('run_id')}/{match.group('source')}/issue-candidates.md"
        )
        index.setdefault(candidate_path, set()).add(int(match.group("idx")))
    return index


def _decided_count(candidate_count: int, decided_indices: set[int] | None) -> int:
    if not decided_indices:
        return 0
    return sum(1 for idx in range(1, candidate_count + 1) if idx in decided_indices)


def _read_repo_file(path: str) -> str:
    encoded_path = urllib.parse.quote(path, safe="/")
    payload = _github_api_json(f"/contents/{encoded_path}?ref={urllib.parse.quote(GITHUB_REF, safe='')}")
    if payload.get("encoding") != "base64":
        raise RuntimeError(f"Unexpected GitHub content encoding for {path}: {payload.get('encoding')}")
    return base64.b64decode(payload.get("content", "")).decode("utf-8", "replace")


def _load_state() -> dict[str, Any]:
    try:
        obj = s3.get_object(Bucket=STATE_BUCKET, Key=STATE_KEY)
    except Exception as exc:  # boto3 raises ClientError, but keep Lambda imports minimal.
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        if code in {"NoSuchKey", "NoSuchBucket", "404"}:
            return {"version": 1, "seen": {}, "created_at": _now_iso()}
        raise

    state = json.loads(obj["Body"].read().decode("utf-8"))
    if not isinstance(state.get("seen"), dict):
        state["seen"] = {}
    return state


def _save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = _now_iso()
    s3.put_object(
        Bucket=STATE_BUCKET,
        Key=STATE_KEY,
        Body=json.dumps(state, indent=2, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )


def _first_matching_value(pattern: re.Pattern[str], text: str, default: str = "unknown") -> str:
    match = pattern.search(text)
    if not match:
        return default
    return " ".join(match.group("value").strip().split())


def _normalized_classification(value: str) -> str:
    match = CLASSIFICATION_TOKEN_RE.search(value.lower())
    return match.group(1) if match else "unknown"


def _is_status_transition_candidate(text: str) -> bool:
    markers = {m.group(0).lower() for m in STATUS_TRANSITION_RE.finditer(text)}
    has_existing = any("existing id" in m or "existing ticket" in m or "tracker id" in m for m in markers)
    has_status = any("requested status" in m or "recommended status" in m for m in markers)
    return has_existing and has_status


def _is_housekeeping_candidate(source: str, text: str) -> bool:
    return source == "XYZ_PILOT_DAILY_STATUS" and bool(PRUNE_HOUSEKEEPING_RE.search(text))


def _candidate_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            return stripped.lstrip("# ").strip()
        if stripped.startswith("# "):
            title = stripped.lstrip("# ").strip()
            if "candidate handoff" not in title.lower():
                return title
    for line in text.splitlines():
        stripped = line.strip(" -")
        if stripped:
            return stripped[:100]
    return "Untitled issue candidate file"


def _candidate_blocks(text: str) -> list[dict[str, Any]]:
    matches = list(CANDIDATE_HEADING_RE.finditer(text))
    if not matches:
        sep = text.find("\n---\n")
        body = text[sep + len("\n---\n"):].strip() if sep != -1 else text.strip()
        if not _has_actionable_candidate_fields(body):
            return []
        return [{"index": 1, "title": _candidate_title(text), "body": body}]

    blocks: list[dict[str, Any]] = []
    for idx, match in enumerate(matches, start=1):
        end = matches[idx].start() if idx < len(matches) else len(text)
        body = text[match.start() : end].strip()
        title = match.group(0).lstrip("# ").strip()
        blocks.append({"index": idx, "title": title, "body": body})
    return blocks


def _has_actionable_candidate_fields(body: str) -> bool:
    return bool(
        ACTIONABLE_CANDIDATE_FIELD_RE.search(body)
        or "### Top-table row" in body
        or "### Full-register detail section" in body
    )


def _candidate_count(text: str) -> int:
    return len(_candidate_blocks(text))


def _summarize_candidate(
    entry: dict[str, Any],
    outcomes_by_candidate_path: dict[str, set[int]] | None = None,
) -> dict[str, Any]:
    path = entry["path"]
    match = CANDIDATE_PATH_RE.match(path)
    text = _read_repo_file(path)
    blocks = _candidate_blocks(text)
    candidate_count = len(blocks)
    decided_indices = (outcomes_by_candidate_path or {}).get(path) or set()
    decided = _decided_count(candidate_count, decided_indices)
    candidate_items = []
    for block in blocks:
        severity = _first_matching_value(SEVERITY_RE, block["body"])
        ctype = _first_matching_value(TYPE_RE, block["body"])
        normalized = _normalized_classification(severity)
        status_transition = _is_status_transition_candidate(block["body"])
        housekeeping = _is_housekeeping_candidate(match.group("source") if match else "unknown", block["body"])
        candidate_items.append(
            {
                "index": block["index"],
                "title": block["title"],
                "severity": severity,
                "normalized_severity": normalized,
                "type": ctype,
                "decided": block["index"] in decided_indices,
                "status_transition": status_transition,
                "housekeeping": housekeeping,
                "auto_file_eligible": normalized in LOW_SEVERITY_AUTO_FILE and not status_transition,
            }
        )
    return {
        "key": f"{entry.get('sha', 'unknown')}:{path}",
        "path": path,
        "sha": entry.get("sha", "unknown"),
        "run_id": match.group("run_id") if match else "unknown",
        "source": match.group("source") if match else "unknown",
        "title": _candidate_title(text),
        "severity": candidate_items[0]["severity"] if candidate_items else "unknown",
        "type": candidate_items[0]["type"] if candidate_items else "unknown",
        "candidates": candidate_items,
        "candidate_count": candidate_count,
        "decided_count": decided,
        "all_decided": candidate_count > 0 and decided >= candidate_count,
        "all_housekeeping": bool(candidate_items) and all(item["housekeeping"] for item in candidate_items),
        "url": f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_REF}/{path}",
    }


def _render_text_email(new_candidates: list[dict[str, Any]], all_count: int) -> str:
    lines = [
        "XYZ Pilot candidate-drain digest",
        "",
        "High-severity or ambiguous candidates still require operator review.",
        "Minor defects and feature requests may be auto-dispatched to the guarded XYZ_MASTER background workflow.",
        "",
        f"New candidate files detected: {len(new_candidates)}",
        f"Total committed candidate files observed: {all_count}",
        "",
    ]
    for idx, candidate in enumerate(new_candidates, 1):
        lines.extend(
            [
                f"{idx}. {candidate['title']}",
                f"   Source: {candidate['source']} / run {candidate['run_id']}",
                f"   Type/severity: {candidate['type']} / {candidate['severity']}",
                f"   Candidate count in file: {candidate['candidate_count']}",
                f"   GitHub: {candidate['url']}",
                "",
            ]
        )
    lines.append("Major/Critical candidates require operator approval before tracker mutation.")
    return "\n".join(lines)


def _render_html_email(new_candidates: list[dict[str, Any]], all_count: int) -> str:
    rows = []
    for candidate in new_candidates:
        rows.append(
            "<tr>"
            f"<td>{html.escape(candidate['run_id'])}</td>"
            f"<td>{html.escape(candidate['source'])}</td>"
            f"<td>{html.escape(candidate['type'])}</td>"
            f"<td>{html.escape(candidate['severity'])}</td>"
            f"<td>{html.escape(candidate['title'])}</td>"
            f"<td><a href=\"{html.escape(candidate['url'])}\">candidate file</a></td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html>
  <body style="font-family:Arial,Helvetica,sans-serif;color:#172033">
    <h2>XYZ Pilot candidate-drain digest</h2>
    <p>High-severity or ambiguous candidates still require operator review. Minor defects and feature requests may be auto-dispatched to the guarded XYZ_MASTER background workflow.</p>
    <p>New candidate files detected: <strong>{len(new_candidates)}</strong><br>
       Total committed candidate files observed: <strong>{all_count}</strong></p>
    <table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse">
      <thead>
        <tr><th>Run</th><th>Source</th><th>Type</th><th>Severity</th><th>Summary</th><th>Link</th></tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    <p>Major/Critical candidates require operator approval before tracker mutation.</p>
  </body>
</html>"""


def _send_digest(new_candidates: list[dict[str, Any]], all_count: int) -> str:
    subject = f"XYZ Pilot Candidate Drain: {len(new_candidates)} new issue candidate file(s)"
    response = ses.send_email(
        Source=SES_FROM,
        Destination={"ToAddresses": SES_TO},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": _render_text_email(new_candidates, all_count), "Charset": "UTF-8"},
                "Html": {"Data": _render_html_email(new_candidates, all_count), "Charset": "UTF-8"},
            },
        },
    )
    return response.get("MessageId", "")


def lambda_handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    event = event or {}
    dry_run = bool(event.get("dry_run"))
    include_existing = bool(event.get("include_existing"))
    bootstrap_email = bool(event.get("send_bootstrap_email", True))
    auto_file_enabled = bool(event.get("auto_file_low_severity", AUTO_FILE_LOW_SEVERITY))
    auto_file_mode = str(event.get("auto_file_mode", AUTO_FILE_MODE))
    if auto_file_mode not in VALID_AUTO_FILE_MODES:
        raise RuntimeError(f"Unsupported auto_file_mode: {auto_file_mode}")

    # Operator-approved single-candidate dispatch (XYZ_MASTER approval panel).
    # Short-circuits the cron scan: the panel names the candidate + mode explicitly.
    file_candidate = event.get("file_candidate")
    if isinstance(file_candidate, dict):
        return _handle_targeted_dispatch(file_candidate, dry_run=dry_run)

    tree_entries = _repo_tree_entries()
    entries = _candidate_tree_entries(tree_entries)
    outcomes_by_candidate_path = _outcome_index(tree_entries)
    state = _load_state()
    seen = state.setdefault("seen", {})

    is_bootstrap = not seen
    if is_bootstrap and not include_existing:
        for entry in entries:
            seen[f"{entry.get('sha', 'unknown')}:{entry['path']}"] = {
                "first_seen": _now_iso(),
                "path": entry["path"],
                "bootstrap": True,
            }
        if not dry_run:
            _save_state(state)
        if bootstrap_email and entries and not dry_run:
            message_id = _send_digest(
                [
                    {
                        "run_id": "bootstrap",
                        "source": "candidate-drain",
                        "type": "state initialization",
                        "severity": "informational",
                        "title": f"Initialized candidate-drain state with {len(entries)} existing files",
                        "candidate_count": len(entries),
                        "url": f"https://github.com/{GITHUB_REPO}/tree/{GITHUB_REF}/tests/logs/runs",
                    }
                ],
                len(entries),
            )
        else:
            message_id = ""
        return {
            "status": "bootstrap",
            "candidate_files_seen": len(entries),
            "new_candidate_files": 0,
            "email_sent": bool(message_id),
            "message_id": message_id,
            "dry_run": dry_run,
        }

    new_entries = [entry for entry in entries if f"{entry.get('sha', 'unknown')}:{entry['path']}" not in seen]
    new_candidates_all = [
        _summarize_candidate(entry, outcomes_by_candidate_path)
        for entry in new_entries[-MAX_CANDIDATES:]
    ]
    pending_candidates = [candidate for candidate in new_candidates_all if not candidate["all_decided"]]
    ignored_empty = [candidate for candidate in pending_candidates if candidate["candidate_count"] == 0]
    pending_candidates = [candidate for candidate in pending_candidates if candidate["candidate_count"] > 0]
    ignored_decided = [candidate for candidate in new_candidates_all if candidate["all_decided"]]
    ignored_housekeeping = [candidate for candidate in pending_candidates if candidate.get("all_housekeeping")]
    pending_candidates = [candidate for candidate in pending_candidates if not candidate.get("all_housekeeping")]
    auto_dispatches: list[dict[str, Any]] = []
    digest_candidates: list[dict[str, Any]] = []
    burst_guarded_candidates: list[dict[str, Any]] = []

    for candidate in pending_candidates:
        needs_operator_digest = False
        undecided_items = [item for item in candidate["candidates"] if not item["decided"]]
        burst_guarded = len(undecided_items) > MAX_AUTO_DISPATCH_PER_FILE
        manual_review_source = candidate["source"] in MANUAL_REVIEW_SOURCES
        candidate["burst_guarded"] = burst_guarded
        candidate["manual_review_source"] = manual_review_source
        if burst_guarded:
            burst_guarded_candidates.append(candidate)
        for item in candidate["candidates"]:
            if item["decided"]:
                continue
            if auto_file_enabled and item["auto_file_eligible"] and not burst_guarded and not manual_review_source:
                dispatch = {
                    "run_id": candidate["run_id"],
                    "source": candidate["source"],
                    "candidate_index": item["index"],
                    "mode": auto_file_mode,
                    "workflow": AUTO_FILE_WORKFLOW_ID,
                    "dry_run": dry_run,
                    "classification": item["normalized_severity"],
                }
                if not dry_run:
                    dispatch.update(
                        _dispatch_background_filing(
                            candidate,
                            item["index"],
                            mode=auto_file_mode,
                        )
                    )
                auto_dispatches.append(dispatch)
            else:
                needs_operator_digest = True
        if needs_operator_digest:
            digest_candidates.append(candidate)

    message_id = ""
    if new_candidates_all and not dry_run:
        if digest_candidates:
            message_id = _send_digest(digest_candidates, len(entries))
        for candidate in new_candidates_all:
            seen[candidate["key"]] = {
                "first_seen": _now_iso(),
                "path": candidate["path"],
                "sha": candidate["sha"],
                "run_id": candidate["run_id"],
                "source": candidate["source"],
                "candidate_count": candidate["candidate_count"],
                "decided_count": candidate["decided_count"],
                "ignored_all_decided": candidate["all_decided"],
                "ignored_empty": candidate["candidate_count"] == 0,
                "ignored_housekeeping": candidate.get("all_housekeeping", False),
                "burst_guarded": candidate.get("burst_guarded", False),
                "manual_review_source": candidate.get("manual_review_source", False),
                "auto_file_low_severity": auto_file_enabled,
                "auto_dispatches": [
                    dispatch
                    for dispatch in auto_dispatches
                    if dispatch["run_id"] == candidate["run_id"] and dispatch["source"] == candidate["source"]
                ],
            }
        _save_state(state)

    return {
        "status": "ok",
        "candidate_files_seen": len(entries),
        "new_candidate_files": len(pending_candidates),
        "manual_digest_candidate_files": len(digest_candidates),
        "ignored_decided_candidate_files": len(ignored_decided),
        "ignored_empty_candidate_files": len(ignored_empty),
        "ignored_housekeeping_candidate_files": len(ignored_housekeeping),
        "burst_guarded_candidate_files": len(burst_guarded_candidates),
        "max_auto_dispatch_per_file": MAX_AUTO_DISPATCH_PER_FILE,
        "auto_file_low_severity": auto_file_enabled,
        "auto_file_mode": auto_file_mode,
        "auto_dispatches": auto_dispatches,
        "email_sent": bool(message_id),
        "message_id": message_id,
        "dry_run": dry_run,
    }
