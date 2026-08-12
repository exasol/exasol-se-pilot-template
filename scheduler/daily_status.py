"""<PILOT_NAME> — daily status email (XYZ-PILOT-045).

Fires Tue-Sat at 06:00 CEST (04:00 UTC) via EventBridge cron. Composes
an HTML status email covering the previous calendar day's work and
mails it to every SES-verified user in the allowed Cognito notification
groups. Skips Sun + Mon because the day before each was a non-business
day with no activity to report.

Content sources:
  - EC2 instance state                          (ec2:DescribeInstances)
  - SES verification status of every subscriber (ses:GetIdentity…)
  - Cognito user-pool members + their status    (cognito:ListUsers)
  - Recent commits in the last 24 h             (GitHub API)
  - GitHub Actions workflow health              (GitHub Actions API)
  - Pending Dependabot pull requests            (GitHub Pulls API)
  - Report-only repository retention inventory  (GitHub Git tree API)

The Lambda runs idempotently — re-invoking on the same day re-sends
to the same recipient set; there is no de-dup. (The cron only fires
once per scheduled day so this isn't an issue in normal operation.)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Any

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

EC2_INSTANCE_ID = os.environ["EC2_INSTANCE_ID"]
SES_FROM        = os.environ["SES_FROM"]
SES_TO          = [e.strip() for e in os.environ["SES_TO"].split(",") if e.strip()]
GITHUB_REPO     = os.environ.get("GITHUB_REPO", "<GITHUB_OWNER>/<PROJECT_SLUG>")
AWS_REGION      = os.environ.get("XYZ_AWS_REGION") or os.environ.get("AWS_REGION", "<AWS_REGION>")
ACCOUNT_ID      = os.environ.get("XYZ_AWS_ACCOUNT_ID", "<AWS_ACCOUNT_ID>" if AWS_REGION == "<AWS_REGION>" else "<SOURCE_AWS_ACCOUNT_ID>")
USER_POOL_ID    = os.environ.get(
    "COGNITO_USER_POOL_ID",
    "<COGNITO_USER_POOL_ID>",
)
# XYZ-PILOT-034 routing update 2026-06-27: the 06:00 CEST daily STATUS email
# goes to <PILOT_NAME> OPS plus the broader XYZ_PILOT UAT audience. XYZ_OPS is
# intentionally excluded from daily status routing; those operators still keep
# their Operator Dashboard permissions and lifecycle notification scope.
NOTIFY_INCLUDE_GROUPS = ("OPS_ONLY", "XYZ_PILOT")
NOTIFY_EXCLUDE_GROUPS = ("XYZ_BIZ",)
# XYZ-PILOT-047 + XYZ-PILOT-210 + XYZ-PILOT-259: PAT for tracker
# refresh/candidate emission and GitHub visibility sections. The Lambda no
# longer carries the PAT as a plaintext environment value; it reads the
# Secrets Manager secret named by GITHUB_TOKEN_SECRET_ID at runtime and caches
# it per container. GITHUB_TOKEN_PARAM remains a temporary SSM SecureString
# rollback fallback. Optional — when unavailable the GitHub-backed sections
# degrade and the email still goes out. Required permissions for a fine-grained token on
# <GITHUB_OWNER>/<PROJECT_SLUG>:
#   Contents: Read and write
#   Actions: Read
#   Pull requests: Read
# Classic PAT equivalent: repo + workflow.
GITHUB_TOKEN_SECRET_ID = os.environ.get("GITHUB_TOKEN_SECRET_ID", "xyz-pilot/github/service-token")
GITHUB_TOKEN_PARAM = os.environ.get("GITHUB_TOKEN_PARAM", "/xyz-pilot/chatbot/github-token")
TRACKER_PATH    = "docs/XYZ_PILOT_ISSUE_TRACKER.md"
STATUS_DASHBOARD_URL = os.environ.get(
    "STATUS_DASHBOARD_URL",
    os.environ.get(
        "OPERATOR_DASHBOARD",
        "https://<PROJECT_SLUG>-dashboard.<PILOT_DOMAIN>/dashboard.html",
    ),
)
_GITHUB_TOKEN_CACHE: str | None = None
_GITHUB_TOKEN_WARNED: set[str] = set()

ec2     = boto3.client("ec2",            region_name=AWS_REGION)
ses     = boto3.client("ses",            region_name=AWS_REGION)
cognito = boto3.client("cognito-idp",    region_name=AWS_REGION)
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
secretsmanager = boto3.client("secretsmanager", region_name=AWS_REGION)
ssm     = boto3.client("ssm",            region_name=AWS_REGION)

# XYZ-PILOT-048: Bedrock-backed narrative generation. The deterministic
# sections (environment, subscriber pipeline, recent commits, doc
# freshness, links) are still composed by `_render_html`; the narrative
# sections (headline, today's work, things worth surfacing) come from
# Claude Sonnet 4.6 via the EU inference profile so data stays in-region.
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "us.anthropic.claude-sonnet-4-6" if AWS_REGION.startswith("us-") else "eu.anthropic.claude-sonnet-4-6",
)

CEST_OFFSET = timedelta(hours=2)
CONFIDENTIAL_EMAIL_FOOTER = (
    "Confidential - <PILOT_NAME> - © Exasol AG · ESL © <SEMANTIC_LAYER_VENDOR> · "
    "for XYZ evaluation use only."
)


def _ec2_state() -> str:
    r = ec2.describe_instances(InstanceIds=[EC2_INSTANCE_ID])
    return r["Reservations"][0]["Instances"][0]["State"]["Name"]


# XYZ-PILOT-056 (2026-05-21): live health probes for the MCP server + chatbot,
# so the daily-status email reflects actual reachability, not just EC2 state.
# Skipped when EC2 is stopped — both services live on EC2.
PROBE_USER_AGENT = "xyz-pilot-daily-status-probe/1.0 (+https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>)"
EXA_MCP_URL  = os.environ.get("EXA_MCP_URL", "https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp")
CHATBOT_URL  = os.environ.get("CHATBOT_URL", "https://<PROJECT_SLUG>-chat.<PILOT_DOMAIN>")
QBR_URL      = os.environ.get("QBR_URL",     "https://<PROJECT_SLUG>-qbr.<PILOT_DOMAIN>")
GRAFANA_URL  = os.environ.get("GRAFANA_URL", "https://<PROJECT_SLUG>-grafana.<PILOT_DOMAIN>")


def _probe_mcp(timeout: int = 5) -> dict[str, Any]:
    """Anonymous initialize against the MCP — healthy = 401 or 403
    (XYZ-PILOT-057: Cloudflare bot-mgmt sometimes responds with 403 to
    Lambda's default urllib UA. Accept both; add a real UA header.)"""
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize",'
        b'"params":{"protocolVersion":"2024-11-05","capabilities":{},'
        b'"clientInfo":{"name":"daily-status-probe","version":"0.1"}}}'
    )
    req = urllib.request.Request(EXA_MCP_URL, method="POST", data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": PROBE_USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"healthy": False, "code": r.status, "note": "unexpected 2xx"}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return {"healthy": True, "code": exc.code, "note": f"OAuth-gated (HTTP {exc.code})"}
        return {"healthy": False, "code": exc.code, "note": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "code": 0, "note": str(exc)[:80]}


def _probe_chatbot(timeout: int = 5) -> dict[str, Any]:
    url = f"{CHATBOT_URL.rstrip('/')}/_stcore/health"
    req = urllib.request.Request(url, method="GET", headers={
        "Accept": "text/plain",
        "User-Agent": PROBE_USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(64).decode(errors="replace").strip()
            if r.status == 200 and body.lower() == "ok":
                return {"healthy": True, "code": 200, "note": "OK"}
            return {"healthy": False, "code": r.status, "note": f"HTTP {r.status} body={body[:32]!r}"}
    except urllib.error.HTTPError as exc:
        return {"healthy": False, "code": exc.code, "note": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "code": 0, "note": str(exc)[:80]}


def _probe_qbr(timeout: int = 5) -> dict[str, Any]:
    """XYZ-PILOT-206: live probe for Streamlit QBR `/_stcore/health` endpoint.
    Same shape as _probe_chatbot — Streamlit's standard health endpoint returns
    plain-text `ok` on 200."""
    url = f"{QBR_URL.rstrip('/')}/_stcore/health"
    req = urllib.request.Request(url, method="GET", headers={
        "Accept": "text/plain",
        "User-Agent": PROBE_USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(64).decode(errors="replace").strip()
            if r.status == 200 and body.lower() == "ok":
                return {"healthy": True, "code": 200, "note": "OK"}
            return {"healthy": False, "code": r.status, "note": f"HTTP {r.status} body={body[:32]!r}"}
    except urllib.error.HTTPError as exc:
        return {"healthy": False, "code": exc.code, "note": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "code": 0, "note": str(exc)[:80]}


def _probe_grafana(timeout: int = 5) -> dict[str, Any]:
    """XYZ-PILOT-206: live probe for Grafana `/api/health` endpoint. Grafana's
    standard health endpoint returns a small JSON body on 200 with `database:
    "ok"` when the underlying datastore is up. We accept any 200 as healthy
    (parsing the JSON body would tighten the check but adds parsing risk
    inside the email Lambda for marginal value)."""
    url = f"{GRAFANA_URL.rstrip('/')}/api/health"
    req = urllib.request.Request(url, method="GET", headers={
        "Accept": "application/json",
        "User-Agent": PROBE_USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200:
                return {"healthy": True, "code": 200, "note": "OK"}
            return {"healthy": False, "code": r.status, "note": f"HTTP {r.status}"}
    except urllib.error.HTTPError as exc:
        return {"healthy": False, "code": exc.code, "note": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "code": 0, "note": str(exc)[:80]}


def _ses_status(addresses: list[str] | None = None) -> dict[str, str]:
    """XYZ-PILOT-NNN: accept an explicit address list so we can verify the
    Cognito-discovered superset, not just the historical `SES_TO` env-var
    addresses. Defaults to `SES_TO` for backward compatibility with any
    inline caller. The downstream `deliverable` filter only ships to
    addresses whose state is `Success`, so passing a broader candidate
    list is safe."""
    addrs = list(addresses) if addresses is not None else list(SES_TO)
    if not addrs:
        return {}
    r = ses.get_identity_verification_attributes(Identities=addrs)
    out = {addr: info.get("VerificationStatus", "Unknown")
           for addr, info in r.get("VerificationAttributes", {}).items()}
    for addr in addrs:
        out.setdefault(addr, "NotFound")
    return out


def _users_in_group_emails(group_name: str) -> list[str]:
    """Return emails for all users in one Cognito group (paginated)."""
    out: list[str] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "UserPoolId": USER_POOL_ID,
            "GroupName": group_name,
            "Limit": 60,
        }
        if next_token:
            kwargs["NextToken"] = next_token
        resp = cognito.list_users_in_group(**kwargs)
        for user in resp.get("Users", []):
            email = next(
                (a["Value"] for a in user.get("Attributes", []) if a.get("Name") == "email"),
                "",
            )
            if email and "@" in email:
                out.append(email.strip())
        next_token = resp.get("NextToken")
        if not next_token:
            break
    return out


def _all_candidate_recipients() -> list[str]:
    """Strict Cognito group-based recipients for status notifications."""
    include_seen: dict[str, str] = {}
    exclude_keys: set[str] = set()

    for group_name in NOTIFY_INCLUDE_GROUPS:
        try:
            for addr in _users_in_group_emails(group_name):
                key = addr.lower()
                include_seen.setdefault(key, addr)
        except Exception as exc:  # noqa: BLE001
            log.warning("recipient discovery include-group failed: %s (%s)",
                        group_name, type(exc).__name__)

    for group_name in NOTIFY_EXCLUDE_GROUPS:
        try:
            for addr in _users_in_group_emails(group_name):
                exclude_keys.add(addr.lower())
        except Exception as exc:  # noqa: BLE001
            log.warning("recipient discovery exclude-group failed: %s (%s)",
                        group_name, type(exc).__name__)

    recipients = sorted(
        [addr for key, addr in include_seen.items() if key not in exclude_keys],
        key=str.lower,
    )
    log.info(
        "recipient discovery (Cognito groups include=%s exclude=%s): n=%d",
        ",".join(NOTIFY_INCLUDE_GROUPS),
        ",".join(NOTIFY_EXCLUDE_GROUPS),
        len(recipients),
    )
    return recipients


def _recipient_probe_payload(include_emails: bool = False) -> dict[str, Any]:
    """Side-effect-free validation for the daily-status notification roster."""
    group_members: dict[str, list[str]] = {}
    for group_name in (*NOTIFY_INCLUDE_GROUPS, *NOTIFY_EXCLUDE_GROUPS):
        try:
            group_members[group_name] = sorted(
                set(_users_in_group_emails(group_name)),
                key=str.lower,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "recipient probe group lookup failed: %s (%s)",
                group_name,
                type(exc).__name__,
            )
            group_members[group_name] = []

    recipients = _all_candidate_recipients()
    ses_state = _ses_status(recipients)
    deliverable = sorted([a for a, s in ses_state.items() if s == "Success"], key=str.lower)
    skipped = sorted([a for a, s in ses_state.items() if s != "Success"], key=str.lower)
    payload: dict[str, Any] = {
        "recipient_probe_only": True,
        "include_groups": list(NOTIFY_INCLUDE_GROUPS),
        "exclude_groups": list(NOTIFY_EXCLUDE_GROUPS),
        "group_counts": {k: len(v) for k, v in sorted(group_members.items())},
        "candidate_count": len(recipients),
        "deliverable_count": len(deliverable),
        "skipped_count": len(skipped),
    }
    if include_emails:
        payload["group_members"] = group_members
        payload["candidate_recipients"] = recipients
        payload["deliverable_recipients"] = deliverable
        payload["skipped_recipients"] = skipped
    return payload


def _cognito_users() -> list[dict[str, str]]:
    """XYZ-PILOT-069 sub-4: paginate via PaginationToken so the subscriber
    roster doesn't silently truncate once the pool grows past 60."""
    out: list[dict[str, str]] = []
    paginator = cognito.get_paginator("list_users")
    for page in paginator.paginate(UserPoolId=USER_POOL_ID, Limit=60):
        for u in page.get("Users", []):
            email = next(
                (a["Value"] for a in u.get("Attributes", []) if a["Name"] == "email"),
                u.get("Username", ""),
            )
            out.append({"email": email, "status": u.get("UserStatus", "")})
    return out


def _warn_github_token_once(key: str, message: str, *args: Any) -> None:
    if key in _GITHUB_TOKEN_WARNED:
        return
    log.warning(message, *args)
    _GITHUB_TOKEN_WARNED.add(key)


def _github_token_from_secret() -> str:
    """Return the GitHub PAT from Secrets Manager without ever logging the value."""
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
    """Return the GitHub PAT from SSM fallback without ever logging the value."""
    if _GITHUB_TOKEN_CACHE is not None:
        return _GITHUB_TOKEN_CACHE
    try:
        resp = ssm.get_parameter(Name=GITHUB_TOKEN_PARAM, WithDecryption=True)
        value = str(resp.get("Parameter", {}).get("Value") or "").strip()
    except Exception as exc:  # noqa: BLE001 - secret lookup degrades to unauthenticated GitHub calls.
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


def _gh_headers() -> dict[str, str]:
    """Common GitHub REST headers, including auth when the stored token is available.

    The repo is private (since well before XYZ-PILOT-049 shipped), so anonymous
    calls silently return 404. Without a Bearer token, `_recent_commits` and
    `_doc_freshness` both came back empty — and the Bedrock narrative dutifully
    reported "no code changes" + "doc age=Noned". Same root cause that bit the
    chatbot's snapshot fetch on 2026-05-20 (Milestone J.1).
    """
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "xyz-pilot-daily-status/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _github_token()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _github_error_summary(exc: BaseException, required_permission: str = "") -> str:
    """Secret-safe GitHub API error summary for email/log diagnostics.

    GitHub returns the important "Resource not accessible by personal access
    token" detail in the response body, while `str(HTTPError)` only shows
    "HTTP Error 403: Forbidden". Read a small body slice and never echo the
    Authorization header or token value.
    """
    if isinstance(exc, urllib.error.HTTPError):
        body_text = ""
        try:
            body_text = exc.read(4096).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - diagnostic best effort only.
            body_text = ""
        message = ""
        docs_url = ""
        if body_text:
            try:
                body = json.loads(body_text)
                if isinstance(body, dict):
                    message = str(body.get("message") or "")
                    docs_url = str(body.get("documentation_url") or "")
            except json.JSONDecodeError:
                message = body_text.strip()[:160]
        if not message:
            message = str(exc.reason or exc)
        parts = [f"HTTP {exc.code}: {message}"]
        remaining = (exc.headers.get("x-ratelimit-remaining") or "").strip() if exc.headers else ""
        reset = (exc.headers.get("x-ratelimit-reset") or "").strip() if exc.headers else ""
        if exc.code == 403 and remaining == "0":
            parts.append(f"GitHub rate limit exhausted; reset_epoch={reset or 'unknown'}")
        elif exc.code in (401, 403) and required_permission:
            parts.append(f"check SSM-backed GitHub PAT permission: {required_permission}")
        if docs_url:
            parts.append(f"docs={docs_url}")
        return "; ".join(parts)[:320]
    return f"{type(exc).__name__}: {str(exc)[:160]}"


def _recent_commits(hours: int = 24) -> list[dict[str, Any]]:
    """XYZ-PILOT-069 sub-2: on fetch failure, return a single sentinel dict
    so the email body / Bedrock prompt can render "⚠ GitHub commits API
    unreachable" instead of indistinguishable empty-list quiet-day.
    Callers detect the sentinel via the `error` key.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"https://api.github.com/repos/{GITHUB_REPO}/commits?since={since}&per_page=30"
    req = urllib.request.Request(url, headers=_gh_headers())
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        msg = _github_error_summary(exc, "Contents: Read")
        log.warning("github commits fetch failed: %s", msg)
        return [{"error": msg, "sha": "", "message": f"⚠ GitHub commits API unreachable ({msg}) — commit list may be incomplete", "author": "system", "date": ""}]
    out = []
    for c in data[:15]:
        out.append({
            "sha": c.get("sha", "")[:7],
            "message": (c.get("commit", {}).get("message", "") or "").split("\n", 1)[0][:120],
            "author": c.get("commit", {}).get("author", {}).get("name", "unknown"),
            "date": c.get("commit", {}).get("author", {}).get("date", ""),
        })
    return out


def _workflow_health_24h(hours: int = 24) -> dict[str, Any]:
    """XYZ-PILOT-NNN: GitHub Actions workflow health rollup for last <hours> h.

    Returns a dict shaped:
        {
          "ok": True/False,
          "fetched_at": "<iso UTC>",
          "since_iso": "<iso UTC>",
          "by_workflow": {
            "<workflow name>": {
              "success": N, "failure": N, "cancelled": N, "in_progress": N,
              "skipped": N, "startup_failure": N, "total": N,
              "latest_failure_html_url": "...",
              "latest_failure_sha": "...",
            },
            ...
          },
          "total_runs": N,
          "total_failures": N,
          "error": "<msg>" if fetch failed,
        }

    Per XYZ-PILOT-NNN — surfaces workflow-failure-on-every-push patterns at
    the next 06:00 CEST daily-status fire, instead of waiting for the
    operator to discover them via inbox-flood (the 70-80 GitHub Actions
    failure-notification emails received 2026-05-29 → 2026-06-01 before
    the missing `concurrency:` blocks were noticed).
    """
    since_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        f"https://github.com/{GITHUB_REPO}/actions/runs"
        f"?per_page=100&created=%3E{since_iso}"
    )
    req = urllib.request.Request(url, headers=_gh_headers())
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        msg = _github_error_summary(exc, "Actions: Read (or classic PAT repo + workflow)")
        log.warning("github actions runs fetch failed: %s", msg)
        return {
            "ok": False, "error": msg,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "since_iso": since_iso,
            "by_workflow": {}, "total_runs": 0, "total_failures": 0,
        }
    runs = data.get("workflow_runs", [])
    by_workflow: dict[str, dict[str, Any]] = {}
    total_failures = 0
    for run in runs:
        wf_name = run.get("name") or "(unknown)"
        conclusion = (run.get("conclusion") or run.get("status") or "in_progress").replace("_", "_")
        slot = by_workflow.setdefault(wf_name, {
            "success": 0, "failure": 0, "cancelled": 0, "in_progress": 0,
            "skipped": 0, "startup_failure": 0, "total": 0,
            "latest_failure_html_url": "", "latest_failure_sha": "",
        })
        slot["total"] += 1
        # Bucket conclusion into known keys; anything unknown rolls into "other".
        if conclusion in slot:
            slot[conclusion] += 1
        else:
            slot.setdefault("other", 0)
            slot["other"] += 1
        if conclusion in ("failure", "startup_failure"):
            total_failures += 1
            # Track the latest failure per workflow (runs come back newest-first
            # by default, so the first-seen failure for a given workflow is
            # the most recent).
            if not slot["latest_failure_html_url"]:
                slot["latest_failure_html_url"] = run.get("html_url", "")
                slot["latest_failure_sha"] = (run.get("head_sha") or "")[:7]
    return {
        "ok": True,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "since_iso": since_iso,
        "by_workflow": by_workflow,
        "total_runs": len(runs),
        "total_failures": total_failures,
    }


def _fetch_tracker_text() -> str | None:
    """Read-only GET of docs/XYZ_PILOT_ISSUE_TRACKER.md from origin/main.

    Returns the decoded markdown content as str, or None on any fetch
    failure (caller falls back to commit-only narrative).
    """
    token = _github_token()
    if not token:
        log.warning("GitHub token unavailable — skipping tracker fetch for resolved/filed parsing")
        return None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{TRACKER_PATH}?ref=main"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "xyz-pilot-daily-status/1.0",
    }
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
            meta = json.load(r)
        # XYZ-PILOT-NNN follow-up 2026-06-10: GitHub Contents API returns
        # encoding=none + content="" for files > 1 MB. The tracker crossed
        # that threshold on 2026-06-08 (commit c28b032 = 1,051,183 bytes
        # via XYZ-PILOT-223 filing); two consecutive 06:00 CEST daily-status
        # emails (2026-06-09 + 2026-06-10) shipped with empty
        # TRACKER MOVEMENT + empty Backlog by Severity sections. Fall back
        # to the pre-signed download_url from the same Contents response
        # when content is missing.
        if meta.get("encoding") == "base64" and meta.get("content"):
            return base64.b64decode(meta["content"]).decode("utf-8")
        download_url = meta.get("download_url")
        if download_url:
            log.info("tracker > 1MB Contents-API cap; falling back to download_url")
            with urllib.request.urlopen(
                urllib.request.Request(download_url, headers={"User-Agent": "xyz-pilot-daily-status/1.0"}),
                timeout=30,
            ) as raw:
                return raw.read().decode("utf-8")
        log.warning("tracker fetch: empty content + no download_url (size=%s, encoding=%s)",
                    meta.get("size"), meta.get("encoding"))
        return None
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        log.warning("tracker fetch (for resolved/filed parsing) failed: %s", exc)
        return None


# XYZ-PILOT-074 follow-up: the Bedrock narrative was mis-attributing
# commit-message references like "file XYZ-PILOT-062 through XYZ-PILOT-071" as a
# Resolved range. Cure: parse the tracker's top "Active issues" table for
# currently surfaced IDs (XYZ-PILOT-047 keeps it current), derive resolved dates
# from each row's compact `(Resolved YYYY-MM-DD)` Summary suffix, and hand the
# prompt explicit resolved/filed lists. The matching Full-register `**Status:**`
# date remains the safety fallback and verifier cross-check.
#
# Top-table row patterns:
#   Resolved row → ID, Severity, and Area cells struck through; Summary stays
#   plain, contains no resolution narrative, and ends with a compact marker:
#     | ~~[XYZ-PILOT-070](#xyz-pilot-070)~~ | ~~minor~~ | ~~…~~ | Original issue statement only. (Resolved 2026-05-21) |
#   Open row     → no strikethrough:
#     | [XYZ-PILOT-066](#xyz-pilot-066) | major | … | CLAUDE.md hasn't been … |
# A resolved row's prune date is taken from the compact marker first and from
# the matching Full-register `**Status:**` line as fallback.
# A row's "surfaced/filed" date isn't on the top-table row, only in the prose
# entry's `**Surfaced:** YYYY-MM-DD`. Filed-in-window lookup therefore uses
# the prose Surfaced line for any ID that is currently Open in the top table.
_ACTIVE_TABLE_HEADER_RE = re.compile(
    r"^\| ID \| Severity \| Area \| Summary \|\s*$", re.MULTILINE
)
_ACTIVE_TABLE_ROW_RE    = re.compile(
    r"^\|\s*(?:~~)?\[XYZ-PILOT-(\d+)\][^|]*\|", re.MULTILINE
)
_TOP_RESOLVED_MARKER_RE = re.compile(r"\(Resolved\s+(\d{4}-\d{2}-\d{2})\)\s*\|\s*$")
_OPEN_ROW_RE            = re.compile(
    r"^\|\s*\[XYZ-PILOT-(\d+)\]", re.MULTILINE
)  # bare (no ~~) link = currently Open in the active table
_SURFACED_RE = re.compile(r"^\s*[-*]\s*\*\*Surfaced:\*\*\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)
_DETAIL_STATUS_RE = re.compile(
    r'<a id="xyz-pilot-(\d{3})"></a>\s*\n\s*### XYZ-PILOT-\d{3}\b.*?'
    r'\n-\s+\*\*Status:\*\*\s*([^\n]+)',
    re.DOTALL,
)
_STATUS_RESOLVED_DATE_RE = re.compile(r"\b[Rr]esolved\s+\(?(\d{4}-\d{2}-\d{2})")


def _detail_status_by_id(tracker_text: str) -> dict[int, str]:
    return {
        int(m.group(1)): m.group(2).strip()
        for m in _DETAIL_STATUS_RE.finditer(tracker_text)
    }


def _resolved_date_from_status(status: str) -> date | None:
    m = _STATUS_RESOLVED_DATE_RE.search(status or "")
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _resolved_date_from_top_row(line: str) -> date | None:
    m = _TOP_RESOLVED_MARKER_RE.search(line or "")
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _tracker_movement_in_window(
    tracker_text: str | None,
    since_dt: datetime,
    until_dt: datetime,
) -> dict[str, list[str]]:
    """Parse the tracker's top Active-issues table + prose Surfaced lines.

    Returns {"resolved": […], "filed": […]} where:
      - resolved = XYZ-PILOT IDs whose top-table Summary suffix carries
                   `(Resolved YYYY-MM-DD)` with the date inside the window.
      - filed    = XYZ-PILOT IDs whose top-table row is bare (still Open) and
                   whose prose `**Surfaced:** YYYY-MM-DD` falls in the window.
                   Same-day-filed-and-resolved items land in `resolved` only.

    Both lists are sorted ascending by numeric ID. Returns empty lists on
    missing tracker_text or if the active-table header can't be found.
    """
    if not tracker_text:
        return {"resolved": [], "filed": []}

    # Locate the active-issues table. It starts at `| ID | Severity | …`
    # and runs until the next `## ` section header.
    hdr = _ACTIVE_TABLE_HEADER_RE.search(tracker_text)
    if not hdr:
        log.warning("active-issues table header not found in tracker — resolved/filed parsing skipped")
        return {"resolved": [], "filed": []}
    rest = tracker_text[hdr.end():]
    end_of_table = rest.find("\n## ")
    table = rest if end_of_table < 0 else rest[:end_of_table]

    since_d = since_dt.date()
    until_d = until_dt.date()
    status_by_id = _detail_status_by_id(tracker_text)

    resolved: set[int] = set()
    open_ids: set[int] = set()

    # Walk rows, classify each:
    for line in table.splitlines():
        if not line.startswith("|"):
            continue
        # Pull the first XYZ-PILOT-NNN reference on the line (= the row ID).
        m = re.search(r"\[XYZ-PILOT-(\d+)\]", line)
        if not m:
            continue
        ticket_n = int(m.group(1))
        # Resolved iff the compact top-table marker carries
        # `(Resolved YYYY-MM-DD)` with the date in [since_d, until_d].
        # Fallback to Full-register Status for defensive compatibility while
        # the verifier requires both to agree.
        resolved_date = _resolved_date_from_top_row(line) or _resolved_date_from_status(status_by_id.get(ticket_n, ""))
        if resolved_date:
            if since_d <= resolved_date <= until_d:
                resolved.add(ticket_n)
            continue  # resolved rows are never also "filed"
        # No resolved detail status → currently Open. Stash for the filed-in-window
        # lookup against the prose Surfaced line below.
        open_ids.add(ticket_n)

    # For each currently-Open ID, look up its prose `**Surfaced:**` date.
    filed: set[int] = set()
    for tn in open_ids:
        # Crude but robust: find the prose anchor (### XYZ-PILOT-NNN) and pull
        # the first **Surfaced:** YYYY-MM-DD inside its section.
        anchor = re.search(rf"^### XYZ-PILOT-{tn:03d}\b", tracker_text, re.MULTILINE)
        if not anchor:
            continue
        body_start = anchor.end()
        # Section ends at the next ### or end of file.
        next_section = re.search(r"^### ", tracker_text[body_start:], re.MULTILINE)
        body_end = body_start + (next_section.start() if next_section else len(tracker_text) - body_start)
        body = tracker_text[body_start:body_end]
        sf = _SURFACED_RE.search(body)
        if not sf:
            continue
        try:
            d = date.fromisoformat(sf.group(1))
        except ValueError:
            continue
        if since_d <= d <= until_d:
            filed.add(tn)

    return {
        "resolved": [f"XYZ-PILOT-{n:03d}" for n in sorted(resolved)],
        "filed":    [f"XYZ-PILOT-{n:03d}" for n in sorted(filed)],
    }


# Files the daily-status email checks for staleness. List order = display order
# in the "Documentation freshness" section. Track top-level docs/*.md plus
# curated component READMEs; exclude archives, QA snapshots, screenshots, and
# generated architecture captures to keep the morning status actionable.
# XYZ-PILOT-046 captures the rule: milestone-grade commits must keep these in sync.
TRACKED_DOCS = [
    "README.md",
    "docs/00-live-status/XYZ_PILOT_PROGRESS_LOG.md",
    "docs/00-live-status/XYZ_PILOT_DAILY_STATUS.md",
    "docs/XYZ_PILOT_ISSUE_TRACKER.md",
    "docs/00-live-status/XYZ_PILOT_LIVE_RUNBOOK.md",
    "docs/30-runbooks/operations/XYZ_PILOT_DEVELOPER_RUNBOOK.md",
    "docs/30-runbooks/deployment/XYZ_PILOT_DEPLOYMENT_RUNBOOK.md",
    "docs/40-migrations/<AWS_REGION>-uat/XYZ_PILOT_US_EAST_1_UAT_DEPLOYMENT_PLAN.md",
    "docs/50-testing-validation/XYZ_PILOT_TEST_RUNBOOK.md",
    "docs/50-testing-validation/XYZ_PILOT_TEST_OVERVIEW.md",
    "docs/30-runbooks/security/XYZ_PILOT_CREDENTIALS.md",
    "docs/30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md",
    "docs/30-runbooks/github/XYZ_PILOT_GH_REPO_BACKUP_RUNBOOK.md",
    "docs/30-runbooks/mcp/XYZ_PILOT_MCP_CLIENT_GUIDE.md",
    "docs/30-runbooks/mcp/XYZ_PILOT_MCP_CLIENT_MAC_GUIDE.md",
    "docs/30-runbooks/mcp/XYZ_PILOT_MCP_PRODUCTION_HARDENING.md",
    "docs/30-runbooks/operations/XYZ_PILOT_REPOSITORY_RETENTION_RUNBOOK.md",
    "docs/10-product-data/XYZ_PILOT_REQUIREMENTS_V1.md",
    "docs/10-product-data/XYZ_PILOT_SCHEMA_MAPPING.md",
    "docs/30-runbooks/data/XYZ_PILOT_MIGRATION_GUIDE.md",
    "docs/30-runbooks/deployment/XYZ_PILOT_EXASOL_POC_RUNBOOK.md",
    "docs/10-product-data/XYZ_EXASOL_PERFORMANCE_ANALYSIS.md",
    "docs/30-runbooks/data/XYZ_PILOT_SSAS_TO_EXASOL_RUNBOOK.md",
    "docs/10-product-data/XYZ_PILOT_SSAS_TO_EXASOL_LEGACY_DOC_REVIEW.md",
    "docs/30-runbooks/security/XYZ_PILOT_SYS_PASSWORD_ROTATION_RUNBOOK.md",
    "docs/30-runbooks/ui/XYZ_PILOT_STREAMLIT_VISUALS.md",
    "docs/30-runbooks/ui/XYZ_PILOT_UI_RUNBOOK.md",
    "scheduler/README.md",
    "services/exasol-mcp/README.md",
    "services/chatbot/README.md",
    "services/streamlit-insights/README.md",
    "scripts/exasol_warmup/README.md",
    "tests/README.md",
]


def _business_days_since(resolved: date, today: date) -> int:
    """Count Mon–Fri-only days from `resolved` (exclusive) to `today` (inclusive)."""
    if resolved >= today:
        return 0
    bd = 0
    d = resolved
    while d < today:
        d += timedelta(days=1)
        if d.weekday() < 5:  # 0=Mon … 4=Fri
            bd += 1
    return bd


def _subtract_business_days(today: date, n: int) -> date:
    """XYZ-PILOT-061 — return the date `n` business days BEFORE `today`.

    XYZ-PILOT-069 sub-7 (Saturday off-by-one fix): the Lambda fires Tue–Sat,
    so Saturday is in scope. If `today` is itself Sat/Sun the previous
    naive walk would count one too few business days (Sat→Fri→Thu would
    return Thu after 2 steps, but the FIRST step Fri = 1 business day,
    Thu = 2, which IS Thursday — wait, that's correct. The actual bug:
    `_business_days_since` is "days AFTER resolved up to and including
    today"; its inverse should land on a date `r` such that
    `_business_days_since(r, today) == n`. For Sat today + n=2 the naive
    walk lands on Thu, but `_business_days_since(Thu, Sat) == 1` (only Fri
    counts; Sat is not a business day). So the two helpers disagree on
    weekend `today`. Fix: normalise `today` to the previous business day
    first, then count back from there.
    """
    d = today
    # Normalise: if today is Sat/Sun, the "previous business day" is the
    # most recent Mon-Fri. Walk back without decrementing n.
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    while n > 0:
        d = d - timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


# XYZ-PILOT-061: regex that matches the existing visibility-rule sentence
# at the top of the tracker. Greedy enough to handle small formatting
# tweaks but anchored on the sentence's unique start + end fragments.
_VISIBILITY_RULE_RE = re.compile(
    r"^> \*Today is .*?older ones are pruned\.\*$",
    re.MULTILINE,
)

# XYZ-PILOT-061 extension (2026-05-21): also refresh the "Report date"
# header line in docs/00-live-status/XYZ_PILOT_DAILY_STATUS.md so it doesn't drift behind
# the body content (e.g. body has "Today's deliveries (2026-05-21)"
# while header still says "Report date: 2026-05-20 (Wednesday)").
DAILY_STATUS_PATH = "docs/00-live-status/XYZ_PILOT_DAILY_STATUS.md"
_DAILY_STATUS_HEADER_RE = re.compile(
    r"^> \*\*Report date:\*\* \d{4}-\d{2}-\d{2} \([^)]+\)",
    re.MULTILINE,
)


def _format_visibility_line(today: date, now_cest: datetime | None = None) -> str:
    """Render the visibility-rule sentence with today's date + 1-biz-day cutoff + timestamp.

    XYZ-PILOT-164 off-by-one fix (2026-05-30): cut-off is computed as
    `today - 1 business day` so items resolved on the LAST business day
    or later remain visible. For today Tue 2026-06-02, last biz day is
    Mon 2026-06-01 → items resolved Mon-or-Tue visible, items resolved
    Fri 2026-05-29 or earlier pruned.

    XYZ-PILOT-200 (2026-06-02): operator formalised the rule going forward
    as "show items resolved within the LAST business day only" + asked
    for a wall-clock timestamp in the visibility line so operators
    reading the morning email at 06:02 CEST know the line was rendered
    at the same instant. `now_cest` defaults to `datetime.now(CEST)`
    when not provided — the Lambda passes the explicit now_cest for
    deterministic rendering aligned with the rest of the email body.
    """
    cutoff = _subtract_business_days(today, 1)
    if now_cest is None:
        now_cest = datetime.now(timezone(timedelta(hours=2)))  # CEST = UTC+2
    timestamp = now_cest.strftime("%H:%M CEST")
    return (
        f"> *Today is {today.isoformat()} ({today.strftime('%a')}) {timestamp}: "
        f"cut-off date for the struck-through window is therefore "
        f"{cutoff.isoformat()} ({cutoff.strftime('%a')}). "
        f"Resolved-on-or-after-that-date items remain visible with ID, Severity, and Area struck-through; "
        f"older ones are pruned.*"
    )


def _verify_tracker_completeness(content: str) -> tuple[bool, list[str]]:
    """XYZ-PILOT-111: pre/post-write integrity check on the tracker content.

    Returns (ok, errors). Same structural subset as
    `services/chatbot/app.py::_verify_tracker_completeness`, inlined here so the
    Lambda zip stays a single .py file. The richer human-editor shell verifier
    also checks top-table styling and Summary hygiene. Three automated-writer
    invariants are enforced here:
      (A) Detail-section IDs are sequential XYZ-PILOT-001..N with no gaps.
      (B) Every detail section has a matching <a id="xyz-pilot-NNN"> anchor.
      (C) Every top-table row references an existing detail section.
    Returns (True, []) when content is clean.
    """
    errors: list[str] = []
    detail_ids = sorted({int(m) for m in re.findall(r"^### XYZ-PILOT-(\d{3})\b", content, re.MULTILINE)})
    anchor_ids = sorted({int(m) for m in re.findall(r'<a id="xyz-pilot-(\d{3})">', content)})
    top_ids = sorted({int(m) for m in re.findall(r"\|\s*~?~?\[XYZ-PILOT-(\d{3})\]", content)})
    if detail_ids:
        expected = list(range(1, max(detail_ids) + 1))
        missing = sorted(set(expected) - set(detail_ids))
        if missing:
            errors.append(f"detail-section gap: missing {['XYZ-PILOT-%03d' % n for n in missing]}")
    no_anchor = sorted(set(detail_ids) - set(anchor_ids))
    if no_anchor:
        errors.append(f"detail without anchor: {['XYZ-PILOT-%03d' % n for n in no_anchor]}")
    top_no_detail = sorted(set(top_ids) - set(detail_ids))
    if top_no_detail:
        errors.append(f"top-table row without detail: {['XYZ-PILOT-%03d' % n for n in top_no_detail]}")
    return len(errors) == 0, errors


def _emit_prune_candidates_to_repo(
    expired_rows: list[tuple[str, str, int]], today: date, run_id: str
) -> dict[str, Any]:
    """XYZ-PILOT-157 sub-task (b): write the prune queue as a candidate file
    under tests/logs/runs/<run_id>/XYZ_PILOT_DAILY_STATUS/issue-candidates.md
    via the GitHub Contents API. XYZ_MASTER picks it up via
    tests/scripts/run_xyz_master_filing.sh and applies each prune as its own
    one-ticket-per-invocation pass per CLAUDE.md Rule #5.

    `expired_rows` is a list of (poc_id, resolved_date_str, biz_days_since).
    The Lambda never mutates the tracker for prunes anymore — only the
    visibility-rule date refresh stays inline (small, atomic, single line).
    """
    candidate_path = f"tests/logs/runs/{run_id}/XYZ_PILOT_DAILY_STATUS/issue-candidates.md"

    blocks: list[str] = [
        f"# Candidate handoff to XYZ_MASTER — daily-status prune ({run_id})",
        "",
        "- **Source agent:** `XYZ_PILOT_DAILY_STATUS`",
        f"- **Run ID:** `{run_id}`",
        "- **Author:** `xyz-pilot-daily-status` Lambda (EventBridge cron Tue-Sat 04:00 UTC / 06:00 CEST)",
        "- **Per directive:** 2026-05-29 single-writer rule + one-ticket-per-invocation rule (CLAUDE.md Rule #5).",
        f"- **Today:** {today.isoformat()} — rows below are struck-through Resolved with `business_days_since_resolution > 1` and due for top-table pruning per [XYZ-PILOT-047](../../../../docs/XYZ_PILOT_ISSUE_TRACKER.md).",
        "",
        "Operator workflow to drain this queue:",
        "",
        f"1. `tests/scripts/run_xyz_master_filing.sh {run_id} --list` to enumerate candidates.",
        f"2. For each candidate: `tests/scripts/run_xyz_master_filing.sh {run_id} --prompt N` → paste output into Claude Code's Agent tool. The subagent removes ONE row per pass per CLAUDE.md Rule #5.",
        "",
        "---",
        "",
    ]

    for idx, (poc_id, resolved, biz_days) in enumerate(expired_rows, start=1):
        slug = poc_id.lower()
        blocks.extend([
            f"## Candidate {idx} — Prune top-table row {poc_id}",
            "",
            f"- **Existing ticket:** [{poc_id}](../../../../docs/XYZ_PILOT_ISSUE_TRACKER.md#{slug})",
            f"- **Resolution date marker:** {resolved}",
            f"- **Business days since resolution:** {biz_days} (beyond the 1-business-day visibility window (XYZ-PILOT-200): resolution day + next business day stay visible; pruned from the 2nd business day onward)",
            "",
            "### Mutation",
            "",
            f"Remove the single struck-through `| ~~[{poc_id}](#{slug})~~ ... |` row from the **Active issues** top table only. The Full-register detail section for {poc_id} remains untouched (it is the permanent audit-log entry). The detail-section `**Status:**` line already reads `Resolved {resolved}` per [XYZ-PILOT-047](../../../../docs/XYZ_PILOT_ISSUE_TRACKER.md); no further status change is needed.",
            "",
            "### Validation after XYZ_MASTER files",
            "",
            f"- `./scripts/verify_tracker_completeness.sh` exits 0 (check `(D)` no longer flags {poc_id} as stale).",
            f"- Top-table row count for {poc_id} drops from 1 to 0.",
            f"- Full-register `### {poc_id}` detail section remains present and unchanged.",
            "",
            "---",
            "",
        ])

    # Trailing newline to keep the file POSIX-clean.
    content = "\n".join(blocks).rstrip() + "\n"

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{candidate_path}"
    msg = (
        f"chore(tracker): queue {len(expired_rows)} prune candidate(s) for XYZ_MASTER\n\n"
        f"Per XYZ-PILOT-157 sub-task (b): the daily-status Lambda no longer mutates "
        f"the top table directly. Each expired struck-through Resolved row is emitted "
        f"as a candidate under tests/logs/runs/{run_id}/XYZ_PILOT_DAILY_STATUS/ "
        f"for XYZ_MASTER to apply via tests/scripts/run_xyz_master_filing.sh.\n\n"
        f"Co-Authored-By: xyz-pilot-daily-status Lambda <<NO_REPLY_EMAIL>>"
    )
    put_body = json.dumps({
        "message": msg,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": "main",
        "committer": {
            "name": "xyz-pilot-daily-status",
            "email": "<NO_REPLY_EMAIL>",
        },
    }).encode("utf-8")
    token = _github_token()
    if not token:
        return {"action": "skipped", "reason": "GitHub token unavailable"}
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "xyz-pilot-daily-status/1.0",
        "Content-Type": "application/json",
    }
    put_req = urllib.request.Request(url, method="PUT", headers=headers, data=put_body)
    try:
        with urllib.request.urlopen(put_req, timeout=20) as r:
            result = json.load(r)
    except urllib.error.HTTPError as exc:
        return {"action": "error", "step": "candidate_put", "code": exc.code,
                "msg": str(exc), "candidate_path": candidate_path,
                "candidates": [p for p, _, _ in expired_rows]}
    except (urllib.error.URLError, OSError) as exc:
        log.warning("candidate file PUT URLError (transient network): %s", exc)
        return {"action": "error", "step": "candidate_put", "code": 0,
                "msg": f"{type(exc).__name__}: {str(exc)[:120]}",
                "candidate_path": candidate_path,
                "candidates": [p for p, _, _ in expired_rows]}
    return {
        "action": "queued",
        "candidate_path": candidate_path,
        "candidates": [p for p, _, _ in expired_rows],
        "candidate_count": len(expired_rows),
        "commit": result.get("commit", {}).get("sha", "")[:7],
    }


def _trim_tracker_top_table(today: date) -> dict[str, Any]:
    """XYZ-PILOT-047 + XYZ-PILOT-061 + XYZ-PILOT-157 sub-task (b) — daily tracker hygiene.

      (a) Re-write the "Today is YYYY-MM-DD (Day) HH:MM CEST: cut-off ..."
          visibility-rule sentence to today's date + computed 1-biz-day cutoff
          (XYZ-PILOT-061 + XYZ-PILOT-164 + XYZ-PILOT-200). Direct PUT — small,
          single-line, atomic. The timestamp added per XYZ-PILOT-200 surfaces
          the moment the Lambda rendered the line so operators reading the
          06:02 CEST email know exactly when the cut-off snapshot was taken.
      (b) Scan for struck-through Resolved rows past the 1-business-day
          window (biz_days_since > 1) and **prune them directly** from the
          Active-issues table, in the same SHA-guarded PUT as (a). This is a
          sanctioned single-writer hygiene exception (CLAUDE.md Rule #5).
          XYZ-PILOT-287: this reverts the XYZ-PILOT-157(b) candidate reroute, which
          emitted prune candidates that the candidate-drain Lambda dropped as
          housekeeping — so the prune never applied automatically and expired
          rows accumulated until they blocked all XYZ_MASTER filing via verifier
          check (D). The pruned rows remain in the Full register; only the
          short-lived struck Active-table row is removed.

    Skipped silently (no error) when the SSM-backed GitHub token is
    unavailable — daily email still goes out.
    """
    token = _github_token()
    if not token:
        return {"action": "skipped", "reason": "GitHub token unavailable"}

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{TRACKER_PATH}?ref=main"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "xyz-pilot-daily-status/1.0",
    }
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=15
        ) as r:
            meta = json.load(r)
    except urllib.error.HTTPError as exc:
        return {"action": "error", "step": "fetch", "code": exc.code, "msg": str(exc)}

    sha = meta["sha"]
    # XYZ-PILOT-245: the GitHub Contents API returns encoding="none" + content=""
    # for files > 1 MB — the tracker crossed that threshold (~1.13 MB) on
    # 2026-06-08. base64-decoding the empty content yields "", so the regex sub
    # and prune scan below silently no-op (date_updated=False, 0 prune candidates)
    # and the morning hygiene never applies. Mirror the _fetch_tracker_text
    # download_url fallback (XYZ-PILOT-237) that fixed the same bug in the
    # movement-parsing path. The Contents-API PUT below is unaffected (the 1 MB
    # cap is GET-only), and meta["sha"] is returned regardless of content size.
    if meta.get("encoding") == "base64" and meta.get("content"):
        original_content = base64.b64decode(meta["content"]).decode("utf-8")
    else:
        download_url = meta.get("download_url")
        if not download_url:
            log.warning(
                "trim: empty content + no download_url (size=%s, encoding=%s) — "
                "skipping tracker date-refresh this run",
                meta.get("size"), meta.get("encoding"),
            )
            return {"action": "error", "step": "fetch-empty",
                    "size": meta.get("size"), "encoding": meta.get("encoding")}
        log.info("trim: tracker > 1MB Contents-API cap; falling back to download_url")
        with urllib.request.urlopen(
            urllib.request.Request(
                download_url, headers={"User-Agent": "xyz-pilot-daily-status/1.0"}),
            timeout=30,
        ) as raw:
            original_content = raw.read().decode("utf-8")

    # (a) XYZ-PILOT-061 — refresh the visibility-rule date sentence inline.
    new_content = _VISIBILITY_RULE_RE.sub(
        _format_visibility_line(today), original_content
    )
    date_updated = new_content != original_content
    if not date_updated and "Today is " in original_content:
        log.warning(
            "visibility-rule regex did not match — sentence may have drifted; "
            "date NOT refreshed. Check _VISIBILITY_RULE_RE anchors."
        )

    # (b) XYZ-PILOT-157 sub-task (b): scan-only — gather expired rows, do NOT
    # mutate the tracker. Resolution date taken from the compact top-table
    # marker first, then the Full-register Status line.
    status_by_id = _detail_status_by_id(new_content)
    poc_pat = re.compile(r"XYZ-PILOT-(\d+)")
    in_top_table = False
    expired_rows: list[tuple[str, str, int]] = []
    for line in new_content.split("\n"):
        if line.startswith("## Active issues"):
            in_top_table = True
            continue
        if line.startswith("## ") and in_top_table:
            in_top_table = False
            continue
        if in_top_table and line.startswith("|") and "~~" in line:
            pid = poc_pat.search(line)
            if pid:
                poc_id = pid.group(0)
                resolved = (
                    _resolved_date_from_top_row(line)
                    or _resolved_date_from_status(status_by_id.get(int(pid.group(1)), ""))
                )
                if resolved:
                    biz_days = _business_days_since(resolved, today)
                    # XYZ-PILOT-200: one-business-day visibility window — a
                    # Resolved row stays visible on its resolution day
                    # (biz_days_since=0) and through the next business day
                    # (biz_days_since=1); it is pruned once biz_days_since > 1
                    # (i.e. from the 2nd business day after resolution). The
                    # earlier `> 2` threshold kept rows an extra biz day.
                    if biz_days > 1:
                        expired_rows.append((poc_id, resolved, biz_days))

    # (b) XYZ-PILOT-287: prune the expired struck rows DIRECTLY in the same
    # SHA-guarded PUT as the (a) date refresh. This reverts the XYZ-PILOT-157(b)
    # candidate reroute: those prune candidates were dropped as `all_housekeeping`
    # by the candidate-drain Lambda, so the prune NEVER applied automatically and
    # expired rows accumulated until they blocked all XYZ_MASTER filing via
    # verifier (D) (the 2026-06-23 incident). The daily-status hygiene is a
    # sanctioned single-writer exception (CLAUDE.md Rule #5) and already
    # direct-PUTs the (a) header line; the prune rides the same atomic write. A
    # concurrent edit changes `sha` → the PUT 409s below (no clobber; next run
    # re-prunes). Removal is scoped to the Active-issues table and matches only
    # the struck rows the scan above flagged expired.
    pruned_ids: list[str] = []
    if expired_rows:
        expired_id_set = {pid for pid, _, _ in expired_rows}
        in_tt = False
        kept: list[str] = []
        for line in new_content.split("\n"):
            if line.startswith("## Active issues"):
                in_tt = True
            elif line.startswith("## ") and in_tt:
                in_tt = False
            if in_tt and line.startswith("|") and "~~" in line:
                pm = poc_pat.search(line)
                if pm and pm.group(0) in expired_id_set:
                    pruned_ids.append(pm.group(0))
                    continue
            kept.append(line)
        new_content = "\n".join(kept)
    prune_result: dict[str, Any] = {
        "action": "pruned" if pruned_ids else "no-change",
        "pruned": pruned_ids,
    }

    # Nothing changed at all (no date refresh, no expired rows) — short-circuit.
    if not date_updated and not pruned_ids:
        return {"action": "no-change", "date_updated": False, "prune": prune_result}

    # XYZ-PILOT-111: pre-PUT completeness check. `new_content` now differs from
    # `original_content` in the visibility-rule line AND (XYZ-PILOT-287) the
    # pruned expired struck Active rows. The check is the gate that the prune
    # is sound — removing expired struck rows is exactly what clears verifier
    # check (D); if it ever produced unsound output (e.g. orphaned anchors) the
    # outgoing check fails and the PUT is aborted (email still goes out).
    ok_in, errs_in = _verify_tracker_completeness(original_content)
    ok_out, errs_out = _verify_tracker_completeness(new_content)
    if not ok_in:
        log.warning("XYZ-PILOT-111: incoming tracker non-clean: %s", errs_in)
    if not ok_out:
        log.error(
            "XYZ-PILOT-111: ABORTING tracker date-refresh PUT — outgoing content "
            "fails completeness check. Errors: %s. Prune candidates queued: %s. "
            "Daily email still goes out; tracker date stays as-is.",
            errs_out, prune_result.get("candidates", []),
        )
        return {
            "action": "aborted",
            "reason": "XYZ-PILOT-111 completeness check failed on outgoing content",
            "outgoing_errors": errs_out,
            "incoming_errors": errs_in,
            "date_updated": False,
            "prune": prune_result,
        }

    prune_note = f" + prune {len(pruned_ids)} expired row(s) {pruned_ids}" if pruned_ids else ""
    subject = (
        f"chore(tracker): daily hygiene — refresh visibility-rule date → "
        f"{today.isoformat()}{prune_note}"
    )
    msg = (
        f"{subject}\n\n"
        f"Per XYZ-PILOT-061 (date refresh) + XYZ-PILOT-287 (direct prune of expired "
        f"struck Active rows). The XYZ-PILOT-157(b) candidate reroute is reverted for "
        f"prunes: it emitted candidates the candidate-drain Lambda dropped as "
        f"housekeeping, so expired rows accumulated and blocked filing via verifier "
        f"(D). Daily-status hygiene is a sanctioned single-writer exception (Rule #5); "
        f"this SHA-guarded PUT carries both the date refresh and the row prune. "
        f"Performed by xyz-pilot-daily-status Lambda at {today.isoformat()} UTC.\n\n"
        f"Co-Authored-By: Claude Opus 4.8 (1M context) <<OPERATOR_EMAIL>>"
    )
    put_body = json.dumps({
        "message": msg,
        "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
        "sha": sha,
        "branch": "main",
        "committer": {
            "name": "xyz-pilot-daily-status",
            "email": "<NO_REPLY_EMAIL>",
        },
    }).encode("utf-8")
    put_req = urllib.request.Request(
        url.split("?")[0], method="PUT",
        headers={**headers, "Content-Type": "application/json"},
        data=put_body,
    )
    try:
        with urllib.request.urlopen(put_req, timeout=20) as r:
            result = json.load(r)
    except urllib.error.HTTPError as exc:
        return {"action": "error", "step": "date_put", "code": exc.code,
                "msg": str(exc), "date_updated": False, "prune": prune_result}
    except (urllib.error.URLError, OSError) as exc:
        log.warning("date-refresh PUT URLError (transient network): %s", exc)
        return {"action": "error", "step": "date_put", "code": 0,
                "msg": f"{type(exc).__name__}: {str(exc)[:120]}",
                "date_updated": False, "prune": prune_result}
    return {
        "action": "updated",
        "date_updated": True,
        "commit": result.get("commit", {}).get("sha", "")[:7],
        "prune": prune_result,
    }


def _refresh_daily_status_header(today: date) -> dict[str, Any]:
    """XYZ-PILOT-061 extension: refresh the `> **Report date:** YYYY-MM-DD (Day)`
    header line at the top of docs/00-live-status/XYZ_PILOT_DAILY_STATUS.md so it tracks the
    actual current day. Mirrors `_trim_tracker_top_table`'s pattern.

    Skipped silently when the SSM-backed GitHub token is unavailable.
    """
    token = _github_token()
    if not token:
        return {"action": "skipped", "reason": "GitHub token unavailable"}

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DAILY_STATUS_PATH}?ref=main"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "xyz-pilot-daily-status/1.0",
    }
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
            meta = json.load(r)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        return {"action": "error", "step": "fetch", "msg": f"{type(exc).__name__}: {str(exc)[:120]}"}

    sha = meta["sha"]
    # XYZ-PILOT-245 sibling: download_url fallback for >1MB Contents-API responses.
    # DAILY_STATUS.md is small today (~27 KB) so this is defensive/consistency, but
    # the same empty-content trap would bite if it ever grows past 1 MB.
    if meta.get("encoding") == "base64" and meta.get("content"):
        content = base64.b64decode(meta["content"]).decode("utf-8")
    else:
        download_url = meta.get("download_url")
        if not download_url:
            return {"action": "error", "step": "fetch-empty", "size": meta.get("size")}
        with urllib.request.urlopen(
            urllib.request.Request(
                download_url, headers={"User-Agent": "xyz-pilot-daily-status/1.0"}),
            timeout=30,
        ) as raw:
            content = raw.read().decode("utf-8")

    new_line = f"> **Report date:** {today.isoformat()} ({today.strftime('%A')})"
    new_content, n = _DAILY_STATUS_HEADER_RE.subn(new_line, content, count=1)
    if n == 0:
        log.warning("DAILY_STATUS header regex did not match — header line may have drifted")
        return {"action": "no-match"}
    if new_content == content:
        return {"action": "no-change"}

    put_body = json.dumps({
        "message": (f"chore(daily-status): refresh Report date → {today.isoformat()}\n\n"
                    f"Per XYZ-PILOT-061 extension. Hands-off auto-refresh by the "
                    f"xyz-pilot-daily-status Lambda.\n\n"
                    f"Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"),
        "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
        "sha": sha,
        "branch": "main",
        "committer": {"name": "xyz-pilot-daily-status",
                      "email": "<NO_REPLY_EMAIL>"},
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url.split("?")[0], method="PUT",
                                   headers={**headers, "Content-Type": "application/json"},
                                   data=put_body), timeout=20) as r:
            result = json.load(r)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        return {"action": "error", "step": "put", "msg": f"{type(exc).__name__}: {str(exc)[:120]}"}
    return {"action": "updated", "commit": result.get("commit", {}).get("sha", "")[:7]}


def _doc_freshness() -> list[dict[str, Any]]:
    """For each tracked doc, fetch its last commit date via the GitHub REST API.

    XYZ-PILOT-046: visibility into doc drift. Files older than 3 days are flagged
    as "borderline"; older than 7 days as "stale". Used to surface the gap in
    the daily-status email before it compounds.
    """
    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for path in TRACKED_DOCS:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/commits?path={path}&per_page=1"
        req = urllib.request.Request(url, headers=_gh_headers())
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.load(r)
        except Exception as exc:  # noqa: BLE001
            log.warning("freshness fetch failed for %s: %s", path, exc)
            out.append({"path": path, "last": None, "age_days": None, "tag": "unknown"})
            continue
        if not data:
            out.append({"path": path, "last": None, "age_days": None, "tag": "missing"})
            continue
        last_iso = data[0].get("commit", {}).get("author", {}).get("date", "")
        try:
            last_dt = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
            age_days = int((now - last_dt).total_seconds() // 86400)
        except Exception:
            age_days = None
        tag = ("fresh" if age_days is not None and age_days <= 2
               else "borderline" if age_days is not None and age_days <= 6
               else "stale" if age_days is not None
               else "unknown")
        out.append({"path": path, "last": last_iso[:10] if last_iso else "—",
                    "age_days": age_days, "tag": tag})
    return out


def _badge(text: str, color: str) -> str:
    return (f'<span style="display:inline-block;padding:1px 8px;border-radius:3px;'
            f'font-size:.85em;color:#fff;background:{color}">{escape(text)}</span>')


# XYZ-PILOT-125: deterministic narrative compose. Replaces the Bedrock-based
# `_compose_narrative` (kept below as `_compose_narrative_bedrock_legacy`)
# after FIVE prompt-tightening iterations (XYZ-PILOT-120, -121, -122, -123,
# -124, -126) each fixed one Bedrock-output quirk and surfaced the next.
# The structural decoupling in XYZ-PILOT-126 (per-section <div> wrappers in
# Python) was supposed to bound the cascade — but Bedrock's unclosed
# `<table>` tags pulled subsequent Python sections INTO its broken table.
# Final architectural fix: stop asking the LLM to render HTML at all. Data
# is fully structured; render it with Python templates.

_TICKET_DETAIL_RE = re.compile(
    r"^### XYZ-PILOT-(\d{3}) — (.+?)$", re.MULTILINE,
)

def _extract_ticket_what(tracker_text: str, ticket_n: int) -> tuple[str, str, str]:
    """For one resolved/filed ticket, parse the tracker detail section to
    extract (title, outcome, what) for the Today's-work-so-far row.

    - title:   the text after `### XYZ-PILOT-NNN — ` (the section title)
    - outcome: derived from `**Status:** …` line — "Resolved", "Open",
               "Filed", etc.  Falls back to "Updated" if status absent.
    - what:    first 1-2 sentence summary, extracted from the FIRST
               paragraph of the section body that follows the metadata
               (after the `#### Summary` block if present, otherwise the
               first non-blank, non-bullet paragraph).
               Truncated to ~280 chars to fit the 520px What column.
    """
    section_re = re.compile(
        rf"^### XYZ-PILOT-{ticket_n:03d} — (.+?)\n(.*?)(?=^### XYZ-PILOT-\d{{3}} —|^---\s*\n|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = section_re.search(tracker_text)
    if not m:
        return (f"XYZ-PILOT-{ticket_n:03d}", "Resolved", "(no detail-section text found)")
    title = m.group(1).strip()
    body = m.group(2)

    # Extract outcome from Status line
    outcome = "Updated"
    status_m = re.search(r"^\s*-\s*\*\*Status:\*\*\s*(.+?)$", body, re.MULTILINE)
    if status_m:
        stxt = status_m.group(1).strip().strip("~").strip()
        # Take only the first word/phrase before "(" or " 2026-"
        outcome = re.split(r"\s*[(]|\s+\d{4}-\d{2}-\d{2}", stxt, maxsplit=1)[0].strip()
        if not outcome:
            outcome = "Resolved"

    # Extract first paragraph of substance. Skip metadata bullets +
    # blank lines + headings. Take the first non-bullet line as the lead.
    what_lines: list[str] = []
    in_substance = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if in_substance and what_lines:
                break
            continue
        # Skip metadata bullets at top of section
        if stripped.startswith("- **") or stripped.startswith("- Status") or stripped.startswith("#### ") or stripped.startswith("**What was reported"):
            continue
        # Skip headings within the body
        if stripped.startswith("#"):
            continue
        # Skip table rows
        if stripped.startswith("|") or stripped.startswith("---"):
            continue
        in_substance = True
        # Pull bold-prefix labels off the line (e.g. "**Resolution:** Lambda redeployed …")
        cleaned = re.sub(r"\*\*[^*]+\*\*\s*", "", stripped)
        what_lines.append(cleaned)
        # Cap at first 2 sentences or 280 chars
        joined = " ".join(what_lines)
        if len(joined) > 220 or joined.count(".") >= 2:
            break

    what = " ".join(what_lines).strip()
    # Strip surrounding markdown emphasis / inline tildes for clean display
    what = re.sub(r"~~([^~]+)~~", r"\1", what)
    what = re.sub(r"\s+", " ", what)
    if len(what) > 280:
        what = what[:277].rstrip() + "…"
    if not what:
        what = "(no body text)"

    return (title, outcome, what)


def _link(ticket_n: int) -> str:
    """XYZ-PILOT-092 anchor link."""
    return (f'<a href="https://github.com/{GITHUB_REPO}/blob/main/'
            f'docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-{ticket_n:03d}">'
            f'XYZ-PILOT-{ticket_n:03d}</a>')


def _render_headline_status(now_cest: datetime, ec2_state: str,
                             commits: list[dict[str, Any]],
                             ses_state: dict[str, str],
                             cognito_users: list[dict[str, str]],
                             doc_freshness: list[dict[str, Any]]) -> str:
    """Deterministic 4-row Headline status table (XYZ-PILOT-125)."""
    # Row 1: EC2 instance + next auto-start
    ec2_color = {"running": "#1B7A4D", "stopped": "#9A2A2A"}.get(ec2_state, "#888")
    ec2_emoji = {"running": "🟢", "stopped": "🔴"}.get(ec2_state, "⚪")
    ec2_row = (f"<b>EC2 instance</b>", f"{ec2_emoji} {ec2_state.upper()}")

    # Row 2: branch HEAD
    if commits:
        head = commits[0]
        head_row = (f"<b>Branch HEAD</b>",
                    f"<code>{escape(head.get('sha',''))}</code> · "
                    f"{escape(head.get('message',''))[:120]}")
    else:
        head_row = ("<b>Branch HEAD</b>", "(no commits in window)")

    # Row 3: active work today
    today = now_cest.date()
    today_commits = [c for c in commits
                     if c.get("date") and c.get("date").startswith(today.isoformat())]
    active_row = (f"<b>Active work today</b>",
                  f"{len(today_commits)} commit(s) since 00:00 CEST"
                  if today_commits else "— none yet today")

    # Row 4: biggest anomaly. Apply rule-engine.
    anomaly = _detect_biggest_anomaly(ses_state, cognito_users, doc_freshness)
    anomaly_row = (f"<b>Top anomaly</b>", anomaly or "— none flagged")

    rows_html = "".join(
        f'<tr><td style="padding:4px 10px;">{label}</td><td style="padding:4px 10px;">{value}</td></tr>'
        for label, value in [ec2_row, head_row, active_row, anomaly_row]
    )
    return (
        '<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Headline status</h3>'
        '<table style="border-collapse:collapse;margin:.4em 0;">'
        f"{rows_html}"
        '</table>'
    )


def _detect_biggest_anomaly(ses_state: dict[str, str],
                             cognito_users: list[dict[str, str]],
                             doc_freshness: list[dict[str, Any]]) -> str | None:
    """Rule-engine for the Headline-status anomaly row + the
    'Things worth surfacing' section. Returns a short anomaly description
    or None if everything looks fine. Used by both Headline + Things-worth.
    """
    # SES Failed
    failed = [a for a, s in ses_state.items() if s and s.lower() not in ("success", "")]
    if failed:
        return f"SES verification not-OK for {len(failed)} subscriber(s): {', '.join(failed)}"
    # Cognito stuck > 3 days
    stuck = [u["email"] for u in cognito_users if u.get("status") == "FORCE_CHANGE_PASSWORD"]
    if len(stuck) >= 2:
        return f"{len(stuck)} Cognito user(s) still in FORCE_CHANGE_PASSWORD: {', '.join(stuck)}"
    # Stale doc > 6 days
    stale = [d for d in doc_freshness if isinstance(d.get("age_days"), int) and d["age_days"] > 6]
    if stale:
        return f"{len(stale)} doc(s) > 6 days stale (XYZ-PILOT-046)"
    return None


def _render_todays_work(tracker_text: str | None,
                         resolved_ids: list[str],
                         filed_ids: list[str]) -> str:
    """Deterministic 3-column Today's work table (XYZ-PILOT-125 + XYZ-PILOT-120).

    Per XYZ-PILOT-124: ONE ROW PER TICKET, never group. Per XYZ-PILOT-122/-123:
    explicit per-cell width="N" attributes (HTML-4) + `<colgroup>` for
    modern clients — NO `table-layout:fixed`, NO `width:100%` at table
    level (those cascade-leak into other tables in Outlook).
    """
    rows: list[str] = []

    def _row(tid_int: int, outcome: str, what: str) -> str:
        link_html = _link(tid_int)
        return (
            "<tr>"
            f'<td width="140" style="padding:4px 10px;vertical-align:top;'
            f'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em;">'
            f"{link_html}</td>"
            f'<td width="110" style="padding:4px 10px;vertical-align:top;">{escape(outcome)}</td>'
            f'<td width="520" style="padding:4px 10px;vertical-align:top;line-height:1.4;">{escape(what)}</td>'
            "</tr>"
        )

    for tid_str in resolved_ids:
        tid_n = int(tid_str[len("XYZ-PILOT-"):]) if isinstance(tid_str, str) and tid_str.startswith("XYZ-PILOT-") else int(tid_str)
        _, outcome, what = _extract_ticket_what(tracker_text or "", tid_n)
        # Outcome is always Resolved for resolved_ids (parsed from Full-register Status).
        rows.append(_row(tid_n, "Resolved", what))

    for tid_str in filed_ids:
        tid_n = int(tid_str[len("XYZ-PILOT-"):]) if isinstance(tid_str, str) and tid_str.startswith("XYZ-PILOT-") else int(tid_str)
        _, outcome, what = _extract_ticket_what(tracker_text or "", tid_n)
        rows.append(_row(tid_n, "Filed", what))

    if not rows:
        body = ('<tr><td colspan="3" style="padding:8px 10px;color:#888;font-style:italic;">'
                "No tracker movement in the last 24 h.</td></tr>")
    else:
        body = "".join(rows)

    return (
        '<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Today\'s work so far</h3>'
        '<table style="border-collapse:collapse;margin:.4em 0;">'
        '<colgroup>'
        '<col style="width:140px;">'
        '<col style="width:110px;">'
        '<col style="width:520px;">'
        '</colgroup>'
        '<thead><tr style="background:#f6f7f8;">'
        '<th width="140" style="padding:6px 10px;text-align:left;">Ticket(s)</th>'
        '<th width="110" style="padding:6px 10px;text-align:left;">Outcome</th>'
        '<th width="520" style="padding:6px 10px;text-align:left;">What</th>'
        '</tr></thead>'
        f"<tbody>{body}</tbody>"
        '</table>'
        '<!-- XYZ-PILOT-125: deterministic Todays-work table, no Bedrock -->'
    )


def _render_things_worth_surfacing(ses_state: dict[str, str],
                                    cognito_users: list[dict[str, str]],
                                    doc_freshness: list[dict[str, Any]],
                                    commits: list[dict[str, Any]],
                                    now_cest: datetime) -> str:
    """Deterministic anomaly rule-engine output for the 3rd section.
    Replaces the Bedrock-narrated version of "Things worth surfacing"."""
    items: list[str] = []

    # 1. SES verification
    failed = [a for a, s in ses_state.items() if s and s.lower() not in ("success", "")]
    if failed:
        items.append(f"<b>SES verification non-OK</b> for {len(failed)} subscriber(s): "
                     + ", ".join(f"<code>{escape(a)}</code>" for a in failed)
                     + " — they will be silently skipped on every daily-status fire until "
                       "re-verified via <code>aws ses verify-email-identity</code>.")

    # 2. Cognito stuck
    stuck = [u for u in cognito_users if u.get("status") == "FORCE_CHANGE_PASSWORD"]
    if stuck:
        emails = ", ".join(f"<code>{escape(u['email'])}</code>" for u in stuck)
        items.append(f"<b>{len(stuck)} Cognito user(s) still in FORCE_CHANGE_PASSWORD</b>: "
                     f"{emails} — temp-password invite landed but first-login never completed. "
                     "Subscribers cannot use Chatbot / Operator Dashboard until they do.")

    # 3. Stale docs > 6 days (XYZ-PILOT-046)
    stale = [d for d in doc_freshness if isinstance(d.get("age_days"), int) and d["age_days"] > 6]
    if stale:
        items.append(f"<b>{len(stale)} doc(s) &gt; 6 days stale</b> (XYZ-PILOT-046 freshness rule): "
                     + ", ".join(f"<code>{escape(d['path'])}</code>" for d in stale[:5])
                     + (f", and {len(stale)-5} more" if len(stale) > 5 else "") + ".")

    # 4. No commits in 24 h
    if not commits:
        items.append("<b>No commits in the last 24 h</b> — repository is idle. Expected during "
                     "weekends / focus weeks; flag if it persists past Monday.")

    if not items:
        items.append("Nothing flagged today — pipeline + subscriber state nominal.")

    body = "".join(f"<li style=\"margin:.3em 0;\">{i}</li>" for i in items)
    return (
        '<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Things worth surfacing</h3>'
        f'<ol style="margin:.4em 0 .8em 0;padding-left:1.4em;">{body}</ol>'
    )


_BACKLOG_SEVERITY_BUCKETS = ("critical", "major", "minor", "feature", "external")
_BACKLOG_ROW_RE = re.compile(
    r"^\|\s*\[XYZ-PILOT-(\d{3})\]\([^)]+\)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$"
)


def _parse_backlog_by_severity(tracker_text: str | None) -> dict[str, list[dict[str, str]]]:
    """XYZ-PILOT-206: parse the tracker's Active issues top-table into a
    severity-bucketed dict.

    Returns: ``{"critical": [...], "major": [...], "minor": [...], "feature": [...], "external": [...]}``
    Each entry is ``{"id": "XYZ-PILOT-NNN", "area": "...", "summary": "..."}``.
    Excludes terminal-status (struck-through) rows — only Open / Workaround /
    Open · backlog / non-strike rows count as live backlog.
    """
    buckets: dict[str, list[dict[str, str]]] = {sev: [] for sev in _BACKLOG_SEVERITY_BUCKETS}
    if not tracker_text:
        return buckets
    in_active = False
    for raw_line in tracker_text.split("\n"):
        line = raw_line.rstrip()
        if line.startswith("## Active issues"):
            in_active = True
            continue
        if in_active and line.startswith("## ") and not line.startswith("## Active issues"):
            break
        if not in_active:
            continue
        # Skip terminal struck-through rows — they're resolved/wontfix in the
        # 1-biz-day visibility window (XYZ-PILOT-200), not backlog.
        if line.lstrip().startswith("| ~~"):
            continue
        m = _BACKLOG_ROW_RE.match(line)
        if not m:
            continue
        ticket_id = f"XYZ-PILOT-{m.group(1)}"
        sev = m.group(2).replace("*", "").strip().lower()
        area = m.group(3).strip()
        summary = m.group(4).strip()
        if sev in buckets:
            buckets[sev].append({"id": ticket_id, "area": area, "summary": summary})
    return buckets


def _render_e2e_status_html(ec2_state: str, tracker_text: str | None,
                             mcp_probe: dict[str, Any] | None,
                             chatbot_probe: dict[str, Any] | None,
                             qbr_probe: dict[str, Any] | None,
                             grafana_probe: dict[str, Any] | None) -> str:
    """XYZ-PILOT-206: E2E status overview — live ServiceURI probes + tracker
    backlog grouped by severity.

    Output: HTML fragment with three blocks — Live services table, Tracker
    totals row, and per-severity backlog tables (empty buckets are skipped
    so the email doesn't carry blank tables).
    """
    # ---- Live services table ----
    def _svc_row(label: str, url: str, probe: dict[str, Any] | None,
                 healthy_badge: tuple[str, str]) -> str:
        if ec2_state != "running":
            badge = _badge("STOPPED (EC2 down)", "#888")
        elif probe and probe.get("healthy"):
            badge = _badge(*healthy_badge)
        elif probe:
            badge = _badge(f"DOWN · {probe.get('note', '')}", "#C0392B")
        else:
            badge = _badge("UNKNOWN", "#888")
        return (
            f"<tr><td style='padding:4px 10px;'><b>{escape(label)}</b></td>"
            f"<td style='padding:4px 10px;'><a href='{escape(url)}' style='font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'>{escape(url)}</a> &nbsp; {badge}</td></tr>"
        )

    svc_rows = (
        _svc_row("Streamlit QBR",     QBR_URL,     qbr_probe,     ("RUNNING ✓",         "#1AAB40"))
        + _svc_row("<PILOT_NAME> Chatbot", CHATBOT_URL, chatbot_probe, ("RUNNING ✓",         "#1AAB40"))
        + _svc_row("Grafana",         GRAFANA_URL, grafana_probe, ("RUNNING ✓",         "#1AAB40"))
        + _svc_row("Public Exasol MCP", EXA_MCP_URL, mcp_probe,
                   (f"OAuth-gated ✓ ({(mcp_probe or {}).get('code', '')})", "#1AAB40"))
    )

    # ---- Backlog by severity ----
    buckets = _parse_backlog_by_severity(tracker_text)
    total_non_terminal = sum(len(rows) for rows in buckets.values())
    sev_emoji = {
        "critical": "\U0001F534",  # red circle
        "major":    "\U0001F7E0",  # orange circle
        "minor":    "\U0001F7E1",  # yellow circle
        "feature":  "\U0001F535",  # blue circle
        "external": "⚪",      # white circle
    }
    backlog_tables_html = ""
    for sev in _BACKLOG_SEVERITY_BUCKETS:
        rows = buckets[sev]
        if not rows:
            continue
        tbody = ""
        for entry in rows:
            tid_int = int(entry["id"].rsplit("-", 1)[-1])
            link = _link(tid_int)
            # Summary may contain Markdown links (`[XYZ-PILOT-NNN](#xyz-pilot-NNN)`)
            # plus emphasis (`*italic*`, `**bold**`) — preserve as-is via
            # _linkify_xyz_pilot_refs after render; escape only what's safe to
            # escape (anchor text + area). For the summary column, cap at
            # 220 chars so the email layout stays bounded for long entries.
            summary_capped = entry["summary"]
            if len(summary_capped) > 220:
                summary_capped = summary_capped[:217] + "..."
            tbody += (
                f"<tr>"
                f"<td style='padding:4px 10px;vertical-align:top;white-space:nowrap;'>{link}</td>"
                f"<td style='padding:4px 10px;vertical-align:top;'>{escape(entry['area'])}</td>"
                f"<td style='padding:4px 10px;vertical-align:top;color:#444;font-size:.95em;'>{escape(summary_capped)}</td>"
                f"</tr>"
            )
        backlog_tables_html += (
            f"<h4 style='margin:.8em 0 .2em 0;'>{sev_emoji[sev]} {sev.capitalize()} "
            f"<span style='color:#888;font-weight:normal;font-size:.85em;'>({len(rows)})</span></h4>"
            f"<table style='border-collapse:collapse;margin:.2em 0;'>"
            f"<thead><tr style='background:#f6f7f8;'>"
            f"<th style='padding:6px 10px;text-align:left;'>ID</th>"
            f"<th style='padding:6px 10px;text-align:left;'>Area</th>"
            f"<th style='padding:6px 10px;text-align:left;'>Summary</th>"
            f"</tr></thead><tbody>{tbody}</tbody></table>"
        )
    if total_non_terminal == 0:
        backlog_tables_html = (
            "<p style='color:#444;'><i>No non-terminal backlog rows. \U0001F389</i></p>"
        )

    totals_summary = (
        f"<p style='color:#444;margin:.4em 0;'>"
        f"<b>{total_non_terminal}</b> non-terminal backlog row(s) across {sum(1 for s in _BACKLOG_SEVERITY_BUCKETS if buckets[s])} severity bucket(s). "
        f"Terminal Resolved/Wontfix rows within the 1-business-day visibility window "
        f"<span style='color:#888;'>([XYZ-PILOT-200](https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-200))</span> "
        f"are excluded — see the live tracker for those.</p>"
    )

    return (
        '<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">'
        '<PILOT_NAME> E2E status '
        '<span style="color:#888;font-weight:normal;font-size:.85em;">(XYZ-PILOT-206)</span>'
        '</h3>'
        '<h4 style="margin:.4em 0 .2em 0;">Live services</h4>'
        '<table style="border-collapse:collapse;margin:.2em 0;">'
        f'{svc_rows}'
        '</table>'
        '<h4 style="margin:.8em 0 .2em 0;">Backlog by severity</h4>'
        f'{totals_summary}'
        f'{backlog_tables_html}'
    )


def _compose_narrative_deterministic(now_cest: datetime, ec2_state: str,
                                      ses_state: dict[str, str],
                                      cognito_users: list[dict[str, str]],
                                      commits: list[dict[str, Any]],
                                      doc_freshness: list[dict[str, Any]],
                                      tracker_text: str | None,
                                      resolved_ids: list[str],
                                      filed_ids: list[str],
                                      mcp_probe: dict[str, Any] | None = None,
                                      chatbot_probe: dict[str, Any] | None = None,
                                      qbr_probe: dict[str, Any] | None = None,
                                      grafana_probe: dict[str, Any] | None = None) -> str:
    """XYZ-PILOT-125: pure-Python narrative compose. Zero Bedrock dependency.
    Replaces `_compose_narrative` (renamed `_compose_narrative_bedrock_legacy`
    and kept below for emergency rollback).

    XYZ-PILOT-206: the E2E status section (live ServiceURI probes + backlog by
    severity) is injected between Headline status and Today's work — that
    placement was the operator-confirmed reading flow.
    """
    return (
        _render_headline_status(now_cest, ec2_state, commits, ses_state, cognito_users, doc_freshness)
        + "\n"
        + _render_e2e_status_html(ec2_state, tracker_text,
                                   mcp_probe, chatbot_probe, qbr_probe, grafana_probe)
        + "\n"
        + _render_todays_work(tracker_text, resolved_ids, filed_ids)
        + "\n"
        + _render_things_worth_surfacing(ses_state, cognito_users, doc_freshness, commits, now_cest)
    )


def _compose_narrative_bedrock_legacy(now_cest: datetime, yesterday_cest: datetime,
                       ec2_state: str, ses_state: dict[str, str],
                       cognito_users: list[dict[str, str]],
                       commits: list[dict[str, Any]],
                       doc_freshness: list[dict[str, Any]],
                       resolved_ids: list[str],
                       filed_ids: list[str]) -> str:
    """Ask Bedrock Claude Sonnet 4.6 to compose the narrative sections.

    Returns HTML fragment to be inserted between the Environment table
    and the Subscriber pipeline table. Three sections:
      1. Headline status (4-row summary table)
      2. Today's work so far (3-column Ticket(s) | Outcome | What table — XYZ-PILOT-120)
      3. Things worth surfacing (numbered list of decisions/anomalies)

    Falls back to an empty string on any Bedrock failure — the rest of
    the email still composes from deterministic data.
    """
    # Pack the data into a compact text bundle for the model.
    commits_lines = "\n".join(
        f"  {c['sha']} | {c['author']:18s} | {c['message']}" for c in commits[:12]
    ) or "  (no commits in the last 24 h)"
    ses_lines = "\n".join(
        f"  {addr:40s} {ses_state.get(addr, '—')}" for addr in sorted(ses_state)
    )
    cog_lines = "\n".join(
        f"  {u['email']:40s} {u['status']}" for u in sorted(cognito_users, key=lambda x: x["email"])
    ) or "  (no Cognito users)"
    def _fmt_age(d: dict[str, Any]) -> str:
        ad = d.get("age_days")
        return f"{ad}d" if isinstance(ad, int) else "—"

    fresh_lines = "\n".join(
        f"  {d['path']:50s} {d.get('last') or '—'}  age={_fmt_age(d)}  tag={d.get('tag') or '—'}"
        for d in doc_freshness
    )
    # Authoritative ticket movement (from the tracker, not from commit verbs).
    resolved_line = ", ".join(resolved_ids) if resolved_ids else "(none)"
    filed_line    = ", ".join(filed_ids)    if filed_ids    else "(none)"

    user_prompt = f"""You are composing the narrative middle of an automated daily-status email for the <PILOT_NAME> (an Exasol-on-AWS analytics pilot).

The deterministic sections (Environment, Subscriber pipeline, Recent commits, Documentation freshness, Links) are already rendered by the calling code. Your job is to produce three additional HTML sections, returned as a single HTML fragment with no <html>/<body> wrappers. Use the same inline CSS style as the rest of the email: <h3 style="border-bottom:1px solid #eee;padding-bottom:4px;"> for section headers, <table style="border-collapse:collapse;margin:.4em 0;"> with padded cells for tables, yellow callout boxes (<div style="background:#FFF6E5;border:1px solid #F0C36D;border-radius:4px;padding:10px 14px;margin:1em 0;">) for things that need a heads-up.

Be terse and operational, like a senior engineer briefing a teammate at start-of-day. No fluff. No emoji except ⚠ for genuine warnings. Quote specific IDs (XYZ-PILOT-NNN, commit SHAs, email addresses). Surface decisions that need someone to make a call.

THE THREE SECTIONS TO PRODUCE (in this order):

### Headline status
A 4-row table with the most important top-of-mind facts. Suggested rows:
  - EC2 instance state (with context: when does it auto-start next? Any ad-hoc start needed?)
  - Branch HEAD short SHA + subject of the latest commit
  - "Active work today" (count of commits since 00:00 CEST OR a "— none yet today" if zero)
  - Any single biggest anomaly worth flagging at the top (stale doc, SES Failed, etc.) — yellow callout if real

### Today's work so far

**Table format (XYZ-PILOT-120 — REQUIRED):** a 3-column HTML table with **exactly** these columns, in **this** order:

| Ticket(s) | Outcome | What |
|---|---|---|

- **Ticket(s)**: one or more `XYZ-PILOT-NNN` IDs, grouped where related. Link each ID to the tracker anchor using the same `<a href="https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md">XYZ-PILOT-NNN</a>` pattern used elsewhere in the email.
- **Outcome**: ONE WORD verdict — typically `Resolved`, but also `Filed`, `Re-opened`, `Blocked`, `Backlog`, `Phase-1 MVP`, `Phase-2 deferred`. **Single word or short hyphenated phrase. No prose.**
- **What**: 1-2 line **maximum** description of what was actually done — the technical substance. Examples of good "What" text:
  - "Chart-path V_→MV_ rewrite + SYSTEM_PROMPT guidance; cold-cache latency 43 s → 0.2 s (207×)."
  - "New `xyz-pilot-user-mgmt` Lambda with 3 actions (cohort/registered/invite) + Operator Dashboard card."
  - "Multi-writer tracker completeness verifier wired into chatbot + Lambda; CLAUDE.md rule extended."
  - "Reconstructed 17 missing detail sections from top-table + git log + in-code markers."
  Do NOT pad with what HASN'T happened or what's next — that goes in section 3. Do NOT exceed 2 visible lines per row.

Use the **TRACKER MOVEMENT** block below (authoritative — derived from `**Status:** Resolved (date)` markers in the tracker prose), NOT raw verbs in commit messages, to decide which XYZ-PILOT IDs count as "Resolved today". **ONE ROW PER TICKET — STRICT (XYZ-PILOT-124)**: every XYZ-PILOT-NNN gets its OWN row with its OWN Outcome and its OWN What. **Never group multiple ticket IDs into a single row's Ticket(s) cell** — that hides their individual outcomes / what-was-done details. If two tickets share the same physical implementation, repeat the What text across both rows (or write each one with its own distinct framing); never leave Outcome or What empty by piggy-backing them onto an earlier row's cell. The commit list is supplementary context.

**SUPERSEDED-RESOLUTION DISCIPLINE (XYZ-PILOT-155 follow-up, 2026-06-09).** When a ticket's resolution-evidence prose (the `#### Resolution - shipped <date>` section appended by the XYZ_MASTER background filer) contains the word **`superseded`** — typically phrased "superseded by XYZ-PILOT-NNN" — that ticket is an ADMINISTRATIVE RECLASSIFICATION, not a shipment. Treat it as Outcome=`Superseded` (NOT `Resolved`) and DO NOT count it in any "X critical/major/minor defects shipped" tally. The actual work moved to the successor ticket(s) named in the prose and remains Open there. Same rule applies to `Wontfix` rows: they are administrative closures, NOT shipments. The "shipped" tally must only count Resolved-status rows whose resolution-evidence describes actual code/infra/doc work landing — not status transitions whose only purpose is to reclassify the ticket. Example: XYZ-PILOT-155 (<AWS_REGION> UAT readiness) was Resolved 2026-06-09 with resolution-evidence "superseded by XYZ-PILOT-226"; XYZ-PILOT-226 is the real ticket carrying the work and is Open · major. The headline must NOT advertise XYZ-PILOT-155 as "1 critical defect shipped".

Include EVERY ID from the RESOLVED list — do not collapse multiple IDs into a "various" bucket and do not silently drop any. If the resolved list is long, the table grows; that's correct behaviour. If a commit message references a ticket that is NOT in the RESOLVED list (e.g. the commit only filed it, or only made partial progress), add it to the table with Outcome=`Filed` or `Backlog touched` rather than `Resolved`.

**Column widths (XYZ-PILOT-123 — REQUIRED, table-layout:fixed REMOVED to fix cascade-leak):**

Render the table with this **EXACT** structure (XYZ-PILOT-123: isolated layout, no `table-layout:fixed`, no `width:100%` at table level — those leaked into the Headline status table above AND cascaded into the deterministic Subscriber-pipeline tables below, breaking their rendering):

```
<table style="border-collapse:collapse;margin:.4em 0;">
  <colgroup>
    <col style="width:140px;">
    <col style="width:110px;">
    <col style="width:520px;">
  </colgroup>
  <thead>
    <tr style="background:#f6f7f8;">
      <th width="140" style="padding:6px 10px;text-align:left;">Ticket(s)</th>
      <th width="110" style="padding:6px 10px;text-align:left;">Outcome</th>
      <th width="520" style="padding:6px 10px;text-align:left;">What</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td width="140" style="padding:4px 10px;vertical-align:top;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em;">[linked XYZ-PILOT-NNN; multiple? separate with &lt;br&gt;]</td>
      <td width="110" style="padding:4px 10px;vertical-align:top;">[Resolved or Filed or Re-opened ...]</td>
      <td width="520" style="padding:4px 10px;vertical-align:top;line-height:1.4;">[1-2 line technical-substance summary, wraps naturally]</td>
    </tr>
    [... one row per individual ticket — DO NOT group multiple IDs in one Ticket(s) cell ...]
  </tbody>
</table>
```

**Critical rules to avoid cascade-leak into the rest of the email (XYZ-PILOT-123):**
1. The OUTER `<table>` **MUST NOT** have `table-layout:fixed`. **MUST NOT** have `width:100%`. Just `border-collapse:collapse;margin:.4em 0;` — same as every other table in the email.
2. Use HTML-4 `width="N"` attributes on each `<th>` and `<td>` — Outlook honors these more reliably than `style="width:Npx"` and they DO NOT cascade to subsequent tables.
3. The `<colgroup><col style="width:Npx"></colgroup>` provides a CSS-side hint for modern clients.
4. **The Today's work so far table affects ONLY ITSELF.** Do NOT apply this width spec to the Headline status table above (which keeps its default plain style). Do NOT mention layout/widths anywhere else in your output.
5. Always close the `</table>` cleanly before the next `<h3>` section. Add a comment `<!-- end of Today's work table -->` immediately after the `</table>` so any cascade-leak is at least diagnosable.

The What column wraps naturally at 520 px wide; do NOT use `word-break:break-word` (let normal word-wrap handle it).

**Centering / alignment cascade-leak prevention (XYZ-PILOT-124 — STRICT):**
- Do **NOT** use `text-align:center` anywhere in your output. Every section, every table, every cell should be left-aligned.
- Do **NOT** use the HTML-4 `align=` attribute (e.g. `<table align="center">`, `<td align="center">`, `<div align="center">`). It cascades into subsequent sibling elements in some email clients (Outlook).
- Do **NOT** wrap any of your three sections in a `<center>` element or `<div style="text-align:center">`.
- Every section starts with a left-aligned `<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">` (no `text-align`).
- Every `<table>` you emit closes cleanly with `</table>` before the next `<h3>` or `<p>`.
- Right after the Todays-work table's `</table>`, emit an HTML comment `<!-- end of Todays work table -->` so any future cascade-leak is diagnosable.

The deterministic sections below your output (Subscriber pipeline, Recent commits, Documentation freshness, Environment, Links) MUST render exactly as the calling code emits them — left-aligned, default font, no special alignment. Any `align=center` / `text-align:center` you accidentally leave in your fragment will leak into those sections via the email-client cascade and break their rendering.

If the FILED list is non-empty, add a brief sentence under the table: "N new defects filed today: XYZ-PILOT-AAA, XYZ-PILOT-BBB…". Don't list them as work-done items.

### Things worth surfacing
A numbered list (HTML <ol><li>) of decisions / anomalies / open questions the operator should act on. Be specific. Examples of what counts:
  - SES Failed address → needs re-verification (call out who, how many times it has lapsed)
  - Cognito user stuck in FORCE_CHANGE_PASSWORD > 3 days → likely hit the OAuth-Error trap (XYZ-PILOT-044), needs a heads-up
  - Stale doc (>6 days) → needs a pass per XYZ-PILOT-046
  - Open XYZ-PILOT tickets with no movement in 24 h → status check
  - Anything from the data below that looks off

If nothing needs surfacing today, output one item saying so honestly — do not invent issues.

DATA AS OF {now_cest.strftime('%a %Y-%m-%d %H:%M CEST')} (reporting on {yesterday_cest.strftime('%a %Y-%m-%d')}):

EC2 state: {ec2_state}

TRACKER MOVEMENT (authoritative — derived from tracker `**Status:**` markers, NOT commit verbs):
  RESOLVED in this window: {resolved_line}
  FILED    in this window: {filed_line}

RECENT COMMITS (newest first, supplementary context only — do NOT use commit verbs to decide Resolved/Filed):
{commits_lines}

SES VERIFICATION STATUS:
{ses_lines}

COGNITO USERS:
{cog_lines}

DOCUMENTATION FRESHNESS:
{fresh_lines}

Output the HTML fragment only — three <h3> sections with their content, in the order above. No preamble, no closing remarks.
"""

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4000,
        "temperature": 0.3,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    try:
        resp = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps(body).encode("utf-8"),
            accept="application/json",
            contentType="application/json",
        )
        result = json.loads(resp["body"].read())
        chunks = result.get("content", [])
        text = "".join(c.get("text", "") for c in chunks if c.get("type") == "text")
        log.info("bedrock narrative composed: %d chars, stop_reason=%s",
                 len(text), result.get("stop_reason"))
        return text.strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("bedrock narrative failed (falling back to deterministic-only): %s", exc)
        return ""


def _dependabot_open_prs() -> dict[str, Any]:
    """XYZ-PILOT-196: list currently-open Dependabot PRs on the repo.

    Returns:
        {
          "ok": True/False,
          "fetched_at": "<iso UTC>",
          "prs": [
            {"number": N, "title": str, "createdAt": "<iso UTC>",
             "url": str, "days_open": int},
            ...
          ],
          "total": N,
          "error": "<msg>" if fetch failed,
        }

    Surfaces pending review-requested Dependabot PRs at the next
    06:00 CEST daily-status fire. Closes the "operator missed PR #3
    + #4 in the email flood" gap surfaced 2026-06-01 evening.
    Lightweight — single GitHub Pulls API call, no auth elevation,
    runs in <1 s on the Lambda. Falls back to ok=False on API
    failure (advisory; never fails the digest).
    """
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        f"https://api.github.com/repos/{GITHUB_REPO}/pulls"
        f"?state=open&per_page=30&sort=created&direction=asc"
    )
    req = urllib.request.Request(url, headers=_gh_headers())
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        msg = _github_error_summary(exc, "Pull requests: Read (or classic PAT repo)")
        log.warning("dependabot open PRs fetch failed: %s", msg)
        return {
            "ok": False,
            "fetched_at": fetched_at,
            "prs": [],
            "total": 0,
            "error": msg,
        }
    now = datetime.now(timezone.utc)
    prs: list[dict[str, Any]] = []
    for pr in data or []:
        author = ((pr.get("user") or {}).get("login") or "").lower()
        if author not in ("dependabot[bot]", "app/dependabot"):
            continue
        created_at_str = pr.get("created_at") or ""
        try:
            created_at = datetime.strptime(created_at_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            days_open = (now - created_at).days
        except ValueError:
            days_open = 0
        prs.append({
            "number": int(pr.get("number", 0)),
            "title": str(pr.get("title", ""))[:160],
            "createdAt": created_at_str,
            "url": pr.get("html_url", ""),
            "days_open": days_open,
        })
    prs.sort(key=lambda p: p["number"])
    return {
        "ok": True,
        "fetched_at": fetched_at,
        "prs": prs,
        "total": len(prs),
    }


_RETENTION_DISPOSABLE_NAMES = {".DS_Store"}
_RETENTION_DISPOSABLE_SUFFIXES = {".pyc", ".tmp", ".bak", ".orig"}
_RETENTION_E2E_RAW_NAMES = {
    "agent.stdout",
    "agent.stderr",
    "notification.stdout",
    "notification.stderr",
}
_RETENTION_E2E_RAW_SUFFIXES = {
    ".body",
    ".err",
    ".out",
    ".status",
    ".jsonl",
}
_RETENTION_E2E_AUDIT_NAMES = {
    "manifest.md",
    "results.md",
    "report.md",
    "commands.md",
    "skills.md",
    "issue-candidates.md",
    "run.sh",
    "agent.exit",
    "notification.sent.json",
}


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _retention_classify(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    suffix = ""
    if "." in name:
        suffix = "." + name.rsplit(".", 1)[-1].lower()
    parts = path.split("/")
    if name in _RETENTION_DISPOSABLE_NAMES or suffix in _RETENTION_DISPOSABLE_SUFFIXES or "__pycache__" in parts:
        return "disposable_tracked_junk"
    if path.startswith("docs/archive/XYZ_PILOT_ISSUE_TRACKER_"):
        return "tracker_guardrail_archive"
    if path.startswith("docs/archive/"):
        return "docs_archive_other"
    if path.startswith("tests/logs/runs/"):
        if "/candidate-outcomes/" in path:
            return "e2e_candidate_outcome_audit"
        if name in _RETENTION_E2E_AUDIT_NAMES:
            return "e2e_run_audit_evidence"
        if name in _RETENTION_E2E_RAW_NAMES or suffix in _RETENTION_E2E_RAW_SUFFIXES:
            return "e2e_raw_runtime_artifact"
        return "e2e_other_runtime_artifact"
    if path.startswith("docs/70-generated-qa/qa/") or path.startswith("docs/70-generated-qa/architecture/"):
        return "generated_reference_capture"
    if path.startswith("clients/") or path.startswith("archives/"):
        return "client_source_archive"
    return "ordinary_tracked_file"


def _retention_policy(category: str) -> str:
    return {
        "disposable_tracked_junk": "Cleanup candidate after operator approval; no audit value expected.",
        "tracker_guardrail_archive": "Keep; report growth. Cleanup only after off-platform backup and explicit operator policy.",
        "docs_archive_other": "Keep; report growth. Archive-class content requires owner approval before deletion.",
        "e2e_candidate_outcome_audit": "Keep; candidate decision audit trail.",
        "e2e_run_audit_evidence": "Keep; E2E run review evidence.",
        "e2e_raw_runtime_artifact": "Report-only candidate after retention window; preserve until policy approved.",
        "e2e_other_runtime_artifact": "Report-only candidate after retention window; preserve until policy approved.",
        "generated_reference_capture": "Keep unless superseded by explicit doc owner cleanup.",
        "client_source_archive": "Never auto-delete; source/original input material.",
        "ordinary_tracked_file": "No cleanup action.",
    }.get(category, "No cleanup action.")


def _retention_dir_key(path: str) -> str:
    parts = path.split("/")
    if len(parts) >= 4 and parts[:3] == ["tests", "logs", "runs"]:
        return "/".join(parts[:4])
    if len(parts) >= 2 and parts[:2] == ["docs", "archive"]:
        return "docs/archive"
    if len(parts) == 2:
        return parts[0]
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0] if parts else "."


def _repo_retention_report() -> dict[str, Any]:
    """Report-only tracked-file retention scan for the 06:00 status email.

    Uses the GitHub Git tree API so Lambda does not need a local checkout.
    No files are deleted, moved, or mutated. This is the same conservative
    policy model as `scripts/check_repo_retention_candidates.py`.
    """
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"https://api.github.com/repos/{GITHUB_REPO}/git/trees/main?recursive=1"
    req = urllib.request.Request(url, headers=_gh_headers())
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        msg = _github_error_summary(exc, "Contents: Read (or classic PAT repo)")
        log.warning("repository retention tree fetch failed: %s", msg)
        return {
            "ok": False,
            "mode": "report-only",
            "fetched_at": fetched_at,
            "error": msg,
            "categories": {},
            "directories": [],
            "tracked_file_count": 0,
            "tracked_bytes": 0,
        }

    categories: dict[str, dict[str, Any]] = {}
    directories: dict[str, int] = {}
    tracked_file_count = 0
    tracked_bytes = 0
    for item in data.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = str(item.get("path", ""))
        size = int(item.get("size") or 0)
        tracked_file_count += 1
        tracked_bytes += size
        category = _retention_classify(path)
        bucket = categories.setdefault(
            category,
            {"count": 0, "bytes": 0, "policy": _retention_policy(category), "files": []},
        )
        bucket["count"] += 1
        bucket["bytes"] += size
        bucket["files"].append({"path": path, "bytes": size})
        dkey = _retention_dir_key(path)
        directories[dkey] = directories.get(dkey, 0) + size

    for bucket in categories.values():
        bucket["files"] = sorted(
            bucket["files"],
            key=lambda row: (-int(row["bytes"]), str(row["path"])),
        )

    return {
        "ok": True,
        "mode": "report-only",
        "fetched_at": fetched_at,
        "truncated": bool(data.get("truncated")),
        "tracked_file_count": tracked_file_count,
        "tracked_bytes": tracked_bytes,
        "categories": categories,
        "directories": [
            {"path": path, "bytes": size}
            for path, size in sorted(directories.items(), key=lambda row: (-row[1], row[0]))
        ],
    }


def _render_dependabot_prs_html(data: dict[str, Any]) -> str:
    """XYZ-PILOT-196: render the Dependabot-open-PRs digest as an HTML section.

    Surfaces pending PRs requiring operator review/merge. Empty state
    is a quiet green confirmation; non-empty state is a compact table
    with PR number + days-open badge + title link.
    """
    if not data.get("ok"):
        return (
            '<div style="background:#FFF6E5;border:1px solid #F0C36D;border-radius:4px;'
            'padding:10px 14px;margin:1em 0;">'
            f'⚠ Dependabot open-PRs fetch failed: '
            f'<code>{escape(str(data.get("error", "unknown error")))[:200]}</code>'
            '</div>'
        )
    prs = data.get("prs", []) or []
    if not prs:
        return '<p style="color:#155724;font-size:.9em;">✅ No pending Dependabot PRs.</p>'

    rows = []
    for pr in prs[:10]:
        days = int(pr.get("days_open", 0))
        if days > 30:
            badge_bg, badge_fg, badge_text = "#F8D7DA", "#721C24", f"🔴 {days}d"
        elif days > 14:
            badge_bg, badge_fg, badge_text = "#FFF3CD", "#856404", f"🟡 {days}d"
        else:
            badge_bg, badge_fg, badge_text = "#D1ECF1", "#0C5460", f"🆕 {days}d"
        rows.append(
            "<tr>"
            f'<td style="padding:4px 8px;white-space:nowrap;">'
            f'<a href="{escape(pr.get("url",""))}" style="color:#0366D6;text-decoration:none;">'
            f'#{pr["number"]}</a></td>'
            f'<td style="padding:4px 8px;white-space:nowrap;">'
            f'<span style="background:{badge_bg};color:{badge_fg};font-size:.75em;'
            f'padding:2px 6px;border-radius:3px;font-weight:600;">{badge_text}</span></td>'
            f'<td style="padding:4px 8px;font-size:.9em;">{escape(pr["title"])}</td>'
            "</tr>"
        )
    tail = ""
    if len(prs) > 10:
        tail = (
            f'<p style="color:#666;font-size:.85em;margin-top:.4em;">'
            f'+ {len(prs) - 10} more — see '
            f'<code>gh pr list -A app/dependabot</code></p>'
        )
    return (
        f'<p style="color:#856404;font-size:.9em;margin-bottom:.4em;">'
        f'🔄 {len(prs)} pending Dependabot PR(s) awaiting review/merge.</p>'
        '<table style="border-collapse:collapse;width:100%;font-family:-apple-system,'
        'BlinkMacSystemFont,sans-serif;">'
        '<thead><tr style="background:#F6F8FA;text-align:left;font-size:.85em;">'
        '<th style="padding:4px 8px;">#</th>'
        '<th style="padding:4px 8px;">Age</th>'
        '<th style="padding:4px 8px;">Title</th>'
        '</tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody></table>'
        + tail
    )


def _render_repo_retention_html(data: dict[str, Any]) -> str:
    """Render report-only repository retention signals for the morning status."""
    if not data.get("ok"):
        return (
            '<div style="background:#FFF6E5;border:1px solid #F0C36D;border-radius:4px;'
            'padding:10px 14px;margin:1em 0;">'
            f'⚠ Repository retention report failed: '
            f'<code>{escape(str(data.get("error", "unknown error")))[:200]}</code>'
            '</div>'
        )

    categories = data.get("categories", {}) or {}
    directories = data.get("directories", []) or []
    tracked_count = int(data.get("tracked_file_count", 0))
    tracked_bytes = int(data.get("tracked_bytes", 0))
    truncated_note = ""
    if data.get("truncated"):
        truncated_note = (
            '<p style="color:#856404;font-size:.85em;">'
            '⚠ GitHub tree response was truncated; size totals may be incomplete.</p>'
        )

    category_rows = ""
    for name, bucket in sorted(
        categories.items(),
        key=lambda item: (-int(item[1].get("bytes", 0)), item[0]),
    )[:10]:
        policy = str(bucket.get("policy", "No cleanup action."))
        category_rows += (
            "<tr>"
            f"<td style='padding:4px 10px;'><code>{escape(name)}</code></td>"
            f"<td style='padding:4px 10px;text-align:right;'>{int(bucket.get('count', 0))}</td>"
            f"<td style='padding:4px 10px;text-align:right;'>{escape(_human_bytes(int(bucket.get('bytes', 0))))}</td>"
            f"<td style='padding:4px 10px;color:#555;font-size:.9em;'>{escape(policy)}</td>"
            "</tr>"
        )

    dir_rows = ""
    for item in directories[:8]:
        dir_rows += (
            "<tr>"
            f"<td style='padding:4px 10px;'><code>{escape(str(item.get('path', '')))}</code></td>"
            f"<td style='padding:4px 10px;text-align:right;'>{escape(_human_bytes(int(item.get('bytes', 0))))}</td>"
            "</tr>"
        )

    detail_classes = ("disposable_tracked_junk", "e2e_raw_runtime_artifact", "e2e_other_runtime_artifact")
    file_rows = ""
    for category in detail_classes:
        bucket = categories.get(category) or {}
        for item in (bucket.get("files") or [])[:5]:
            file_rows += (
                "<tr>"
                f"<td style='padding:4px 10px;'><code>{escape(str(item.get('path', '')))}</code></td>"
                f"<td style='padding:4px 10px;'><code>{escape(category)}</code></td>"
                f"<td style='padding:4px 10px;text-align:right;'>{escape(_human_bytes(int(item.get('bytes', 0))))}</td>"
                "</tr>"
            )
    if not file_rows:
        file_rows = (
            "<tr><td colspan='3' style='padding:6px 10px;color:#155724;'>"
            "No tracked disposable/runtime-noise files are currently flagged.</td></tr>"
        )

    return (
        '<p style="color:#444;font-size:.9em;margin:.2em 0 .6em 0;">'
        '<b>Report-only.</b> No files are deleted by the 06:00 cleanup task. '
        'Deletion rules require explicit operator approval before activation.'
        f' Scanned {tracked_count} tracked file(s), {_human_bytes(tracked_bytes)} total.'
        '</p>'
        f'{truncated_note}'
        '<h4 style="margin:.6em 0 .2em 0;">Candidate classes by size</h4>'
        '<table style="border-collapse:collapse;margin:.2em 0;">'
        '<thead><tr style="background:#f6f7f8;">'
        '<th style="padding:6px 10px;text-align:left;">Class</th>'
        '<th style="padding:6px 10px;text-align:right;">Files</th>'
        '<th style="padding:6px 10px;text-align:right;">Size</th>'
        '<th style="padding:6px 10px;text-align:left;">Policy</th>'
        '</tr></thead><tbody>'
        f'{category_rows}'
        '</tbody></table>'
        '<h4 style="margin:.8em 0 .2em 0;">Largest tracked directories</h4>'
        '<table style="border-collapse:collapse;margin:.2em 0;">'
        '<thead><tr style="background:#f6f7f8;">'
        '<th style="padding:6px 10px;text-align:left;">Directory</th>'
        '<th style="padding:6px 10px;text-align:right;">Size</th>'
        '</tr></thead><tbody>'
        f'{dir_rows}'
        '</tbody></table>'
        '<h4 style="margin:.8em 0 .2em 0;">Largest report-only cleanup candidates</h4>'
        '<table style="border-collapse:collapse;margin:.2em 0;">'
        '<thead><tr style="background:#f6f7f8;">'
        '<th style="padding:6px 10px;text-align:left;">File</th>'
        '<th style="padding:6px 10px;text-align:left;">Class</th>'
        '<th style="padding:6px 10px;text-align:right;">Size</th>'
        '</tr></thead><tbody>'
        f'{file_rows}'
        '</tbody></table>'
    )


def _render_workflow_health_html(data: dict[str, Any]) -> str:
    """XYZ-PILOT-NNN: render the workflow_health rollup as an HTML section.

    Surfaces failed workflows + their count over the last 24 h. Renders
    a yellow callout box if any workflow failed; otherwise a quiet green
    line confirming all workflows healthy.
    """
    if not data.get("ok"):
        return (
            '<div style="background:#FFF6E5;border:1px solid #F0C36D;border-radius:4px;'
            'padding:10px 14px;margin:1em 0;">'
            f'⚠ GitHub Actions workflow health fetch failed: '
            f'<code>{escape(str(data.get("error", "unknown error")))[:200]}</code>'
            '</div>'
        )
    by_wf = data.get("by_workflow", {})
    if not by_wf:
        return (
            '<p style="color:#666;font-size:.9em;">No workflow runs in the last 24 h.</p>'
        )
    total_failures = int(data.get("total_failures", 0))
    total_runs = int(data.get("total_runs", 0))

    # Sort: failed-workflows first (by failure count desc), then healthy alphabetical.
    def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
        name, slot = item
        fails = int(slot.get("failure", 0)) + int(slot.get("startup_failure", 0))
        return (-fails, name.lower())

    rows = []
    for name, slot in sorted(by_wf.items(), key=_sort_key):
        ok_count = int(slot.get("success", 0))
        fail_count = int(slot.get("failure", 0)) + int(slot.get("startup_failure", 0))
        cancel_count = int(slot.get("cancelled", 0))
        in_prog = int(slot.get("in_progress", 0))
        total = int(slot.get("total", 0))
        if fail_count > 0:
            badge_bg = "#F8D7DA"; badge_fg = "#721C24"; badge_text = f"✗ {fail_count} failed"
        elif total == ok_count and ok_count > 0:
            badge_bg = "#D4EDDA"; badge_fg = "#155724"; badge_text = "✓ healthy"
        else:
            badge_bg = "#FFF3CD"; badge_fg = "#856404"; badge_text = "in progress"
        link_cell = ""
        if slot.get("latest_failure_html_url"):
            link_cell = (
                f' &nbsp;<a href="{escape(slot["latest_failure_html_url"])}" '
                f'style="color:#721C24;font-size:.85em;">latest failure ({escape(slot.get("latest_failure_sha", "?"))})</a>'
            )
        rows.append(
            '<tr style="border-bottom:1px solid #eaeaea;">'
            f'<td style="padding:6px 10px;"><b>{escape(name)}</b></td>'
            f'<td style="padding:6px 10px;">{ok_count}/{total}</td>'
            f'<td style="padding:6px 10px;">'
            f'<span style="background:{badge_bg};color:{badge_fg};padding:2px 8px;border-radius:3px;font-weight:600;font-size:.85em;">{badge_text}</span>'
            f'{link_cell}</td></tr>'
        )
    table_html = (
        '<table style="border-collapse:collapse;margin:.4em 0;width:100%;">'
        '<thead><tr style="background:#f6f7f8;">'
        '<th style="padding:6px 10px;text-align:left;">Workflow</th>'
        '<th style="padding:6px 10px;text-align:left;">Success / Total</th>'
        '<th style="padding:6px 10px;text-align:left;">Status</th>'
        '</tr></thead><tbody>'
        + "".join(rows) +
        '</tbody></table>'
    )
    if total_failures > 0:
        callout = (
            '<div style="background:#FFF6E5;border:1px solid #F0C36D;border-radius:4px;'
            'padding:10px 14px;margin:1em 0;">'
            f'⚠ <b>{total_failures} workflow failure{"s" if total_failures != 1 else ""} '
            f'in the last 24 h</b> across {total_runs} total runs. '
            'Click the per-row "latest failure" links to diagnose.'
            '</div>'
        )
        return callout + table_html
    return (
        f'<p style="color:#155724;font-size:.9em;">'
        f'✓ All {total_runs} workflow runs in the last 24 h completed without failures.'
        '</p>' + table_html
    )


def _render_freshness_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p><i>Freshness probe failed.</i></p>"
    color = {"fresh": "#1AAB40", "borderline": "#D4A017",
             "stale": "#C0392B", "missing": "#888", "unknown": "#888"}
    html_rows = ""
    for r in rows:
        age = r.get("age_days")
        age_str = f"{age}d ago" if age is not None else "—"
        tag = r.get("tag", "unknown")
        html_rows += (
            f"<tr>"
            f"<td style='padding:4px 10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.92em;'>{escape(r['path'])}</td>"
            f"<td style='padding:4px 10px;font-size:.9em;color:#555;'>{escape(r.get('last') or '—')}</td>"
            f"<td style='padding:4px 10px;font-size:.9em;color:#555;'>{escape(age_str)}</td>"
            f"<td style='padding:4px 10px;'>{_badge(tag, color.get(tag, '#888'))}</td>"
            f"</tr>"
        )
    return ("<table style='border-collapse:collapse;'>"
            "<thead><tr style='background:#f6f7f8;'>"
            "<th style='padding:6px 10px;text-align:left;'>File</th>"
            "<th style='padding:6px 10px;text-align:left;'>Last touched</th>"
            "<th style='padding:6px 10px;text-align:left;'>Age</th>"
            "<th style='padding:6px 10px;text-align:left;'>State</th>"
            "</tr></thead><tbody>" + html_rows + "</tbody></table>"
            "<p style='color:#888;font-size:.85em;margin-top:.6em;'>"
            "<i>fresh</i> = ≤ 2 days · <i>borderline</i> = 3–6 days · "
            "<i>stale</i> = ≥ 7 days. The discipline is in XYZ-PILOT-046.</p>")


# XYZ-PILOT-092 — turn every bare `XYZ-PILOT-NNN` reference in the email body
# into a hyperlink to the tracker anchor on GitHub. Operators clicking the
# ticket id in the morning status email land directly on the entry instead
# of having to grep the tracker file themselves.
_XYZ_PILOT_REF_RE = re.compile(r"\bXYZ-PILOT-(\d{3})\b")
_A_TAG_RE = re.compile(r"<a [^>]*>.*?</a>", re.DOTALL | re.IGNORECASE)


def _linkify_xyz_pilot_refs(html: str) -> str:
    """Wrap every bare `XYZ-PILOT-NNN` mention in an `<a href>` tag pointing
    to the tracker anchor. Refs already inside an `<a>` tag are left alone
    so existing hyperlinks (e.g. the "Issue tracker" footer link, the
    Bedrock-narrative anchor URLs) don't get nested."""
    def _wrap_one(m: "re.Match[str]") -> str:
        nnn = m.group(1)
        return (
            f'<a href="https://github.com/{GITHUB_REPO}/blob/main/'
            f'docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-{nnn}" '
            f'style="color:#1e40af;text-decoration:underline;">'
            f'XYZ-PILOT-{nnn}</a>'
        )

    # Split on existing <a>...</a> blocks. Even-indexed parts are outside
    # any anchor; odd-indexed parts are inside (leave them untouched).
    parts = _A_TAG_RE.split(html)
    anchors = _A_TAG_RE.findall(html)
    out: list[str] = []
    for i, part in enumerate(parts):
        out.append(_XYZ_PILOT_REF_RE.sub(_wrap_one, part))
        if i < len(anchors):
            out.append(anchors[i])
    return "".join(out)


def _render_html(now_cest: datetime, yesterday_cest: datetime,
                 ec2_state: str, ses_state: dict[str, str],
                 cognito_users: list[dict[str, str]],
                 commits: list[dict[str, Any]],
                 doc_freshness: list[dict[str, Any]],
                 narrative_html: str,
                 workflow_health: dict[str, Any] | None = None,
                 dependabot_prs: dict[str, Any] | None = None,
                 repo_retention: dict[str, Any] | None = None,
                 mcp_probe: dict[str, Any] | None = None,
                 chatbot_probe: dict[str, Any] | None = None) -> str:
    ses_ok = sorted(a for a, s in ses_state.items() if s == "Success")
    ses_pending = sorted(a for a, s in ses_state.items() if s == "Pending")
    ses_failed = sorted(a for a, s in ses_state.items() if s == "Failed")

    cognito_confirmed = sorted(u["email"] for u in cognito_users if u["status"] == "CONFIRMED")
    cognito_force    = sorted(u["email"] for u in cognito_users if u["status"] == "FORCE_CHANGE_PASSWORD")
    cognito_other    = sorted((u["email"], u["status"]) for u in cognito_users
                              if u["status"] not in ("CONFIRMED", "FORCE_CHANGE_PASSWORD"))

    ec2_color = "#1AAB40" if ec2_state == "running" else "#888"

    # XYZ-PILOT-056: live probes for chatbot + MCP, surfaced in the Environment
    # table. Skipped when EC2 is stopped; both services live on EC2.
    # XYZ-PILOT-206: probes are now passed in pre-computed from lambda_handler
    # so the E2E status section + this Environment table share probe results
    # without duplicate HTTP calls. Fallback path runs them locally if
    # _render_html is invoked outside the lambda_handler context (tests).
    if ec2_state == "running":
        if mcp_probe is None:
            mcp_probe = _probe_mcp()
        if chatbot_probe is None:
            chatbot_probe = _probe_chatbot()
        mcp_status_badge = (_badge(f"OAuth-gated ✓ ({mcp_probe['code']})", "#1AAB40")
                            if mcp_probe["healthy"]
                            else _badge(f"DOWN · {mcp_probe['note']}", "#C0392B"))
        chatbot_status_badge = (_badge("RUNNING ✓", "#1AAB40")
                                if chatbot_probe["healthy"]
                                else _badge(f"DOWN · {chatbot_probe['note']}", "#C0392B"))
    else:
        mcp_status_badge     = _badge("STOPPED (EC2 down)", "#888")
        chatbot_status_badge = _badge("STOPPED (EC2 down)", "#888")

    rows_subs = ""
    # XYZ-PILOT-NNN: iterate the same Cognito ∪ SES_TO union the recipient
    # discovery uses, so the email body's "Subscriber pipeline" table
    # shows every candidate recipient (env-var-only addresses get a
    # `not in pool` Cognito badge; Cognito-only addresses get whatever
    # SES verification state the broader _ses_status() call discovered).
    for addr in sorted(set(SES_TO + [u["email"] for u in cognito_users])):
        ses_s = ses_state.get(addr, "—")
        cog_s = next((u["status"] for u in cognito_users if u["email"] == addr), "—")
        ses_badge = (_badge("✅", "#1AAB40") if ses_s == "Success"
                     else _badge("⏳ Pending", "#D4A017") if ses_s == "Pending"
                     else _badge("❌ Failed", "#C0392B") if ses_s == "Failed"
                     else _badge(ses_s, "#888"))
        cog_badge = (_badge("CONFIRMED", "#1AAB40") if cog_s == "CONFIRMED"
                     else _badge("FORCE_CHANGE_PASSWORD", "#D4A017") if cog_s == "FORCE_CHANGE_PASSWORD"
                     else _badge("not in pool", "#888") if cog_s == "—"
                     else _badge(cog_s, "#888"))
        rows_subs += f"<tr><td style='padding:4px 10px;'>{escape(addr)}</td><td style='padding:4px 10px;'>{ses_badge}</td><td style='padding:4px 10px;'>{cog_badge}</td></tr>"

    commits_html = "<p><i>No commits in the last 24 hours.</i></p>"
    if commits:
        rows = ""
        for c in commits:
            rows += (
                f"<tr>"
                f"<td style='padding:4px 10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.92em;'>"
                f"<a href='https://github.com/{GITHUB_REPO}/commit/{c['sha']}'>{escape(c['sha'])}</a></td>"
                f"<td style='padding:4px 10px;'>{escape(c['message'])}</td>"
                f"<td style='padding:4px 10px;color:#666;font-size:.9em;'>{escape(c['author'])}</td>"
                f"</tr>"
            )
        commits_html = ("<table style='border-collapse:collapse;'>"
                        "<thead><tr style='background:#f6f7f8;'>"
                        "<th style='padding:6px 10px;text-align:left;'>SHA</th>"
                        "<th style='padding:6px 10px;text-align:left;'>Message</th>"
                        "<th style='padding:6px 10px;text-align:left;'>Author</th>"
                        "</tr></thead><tbody>" + rows + "</tbody></table>")

    # XYZ-PILOT-126: every section is wrapped in its own `<div style="text-align:left;">`
    # so any cross-section cascade (especially from the Bedrock-emitted
    # narrative middle, which kept leaking text-align:center across to the
    # downstream deterministic sections per XYZ-PILOT-123 / XYZ-PILOT-124) is
    # explicitly reset at each section boundary. Structural decoupling at the
    # Python orchestrator level — independent of what Bedrock chooses to emit
    # inside its own div.
    _S = '<div style="text-align:left;">'  # section opener
    _E = '</div>'                           # section closer
    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;max-width:780px;line-height:1.5;color:#222;text-align:left;">
{_S}<h2 style="margin-bottom:.1em;"><PILOT_NAME> — daily status</h2>
<p style="color:#666;margin-top:.2em;">
  Report for <b>{escape(yesterday_cest.strftime('%a %d.%m.%Y'))}</b>,
  generated {escape(now_cest.strftime('%a %d.%m.%Y %H:%M CEST'))}.
</p>{_E}

{_S}<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Environment</h3>
<table style="border-collapse:collapse;margin:.4em 0;">
  <tr><td style="padding:4px 10px;"><b>EC2 instance</b></td><td style="padding:4px 10px;">{_badge(ec2_state.upper(), ec2_color)}</td></tr>
  <tr><td style="padding:4px 10px;"><b>Status dashboard</b></td><td style="padding:4px 10px;"><a href="{STATUS_DASHBOARD_URL}">live status page</a></td></tr>
  <tr><td style="padding:4px 10px;"><b>Exasol MCP endpoint</b></td><td style="padding:4px 10px;"><a href="{EXA_MCP_URL}" style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{EXA_MCP_URL}</a> &nbsp; {mcp_status_badge}</td></tr>
  <tr><td style="padding:4px 10px;"><b><PILOT_NAME> Chatbot</b></td><td style="padding:4px 10px;"><a href="{CHATBOT_URL}" style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{CHATBOT_URL}</a> &nbsp; {chatbot_status_badge}</td></tr>
</table>{_E}

{_S}{narrative_html or ""}{_E}
<!-- XYZ-PILOT-126: end of Bedrock-narrative wrapper; subsequent deterministic sections each have their own left-align div -->

{_S}<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Subscriber pipeline state</h3>
<table style="border-collapse:collapse;margin:.4em 0;">
  <thead><tr style="background:#f6f7f8;">
    <th style="padding:6px 10px;text-align:left;">Email</th>
    <th style="padding:6px 10px;text-align:left;">SES</th>
    <th style="padding:6px 10px;text-align:left;">Cognito</th>
  </tr></thead>
  <tbody>{rows_subs}</tbody>
</table>{_E}

{_S}<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Recent commits (last 24 h)</h3>
{commits_html}{_E}

{_S}<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Documentation freshness <span style="color:#888;font-weight:normal;font-size:.85em;">(XYZ-PILOT-046)</span></h3>
{_render_freshness_html(doc_freshness)}{_E}

{_S}<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Repository retention cleanup candidates <span style="color:#888;font-weight:normal;font-size:.85em;">(report-only)</span></h3>
{_render_repo_retention_html(repo_retention or {"ok": False, "error": "no data passed to _render_html"})}{_E}

{_S}<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Workflow health (last 24 h) <span style="color:#888;font-weight:normal;font-size:.85em;">(XYZ-PILOT-174)</span></h3>
{_render_workflow_health_html(workflow_health or {"ok": False, "error": "no data passed to _render_html"})}{_E}

{_S}<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Pending Dependabot PRs <span style="color:#888;font-weight:normal;font-size:.85em;">(XYZ-PILOT-196)</span></h3>
{_render_dependabot_prs_html(dependabot_prs or {"ok": False, "error": "no data passed to _render_html"})}{_E}

{_S}<h3 style="border-bottom:1px solid #eee;padding-bottom:4px;">Links</h3>
<ul>
  <li>Repository — <a href="https://github.com/{GITHUB_REPO}">github.com/{GITHUB_REPO}</a></li>
  <li>Issue tracker — <a href="https://github.com/{GITHUB_REPO}/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md">XYZ_PILOT_ISSUE_TRACKER.md</a></li>
  <li>Client guide — <a href="https://github.com/{GITHUB_REPO}/blob/main/docs/30-runbooks/mcp/XYZ_PILOT_MCP_CLIENT_GUIDE.md">XYZ_PILOT_MCP_CLIENT_GUIDE.md</a></li>
  <li>Progress log — <a href="https://github.com/{GITHUB_REPO}/blob/main/docs/00-live-status/XYZ_PILOT_PROGRESS_LOG.md">XYZ_PILOT_PROGRESS_LOG.md</a></li>
</ul>{_E}

{_S}<p style="color:#888;font-size:.85em;margin-top:2em;">
  Auto-generated by <code>xyz-pilot-daily-status</code> Lambda · XYZ-PILOT-045 ·
  fires Tue–Sat 06:00 CEST. Reply to <OPERATOR_EMAIL> to unsubscribe.<br>
  {escape(CONFIDENTIAL_EMAIL_FOOTER)}
</p>{_E}
</body></html>"""


def lambda_handler(event: dict, context: Any) -> dict:
    now_cest = datetime.now(timezone.utc) + CEST_OFFSET
    yesterday_cest = now_cest - timedelta(days=1)

    log.info("daily-status fire · now_cest=%s · weekday=%s",
             now_cest.isoformat(), now_cest.strftime("%a"))

    # XYZ-PILOT-210: side-effect-free runtime validation path after PAT
    # permission rotation. It avoids tracker refresh, SES fan-out, and service
    # probes; it only exercises the two GitHub API calls that regressed.
    if isinstance(event, dict) and event.get("github_probe_only"):
        hours_raw = event.get("hours", 24)
        try:
            hours = max(1, min(168, int(hours_raw)))
        except (TypeError, ValueError):
            hours = 24
        workflow_health = _workflow_health_24h(hours)
        dependabot_prs = _dependabot_open_prs()
        repo_retention = _repo_retention_report()
        return {
            "github_probe_only": True,
            "hours": hours,
            "workflow_health": workflow_health,
            "dependabot_prs": dependabot_prs,
            "repo_retention": repo_retention,
        }

    # Side-effect-free recipient validation. This intentionally runs before
    # tracker trimming, service probes, and SES fan-out so operators can verify
    # Cognito group delivery rules without mutating the tracker or sending mail.
    if isinstance(event, dict) and event.get("recipient_probe_only"):
        return _recipient_probe_payload(
            include_emails=bool(event.get("include_emails")),
        )

    # XYZ-PILOT-047 — auto-trim BEFORE everything else so the rest of this run
    # (freshness probe, commits list) sees the post-trim state.
    trim_result = _trim_tracker_top_table(now_cest.date())
    log.info("tracker auto-trim: %s", trim_result)

    # XYZ-PILOT-061 extension — refresh the DAILY_STATUS header date too.
    daily_status_refresh = _refresh_daily_status_header(now_cest.date())
    log.info("daily-status header refresh: %s", daily_status_refresh)

    # Short-circuit: operator-driven trim verification (e.g. after PAT rotation)
    # supplies `{"trim_only": true}` so the email render + SES send are skipped.
    # EventBridge cron payloads never carry this field, so the daily 06:00 CEST
    # path is unaffected.
    if isinstance(event, dict) and event.get("trim_only"):
        log.info("trim_only=True · skipping email render + SES send")
        return {"trim_only": True, "tracker_trim": trim_result,
                "daily_status_refresh": daily_status_refresh}

    ec2_state = _ec2_state()
    cognito_users = _cognito_users()
    # Discover recipients from the strict Cognito group policy
    # OPS_ONLY ∪ XYZ_PILOT, minus XYZ_BIZ. SES verification is
    # checked after discovery so a single unverified user cannot fail the
    # whole daily-status send.
    recipients = _all_candidate_recipients()
    ses_state = _ses_status(recipients)
    commits = _recent_commits()
    doc_freshness = _doc_freshness()
    workflow_health = _workflow_health_24h()
    log.info("workflow health 24h: total_runs=%d total_failures=%d",
             workflow_health.get("total_runs", 0), workflow_health.get("total_failures", 0))

    # XYZ-PILOT-196: pending Dependabot PRs digest.
    dependabot_prs = _dependabot_open_prs()
    log.info("dependabot open PRs: total=%d ok=%s",
             dependabot_prs.get("total", 0), dependabot_prs.get("ok", False))

    # Repository retention cleanup candidates: report-only size inventory.
    repo_retention = _repo_retention_report()
    log.info("repo retention report: ok=%s tracked_files=%d tracked_bytes=%d",
             repo_retention.get("ok", False),
             repo_retention.get("tracked_file_count", 0),
             repo_retention.get("tracked_bytes", 0))

    # XYZ-PILOT-074 follow-up: authoritative resolved/filed split from the
    # tracker, used by the Bedrock prompt to avoid the prior mis-attribution
    # of commit-message references like "file XYZ-PILOT-062 through XYZ-PILOT-071"
    # as a Resolved range. Window matches the commits window (24 h).
    window_until = now_cest
    window_since = now_cest - timedelta(hours=24)
    tracker_text = _fetch_tracker_text()
    movement = _tracker_movement_in_window(tracker_text, window_since, window_until)
    log.info("tracker movement in last 24h: resolved=%d filed=%d",
             len(movement["resolved"]), len(movement["filed"]))

    # XYZ-PILOT-206: probe all 4 public ServiceURIs once here and thread the
    # results into the renderers, so the new E2E status section + the
    # existing Environment table share the same probe results without
    # duplicate HTTP calls. Skipped (None) when EC2 is stopped — the
    # renderers fall back to a "STOPPED (EC2 down)" badge.
    if ec2_state == "running":
        mcp_probe     = _probe_mcp()
        chatbot_probe = _probe_chatbot()
        qbr_probe     = _probe_qbr()
        grafana_probe = _probe_grafana()
        log.info("ServiceURI probes · mcp=%s chatbot=%s qbr=%s grafana=%s",
                 mcp_probe.get("code"), chatbot_probe.get("code"),
                 qbr_probe.get("code"), grafana_probe.get("code"))
    else:
        mcp_probe = chatbot_probe = qbr_probe = grafana_probe = None
        log.info("ServiceURI probes skipped (EC2 not running)")

    # XYZ-PILOT-125: deterministic Python narrative (replaces XYZ-PILOT-048
    # Bedrock-composed version after FIVE prompt iterations failed to
    # produce reliably-renderable HTML). Pure-Python, model-independent,
    # zero LLM-output surprise.
    # movement["resolved"] / ["filed"] are already strings like "XYZ-PILOT-055"
    narrative_html = _compose_narrative_deterministic(
        now_cest, ec2_state, ses_state, cognito_users, commits, doc_freshness,
        tracker_text, movement["resolved"], movement["filed"],
        mcp_probe=mcp_probe, chatbot_probe=chatbot_probe,
        qbr_probe=qbr_probe, grafana_probe=grafana_probe,
    )

    html = _render_html(now_cest, yesterday_cest, ec2_state,
                        ses_state, cognito_users, commits, doc_freshness,
                        narrative_html, workflow_health, dependabot_prs,
                        repo_retention=repo_retention,
                        mcp_probe=mcp_probe, chatbot_probe=chatbot_probe)
    # XYZ-PILOT-092: linkify every `XYZ-PILOT-NNN` reference in the rendered
    # HTML to the tracker anchor on GitHub. Skip refs already inside an
    # <a href=…> tag so existing hyperlinks aren't double-wrapped.
    html = _linkify_xyz_pilot_refs(html)
    subject = f"{yesterday_cest.strftime('%d.%m')} - <PILOT_NAME>: What's today's status"

    # Event-based trial-send overrides (XYZ-PILOT-074 follow-up):
    # `test_recipient_override` → send to a single specific verified address
    #                              (typically the operator) instead of the full
    #                              SES_TO list. Use to preview a new prompt /
    #                              format without spamming the 6 subscribers.
    # `test_subject_override`   → use exact subject string instead of the
    #                              auto-generated yesterday-stamped one.
    # `cc`                      → optional CC list (string or list of strings)
    #                              added to the SES destination. Used for
    #                              one-off catch-up sends where an operator
    #                              wants visibility into the trial send.
    # `preamble_html`           → optional HTML snippet prepended to the email
    #                              body. Used for one-off catch-up emails to a
    #                              subscriber who was missed in a prior fan-out
    #                              (e.g. <OPS_USER_3> before XYZ-PILOT-NNN recipient
    #                              discovery from Cognito was wired in).
    test_recipient_override = (event or {}).get("test_recipient_override")
    test_subject_override   = (event or {}).get("test_subject_override")
    cc_param                = (event or {}).get("cc")
    preamble_html           = (event or {}).get("preamble_html") or ""
    if test_subject_override:
        subject = test_subject_override
        log.info("trial-send: subject override = %s", subject)
    if test_recipient_override:
        log.info("trial-send: recipient override = %s", test_recipient_override)
        deliverable = [test_recipient_override]
        skipped = []
    else:
        deliverable = sorted([a for a, s in ses_state.items() if s == "Success"])
        skipped = sorted([a for a, s in ses_state.items() if s != "Success"])
    if not deliverable:
        log.warning("no verified recipients; skipping send")
        return {"sent": 0, "skipped": skipped, "subject": subject}
    if SES_FROM not in deliverable and not test_recipient_override:
        log.warning("sender %s not verified", SES_FROM)

    cc_list: list[str] = []
    if isinstance(cc_param, str) and cc_param.strip():
        cc_list = [c.strip() for c in cc_param.split(",") if c.strip()]
    elif isinstance(cc_param, list):
        cc_list = [str(c).strip() for c in cc_param if str(c).strip()]
    if cc_list:
        log.info("trial-send: cc = %s", cc_list)

    final_html = (preamble_html + html) if preamble_html else html

    destination: dict[str, list[str]] = {"ToAddresses": deliverable}
    if cc_list:
        destination["CcAddresses"] = cc_list
    resp = ses.send_email(
        Source=SES_FROM,
        Destination=destination,
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Html": {"Data": final_html, "Charset": "UTF-8"}},
        },
    )
    log.info("sent · MessageId=%s · to=%s · skipped=%s",
             resp.get("MessageId"), deliverable, skipped)
    return {"sent": len(deliverable), "skipped": skipped,
            "subject": subject, "message_id": resp.get("MessageId"),
            "tracker_trim": trim_result,
            "daily_status_refresh": daily_status_refresh}
