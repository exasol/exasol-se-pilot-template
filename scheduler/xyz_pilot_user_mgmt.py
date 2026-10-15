"""XYZ-PILOT-116 — Operator-facing user-management Lambda for the New User
Onboarding Facility on the Operator Dashboard.

Three actions exposed via the `action` JSON field:

  {"action": "cohort_table"}
      → returns the cohort grouped by Cognito UserStatus:
        {"CONFIRMED": [...emails...], "FORCE_CHANGE_PASSWORD": [...]}
        plus per-user SES verification status so the dashboard can
        render the same table as the prose summary used in chat.

  {"action": "list_registered"}
      → returns only the subset with UserStatus == "CONFIRMED" AND
        SES VerificationStatus == "Success" — i.e. subscribers who
        completed BOTH onboarding halves and can actually use the
        Pilot end-to-end.

  {"action": "invite_user", "email": "<addr>"}
      → idempotently creates the Cognito user (AdminCreateUser, sends
        the temp-password invite by default) AND triggers the AWS SES
        verify-email-identity flow so they enter the daily-status
        recipient list once they click both links.

  {"action": "service_probe"}
      → performs bounded server-side HTTP probes for tunnel-backed
        ServiceURIs so the dashboard does not rely on browser CORS.

The Lambda is invoked from `dashboard.html` (Operator Dashboard) via
Cognito Identity Pool temporary credentials; the auth-role attached to
the pool (`xyz-pilot-secret-retrievers-authenticated`) needs
`lambda:InvokeFunction` on this Lambda's ARN.

The Lambda's OWN execution role needs:
  - cognito-idp:ListUsers
  - cognito-idp:AdminCreateUser
  - cognito-idp:AdminGetUser
  - cognito-idp:AdminAddUserToGroup
  - cognito-idp:AdminListGroupsForUser
  - ses:VerifyEmailIdentity
  - ses:GetIdentityVerificationAttributes
  - ses:ListIdentities
  - s3:GetObject on the onboarding-watchdog state object
  - secretsmanager:GetSecretValue on GITHUB_TOKEN_SECRET_ID for GitHub-backed actions
  - ssm:GetParameter on GITHUB_TOKEN_PARAM as the temporary rollback fallback
  - kms:Decrypt for the SSM SecureString key

Env vars:
  USER_POOL_ID  — Cognito User Pool (default: <COGNITO_USER_POOL_ID>)
  AWS_REGION    — provided by Lambda runtime
  ONBOARDING_STATE_BUCKET — status bucket for watchdog state
  ONBOARDING_STATE_KEY    — S3 key for watchdog state
  GITHUB_TOKEN_SECRET_ID — Secrets Manager secret name for the GitHub PAT
  GITHUB_TOKEN_PARAM     — SSM SecureString name for the GitHub PAT fallback
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

USER_POOL_ID = os.environ.get("USER_POOL_ID", "<COGNITO_USER_POOL_ID>")
REGION = os.environ.get("AWS_REGION", "<AWS_REGION>")
ACCOUNT_ID = os.environ.get("XYZ_AWS_ACCOUNT_ID", "<AWS_ACCOUNT_ID>")
ONBOARDING_STATE_BUCKET = os.environ.get("ONBOARDING_STATE_BUCKET", f"xyz-pilot-status-{ACCOUNT_ID}")
ONBOARDING_STATE_KEY = os.environ.get("ONBOARDING_STATE_KEY", "sns-watchdog/state.json")
# XYZ-PILOT-291 Option B: the EC2 warm-up publishes its progress here; the
# dashboard polls the `warmup_progress` action to stream warm-up state into
# its log in real time.
WARMUP_PROGRESS_KEY = os.environ.get("WARMUP_PROGRESS_KEY", "warmup/progress.json")
# XYZ-PILOT-289: Exasol Personal license rotation scripts publish non-secret
# evidence here so the Operator Dashboard activity log can show the current
# ConfD contract/check state after export/import/rotation events.
LICENSE_EVIDENCE_KEY = os.environ.get("LICENSE_EVIDENCE_KEY", "license/evidence.json")
XYZ_MASTER_APPROVER_EMAILS = {
    e.strip().lower()
    for e in os.environ.get(
        "XYZ_MASTER_APPROVER_EMAILS",
        "<OPERATOR_EMAIL>,<OPS_USER_2_EMAIL>,<OPS_USER_3_EMAIL>",
    ).split(",")
    if e.strip()
}
XYZ_MASTER_WORKFLOW_ID = os.environ.get("XYZ_MASTER_WORKFLOW_ID", "xyz-master-background-filing.yml")
XYZ_MASTER_BACKLOG_MIN_RUN_ID = os.environ.get("XYZ_MASTER_BACKLOG_MIN_RUN_ID", "2026-06-03T000000Z")
XYZ_MASTER_BACKLOG_LIMIT = int(os.environ.get("XYZ_MASTER_BACKLOG_LIMIT", "50"))
XYZ_MASTER_CANDIDATE_FILE_VISIBLE_LIMIT = int(os.environ.get("XYZ_MASTER_CANDIDATE_FILE_VISIBLE_LIMIT", "10"))
XYZ_MASTER_CANDIDATE_FILE_SCAN_LIMIT = int(os.environ.get("XYZ_MASTER_CANDIDATE_FILE_SCAN_LIMIT", "40"))

GROUP_OPS_ONLY = "OPS_ONLY"
GROUP_XYZ_PILOT = "XYZ_PILOT"
GROUP_XYZ_OPS = "XYZ_OPS"
GROUP_XYZ_BIZ = "XYZ_BIZ"
ALL_COGNITO_GROUPS = (GROUP_OPS_ONLY, GROUP_XYZ_PILOT, GROUP_XYZ_OPS, GROUP_XYZ_BIZ)
DASHBOARD_ACCESS_GROUPS = {GROUP_OPS_ONLY, GROUP_XYZ_PILOT, GROUP_XYZ_OPS}
SECRET_PANEL_GROUPS = {GROUP_OPS_ONLY, GROUP_XYZ_OPS}
FULL_OPERATOR_GROUPS = {GROUP_OPS_ONLY}
INVITER_GROUPS = {GROUP_OPS_ONLY, GROUP_XYZ_OPS}
DEFAULT_INVITE_GROUPS = (GROUP_XYZ_BIZ,)

cognito = boto3.client("cognito-idp", region_name=REGION)
ses = boto3.client("ses", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)

# ---------------------------------------------------------------------------
# XYZ-PILOT-250 — swarm scheduling constants
# ---------------------------------------------------------------------------
SWARM_GROUP = "xyz-pilot-swarm"
SWARM_WORKFLOW = "e2e-swarm-nightly.yml"
SWARM_MAX_HORIZON_DAYS = 30
# XYZ_STRESS virtual-user hard ceiling. The old value (20) was bound to the
# Exasol Personal license's 20-session cap; after the license rotated to
# unlimited sessions the binding constraint is the single EC2 node's CPU/RAM,
# not the DB session count. Keep a generous-but-bounded, env-overridable cap so
# a typo can't OOM the node — raise XYZ_SWARM_STRESS_HARD_MAX if a heavier run
# is genuinely wanted. The >10 still requires the explicit higher-allow flag.
SWARM_STRESS_HARD_MAX = int(os.environ.get("XYZ_SWARM_STRESS_HARD_MAX", "50"))
OPT_IN_AGENTS = {"XYZ_STRESS", "XYZ_REGRESSION", "XYZ_INFRASTRUCTURE"}
BASE_AGENTS = ["XYZ_EXPERT", "XYZ_SMOKE", "XYZ_INTEGRATION", "XYZ_E2E", "XYZ_UAT", "XYZ_CHAOS"]
ADV_DENY = ("TOKEN", "PASSWORD", "SECRET", "SES_FROM")
SWARM_SCHEDULER_ROLE_ARN = os.environ.get("SWARM_SCHEDULER_ROLE_ARN", "")
OPS_ONLY = [
    e.strip()
    for e in os.environ.get(
        "OPS_RECIPIENTS",
        "<OPERATOR_EMAIL>,<OPS_USER_2_EMAIL>,<OPS_USER_3_EMAIL>",
    ).split(",")
    if e.strip()
]


class SwarmScheduleError(Exception):
    """Raised when a swarm_schedule request contains invalid parameters."""


def _cest_to_utc(date_str: str, time_str: str) -> str:
    """Convert a CEST (UTC+2) date + time string to a UTC ISO-8601 string."""
    local = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return (local - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:00Z")


def _validate_fire(utc_iso: str, now: "datetime | None" = None) -> "datetime":
    """Parse and validate the UTC fire time; return the aware datetime."""
    now = now or datetime.now(timezone.utc)
    fire = datetime.strptime(utc_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if fire <= now:
        raise SwarmScheduleError("run time must be in the future")
    if fire > now + timedelta(days=SWARM_MAX_HORIZON_DAYS):
        raise SwarmScheduleError(f"run time must be within {SWARM_MAX_HORIZON_DAYS} days")
    return fire


def _validate_stress(n: int, allow_higher: bool) -> int:
    """Validate the XYZ_STRESS_USERS count; return validated value."""
    if n > SWARM_STRESS_HARD_MAX:
        raise SwarmScheduleError(f"XYZ_STRESS_USERS hard max is {SWARM_STRESS_HARD_MAX}")
    if n > 10 and not allow_higher:
        raise SwarmScheduleError("XYZ_STRESS_USERS > 10 requires the higher-allow flag")
    if n < 1:
        raise SwarmScheduleError("XYZ_STRESS_USERS must be >= 1")
    return n


def _parse_advanced(text: str) -> dict:
    """Parse KEY=VALUE overrides from a multiline string; enforce XYZ_ prefix and deny-list."""
    out: dict = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise SwarmScheduleError(f"advanced line not KEY=VALUE: {line}")
        k, v = line.split("=", 1)
        if not k.startswith("XYZ_") or not k.replace("_", "").isalnum():
            raise SwarmScheduleError(f"advanced key must match XYZ_*: {k}")
        if any(d in k for d in ADV_DENY):
            raise SwarmScheduleError(f"advanced key not allowed: {k}")
        out[k] = v
    return out


def _build_workflow_inputs(cfg: dict) -> dict:
    """Map a swarm config dict to GitHub Actions workflow_dispatch inputs."""
    base = [a for a in cfg.get("agents", []) if a not in OPT_IN_AGENTS]
    return {
        "tester_agents": " ".join(base),
        "include_stress": "XYZ_STRESS" in cfg.get("agents", []),
        "include_regression": "XYZ_REGRESSION" in cfg.get("agents", []),
        "include_infrastructure": "XYZ_INFRASTRUCTURE" in cfg.get("agents", []),
        "stress_users": str(cfg.get("stress_users", 10)),
        "session_hold_seconds": str(cfg.get("session_hold_seconds", 20)),
        "dry_run": bool(cfg.get("dry_run", False)),
        "force_run": bool(cfg.get("force_run", False)),
        "notify": bool(cfg.get("notify", True)),
        "notify_recipients": ",".join(cfg.get("notify_recipients", [])),
    }


def _scheduler_client():
    return boto3.client("scheduler", region_name=REGION)


def _swarm_dispatch(cfg: dict, schedule_name: "str | None") -> dict:
    """Fire the e2e-swarm workflow_dispatch from a stored config; self-clean the one-shot."""
    inputs = _build_workflow_inputs(cfg)
    # GitHub workflow_dispatch requires string values (booleans as "true"/"false").
    gh_inputs = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in inputs.items()}
    _gh_request(
        "POST",
        f"repos/{GITHUB_REPO}/actions/workflows/{SWARM_WORKFLOW}/dispatches",
        body={"ref": "main", "inputs": gh_inputs},
    )
    if schedule_name:
        try:
            _scheduler_client().delete_schedule(Name=schedule_name, GroupName=SWARM_GROUP)
        except Exception as e:  # noqa: BLE001
            print("swarm_dispatch: self-delete failed:", str(e)[:160])
    return {"dispatched": True, "inputs": gh_inputs}


def _swarm_schedules_list() -> dict:
    """List pending one-shot swarm schedules in the xyz-pilot-swarm group."""
    sch = _scheduler_client()
    out = []
    resp = sch.list_schedules(GroupName=SWARM_GROUP)
    for s in resp.get("Schedules", []):
        name = s.get("Name", "")
        fires_at, agents = "", []
        try:
            full = sch.get_schedule(Name=name, GroupName=SWARM_GROUP)
            fires_at = full.get("ScheduleExpression", "")
            inp = json.loads(full.get("Target", {}).get("Input", "{}"))
            agents = inp.get("config", {}).get("agents", [])
        except Exception:  # noqa: BLE001
            pass
        out.append({"name": name, "fires_at_utc": fires_at, "agents": agents})
    return {"schedules": out}


def _swarm_schedule_cancel(name: str) -> dict:
    """Delete a pending one-shot schedule. Idempotent on not-found."""
    sch = _scheduler_client()
    try:
        sch.delete_schedule(Name=name, GroupName=SWARM_GROUP)
    except Exception as e:  # noqa: BLE001
        nf = getattr(getattr(sch, "exceptions", None), "ResourceNotFoundException", None)
        if not ((nf is not None and isinstance(e, nf)) or "ResourceNotFound" in type(e).__name__):
            raise
    return {"ok": True, "name": name}


def _parse_run_results(text: str) -> dict:
    """Count PASS / FAIL / WARN(+ADVISORY) markers in a committed agent results blob."""
    return {
        "pass": len(re.findall(r"\bPASS\b", text or "")),
        "fail": len(re.findall(r"\bFAIL\b", text or "")),
        "warn": len(re.findall(r"\bWARN\b|\bADVISORY\b", text or "")),
    }


def _gh_contents(path: str):
    """GitHub contents API (read-only): list for a dir, object for a file."""
    return _gh_request("GET", f"repos/{GITHUB_REPO}/contents/{path}", ref="main")


def _gh_file_text(path: str) -> str:
    obj = _gh_contents(path)
    if isinstance(obj, dict) and obj.get("encoding") == "base64":
        return base64.b64decode(obj.get("content", "")).decode("utf-8", "ignore")
    return ""


_RUN_MANIFEST_RE = re.compile(r"^tests/logs/runs/([^/]+)/manifest\.md$")


def _swarm_runs_list() -> dict:
    """Newest-first list of committed *tester-swarm* run-ids — only runs that have a
    manifest.md (i.e. real swarm runs with per-agent results), so the panel dropdown is
    not convoluted with XYZ_MASTER/codex candidate-handoff folders that have nothing to
    analyze. One read-only call to the GitHub git-trees API (recursive)."""
    tree = _gh_request("GET", f"repos/{GITHUB_REPO}/git/trees/main", recursive="1")
    entries = tree.get("tree", []) if isinstance(tree, dict) else []
    runs = sorted(
        {m.group(1) for e in entries for m in [_RUN_MANIFEST_RE.match(e.get("path", ""))] if m},
        reverse=True,
    )
    out = {"runs": runs[:30], "total_swarm_runs": len(runs)}
    if isinstance(tree, dict) and tree.get("truncated"):
        out["truncated"] = True  # tree too large to enumerate fully; list may be partial
    return out


def _swarm_run_results(run_id: str) -> dict:
    """Pull + analyze a committed run's per-agent results from GitHub (read-only)."""
    base = f"tests/logs/runs/{run_id}"
    entries = _gh_contents(base)
    agents = []
    for e in (entries or []):
        if not isinstance(e, dict) or e.get("type") != "dir" or e["name"] == "candidate-outcomes":
            continue
        try:
            txt = _gh_file_text(f"{base}/{e['name']}/results.md")
        except Exception:  # noqa: BLE001
            txt = ""
        if txt:
            agents.append({"name": e["name"], **_parse_run_results(txt)})
    try:
        co = _gh_contents(f"{base}/candidate-outcomes")
        candidates = len([c for c in co if isinstance(c, dict) and c.get("type") == "file"])
    except Exception:  # noqa: BLE001
        candidates = 0
    return {
        "run_id": run_id,
        "agents": agents,
        "candidates": candidates,
        "github_url": f"https://github.com/{GITHUB_REPO}/tree/main/{base}",
    }


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_CANDIDATE_HEADING_RE = re.compile(r"^##\s+Candidate\b(.*)$", re.MULTILINE)
_ACTIONABLE_CANDIDATE_FIELD_RE = re.compile(
    r"^-\s+\*\*(?:"
    r"Agent|Run|Type|Severity|Proposed severity|Recommended severity|"
    r"Requested severity|Requested classification|Recommended classification|"
    r"Area|Proposed area|Recommended area|Observed|Expected|Evidence|"
    r"Existing ID|Existing ticket|Tracker ID|Requested status|Recommended status|"
    r"Proposed status|Status"
    r"):\*\*",
    re.MULTILINE,
)
_RUN_ID_DASHED_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{6}Z)")
_RUN_ID_COMPACT_TS_RE = re.compile(r"(\d{8}T\d{6}Z)")
_OUTCOME_RE = re.compile(
    r"^tests/logs/runs/([^/]+)/([^/]+)/candidate-outcomes/candidate-(\d{3})\..+\.md$"
)
_TRACKER_HEADING_RE = re.compile(r"^### (XYZ-PILOT-\d{3})\s+(?:—|--?|[-–])\s+(.+)$", re.MULTILINE)
_TRACKER_STATUS_RE = re.compile(r"^- \*\*Status:\*\*\s*([^\n]+)$", re.MULTILINE)
_TRACKER_SEVERITY_RE = re.compile(r"^- \*\*Severity:\*\*\s*([^\n]+)$", re.MULTILINE)
_TRACKER_AREA_RE = re.compile(r"^- \*\*Area:\*\*\s*([^\n]+)$", re.MULTILINE)
_CANDIDATE_DETAIL_TITLE_RE = re.compile(
    r"^### XYZ-PILOT-(?:NNN|\d{3})\s+(?:—|--?|[-–])\s+(.+)$",
    re.MULTILINE,
)
_XYZ_ID_RE = re.compile(r"\bXYZ-PILOT-\d{3}\b")
_VALID_CLASSIFICATIONS = {"critical", "major", "minor", "feature", "external", "hygiene", "status"}
_OPEN_PANEL_CLASSIFICATIONS = {"critical", "major", "minor", "feature", "external", "hygiene"}
_PRIORITY_TARGET_CLASSIFICATIONS = {"critical", "major", "minor", "feature"}
_VALID_DENIAL_REASONS = {"duplicate", "wontfix", "invalid", "out-of-scope"}
_TERMINAL_STATUSES = ("resolved", "wontfix")


def _short(email: str) -> str:
    """`<OPERATOR_EMAIL>` → `<OPERATOR_USER>`."""
    return email.split("@", 1)[0]


def _list_cognito_users() -> list[dict[str, Any]]:
    """ListUsers with pagination."""
    users: list[dict[str, Any]] = []
    pagination_token = None
    while True:
        kwargs = {"UserPoolId": USER_POOL_ID, "Limit": 60}
        if pagination_token:
            kwargs["PaginationToken"] = pagination_token
        resp = cognito.list_users(**kwargs)
        users.extend(resp.get("Users", []))
        pagination_token = resp.get("PaginationToken")
        if not pagination_token:
            break
    return users


def _user_email(u: dict[str, Any]) -> str | None:
    for a in u.get("Attributes", []):
        if a["Name"] == "email":
            return a["Value"]
    return None


def _ses_verification_map(emails: list[str]) -> dict[str, str]:
    """Single SES GetIdentityVerificationAttributes call → {email: status}."""
    if not emails:
        return {}
    resp = ses.get_identity_verification_attributes(Identities=emails)
    return {
        addr: attrs.get("VerificationStatus", "Unknown")
        for addr, attrs in resp.get("VerificationAttributes", {}).items()
    }


def _load_onboarding_state() -> dict[str, Any]:
    """Read the watchdog state used for reminder/deactivation trails.

    The dashboard should remain usable if the state object is absent or the
    IAM grant is temporarily missing, so this helper degrades to an empty map.
    """
    try:
        obj = s3.get_object(Bucket=ONBOARDING_STATE_BUCKET, Key=ONBOARDING_STATE_KEY)
        state = json.loads(obj["Body"].read().decode("utf-8"))
        if isinstance(state, dict) and isinstance(state.get("users"), dict):
            return state
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding state unavailable: %s", type(exc).__name__)
    return {"users": {}}


def _state_for(state: dict[str, Any], email: str) -> dict[str, Any]:
    users = state.get("users") if isinstance(state, dict) else {}
    if not isinstance(users, dict):
        return {}
    rec = users.get(email.lower(), {})
    return rec if isinstance(rec, dict) else {}


def _is_deactivated(cognito_user: dict[str, Any] | None, onboarding_state: dict[str, Any]) -> bool:
    if onboarding_state.get("deactivation"):
        return True
    if cognito_user is not None and cognito_user.get("Enabled") is False:
        return True
    return False


def _onboarding_trail(onboarding_state: dict[str, Any]) -> dict[str, Any]:
    """Compact trail surfaced in Operator Dashboard individual status."""
    if not onboarding_state:
        return {}
    keys = (
        "cognito", "ses", "sns", "reminder", "weekly_reminder", "deactivation",
        "deactivation_notice", "last_checked_at_utc",
    )
    return {k: onboarding_state[k] for k in keys if k in onboarding_state}


def _action_cohort_table() -> dict[str, Any]:
    """Cohort grouped by Cognito UserStatus + per-user SES verification."""
    users = _list_cognito_users()
    state = _load_onboarding_state()
    emails = [e for e in (_user_email(u) for u in users) if e]
    ses_status = _ses_verification_map(emails)

    by_status: dict[str, list[dict[str, str]]] = {}
    for u in users:
        email = _user_email(u)
        if not email:
            continue
        onboarding_state = _state_for(state, email)
        status = "DEACTIVATED" if _is_deactivated(u, onboarding_state) else u.get("UserStatus", "Unknown")
        by_status.setdefault(status, []).append({
            "email": email,
            "short": _short(email),
            "ses_status": ses_status.get(email, "Pending"),
            "cognito_enabled": u.get("Enabled"),
            "deactivated": status == "DEACTIVATED",
            "created": u.get("UserCreateDate").isoformat() if u.get("UserCreateDate") else None,
            "modified": u.get("UserLastModifiedDate").isoformat() if u.get("UserLastModifiedDate") else None,
            "onboarding_trail": _onboarding_trail(onboarding_state),
        })
    for email, onboarding_state in (state.get("users") or {}).items():
        if email in emails or not isinstance(onboarding_state, dict) or not onboarding_state.get("deactivation"):
            continue
        by_status.setdefault("DEACTIVATED", []).append({
            "email": email,
            "short": _short(email),
            "ses_status": "NotVerified",
            "cognito_enabled": False,
            "deactivated": True,
            "created": None,
            "modified": None,
            "onboarding_trail": _onboarding_trail(onboarding_state),
        })
    # Sort each status bucket alphabetically by short name
    for k in by_status:
        by_status[k].sort(key=lambda x: x["short"])
    total_users = sum(len(v) for v in by_status.values())
    return {"action": "cohort_table", "by_status": by_status, "total_users": total_users}


def _action_list_registered() -> dict[str, Any]:
    """Users who completed BOTH onboarding halves: Cognito CONFIRMED + SES Success."""
    users = _list_cognito_users()
    state = _load_onboarding_state()
    emails = [e for e in (_user_email(u) for u in users) if e]
    ses_status = _ses_verification_map(emails)
    fully_registered: list[dict[str, str]] = []
    pending: list[dict[str, str]] = []
    deactivated: list[dict[str, str]] = []
    for u in users:
        email = _user_email(u)
        if not email:
            continue
        onboarding_state = _state_for(state, email)
        c_ok = u.get("UserStatus") == "CONFIRMED"
        s_ok = ses_status.get(email) == "Success"
        rec = {
            "email": email,
            "short": _short(email),
            "cognito_status": u.get("UserStatus", "Unknown"),
            "cognito_enabled": u.get("Enabled"),
            "ses_status": ses_status.get(email, "Pending"),
            "fully_registered": c_ok and s_ok,
            "deactivated": _is_deactivated(u, onboarding_state),
            "onboarding_trail": _onboarding_trail(onboarding_state),
        }
        if rec["deactivated"]:
            deactivated.append(rec)
        elif c_ok and s_ok:
            fully_registered.append(rec)
        else:
            pending.append(rec)
    for email, onboarding_state in (state.get("users") or {}).items():
        if email in emails or not isinstance(onboarding_state, dict) or not onboarding_state.get("deactivation"):
            continue
        deactivated.append({
            "email": email,
            "short": _short(email),
            "cognito_status": "not-in-cognito",
            "cognito_enabled": False,
            "ses_status": "NotVerified",
            "fully_registered": False,
            "deactivated": True,
            "onboarding_trail": _onboarding_trail(onboarding_state),
        })
    fully_registered.sort(key=lambda x: x["short"])
    pending.sort(key=lambda x: x["short"])
    deactivated.sort(key=lambda x: x["short"])
    return {
        "action": "list_registered",
        "fully_registered": fully_registered,
        "pending": pending,
        "deactivated": deactivated,
        "summary": {
            "fully_registered_count": len(fully_registered),
            "pending_count": len(pending),
            "deactivated_count": len(deactivated),
            "total": len(fully_registered) + len(pending) + len(deactivated),
        },
    }


def _action_get_user_status(email: str) -> dict[str, Any]:
    """XYZ-PILOT-115 sub-feature (b): individual user lookup.

    Scopes Cognito + SES calls to a single user instead of pulling the
    whole cohort. Returns the same shape as one row of `cohort_table`
    plus Cognito user-pool create/modified timestamps.
    """
    if not _EMAIL_RE.match(email):
        return {"action": "get_user_status", "ok": False, "error": f"invalid email: {email!r}"}
    state = _load_onboarding_state()
    onboarding_state = _state_for(state, email)
    try:
        u = cognito.admin_get_user(UserPoolId=USER_POOL_ID, Username=email)
    except cognito.exceptions.UserNotFoundException:
        if onboarding_state:
            return {
                "action": "get_user_status",
                "ok": True,
                "user": {
                    "email": email,
                    "short": _short(email),
                    "cognito_status": "not-in-cognito",
                    "cognito_enabled": False,
                    "ses_status": "NotVerified",
                    "created": None,
                    "modified": None,
                    "fully_registered": False,
                    "deactivated": bool(onboarding_state.get("deactivation")),
                    "onboarding_trail": _onboarding_trail(onboarding_state),
                },
            }
        return {"action": "get_user_status", "ok": False, "error": f"user not found in Cognito: {email}"}
    except Exception as exc:
        return {"action": "get_user_status", "ok": False, "error": f"{type(exc).__name__}: {exc}"}

    ses_attr = ses.get_identity_verification_attributes(Identities=[email]).get(
        "VerificationAttributes", {})
    ses_status = ses_attr.get(email, {}).get("VerificationStatus", "NotVerified")

    return {
        "action": "get_user_status",
        "ok": True,
        "user": {
            "email": email,
            "short": _short(email),
            "cognito_status": u.get("UserStatus"),
            "cognito_enabled": u.get("Enabled"),
            "ses_status": ses_status,
            "created":  u.get("UserCreateDate").isoformat() if u.get("UserCreateDate") else None,
            "modified": u.get("UserLastModifiedDate").isoformat() if u.get("UserLastModifiedDate") else None,
            "fully_registered": (u.get("UserStatus") == "CONFIRMED" and ses_status == "Success"),
            "deactivated": _is_deactivated(u, onboarding_state),
            "onboarding_trail": _onboarding_trail(onboarding_state),
        },
    }


def _action_login_history(email: str, days: int = 30) -> dict[str, Any]:
    """XYZ-PILOT-129/XYZ-PILOT-146: hybrid login history.

    Primary source is Cognito Advanced Security Features (ASF), enabled
    2026-05-27 ~10:25 CEST. ASF gives real per-user attribution that
    CloudTrail could not: every Cognito auth event is logged with username,
    event type, response, IP, device, location, and risk decision.

    The legacy CloudTrail view is still returned as historical context so
    pre-ASF activity is not hidden. CloudTrail hosted-UI events are pool-wide
    only; admin actions targeting the selected email remain user-specific.

    Returns:
        {
          "action": "login_history",
          "ok": True,
          "email": "...",
          "days": N,
          "auth_source": "cognito_asf_plus_cloudtrail_legacy",
          "events": [{event_id, event_time, event_type, event_response,
                      ip_address, city, country, device_family,
                      risk_decision}, ...],
          "by_day": [{"date":"2026-05-27","total":3,"pass":3,"fail":0,
                      "first":"08:14:11","last":"10:18:55"}, ...],
          "summary": {"total_events":N, "pass_count":M, "fail_count":K,
                      "active_days":D, "first_seen":ts, "last_seen":ts},
          "legacy_cloudtrail": {
              "pool_signins_by_day": [...],
              "pool_summary": {...},
              "admin_actions_for_user": [...]
          },
          "note": "..." (present when ASF history is shallow)
        }

    Falls back to a structured error if AdminListUserAuthEvents fails
    (e.g., ASF disabled, user-not-found, throttling). The legacy helper
    degrades independently so CloudTrail failures do not mask ASF results.
    """
    from datetime import datetime, timedelta, timezone

    if not _EMAIL_RE.match(email):
        return {"action": "login_history", "ok": False, "error": f"invalid email: {email!r}"}
    days = min(max(int(days or 30), 1), 90)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # ASF events are scoped per-user via username (= Cognito sub OR email
    # depending on alias config — our pool uses email as username so the
    # email itself is the right key).
    events_raw: list[dict[str, Any]] = []
    next_token: str | None = None
    while True:
        try:
            kwargs = {
                "UserPoolId": USER_POOL_ID,
                "Username": email,
                "MaxResults": 50,
            }
            if next_token:
                kwargs["NextToken"] = next_token
            resp = cognito.admin_list_user_auth_events(**kwargs)
        except cognito.exceptions.UserNotFoundException:
            return {"action": "login_history", "ok": False,
                    "error": f"user not found in Cognito: {email}"}
        except cognito.exceptions.UserPoolAddOnNotEnabledException:
            return {"action": "login_history", "ok": False,
                    "error": "Cognito Advanced Security Features (ASF) is not enabled — "
                             "set UserPoolTier=PLUS + AdvancedSecurityMode=AUDIT"}
        except Exception as exc:
            return {"action": "login_history", "ok": False,
                    "error": f"{type(exc).__name__}: {exc}"}
        events_raw.extend(resp.get("AuthEvents", []))
        next_token = resp.get("NextToken")
        if not next_token or len(events_raw) >= 500:
            break

    # Filter to window + flatten into a clean shape
    events: list[dict[str, Any]] = []
    for ev in events_raw:
        et = ev.get("CreationDate")
        if not et:
            continue
        ts = et if isinstance(et, datetime) else datetime.fromisoformat(str(et))
        ts = ts.astimezone(timezone.utc)
        if ts < since:
            continue
        ctx = ev.get("EventContextData") or {}
        risk = ev.get("EventRisk") or {}
        events.append({
            "event_id": ev.get("EventId"),
            "event_time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event_type": ev.get("EventType"),
            "event_response": ev.get("EventResponse"),
            "ip_address": ctx.get("IpAddress"),
            "city": ctx.get("City"),
            "country": ctx.get("Country"),
            "device_family": ctx.get("DeviceName"),
            "risk_decision": risk.get("RiskDecision"),
        })

    # Aggregate by day
    by_day_map: dict[str, dict[str, Any]] = {}
    for ev in events:
        date_key = ev["event_time"][:10]
        time_str = ev["event_time"][11:19]
        rec = by_day_map.setdefault(date_key,
            {"date": date_key, "total": 0, "pass": 0, "fail": 0, "first": time_str, "last": time_str})
        rec["total"] += 1
        if ev["event_response"] == "Pass":
            rec["pass"] += 1
        else:
            rec["fail"] += 1
        if time_str < rec["first"]:
            rec["first"] = time_str
        if time_str > rec["last"]:
            rec["last"] = time_str

    by_day = sorted(by_day_map.values(), key=lambda r: r["date"], reverse=True)
    pass_count = sum(1 for e in events if e["event_response"] == "Pass")
    fail_count = sum(1 for e in events if e["event_response"] != "Pass")
    first_seen = events[-1]["event_time"] if events else None
    last_seen = events[0]["event_time"] if events else None

    legacy = _action_login_history_legacy_cloudtrail(email, days)
    legacy_cloudtrail = None
    if legacy.get("ok"):
        legacy_cloudtrail = {
            "note": legacy.get("note"),
            "pool_signins_by_day": legacy.get("pool_signins_by_day", []),
            "pool_summary": legacy.get("pool_summary", {}),
            "admin_actions_for_user": legacy.get("admin_actions_for_user", []),
        }
    else:
        legacy_cloudtrail = {
            "ok": False,
            "error": legacy.get("error", "CloudTrail legacy lookup failed"),
        }

    note = None
    if not events:
        note = ("Cognito Advanced Security Features was enabled 2026-05-27 ~10:25 CEST. "
                "No ASF auth events are recorded yet for this user — ASF events accumulate "
                "going forward. Pre-ASF hosted-UI activity is still shown below as a "
                "pool-wide CloudTrail aggregate plus user-specific admin actions where "
                "available; CloudTrail cannot attribute hosted-UI sign-ins to an "
                "individual user before ASF.")

    return {
        "action": "login_history",
        "ok": True,
        "email": email,
        "days": days,
        "auth_source": "cognito_asf_plus_cloudtrail_legacy",
        "events": events[:50],  # cap UI list at 50 most recent
        "by_day": by_day,
        "summary": {
            "total_events": len(events),
            "pass_count": pass_count,
            "fail_count": fail_count,
            "active_days": len(by_day),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "window_start": since.date().isoformat(),
            "window_end": datetime.now(timezone.utc).date().isoformat(),
        },
        "legacy_cloudtrail": legacy_cloudtrail,
        **({"note": note} if note else {}),
    }


def _action_login_history_legacy_cloudtrail(email: str, days: int = 30) -> dict[str, Any]:
    """XYZ-PILOT-117 CloudTrail implementation kept as historical context.

    Cognito hosted-UI OAuth events are pool-wide in CloudTrail. Admin actions
    targeting a specific email remain useful for onboarding diagnostics.
    """
    from datetime import datetime, timedelta, timezone

    if not _EMAIL_RE.match(email):
        return {"action": "login_history", "ok": False, "error": f"invalid email: {email!r}"}
    days = min(max(int(days or 30), 1), 90)
    cloudtrail = boto3.client("cloudtrail", region_name=REGION)
    start_time = datetime.now(timezone.utc) - timedelta(days=days)

    # ── (1) Pool-wide hosted-UI sign-in event aggregate (no user filter) ──
    pool_events: list[dict[str, Any]] = []
    for event_name in ("Token_POST", "OAuth2_Authorize_GET"):
        next_token = None
        while True:
            kwargs = {
                "LookupAttributes": [{"AttributeKey": "EventName", "AttributeValue": event_name}],
                "StartTime": start_time,
                "MaxResults": 50,
            }
            if next_token:
                kwargs["NextToken"] = next_token
            try:
                resp = cloudtrail.lookup_events(**kwargs)
            except Exception as exc:
                return {"action": "login_history", "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            for ev in resp.get("Events", []):
                pool_events.append({
                    "event_name": ev.get("EventName"),
                    "event_time": ev.get("EventTime"),
                })
            next_token = resp.get("NextToken")
            if not next_token or len(pool_events) >= 2000:
                break

    pool_by_day: dict[str, dict[str, Any]] = {}
    for ev in pool_events:
        et = ev["event_time"]
        if not et:
            continue
        ts = et if isinstance(et, datetime) else datetime.fromisoformat(str(et))
        ts = ts.astimezone(timezone.utc)
        date_key = ts.date().isoformat()
        rec = pool_by_day.setdefault(date_key, {"date": date_key, "Token_POST": 0, "OAuth2_Authorize_GET": 0})
        rec[ev["event_name"]] = rec.get(ev["event_name"], 0) + 1
    pool_by_day_list = sorted(pool_by_day.values(), key=lambda r: r["date"], reverse=True)

    # ── (2) Admin actions targeting this specific email ──
    admin_events: list[dict[str, Any]] = []
    next_token = None
    while True:
        kwargs = {
            "LookupAttributes": [{"AttributeKey": "Username", "AttributeValue": email}],
            "StartTime": start_time,
            "MaxResults": 50,
        }
        if next_token:
            kwargs["NextToken"] = next_token
        try:
            resp = cloudtrail.lookup_events(**kwargs)
        except Exception as exc:
            return {"action": "login_history", "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        for ev in resp.get("Events", []):
            admin_events.append({
                "event_time": ev.get("EventTime").isoformat() if ev.get("EventTime") else None,
                "event_name": ev.get("EventName"),
                "username":   ev.get("Username"),
            })
        next_token = resp.get("NextToken")
        if not next_token or len(admin_events) >= 200:
            break

    pool_total = sum(r["Token_POST"] + r["OAuth2_Authorize_GET"] for r in pool_by_day_list)

    return {
        "action": "login_history",
        "ok": True,
        "email": email,
        "days": days,
        "note": (
            "Cognito hosted-UI OAuth events appear in CloudTrail with "
            "userIdentity.type=\"Unknown\" — per-user sign-in attribution "
            "is not possible without Cognito Advanced Security Features (paid) "
            "or custom CloudWatch logging from dashboard.html/chatbot. "
            "This MVP returns POOL-WIDE sign-in aggregates + admin actions "
            "targeting this email."
        ),
        "pool_signins_by_day": pool_by_day_list,
        "pool_summary": {
            "total_signin_events": pool_total,
            "active_days": len(pool_by_day_list),
            "window_start": start_time.date().isoformat(),
            "window_end":   datetime.now(timezone.utc).date().isoformat(),
        },
        "admin_actions_for_user": admin_events,
    }


def _action_invite_user(email: str, event: dict[str, Any]) -> dict[str, Any]:
    """Idempotently invite email to BOTH Cognito + SES.

    - Cognito AdminCreateUser with SUPPRESS=False to send the temp-password
      invite. If the user already exists, returns the existing status.
    - SES VerifyEmailIdentity to trigger the AWS verification email. Always
      safe to call (idempotent server-side; doesn't re-send if already
      Success — re-sends if currently Pending or absent).
    - Cognito group assignment follows the Operator Dashboard RBAC contract:
      OPS_ONLY can assign any group; XYZ_OPS can assign only XYZ_OPS/XYZ_BIZ.
    """
    if not _EMAIL_RE.match(email):
        return {"action": "invite_user", "ok": False, "error": f"invalid email: {email!r}"}

    ctx = _operator_context(event)
    if not ctx.get("can_invite"):
        return {
            "action": "invite_user",
            "ok": False,
            **ctx,
            "error": "only OPS_ONLY and XYZ_OPS users may invite new <PILOT_NAME> users",
        }

    try:
        requested_groups = _requested_invite_groups(event)
    except ValueError as exc:
        return {"action": "invite_user", "ok": False, **ctx, "error": str(exc)}
    allowed_groups = set(ctx.get("allowed_invite_groups") or [])
    denied = [g for g in requested_groups if g not in allowed_groups]
    if denied:
        return {
            "action": "invite_user",
            "ok": False,
            **ctx,
            "requested_groups": requested_groups,
            "error": "inviter is not allowed to assign group(s): " + ", ".join(denied),
        }

    result: dict[str, Any] = {
        **ctx,
        "action": "invite_user",
        "email": email,
        "groups": requested_groups,
        "steps": [],
    }

    # Step 1: Cognito
    try:
        cognito.admin_create_user(
            UserPoolId=USER_POOL_ID,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
        result["steps"].append({"step": "cognito_invite", "ok": True, "detail": "created — temp password email sent by Cognito"})
    except cognito.exceptions.UsernameExistsException:
        # Already exists — fetch status so the dashboard can report
        try:
            existing = cognito.admin_get_user(UserPoolId=USER_POOL_ID, Username=email)
            result["steps"].append({
                "step": "cognito_invite", "ok": True, "skipped": True,
                "detail": f"already exists — status={existing.get('UserStatus')}",
            })
        except Exception as exc:
            result["steps"].append({"step": "cognito_invite", "ok": False, "error": str(exc)})
    except Exception as exc:
        result["steps"].append({"step": "cognito_invite", "ok": False, "error": str(exc)})

    # Step 1b: Cognito group assignment. Keep this explicit so XYZ_BIZ users
    # get chatbot/data access without Operator Dashboard access by default.
    for group_name in requested_groups:
        try:
            cognito.admin_add_user_to_group(
                UserPoolId=USER_POOL_ID,
                Username=email,
                GroupName=group_name,
            )
            result["steps"].append({"step": f"group:{group_name}", "ok": True, "detail": "assigned"})
        except Exception as exc:  # noqa: BLE001 - surface per-group assignment failures.
            result["steps"].append({"step": f"group:{group_name}", "ok": False, "error": str(exc)})

    # Step 2: SES verify-email-identity
    try:
        ses.verify_email_identity(EmailAddress=email)
        result["steps"].append({"step": "ses_verify", "ok": True, "detail": "verification email sent (or re-sent if pending)"})
    except Exception as exc:
        result["steps"].append({"step": "ses_verify", "ok": False, "error": str(exc)})

    result["ok"] = all(s.get("ok", False) for s in result["steps"])
    return result


# ─── XYZ-PILOT-127: GitHub Repository Commit Retriever ─────────────────────────
# GitHub-backed actions use the PAT stored in Secrets Manager, not a plaintext
# Lambda environment value. SSM Parameter Store remains a temporary rollback
# fallback from XYZ-PILOT-259. Env vars carry only secret/parameter identifiers.

GITHUB_REPO = os.environ.get("GITHUB_REPO", "<GITHUB_OWNER>/<PROJECT_SLUG>")
GITHUB_TOKEN_SECRET_ID = os.environ.get("GITHUB_TOKEN_SECRET_ID", "xyz-pilot/github/service-token")
GITHUB_TOKEN_PARAM = os.environ.get("GITHUB_TOKEN_PARAM", "/xyz-pilot/github/service-token")
secretsmanager = boto3.client("secretsmanager", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)
_GITHUB_TOKEN_CACHE: str | None = None
_GITHUB_TOKEN_WARNED: set[str] = set()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_SERVICE_PROBE_OPENER = urllib.request.build_opener(_NoRedirect)
_SERVICE_PROBES = (
    {
        "id": "qbr",
        "name": "Streamlit QBR",
        "url": "https://<PROJECT_SLUG>-qbr.<PILOT_DOMAIN>/_stcore/health",
        "method": "GET",
        "ok_statuses": {200},
        "body_equals": "ok",
    },
    {
        "id": "grafana",
        "name": "Grafana",
        "url": "https://<PROJECT_SLUG>-qbr.<PILOT_DOMAIN>/_stcore/health",
        "method": "GET",
        "ok_statuses": {200},
    },
    {
        "id": "mcp",
        "name": "Exasol MCP (OAuth-gated)",
        "url": "https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp",
        "method": "HEAD",
        "ok_statuses": {200, 204, 301, 302, 401, 403},
    },
    {
        "id": "chatbot",
        "name": "<PILOT_NAME> Chatbot",
        "url": "https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp",
        "method": "HEAD",
        "ok_statuses": {200, 204, 301, 302, 401, 403},
    },
)


def _probe_service(spec: dict[str, Any], timeout: int) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    req = urllib.request.Request(
        spec["url"],
        method=spec.get("method", "GET"),
        headers={
            "Accept": "*/*",
            "User-Agent": "xyz-pilot-service-probe/1.0",
        },
    )
    code: int | None = None
    body = ""
    note = ""
    try:
        with _SERVICE_PROBE_OPENER.open(req, timeout=timeout) as resp:
            code = int(getattr(resp, "status", resp.getcode()))
            if spec.get("method", "GET") != "HEAD":
                body = resp.read(512).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        code = int(exc.code)
        if spec.get("method", "GET") != "HEAD":
            body = exc.read(512).decode("utf-8", errors="replace")
        if 300 <= code < 400:
            note = "redirect"
    except Exception as exc:  # noqa: BLE001 - probe failures are reported data.
        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return {
            "id": spec["id"],
            "name": spec["name"],
            "url": spec["url"],
            "method": spec.get("method", "GET"),
            "ok": False,
            "status": "down",
            "code": None,
            "latency_ms": elapsed_ms,
            "checked_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "note": f"{type(exc).__name__}: {str(exc)[:120]}",
        }

    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    ok = code in spec["ok_statuses"]
    expected_body = spec.get("body_equals")
    if expected_body is not None:
        ok = ok and body.strip().lower() == expected_body
        if not ok and not note:
            note = "unexpected health body"
    if not note:
        note = f"HTTP {code}"
    return {
        "id": spec["id"],
        "name": spec["name"],
        "url": spec["url"],
        "method": spec.get("method", "GET"),
        "ok": ok,
        "status": "up" if ok else "down",
        "code": code,
        "latency_ms": elapsed_ms,
        "checked_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "note": note,
    }


def _action_service_probe(event: dict[str, Any]) -> dict[str, Any]:
    raw_timeout = event.get("timeout_seconds", 5)
    try:
        timeout = max(1, min(10, int(raw_timeout)))
    except (TypeError, ValueError):
        timeout = 5
    requested = {
        str(item).strip()
        for item in event.get("ids", [])
        if str(item).strip()
    }
    specs = [s for s in _SERVICE_PROBES if not requested or s["id"] in requested]
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    probes = [_probe_service(spec, timeout) for spec in specs]
    return {
        "action": "service_probe",
        "ok": True,
        "checked_at_utc": checked_at,
        "timeout_seconds": timeout,
        "probes": probes,
    }


def _action_warmup_progress(event: dict[str, Any]) -> dict[str, Any]:
    """XYZ-PILOT-291: return the warm-up progress JSON the EC2 publishes to
    s3://<status-bucket>/warmup/progress.json, so the Operator Dashboard can
    stream warm-up state into its log in real time. Degrades to available=false
    when the object is absent (env stopped / not yet warming) or unreadable."""
    try:
        obj = s3.get_object(Bucket=ONBOARDING_STATE_BUCKET, Key=WARMUP_PROGRESS_KEY)
        progress = json.loads(obj["Body"].read().decode("utf-8"))
        return {"action": "warmup_progress", "ok": True, "available": True,
                "progress": progress if isinstance(progress, dict) else {}}
    except Exception as exc:  # noqa: BLE001 — absence is a normal state (env stopped/not warming)
        return {"action": "warmup_progress", "ok": True, "available": False,
                "note": type(exc).__name__}


_LICENSE_EVIDENCE_ALLOWED_KEYS = {
    "published_at_utc",
    "operation",
    "operator",
    "node",
    "play_id",
    "license_id",
    "company_name",
    "distributor",
    "expiration_date",
    "max_session_limit",
    "max_db_mem_size_in_gb",
    "max_db_raw_data_size_in_gb",
    "max_nodes_per_cluster",
    "max_num_clusters",
    "license_run_check",
    "db_state",
    "replacement_license_basename",
    "replacement_license_bytes",
    "replacement_license_sha256",
    "backup_license_basename",
    "backup_license_sha256",
    "ssm_command_id",
    "git_commit",
    "notes",
}


def _safe_license_evidence(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    safe: dict[str, Any] = {}
    for key in _LICENSE_EVIDENCE_ALLOWED_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = value
        elif isinstance(value, list):
            safe[key] = [str(item)[:160] for item in value[:10]]
        else:
            safe[key] = str(value)[:240]
    return safe


def _action_license_evidence(event: dict[str, Any]) -> dict[str, Any]:
    """Return the latest non-secret Exasol license-operation evidence.

    The signed .exasol_license body is never stored in this S3 object. It
    carries only ConfD summary fields, file hashes, command ids, and timestamps
    so operators can see license rotations in the dashboard log.
    """
    try:
        obj = s3.get_object(Bucket=ONBOARDING_STATE_BUCKET, Key=LICENSE_EVIDENCE_KEY)
        evidence = json.loads(obj["Body"].read().decode("utf-8"))
        return {
            "action": "license_evidence",
            "ok": True,
            "available": True,
            "bucket": ONBOARDING_STATE_BUCKET,
            "key": LICENSE_EVIDENCE_KEY,
            "last_modified_utc": obj.get("LastModified").astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if obj.get("LastModified") else "",
            "evidence": _safe_license_evidence(evidence),
        }
    except Exception as exc:  # noqa: BLE001 - absence is normal before first rotation.
        return {
            "action": "license_evidence",
            "ok": True,
            "available": False,
            "bucket": ONBOARDING_STATE_BUCKET,
            "key": LICENSE_EVIDENCE_KEY,
            "note": type(exc).__name__,
        }


def _warn_github_token_once(key: str, message: str, *args: Any) -> None:
    if key in _GITHUB_TOKEN_WARNED:
        return
    log.warning(message, *args)
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
    except Exception as exc:  # noqa: BLE001 - fallback to SSM below.
        _warn_github_token_once(
            "secret_lookup",
            "GitHub token Secrets Manager lookup failed for %s: %s; falling back to SSM",
            GITHUB_TOKEN_SECRET_ID,
            type(exc).__name__,
        )
        return ""
    if not value:
        _warn_github_token_once(
            "secret_empty",
            "GitHub token Secrets Manager secret %s returned an empty value; falling back to SSM",
            GITHUB_TOKEN_SECRET_ID,
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
    except Exception as exc:  # noqa: BLE001 - caller surfaces GitHub function failure.
        _warn_github_token_once(
            "ssm_lookup",
            "GitHub token SSM lookup failed for %s: %s",
            GITHUB_TOKEN_PARAM,
            type(exc).__name__,
        )
        return ""
    if not value:
        _warn_github_token_once(
            "ssm_empty",
            "GitHub token SSM parameter %s returned an empty value",
            GITHUB_TOKEN_PARAM,
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


def _gh_request(method: str, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
    """Authenticated GitHub REST call. Returns parsed JSON or empty dict."""
    token = _github_token()
    if not token:
        raise RuntimeError("GitHub token unavailable from Secrets Manager or SSM fallback — Lambda needs the PAT")
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"https://api.github.com/{path.lstrip('/')}" + (f"?{qs}" if qs else "")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "xyz-pilot-user-mgmt",
        "X-GitHub-Api-Version": "2022-11-28",
    }, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as r:
        payload = r.read()
        if not payload:
            return {}
        return json.loads(payload.decode("utf-8"))


def _gh_get(path: str, **params: Any) -> Any:
    """Authenticated GitHub REST GET. Returns parsed JSON. Raises on HTTP error."""
    return _gh_request("GET", path, None, **params)


def _gh_contents_text(content: dict) -> str:
    """Decode a GitHub Contents-API response body, falling back to the pre-signed
    download_url for files > 1 MB.

    The Contents API returns encoding="none" + content="" once a file exceeds
    1 MB. The tracker (docs/XYZ_PILOT_ISSUE_TRACKER.md) crossed that threshold on
    2026-06-08 (~1.13 MB), so a direct base64 decode yields "" and silently
    blinds the caller (e.g. duplicate-detection sees zero tickets). Mirrors the
    XYZ-PILOT-237 / XYZ-PILOT-245 fixes in daily_status.py.
    """
    if content.get("encoding") == "base64" and content.get("content"):
        return base64.b64decode(content["content"]).decode("utf-8", errors="replace")
    download_url = content.get("download_url")
    if not download_url:
        raise RuntimeError(
            f"GitHub Contents API: empty content + no download_url "
            f"(size={content.get('size')}, encoding={content.get('encoding')})")
    with urllib.request.urlopen(
        urllib.request.Request(download_url, headers={"User-Agent": "xyz-pilot-user-mgmt/1.0"}),
        timeout=30,
    ) as raw:
        return raw.read().decode("utf-8", errors="replace")


def _gh_put(path: str, body: dict[str, Any]) -> Any:
    return _gh_request("PUT", path, body)


def _gh_post(path: str, body: dict[str, Any]) -> Any:
    return _gh_request("POST", path, body)


def _github_http_error(exc: urllib.error.HTTPError) -> str:
    """Compact GitHub API error without exposing auth material."""
    message = ""
    try:
        raw = exc.read().decode("utf-8", errors="replace")
        payload = json.loads(raw)
        if isinstance(payload, dict):
            message = str(payload.get("message") or "").strip()
    except Exception:  # noqa: BLE001 - diagnostics must not mask original error.
        message = ""
    suffix = f" — {message}" if message else ""
    return f"GitHub API {exc.code}: {exc.reason}{suffix}"


def _action_github_collaborators() -> dict[str, Any]:
    """List GitHub collaborators on the repo + their permissions.
    Optionally augment with a `commit_count_30d` if cheap enough."""
    try:
        raw = _gh_get(f"repos/{GITHUB_REPO}/collaborators", per_page=100)
    except urllib.error.HTTPError as exc:
        return {"action": "github_collaborators", "ok": False,
                "error": f"GitHub API {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"action": "github_collaborators", "ok": False,
                "error": f"{type(exc).__name__}: {exc}"}
    out = []
    for c in raw:
        perms = c.get("permissions") or {}
        # admin > maintain > push > triage > pull
        role = "admin" if perms.get("admin") else \
               "maintain" if perms.get("maintain") else \
               "push" if perms.get("push") else \
               "triage" if perms.get("triage") else \
               "pull" if perms.get("pull") else "?"
        out.append({
            "login": c.get("login"),
            "id": c.get("id"),
            "role": role,
            "avatar_url": c.get("avatar_url"),
            "html_url": c.get("html_url"),
            "site_admin": c.get("site_admin", False),
        })
    return {"action": "github_collaborators", "ok": True,
            "repo": GITHUB_REPO, "count": len(out), "collaborators": out}


def _commit_to_row(c: dict[str, Any], with_files: bool = True) -> dict[str, Any]:
    """Normalise a /commits response item into a flat row. Optionally fetch
    /commits/{sha} for the files-changed list + +/- line stats (expensive —
    one extra API call per commit, so cap callers at <=20 commits)."""
    sha = c.get("sha", "")
    co_author = (c.get("commit") or {}).get("author") or {}
    co_message = (c.get("commit") or {}).get("message", "") or ""
    row: dict[str, Any] = {
        "sha": sha[:7],
        "full_sha": sha,
        "author_name": co_author.get("name"),
        "author_email": co_author.get("email"),
        "date": co_author.get("date"),
        "subject": co_message.split("\n", 1)[0],
        "url": c.get("html_url"),
    }
    if with_files and sha:
        try:
            detail = _gh_get(f"repos/{GITHUB_REPO}/commits/{sha}")
            stats = detail.get("stats") or {}
            row["additions"] = stats.get("additions", 0)
            row["deletions"] = stats.get("deletions", 0)
            row["total_changes"] = stats.get("total", 0)
            row["files"] = [
                {"filename": f.get("filename"), "status": f.get("status"),
                 "additions": f.get("additions", 0), "deletions": f.get("deletions", 0)}
                for f in (detail.get("files") or [])[:25]
            ]
        except Exception as exc:
            row["files"] = []
            row["files_error"] = str(exc)
    return row


def _action_github_commits(author: str | None, days: int = 30, with_files: bool = True) -> dict[str, Any]:
    """List commits in the last `days` days, optionally filtered by GitHub author
    (login or commit-author email). Returns flat per-commit rows with files."""
    days = max(min(int(days or 30), 90), 1)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    params: dict[str, Any] = {"since": since, "per_page": 50}
    if author:
        params["author"] = author.strip()
    try:
        raw = _gh_get(f"repos/{GITHUB_REPO}/commits", **params)
    except urllib.error.HTTPError as exc:
        return {"action": "github_commits", "ok": False,
                "error": f"GitHub API {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"action": "github_commits", "ok": False,
                "error": f"{type(exc).__name__}: {exc}"}
    # Cap at 20 to avoid GitHub rate-limit headaches + Lambda timeout
    commits = [_commit_to_row(c, with_files=with_files) for c in raw[:20]]
    return {"action": "github_commits", "ok": True, "repo": GITHUB_REPO,
            "author": author, "days": days, "count": len(commits), "commits": commits}


def _action_github_commits_on_date(date_str: str, with_files: bool = True) -> dict[str, Any]:
    """List ALL commits on a single UTC calendar date (YYYY-MM-DD)."""
    try:
        d = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc, hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        return {"action": "github_commits_on_date", "ok": False,
                "error": f"invalid date: {date_str!r}; want YYYY-MM-DD"}
    since = d.isoformat()
    until = (d + timedelta(days=1)).isoformat()
    try:
        raw = _gh_get(f"repos/{GITHUB_REPO}/commits", since=since, until=until, per_page=100)
    except urllib.error.HTTPError as exc:
        return {"action": "github_commits_on_date", "ok": False,
                "error": f"GitHub API {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"action": "github_commits_on_date", "ok": False,
                "error": f"{type(exc).__name__}: {exc}"}
    commits = [_commit_to_row(c, with_files=with_files) for c in raw[:25]]
    return {"action": "github_commits_on_date", "ok": True, "repo": GITHUB_REPO,
            "date": d.date().isoformat(), "count": len(commits), "commits": commits}


# ─── XYZ-PILOT-215: Operator Dashboard XYZ_MASTER approval queue ─────────────

def _bearer_token(event: dict[str, Any]) -> str | None:
    headers = (event or {}).get("headers") or {}
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if isinstance(auth, str) and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


def _operator_principal_from_token(event: dict[str, Any]) -> dict[str, Any]:
    token = _bearer_token(event)
    if not token:
        return {"email": None, "username": None, "groups": [], "token_valid": False}
    try:
        resp = cognito.get_user(AccessToken=token)
        email = None
        for attr in resp.get("UserAttributes", []):
            if attr.get("Name") == "email":
                email = str(attr.get("Value", "")).strip().lower() or None
                break
        username = str(resp.get("Username") or email or "").strip()
        groups = _groups_for_principal(username=username, email=email)
        return {
            "email": email,
            "username": username or None,
            "groups": groups,
            "token_valid": True,
        }
    except Exception as exc:  # noqa: BLE001 - returns non-elevated on token failure.
        log.warning("operator token validation failed: %s", type(exc).__name__)
    return {"email": None, "username": None, "groups": [], "token_valid": False}


def _groups_for_principal(username: str | None, email: str | None) -> list[str]:
    """Return Cognito group names for the authenticated principal.

    Cognito pools can use a UUID/sub-like username while email is configured as
    an alias. Try the token's Username first and the email second for rollout
    resilience.
    """
    names = [n for n in (username, email) if n]
    seen: set[str] = set()
    for name in names:
        try:
            resp = cognito.admin_list_groups_for_user(UserPoolId=USER_POOL_ID, Username=name)
            for group in resp.get("Groups", []):
                group_name = str(group.get("GroupName") or "").strip()
                if group_name:
                    seen.add(group_name)
            if seen:
                break
        except Exception as exc:  # noqa: BLE001 - fall back to next identifier.
            log.warning("operator group lookup failed for %s: %s", name, type(exc).__name__)
    return sorted(seen)


def _operator_context(event: dict[str, Any]) -> dict[str, Any]:
    principal = _operator_principal_from_token(event)
    email = principal.get("email")
    groups = set(principal.get("groups") or [])
    full_operator = bool((email and email in XYZ_MASTER_APPROVER_EMAILS) or groups.intersection(FULL_OPERATOR_GROUPS))
    dashboard_access = bool(full_operator or groups.intersection(DASHBOARD_ACCESS_GROUPS))
    secret_panel_access = bool(full_operator or groups.intersection(SECRET_PANEL_GROUPS))
    can_invite = bool(full_operator or groups.intersection(INVITER_GROUPS))
    if full_operator:
        allowed_invite_groups = list(ALL_COGNITO_GROUPS)
    elif GROUP_XYZ_OPS in groups:
        allowed_invite_groups = [GROUP_XYZ_OPS, GROUP_XYZ_BIZ]
    else:
        allowed_invite_groups = []
    return {
        "email": email,
        "username": principal.get("username"),
        "groups": sorted(groups),
        "token_valid": bool(principal.get("token_valid")),
        "dashboard_access": dashboard_access,
        "full_operator": full_operator,
        "elevated_operator": full_operator,
        "can_invite": can_invite,
        "allowed_invite_groups": allowed_invite_groups,
        "default_invite_groups": list(DEFAULT_INVITE_GROUPS),
        "visible_panels": {
            "start_stop": dashboard_access,
            "service_password_retrieval": secret_panel_access,
            "new_user_onboarding": dashboard_access,
            "github_commit_retriever": full_operator,
            "xyz_master_backlog": full_operator,
            "swarm_tester": full_operator,
        },
        "allowed_operator_count": len(XYZ_MASTER_APPROVER_EMAILS),
    }


def _requested_invite_groups(event: dict[str, Any]) -> list[str]:
    raw = event.get("groups", event.get("target_groups", DEFAULT_INVITE_GROUPS))
    if isinstance(raw, str):
        raw_values = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, list):
        raw_values = [str(item).strip() for item in raw]
    else:
        raw_values = list(DEFAULT_INVITE_GROUPS)
    out: list[str] = []
    for group_name in raw_values:
        if not group_name:
            continue
        if group_name not in ALL_COGNITO_GROUPS:
            raise ValueError(f"unknown Cognito group: {group_name}")
        if group_name not in out:
            out.append(group_name)
    if len(out) > 1:
        raise ValueError("exactly one target Cognito group must be selected")
    return out or list(DEFAULT_INVITE_GROUPS)


def _require_dashboard_user(event: dict[str, Any], action: str) -> dict[str, Any] | None:
    ctx = _operator_context(event)
    if not ctx.get("dashboard_access"):
        return {
            "action": action,
            "ok": False,
            **ctx,
            "error": "current Cognito user is not authorized for the <PILOT_NAME> Operator Dashboard",
        }
    return None


def _require_full_operator_result(event: dict[str, Any], action: str) -> dict[str, Any] | None:
    ctx = _operator_context(event)
    if not ctx.get("full_operator"):
        return {
            "action": action,
            "ok": False,
            **ctx,
            "error": "only OPS_ONLY / elevated <PILOT_NAME> operators may use this Operator Dashboard panel",
        }
    return None


def _require_xyz_master_operator(event: dict[str, Any]) -> str:
    ctx = _operator_context(event)
    if not ctx["full_operator"]:
        raise PermissionError("only elevated <PILOT_NAME> operators may approve or deny XYZ_MASTER candidates")
    return str(ctx["email"])


def _gh_blob_text(sha: str) -> str:
    blob = _gh_get(f"repos/{GITHUB_REPO}/git/blobs/{sha}")
    content = blob.get("content", "")
    if blob.get("encoding") == "base64":
        return base64.b64decode(content).decode("utf-8", errors="replace")
    return str(content)


def _candidate_blocks(src: str) -> list[dict[str, Any]]:
    matches = list(_CANDIDATE_HEADING_RE.finditer(src))
    if not matches:
        sep = src.find("\n---\n")
        preamble = src[:sep] if sep != -1 else src
        body = src[sep + len("\n---\n"):].strip() if sep != -1 else src.strip()
        if not _has_actionable_candidate_fields(body):
            return []
        first = re.search(r"^#\s+(.+)$", preamble, re.MULTILINE)
        return [{"index": 1, "title": first.group(1).strip() if first else "(implicit candidate)", "body": body}]
    blocks = []
    for idx, match in enumerate(matches, start=1):
        end = matches[idx].start() if idx < len(matches) else len(src)
        blocks.append({
            "index": idx,
            "title": match.group(1).strip(" -:").strip() or f"candidate {idx}",
            "body": src[match.end():end].strip(),
        })
    return blocks


def _has_actionable_candidate_fields(body: str) -> bool:
    return bool(
        _ACTIONABLE_CANDIDATE_FIELD_RE.search(body)
        or "### Top-table row" in body
        or "### Full-register detail section" in body
    )


def _candidate_field(body: str, name: str) -> str | None:
    m = re.search(rf"^-\s+\*\*{re.escape(name)}:\*\*\s*`?([^`\n]+)`?\s*$", body, re.MULTILINE)
    return m.group(1).strip() if m else None


def _candidate_any_field(body: str, *names: str, default: str = "") -> str:
    for name in names:
        value = _candidate_field(body, name)
        if value:
            return value
    return default


def _candidate_xyz_id(body: str) -> str | None:
    # XYZ-PILOT-259: accept "Existing issue" too — OpenAI Codex candidates use that
    # label, and without it the existing-ID lookup returned None, so a Resolved
    # status-transition was mis-classified as a new-ticket and the direct-main
    # gate wrongly demanded PR mode for it. Keep the canonical labels first.
    for name in ("Existing ID", "Existing issue", "Existing ticket", "Recommended ID", "Tracker ID"):
        value = _candidate_field(body, name)
        if value:
            match = _XYZ_ID_RE.search(value.upper())
            if match:
                return match.group(0)
    return None


def _normalize_tracker_title(title: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", title)
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def _candidate_detail_title(body: str) -> str | None:
    match = _CANDIDATE_DETAIL_TITLE_RE.search(body)
    return match.group(1).strip() if match else None


def _tracker_index() -> tuple[set[str], dict[str, str]]:
    content = _gh_get(
        f"repos/{GITHUB_REPO}/contents/{urllib.parse.quote('docs/XYZ_PILOT_ISSUE_TRACKER.md', safe='/')}",
        ref="main",
    )
    src = _gh_contents_text(content)  # XYZ-PILOT-245 sibling: >1MB download_url fallback
    titles: set[str] = set()
    statuses: dict[str, str] = {}
    matches = list(_TRACKER_HEADING_RE.finditer(src))
    for idx, match in enumerate(matches):
        issue_id = match.group(1)
        titles.add(_normalize_tracker_title(match.group(2)))
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(src)
        section = src[match.start():end]
        status_match = _TRACKER_STATUS_RE.search(section)
        if status_match:
            statuses[issue_id] = status_match.group(1).strip().lower()
    return titles, statuses


def _tracker_source() -> str:
    content = _gh_get(
        f"repos/{GITHUB_REPO}/contents/{urllib.parse.quote('docs/XYZ_PILOT_ISSUE_TRACKER.md', safe='/')}",
        ref="main",
    )
    return _gh_contents_text(content)  # XYZ-PILOT-245 sibling: >1MB download_url fallback


def _clean_tracker_cell(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^~~|~~$", "", text)
    text = re.sub(r"^\*\*|\*\*$", "", text)
    return text.strip()


def _open_priority_issues_from_active_table(src: str) -> list[dict[str, str]]:
    active = src.split("## Full register", 1)[0]
    issues: list[dict[str, str]] = []
    for line in active.splitlines():
        if not line.startswith("| [XYZ-PILOT-"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        id_match = _XYZ_ID_RE.search(cells[0])
        if not id_match:
            continue
        severity = _clean_tracker_cell(cells[1]).lower()
        if severity not in _OPEN_PANEL_CLASSIFICATIONS:
            continue
        issues.append({
            "id": id_match.group(0),
            "title": _clean_tracker_cell(cells[3]),
            "status": "Open",
            "severity": severity,
            "area": _clean_tracker_cell(cells[2]),
        })
    return issues


def _open_priority_issues_from_tracker(src: str) -> list[dict[str, str]]:
    active_issues = _open_priority_issues_from_active_table(src)
    if active_issues:
        return active_issues

    issues: list[dict[str, str]] = []
    matches = list(_TRACKER_HEADING_RE.finditer(src))
    for idx, match in enumerate(matches):
        issue_id = match.group(1)
        title = match.group(2).strip()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(src)
        section = src[match.start():end]
        status_match = _TRACKER_STATUS_RE.search(section)
        severity_match = _TRACKER_SEVERITY_RE.search(section)
        if not status_match or not severity_match:
            continue
        status = status_match.group(1).strip()
        severity = severity_match.group(1).strip().lower()
        if status.lower() != "open" or severity not in _OPEN_PANEL_CLASSIFICATIONS:
            continue
        area_match = _TRACKER_AREA_RE.search(section)
        issues.append({
            "id": issue_id,
            "title": title,
            "status": status,
            "severity": severity,
            "area": area_match.group(1).strip() if area_match else "",
        })
    return issues


def _action_xyz_master_open_issues(event: dict[str, Any]) -> dict[str, Any]:
    blocked = _require_full_operator_result(event, "xyz_master_open_issues")
    if blocked:
        return blocked
    ctx = _operator_context(event)
    try:
        issues = _open_priority_issues_from_tracker(_tracker_source())
        return {
            "action": "xyz_master_open_issues",
            "ok": True,
            **ctx,
            "count": len(issues),
            "issues": issues,
        }
    except urllib.error.HTTPError as exc:
        return {"action": "xyz_master_open_issues", "ok": False, **ctx,
                "error": f"GitHub API {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"action": "xyz_master_open_issues", "ok": False, **ctx,
                "error": f"{type(exc).__name__}: {exc}"}


def _action_xyz_master_operator_context(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "xyz_master_operator_context",
        "ok": True,
        **_operator_context(event),
    }


def _candidate_status(body: str) -> str:
    return _candidate_any_field(
        body,
        "Requested status",
        "Recommended status",
        "Proposed status",
        "Status",
        default="Open",
    )


def _candidate_classification(body: str) -> str:
    raw = _candidate_any_field(
        body,
        "Requested severity",
        "Requested classification",
        "Recommended classification",
        "Proposed severity",
        "Recommended severity",
        "Severity",
        default="minor",
    ).lower()
    match = re.search(r"\b(critical|major|minor|feature|external|hygiene|status)\b", raw)
    return match.group(1) if match else "minor"


def _candidate_already_tracked(
    title: str,
    body: str,
    tracker_titles: set[str],
    tracker_statuses: dict[str, str],
) -> str | None:
    issue_id = _candidate_xyz_id(body)
    if not issue_id and _normalize_tracker_title(title) in tracker_titles:
        return "candidate title already exists in tracker"

    detail_title = _candidate_detail_title(body)
    if not issue_id and detail_title and _normalize_tracker_title(detail_title) in tracker_titles:
        return "detail title already exists in tracker"

    status = _candidate_status(body).lower()
    if issue_id and any(status.startswith(prefix) for prefix in _TERMINAL_STATUSES):
        tracker_status = tracker_statuses.get(issue_id, "")
        if any(tracker_status.startswith(prefix) for prefix in _TERMINAL_STATUSES):
            return f"{issue_id} is already terminal in tracker"
    return None


def _candidate_mutation_kind(body: str) -> str:
    if _candidate_xyz_id(body) and re.search(r"\b(Resolved|Wontfix)\b", _candidate_status(body), re.IGNORECASE):
        return "status-transition"
    if _candidate_xyz_id(body) and _candidate_any_field(
        body,
        "Requested severity",
        "Requested classification",
        "Recommended classification",
    ):
        return "severity-transition"
    return "new-ticket"


def _candidate_template_kind(body: str) -> str:
    if "### Top-table row" in body and "### Full-register detail section" in body:
        return "full"
    return "normalized-lightweight"


_PRUNE_HOUSEKEEPING_RE = re.compile(
    r"\bPrune top-table row XYZ-PILOT-\d{3}\b|Remove the single struck-through",
    re.IGNORECASE,
)


def _candidate_housekeeping_reason(source: str, title: str, body: str) -> str | None:
    if source == "XYZ_PILOT_DAILY_STATUS" and _PRUNE_HOUSEKEEPING_RE.search(f"{title}\n{body}"):
        return "daily-status top-table prune housekeeping"
    return None


def _candidate_path(run_id: str, source: str) -> str:
    if not run_id or "/" in run_id or not source or "/" in source:
        raise ValueError("invalid run_id/source")
    return f"tests/logs/runs/{run_id}/{source}/issue-candidates.md"


def _candidate_run_timestamp(run_id: str) -> datetime | None:
    match = _RUN_ID_DASHED_TS_RE.search(run_id)
    if match:
        return datetime.strptime(match.group(1), "%Y-%m-%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    match = _RUN_ID_COMPACT_TS_RE.search(run_id)
    if match:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return None


def _candidate_run_sort_key(meta: dict[str, Any]) -> tuple[bool, datetime, str, str]:
    # Run IDs are not uniformly ISO-prefixed. Named runs such as
    # "uat-<AWS_REGION>-2026-06-17T100705Z" sort ahead of "2026-..." as plain
    # strings and can starve fresh close-out candidates from the scan window.
    ts = _candidate_run_timestamp(str(meta.get("run_id", "")))
    return (
        ts is not None,
        ts or datetime.min.replace(tzinfo=timezone.utc),
        str(meta.get("run_id", "")),
        str(meta.get("source", "")),
    )


def _candidate_run_is_historical(run_id: str) -> bool:
    cutoff_ts = _candidate_run_timestamp(XYZ_MASTER_BACKLOG_MIN_RUN_ID)
    run_ts = _candidate_run_timestamp(run_id)
    if cutoff_ts is not None and run_ts is not None:
        return run_ts < cutoff_ts
    return run_id < XYZ_MASTER_BACKLOG_MIN_RUN_ID


def _list_candidate_tree() -> tuple[dict[str, dict[str, Any]], set[tuple[str, str, int]]]:
    tree = _gh_get(f"repos/{GITHUB_REPO}/git/trees/main", recursive="1").get("tree", [])
    candidate_files: dict[str, dict[str, Any]] = {}
    decided: set[tuple[str, str, int]] = set()
    for item in tree:
        path = item.get("path", "")
        if item.get("type") != "blob":
            continue
        if path.startswith("tests/logs/runs/") and path.endswith("/issue-candidates.md"):
            parts = path.split("/")
            if len(parts) == 6:
                candidate_files[path] = {
                    "path": path,
                    "run_id": parts[3],
                    "source": parts[4],
                    "sha": item.get("sha"),
                }
        else:
            match = _OUTCOME_RE.match(path)
            if match:
                decided.add((match.group(1), match.group(2), int(match.group(3))))
    return candidate_files, decided


def _action_xyz_master_candidates(event: dict[str, Any]) -> dict[str, Any]:
    blocked = _require_full_operator_result(event, "xyz_master_candidates")
    if blocked:
        return blocked
    ctx = _operator_context(event)
    try:
        files, decided = _list_candidate_tree()
        tracker_titles, tracker_statuses = _tracker_index()
        pending: list[dict[str, Any]] = []
        suppressed = {
            "decided": 0,
            "historical": 0,
            "already_tracked": 0,
            "housekeeping": 0,
            "empty": 0,
            "burst": 0,
            "scan_limit": 0,
        }
        burst_files: list[dict[str, Any]] = []
        scanned_files = 0
        for meta in sorted(files.values(), key=_candidate_run_sort_key, reverse=True):
            if not meta.get("sha"):
                continue
            if _candidate_run_is_historical(meta["run_id"]):
                suppressed["historical"] += 1
                continue
            if scanned_files >= XYZ_MASTER_CANDIDATE_FILE_SCAN_LIMIT:
                suppressed["scan_limit"] += 1
                continue
            scanned_files += 1
            src = _gh_blob_text(str(meta["sha"]))
            blocks = _candidate_blocks(src)
            if not blocks:
                suppressed["empty"] += 1
                continue
            file_pending: list[dict[str, Any]] = []
            for block in blocks:
                key = (meta["run_id"], meta["source"], int(block["index"]))
                if key in decided:
                    suppressed["decided"] += 1
                    continue
                body = block["body"]
                if _candidate_housekeeping_reason(meta["source"], block["title"], body):
                    suppressed["housekeeping"] += 1
                    continue
                already_tracked = _candidate_already_tracked(block["title"], body, tracker_titles, tracker_statuses)
                if already_tracked:
                    suppressed["already_tracked"] += 1
                    continue
                file_pending.append({
                    "run_id": meta["run_id"],
                    "source": meta["source"],
                    "candidate_index": block["index"],
                    "mutation_kind": _candidate_mutation_kind(body),
                    "template_kind": _candidate_template_kind(body),
                    "path": meta["path"],
                    "title": block["title"],
                    "proposed_severity": _candidate_classification(body),
                    "proposed_status": _candidate_status(body),
                    "proposed_area": _candidate_any_field(
                        body,
                        "Proposed area",
                        "Recommended area",
                        "Area",
                        default="",
                    ),
                })
            if len(file_pending) > XYZ_MASTER_CANDIDATE_FILE_VISIBLE_LIMIT:
                suppressed["burst"] += len(file_pending)
                burst_files.append({
                    "run_id": meta["run_id"],
                    "source": meta["source"],
                    "path": meta["path"],
                    "candidate_count": len(file_pending),
                    "visible_limit": XYZ_MASTER_CANDIDATE_FILE_VISIBLE_LIMIT,
                })
                continue
            for candidate in file_pending:
                pending.append(candidate)
                if len(pending) >= XYZ_MASTER_BACKLOG_LIMIT:
                    break
            if len(pending) >= XYZ_MASTER_BACKLOG_LIMIT:
                break
        return {
            "action": "xyz_master_candidates",
            "ok": True,
            **ctx,
            "count": len(pending),
            "pending": pending[:XYZ_MASTER_BACKLOG_LIMIT],
            "suppressed": suppressed,
            "burst_files": burst_files[:10],
            "candidate_file_visible_limit": XYZ_MASTER_CANDIDATE_FILE_VISIBLE_LIMIT,
            "candidate_file_scan_limit": XYZ_MASTER_CANDIDATE_FILE_SCAN_LIMIT,
            "candidate_files_scanned": scanned_files,
            "min_run_id": XYZ_MASTER_BACKLOG_MIN_RUN_ID,
        }
    except urllib.error.HTTPError as exc:
        return {"action": "xyz_master_candidates", "ok": False, **ctx,
                "error": f"GitHub API {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"action": "xyz_master_candidates", "ok": False, **ctx,
                "error": f"{type(exc).__name__}: {exc}"}


def _action_xyz_master_candidate_detail(event: dict[str, Any]) -> dict[str, Any]:
    blocked = _require_full_operator_result(event, "xyz_master_candidate_detail")
    if blocked:
        return blocked
    ctx = _operator_context(event)
    run_id = str(event.get("run_id", "")).strip()
    source = str(event.get("source", "")).strip()
    try:
        candidate_index = int(event.get("candidate_index", 1))
        path, block = _load_xyz_master_candidate(run_id, source, candidate_index)
        return {
            "action": "xyz_master_candidate_detail",
            "ok": True,
            **ctx,
            "run_id": run_id,
            "source": source,
            "candidate_index": candidate_index,
            "mutation_kind": _candidate_mutation_kind(block["body"]),
            "template_kind": _candidate_template_kind(block["body"]),
            "path": path,
            "title": block["title"],
            "proposed_severity": _candidate_classification(block["body"]),
            "proposed_status": _candidate_status(block["body"]),
            "proposed_area": _candidate_any_field(
                block["body"],
                "Proposed area",
                "Recommended area",
                "Area",
                default="",
            ),
            "body": block["body"][:20000],
        }
    except urllib.error.HTTPError as exc:
        return {"action": "xyz_master_candidate_detail", "ok": False, **ctx,
                "error": f"GitHub API {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"action": "xyz_master_candidate_detail", "ok": False, **ctx,
                "error": f"{type(exc).__name__}: {exc}"}


def _load_xyz_master_candidate(run_id: str, source: str, candidate_index: int) -> tuple[str, dict[str, Any]]:
    path = _candidate_path(run_id, source)
    content = _gh_get(f"repos/{GITHUB_REPO}/contents/{urllib.parse.quote(path, safe='/')}", ref="main")
    src = base64.b64decode(content.get("content", "")).decode("utf-8", errors="replace")
    blocks = _candidate_blocks(src)
    block = next((b for b in blocks if b["index"] == candidate_index), None)
    if not block:
        raise ValueError(f"candidate {candidate_index} not found")
    return path, block


def _write_candidate_outcome_marker(
    *,
    candidate_path: str,
    candidate_index: int,
    outcome_token: str,
    outcome: str,
    decided_by: str,
    details_lines: list[str],
    message: str,
    extra_frontmatter: dict[str, str] | None = None,
) -> str:
    parts = [f"candidate-{candidate_index:03d}", outcome_token]
    marker_path = "/".join(candidate_path.split("/")[:-1]) + "/candidate-outcomes/" + ".".join(parts) + ".md"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    frontmatter = [
        "---",
        f"candidate_index: {candidate_index}",
        f"source_file: {json.dumps(candidate_path)}",
        f"outcome: {json.dumps(outcome)}",
        f"decision_by: {json.dumps(decided_by)}",
        f"decision_at_utc: {json.dumps(now)}",
    ]
    if extra_frontmatter:
        for key, value in extra_frontmatter.items():
            frontmatter.append(f"{key}: {json.dumps(value)}")
    frontmatter.extend(["---", ""])
    marker = "\n".join(frontmatter + details_lines + [""])
    _gh_put(
        f"repos/{GITHUB_REPO}/contents/{urllib.parse.quote(marker_path, safe='/')}",
        {
            "message": message,
            "content": base64.b64encode(marker.encode("utf-8")).decode("ascii"),
            "branch": "main",
        },
    )
    return marker_path


def _action_xyz_master_dispatch_candidate(event: dict[str, Any]) -> dict[str, Any]:
    try:
        operator_email = _require_xyz_master_operator(event)
    except PermissionError as exc:
        return {"action": "xyz_master_dispatch_candidate", "ok": False, "elevated_operator": False, "error": str(exc)}

    run_id = str(event.get("run_id", "")).strip()
    source = str(event.get("source", "")).strip()
    mode = str(event.get("mode", "dry-run")).strip()
    classification = str(event.get("classification_override", "")).strip().lower()
    try:
        candidate_index = int(event.get("candidate_index", 1))
        _, block = _load_xyz_master_candidate(run_id, source, candidate_index)
        if mode not in {"dry-run", "pr", "direct-main"}:
            raise ValueError("mode must be dry-run, pr, or direct-main")
        if classification and classification not in _VALID_CLASSIFICATIONS:
            raise ValueError("invalid severity/classification override")
        proposed_classification = _candidate_classification(block["body"])
        mutation_kind = _candidate_mutation_kind(block["body"])
        effective_classification = classification or proposed_classification
        if (
            mode == "direct-main"
            and mutation_kind not in {"status-transition", "severity-transition"}
            and effective_classification not in {"minor", "feature"}
        ):
            return {
                "action": "xyz_master_dispatch_candidate",
                "ok": False,
                "elevated_operator": True,
                "operator_email": operator_email,
                "mode": mode,
                "candidate_classification": proposed_classification,
                "requested_classification": effective_classification,
                "mutation_kind": mutation_kind,
                "error": (
                    "direct-main is restricted to minor defects and feature requests; "
                    "choose PR mode for major/critical candidates or approve this candidate "
                    f"as {proposed_classification}."
                ),
            }
        inputs = {
            "run_id": run_id,
            "source": source,
            "candidate_index": str(candidate_index),
            "classification_override": classification,
            "mode": mode,
            "operator_approved_high_severity": str(classification in {"major", "critical"}).lower(),
        }
        _gh_post(
            f"repos/{GITHUB_REPO}/actions/workflows/{urllib.parse.quote(XYZ_MASTER_WORKFLOW_ID, safe='')}/dispatches",
            {"ref": "main", "inputs": inputs},
        )
        candidate_path = _candidate_path(run_id, source)
        marker_path = _write_candidate_outcome_marker(
            candidate_path=candidate_path,
            candidate_index=candidate_index,
            outcome_token=f"APPROVED_DISPATCHED.{mode.upper().replace('-', '_')}",
            outcome="approved-dispatched",
            decided_by=operator_email,
            extra_frontmatter={
                "mode": mode,
                "workflow_id": XYZ_MASTER_WORKFLOW_ID,
            },
            details_lines=[
                f"# Candidate outcome: APPROVED_DISPATCHED ({mode})",
                "",
                f"Candidate {candidate_index} from `{candidate_path}` was approved by `{operator_email}`.",
                f"Mode: `{mode}`.",
                (
                    "Backlog drain marker written immediately after workflow dispatch acceptance "
                    "so generic approver queues do not retain already-approved candidates."
                ),
                "",
            ],
            message=f"XYZ_MASTER_SERVICE: mark approved-dispatched {run_id}/{source}/{candidate_index}",
        )
        return {
            "action": "xyz_master_dispatch_candidate",
            "ok": True,
            "elevated_operator": True,
            "operator_email": operator_email,
            "workflow_id": XYZ_MASTER_WORKFLOW_ID,
            "mode": mode,
            "inputs": inputs,
            "marker_path": marker_path,
            "note": "workflow dispatch accepted by GitHub; check Actions for the run/PR",
        }
    except urllib.error.HTTPError as exc:
        detail = _github_http_error(exc)
        hint = None
        if exc.code == 403:
            hint = (
                "Workflow dispatch requires the Lambda SSM-backed GitHub PAT to have "
                "GitHub Actions read/write permission on the repository, or "
                "classic PAT scope `repo` + `workflow`."
            )
        return {
            "action": "xyz_master_dispatch_candidate",
            "ok": False,
            "elevated_operator": True,
            "error": detail,
            **({"hint": hint} if hint else {}),
        }
    except Exception as exc:
        return {"action": "xyz_master_dispatch_candidate", "ok": False, "elevated_operator": True,
                "error": f"{type(exc).__name__}: {exc}"}


def _action_xyz_master_request_priority_change(event: dict[str, Any]) -> dict[str, Any]:
    try:
        operator_email = _require_xyz_master_operator(event)
    except PermissionError as exc:
        return {
            "action": "xyz_master_request_priority_change",
            "ok": False,
            "elevated_operator": False,
            "error": str(exc),
        }

    issue_id = str(event.get("issue_id", "")).strip().upper()
    target = str(event.get("target_severity", "")).strip().lower()
    mode = str(event.get("mode", "direct-main")).strip()
    rationale = str(event.get("rationale", "")).strip()
    try:
        if not re.match(r"^XYZ-PILOT-\d{3}$", issue_id):
            raise ValueError("issue_id must be XYZ-PILOT-NNN")
        if target not in _PRIORITY_TARGET_CLASSIFICATIONS:
            raise ValueError("target_severity must be feature, minor, major, or critical")
        if mode not in {"dry-run", "pr", "direct-main"}:
            raise ValueError("mode must be dry-run, pr, or direct-main")

        issue = next((item for item in _open_priority_issues_from_tracker(_tracker_source()) if item["id"] == issue_id), None)
        if not issue:
            raise ValueError(f"{issue_id} is not a current Open tracker issue with changeable severity")
        current = issue["severity"]
        if current == target:
            raise ValueError(f"{issue_id} is already {target}")

        now = datetime.now(timezone.utc)
        run_id = now.strftime("%Y-%m-%dT%H%M%SZ")
        source = "XYZ_OPERATOR_PRIORITY"
        candidate_path = _candidate_path(run_id, source)
        default_rationale = (
            f"Operator requested priority change from {current} to {target} "
            "through the dedicated Operator Dashboard priority tool."
        )
        candidate = "\n".join([
            f"# Candidate handoff to XYZ_MASTER - Priority change: {issue_id} {current} -> {target}",
            "",
            f"## Candidate - Severity transition: {issue_id} -> {target}",
            "",
            "- **Agent:** `operator-dashboard`",
            f"- **Run:** `{run_id}`",
            "- **Type:** defect",
            f"- **Existing ticket:** [{issue_id}](../../../../docs/XYZ_PILOT_ISSUE_TRACKER.md#{issue_id.lower()})",
            f"- **Current severity:** {current}",
            f"- **Requested severity:** {target}",
            "- **Status:** Open",
            f"- **Area:** {issue.get('area') or 'Tracker priority'}",
            f"- **Observed:** `{issue_id}` is currently Open with `{current}` priority.",
            f"- **Expected:** XYZ_MASTER updates `{issue_id}` to `{target}` priority without mixing the request into the candidate approval backlog UI.",
            f"- **Evidence:** Dedicated Operator Dashboard priority change requested by `{operator_email}` at `{now.strftime('%Y-%m-%dT%H:%M:%SZ')}`.",
            f"- **Severity rationale:** {rationale or default_rationale}",
            "",
        ])
        _gh_put(
            f"repos/{GITHUB_REPO}/contents/{urllib.parse.quote(candidate_path, safe='/')}",
            {
                "message": f"XYZ_MASTER_SERVICE: request {issue_id} priority {current}->{target}",
                "content": base64.b64encode(candidate.encode("utf-8")).decode("ascii"),
                "branch": "main",
            },
        )
        inputs = {
            "run_id": run_id,
            "source": source,
            "candidate_index": "1",
            "classification_override": target,
            "mode": mode,
            "operator_approved_high_severity": str(target in {"major", "critical"}).lower(),
        }
        _gh_post(
            f"repos/{GITHUB_REPO}/actions/workflows/{urllib.parse.quote(XYZ_MASTER_WORKFLOW_ID, safe='')}/dispatches",
            {"ref": "main", "inputs": inputs},
        )
        return {
            "action": "xyz_master_request_priority_change",
            "ok": True,
            "elevated_operator": True,
            "operator_email": operator_email,
            "issue_id": issue_id,
            "previous_severity": current,
            "target_severity": target,
            "candidate_path": candidate_path,
            "workflow_id": XYZ_MASTER_WORKFLOW_ID,
            "mode": mode,
            "inputs": inputs,
            "note": "priority-change candidate committed and XYZ_MASTER workflow dispatch accepted",
        }
    except urllib.error.HTTPError as exc:
        detail = _github_http_error(exc)
        return {
            "action": "xyz_master_request_priority_change",
            "ok": False,
            "elevated_operator": True,
            "error": detail,
        }
    except Exception as exc:
        return {
            "action": "xyz_master_request_priority_change",
            "ok": False,
            "elevated_operator": True,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _action_xyz_master_deny_candidate(event: dict[str, Any]) -> dict[str, Any]:
    try:
        operator_email = _require_xyz_master_operator(event)
    except PermissionError as exc:
        return {"action": "xyz_master_deny_candidate", "ok": False, "elevated_operator": False, "error": str(exc)}

    run_id = str(event.get("run_id", "")).strip()
    source = str(event.get("source", "")).strip()
    reason = str(event.get("reason", "invalid")).strip().lower()
    duplicate_of = str(event.get("duplicate_of", "")).strip().upper()
    notes = str(event.get("notes", "")).strip()
    try:
        candidate_index = int(event.get("candidate_index", 1))
        candidate_path = _candidate_path(run_id, source)
        if reason not in _VALID_DENIAL_REASONS:
            raise ValueError("invalid denial reason")
        if reason == "duplicate" and not re.match(r"^XYZ-PILOT-\d{3}$", duplicate_of):
            raise ValueError("duplicate denial requires duplicate_of=XYZ-PILOT-NNN")
        token = ".".join(filter(None, ["DENIED", reason.upper().replace("-", "_"), duplicate_of]))
        details = [
            f"# Candidate outcome: DENIED -> {reason}",
            "",
            f"Candidate {candidate_index} from `{candidate_path}` was denied by `{operator_email}`.",
            f"Reason: `{reason}`.",
            *( [f"Duplicate of: `{duplicate_of}`."] if duplicate_of else [] ),
            *( ["", "## Notes", "", notes] if notes else [] ),
            "",
        ]
        marker_path = _write_candidate_outcome_marker(
            candidate_path=candidate_path,
            candidate_index=candidate_index,
            outcome_token=token,
            outcome="denied",
            decided_by=operator_email,
            extra_frontmatter={
                "denial_reason": reason,
                **({"duplicate_of": duplicate_of} if duplicate_of else {}),
            },
            details_lines=details,
            message=f"XYZ_MASTER_SERVICE: deny candidate {run_id}/{source}/{candidate_index}",
        )
        return {
            "action": "xyz_master_deny_candidate",
            "ok": True,
            "elevated_operator": True,
            "operator_email": operator_email,
            "marker_path": marker_path,
        }
    except urllib.error.HTTPError as exc:
        return {"action": "xyz_master_deny_candidate", "ok": False, "elevated_operator": True,
                "error": f"GitHub API {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"action": "xyz_master_deny_candidate", "ok": False, "elevated_operator": True,
                "error": f"{type(exc).__name__}: {exc}"}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entrypoint. `event` is the JSON payload from Lambda Invoke."""
    log.info("user-mgmt invoked: action=%s", event.get("action"))
    action = (event or {}).get("action")
    if action == "cohort_table":
        blocked = _require_dashboard_user(event or {}, "cohort_table")
        if blocked:
            return blocked
        return _action_cohort_table()
    if action == "list_registered":
        blocked = _require_dashboard_user(event or {}, "list_registered")
        if blocked:
            return blocked
        return _action_list_registered()
    if action == "invite_user":
        email = (event or {}).get("email", "").strip().lower()
        return _action_invite_user(email, event or {})
    if action == "get_user_status":
        blocked = _require_dashboard_user(event or {}, "get_user_status")
        if blocked:
            return blocked
        email = (event or {}).get("email", "").strip().lower()
        return _action_get_user_status(email)
    if action == "login_history":
        blocked = _require_dashboard_user(event or {}, "login_history")
        if blocked:
            return blocked
        email = (event or {}).get("email", "").strip().lower()
        days = (event or {}).get("days", 30)
        return _action_login_history(email, days)
    if action == "github_collaborators":
        blocked = _require_full_operator_result(event or {}, "github_collaborators")
        if blocked:
            return blocked
        return _action_github_collaborators()
    if action == "github_commits":
        blocked = _require_full_operator_result(event or {}, "github_commits")
        if blocked:
            return blocked
        author = (event or {}).get("author", "").strip() or None
        days = (event or {}).get("days", 30)
        with_files = bool((event or {}).get("with_files", True))
        return _action_github_commits(author, days, with_files)
    if action == "github_commits_on_date":
        blocked = _require_full_operator_result(event or {}, "github_commits_on_date")
        if blocked:
            return blocked
        date_str = (event or {}).get("date", "").strip()
        with_files = bool((event or {}).get("with_files", True))
        return _action_github_commits_on_date(date_str, with_files)
    if action == "xyz_master_operator_context":
        return _action_xyz_master_operator_context(event or {})
    if action == "xyz_master_candidates":
        return _action_xyz_master_candidates(event or {})
    if action == "xyz_master_open_issues":
        return _action_xyz_master_open_issues(event or {})
    if action == "xyz_master_candidate_detail":
        return _action_xyz_master_candidate_detail(event or {})
    if action == "xyz_master_dispatch_candidate":
        return _action_xyz_master_dispatch_candidate(event or {})
    if action == "xyz_master_request_priority_change":
        return _action_xyz_master_request_priority_change(event or {})
    if action == "xyz_master_deny_candidate":
        return _action_xyz_master_deny_candidate(event or {})
    if action == "service_probe":
        blocked = _require_dashboard_user(event or {}, "service_probe")
        if blocked:
            return blocked
        return _action_service_probe(event or {})
    if action == "warmup_progress":
        blocked = _require_dashboard_user(event or {}, "warmup_progress")
        if blocked:
            return blocked
        return _action_warmup_progress(event or {})
    if action == "license_evidence":
        blocked = _require_dashboard_user(event or {}, "license_evidence")
        if blocked:
            return blocked
        return _action_license_evidence(event or {})
    if action == "swarm_schedule":
        blocked = _require_full_operator_result(event or {}, "swarm_schedule")
        if blocked:
            return blocked
        body = event.get("config") or event
        fire_utc = _cest_to_utc(body["date_cest"], body["time_cest"])
        fire = _validate_fire(fire_utc)
        agents = body.get("agents") or (BASE_AGENTS + sorted(OPT_IN_AGENTS))
        stress = _validate_stress(int(body.get("stress_users", 10)), bool(body.get("allow_higher")))
        adv = _parse_advanced(body.get("advanced", ""))
        cfg = {
            "agents": agents,
            "stress_users": stress,
            "session_hold_seconds": int(body.get("session_hold_seconds", 20)),
            "dry_run": bool(body.get("dry_run")),
            "force_run": bool(body.get("force_run")),
            "notify": bool(body.get("notify", True)),
            "notify_recipients": body.get("notify_recipients") or list(OPS_ONLY),
            "advanced": adv,
        }
        name = "xyz-pilot-swarm-" + fire.strftime("%Y%m%dT%H%M%SZ")
        lambda_arn = (
            getattr(context, "invoked_function_arn", None)
            or os.environ.get("AWS_LAMBDA_FUNCTION_ARN", "")
        )
        sch = boto3.client("scheduler", region_name=REGION)
        sch.create_schedule(
            Name=name,
            GroupName=SWARM_GROUP,
            ScheduleExpression="at(" + fire.strftime("%Y-%m-%dT%H:%M:%S") + ")",
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn": lambda_arn,
                "RoleArn": SWARM_SCHEDULER_ROLE_ARN,
                "Input": json.dumps({"action": "swarm_dispatch", "config": cfg, "schedule_name": name}),
            },
        )
        return {
            "ok": True,
            "name": name,
            "fires_at_utc": fire_utc,
            "fires_at_cest": body["date_cest"] + " " + body["time_cest"] + " CEST",
            "config": cfg,
        }

    if action == "swarm_dispatch":
        cfg = event.get("config") or {}
        try:
            return _swarm_dispatch(cfg, event.get("schedule_name"))
        except Exception as e:  # noqa: BLE001
            print("swarm_dispatch error:", str(e)[:200])
            return {"dispatched": False, "error": str(e)[:200]}

    if action == "swarm_schedules_list":
        blocked = _require_full_operator_result(event or {}, "swarm_schedules_list")
        if blocked:
            return blocked
        return _swarm_schedules_list()

    if action == "swarm_schedule_cancel":
        blocked = _require_full_operator_result(event or {}, "swarm_schedule_cancel")
        if blocked:
            return blocked
        return _swarm_schedule_cancel(event.get("name") or event.get("schedule_name") or "")

    if action == "swarm_runs_list":
        blocked = _require_full_operator_result(event or {}, "swarm_runs_list")
        if blocked:
            return blocked
        return _swarm_runs_list()

    if action == "swarm_run_results":
        blocked = _require_full_operator_result(event or {}, "swarm_run_results")
        if blocked:
            return blocked
        return _swarm_run_results(event.get("run_id") or "")

    return {
        "ok": False,
        "error": f"unknown action: {action!r}",
        "valid_actions": [
            "cohort_table", "list_registered", "invite_user",
            "get_user_status", "login_history",
            "github_collaborators", "github_commits", "github_commits_on_date",
            "xyz_master_operator_context",
            "xyz_master_candidates", "xyz_master_open_issues",
            "xyz_master_candidate_detail", "xyz_master_dispatch_candidate",
            "xyz_master_request_priority_change", "xyz_master_deny_candidate",
            "service_probe", "warmup_progress", "license_evidence",
            "swarm_schedule", "swarm_dispatch", "swarm_schedules_list",
            "swarm_schedule_cancel", "swarm_runs_list", "swarm_run_results",
        ],
    }
