"""<PILOT_NAME> scheduler Lambda — single function, dispatches on ACTION env var.

EventBridge invokes this Lambda twice a day:

  ACTION=start  · cron(0 10 ? * MON-FRI *)  →  10:00 UTC = 12:00 CEST, Mon-Fri
  ACTION=stop   · cron(0 22 * * ? *)        →  22:00 UTC = 24:00 CEST, every day

The Lambda starts or stops the <PILOT_NAME> EC2 instance, gives the docker
stack a moment to settle on start, rediscovers the current Cloudflare
quick-tunnel URLs via SSM Run Command, refreshes the public S3-hosted
status dashboard, and sends a notification email via SES to the
configured recipients.

CEST handling: EventBridge cron is in UTC. CEST = UTC + 2 in May (we don't
guarantee correct DST transitions; spring-forward/autumn-back boundaries
would shift our 12:00/24:00 CEST window by an hour for ~1 week. Acceptable
for the POC; documented in the scheduler README.

All AWS resources are tagged Pilot=XYZ-PILOT for cost attribution.
"""

from __future__ import annotations

import datetime as dt
import base64
import html
import json
import logging
import os
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

AWS_REGION = os.environ.get("XYZ_AWS_REGION") or os.environ.get("AWS_REGION", "<AWS_REGION>")

# Required environment variables (set on the Lambda configuration):
EC2_INSTANCE_ID = os.environ["EC2_INSTANCE_ID"]     # e.g. <EC2_INSTANCE_ID>
STATUS_BUCKET   = os.environ["STATUS_BUCKET"]       # e.g. xyz-pilot-status-<SOURCE_AWS_ACCOUNT_ID>
SES_FROM        = os.environ["SES_FROM"]            # e.g. <OPERATOR_EMAIL>
SES_TO          = os.environ["SES_TO"].split(",")   # comma-separated
# XYZ-PILOT-173 — operator-only notification restriction. The historical
# payload key is still event.test_mode=true for compatibility with existing
# dashboard and EventBridge callers. User-facing email copy calls this
# OPS-ONLY, because the flag routes real operational notifications to the
# narrow operator list instead of the full Cognito-union subscriber list.
SES_TO_TEST     = [
    e.strip() for e in os.environ.get(
        "SES_TO_TEST",
        "<OPERATOR_EMAIL>,<OPS_USER_2_EMAIL>,<OPS_USER_3_EMAIL>",
    ).split(",") if e.strip()
]
ACTION          = os.environ["ACTION"]              # "start" or "stop"
EXASOL_DNS      = os.environ.get(
    "EXASOL_DNS",
    "<EXASOL_HOSTNAME>",
)
# Optional — set on xyz-pilot-start only. When both are present the status
# dashboard renders an ad-hoc START button that POSTs to the Function URL.
FUNCTION_URL    = os.environ.get("FUNCTION_URL", "")
TRIGGER_TOKEN   = os.environ.get("TRIGGER_TOKEN", "")
# XYZ-PILOT-035: fixed public URL of the Exasol MCP server (named CF tunnel).
EXA_MCP_URL     = os.environ.get(
    "EXA_MCP_URL",
    "https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp",
)
COGNITO_HOSTED_UI = os.environ.get(
    "COGNITO_HOSTED_UI",
    "https://<COGNITO_DOMAIN_PREFIX>.auth.<AWS_REGION>.amazoncognito.com",
)
OPERATOR_DASHBOARD_BASE_URL = os.environ.get(
    "OPERATOR_DASHBOARD_BASE_URL",
    "https://<PROJECT_SLUG>-dashboard.<PILOT_DOMAIN>",
)
# XYZ-PILOT-063 resolution: Cognito-gated static dashboard that replaces the
# legacy unauthenticated start-button page. Subscribers sign in with their
# Cognito credentials; the page uses Cognito Identity Pool temp creds to
# invoke xyz-pilot-start / xyz-pilot-stop and to poll ec2:DescribeInstances.
OPERATOR_DASHBOARD = os.environ.get(
    "OPERATOR_DASHBOARD",
    f"{OPERATOR_DASHBOARD_BASE_URL}/dashboard.html",
)
# Favicons uploaded alongside dashboard.html — used by both the static
# Operator Dashboard and the rendered legacy s3-website dashboard.
FAVICON_32  = os.environ.get(
    "FAVICON_32",
    f"{OPERATOR_DASHBOARD_BASE_URL}/favicon-32.png",
)
FAVICON_192 = os.environ.get(
    "FAVICON_192",
    f"{OPERATOR_DASHBOARD_BASE_URL}/favicon-192.png",
)

# German federal holidays on which the scheduled 12:00 CEST start cron
# is suppressed (per user direction 2026-05-22 — Pfingstmontag long
# weekend). Manual invokes via the Operator Dashboard or chatbot button
# are NOT affected because they pass notify=False; only the EventBridge
# `aws.events` cron fire (notify=True) is gated. Add future holidays
# here as needed; expired entries can be left in place — they only
# match on today's date.
GERMAN_HOLIDAYS_NO_AUTO_START = {
    dt.date(2026, 5, 25),  # Pfingstmontag (Whit Monday)
}

ec2 = boto3.client("ec2", region_name=AWS_REGION)
ssm = boto3.client("ssm", region_name=AWS_REGION)
ses = boto3.client("ses", region_name=AWS_REGION)
s3  = boto3.client("s3",  region_name=AWS_REGION)
cognito = boto3.client("cognito-idp", region_name=AWS_REGION)
secretsmanager = boto3.client("secretsmanager", region_name=AWS_REGION)

# Notification recipient policy (strict group-based, 2026-06-25):
# START/STOP/STATUS fan-out is derived from Cognito groups only:
#   OPS_ONLY ∪ XYZ_PILOT ∪ XYZ_OPS
# Explicit exclusion:
#   XYZ_BIZ
# SES verification filtering remains in place at send time.
USER_POOL_ID = os.environ.get(
    "COGNITO_USER_POOL_ID",
    "<COGNITO_USER_POOL_ID>",
)
NOTIFY_INCLUDE_GROUPS = ("OPS_ONLY", "XYZ_PILOT", "XYZ_OPS")
NOTIFY_EXCLUDE_GROUPS = ("XYZ_BIZ",)
WARMUP_WARNING_GROUP = "OPS_ONLY"
RESTRICTED_NOTIFY_INCLUDE_GROUPS = ("OPS_ONLY", "XYZ_OPS")
DASHBOARD_START_STOP_GROUPS = {"OPS_ONLY", "XYZ_OPS", "XYZ_PILOT"}
START_STOP_FALLBACK_EMAILS = {
    e.strip().lower()
    for e in os.environ.get(
        "OPERATOR_DASHBOARD_ALLOW_EMAILS",
        ",".join(SES_TO_TEST),
    ).split(",")
    if e.strip()
}


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
    """Strict Cognito group-based recipient discovery with explicit exclusion."""
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


def _warmup_warning_recipients() -> list[str]:
    """Operational warm-up warnings go to OPS_ONLY only."""
    try:
        recipients = sorted(
            set(_users_in_group_emails(WARMUP_WARNING_GROUP)),
            key=str.lower,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "warm-up warning recipient discovery failed for %s (%s)",
            WARMUP_WARNING_GROUP,
            type(exc).__name__,
        )
        recipients = []
    log.info(
        "warm-up warning recipient discovery (Cognito group %s only): n=%d",
        WARMUP_WARNING_GROUP,
        len(recipients),
    )
    return recipients


def _restricted_notification_recipients() -> list[str]:
    """Dashboard ops-only mode sends to OPS_ONLY + XYZ_OPS, excluding XYZ_BIZ."""
    include_seen: dict[str, str] = {}
    exclude_keys: set[str] = set()

    for group_name in RESTRICTED_NOTIFY_INCLUDE_GROUPS:
        try:
            for addr in _users_in_group_emails(group_name):
                include_seen.setdefault(addr.lower(), addr)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "restricted recipient discovery include-group failed: %s (%s)",
                group_name,
                type(exc).__name__,
            )

    for group_name in NOTIFY_EXCLUDE_GROUPS:
        try:
            for addr in _users_in_group_emails(group_name):
                exclude_keys.add(addr.lower())
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "restricted recipient discovery exclude-group failed: %s (%s)",
                group_name,
                type(exc).__name__,
            )

    recipients = sorted(
        [addr for key, addr in include_seen.items() if key not in exclude_keys],
        key=str.lower,
    )
    log.info(
        "restricted recipient discovery (Cognito groups include=%s exclude=%s): n=%d",
        ",".join(RESTRICTED_NOTIFY_INCLUDE_GROUPS),
        ",".join(NOTIFY_EXCLUDE_GROUPS),
        len(recipients),
    )
    return recipients


def _principal_from_bearer(access_token: str | None) -> dict[str, Any]:
    if not access_token:
        return {"email": None, "username": None, "groups": [], "token_valid": False}
    try:
        resp = cognito.get_user(AccessToken=access_token)
    except Exception as exc:  # noqa: BLE001
        log.warning("dashboard start/stop token validation failed: %s", type(exc).__name__)
        return {"email": None, "username": None, "groups": [], "token_valid": False}
    email = None
    for attr in resp.get("UserAttributes", []):
        if attr.get("Name") == "email":
            email = (attr.get("Value") or "").strip().lower() or None
            break
    username = (resp.get("Username") or email or "").strip()
    groups = _groups_for_principal(username=username, email=email)
    return {"email": email, "username": username or None, "groups": groups, "token_valid": True}


def _groups_for_principal(username: str | None, email: str | None) -> list[str]:
    names = [n for n in (username, email) if n]
    out: set[str] = set()
    for name in names:
        try:
            resp = cognito.admin_list_groups_for_user(UserPoolId=USER_POOL_ID, Username=name)
            for group in resp.get("Groups", []):
                group_name = str(group.get("GroupName") or "").strip()
                if group_name:
                    out.add(group_name)
            if out:
                break
        except Exception as exc:  # noqa: BLE001
            log.warning("dashboard start/stop group lookup failed for %s: %s", name, type(exc).__name__)
    return sorted(out)


def _can_dashboard_start_stop(principal: dict[str, Any]) -> bool:
    email = str(principal.get("email") or "").lower()
    groups = set(principal.get("groups") or [])
    if groups.intersection(DASHBOARD_START_STOP_GROUPS):
        return True
    if groups:
        return False
    # Migration fallback for existing OPS_ONLY operators before Cognito group
    # membership / AdminListGroupsForUser IAM is fully deployed.
    return bool(email and email in START_STOP_FALLBACK_EMAILS)


def _ad_hoc_principal_or_error(action: str, bearer_token: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not bearer_token:
        return None, None
    principal = _principal_from_bearer(bearer_token)
    if not principal.get("token_valid") or not principal.get("email"):
        return None, {
            "action": action,
            "ok": False,
            "error": "invalid or expired Cognito token",
            "ad_hoc": True,
        }
    if not _can_dashboard_start_stop(principal):
        return None, {
            "action": action,
            "ok": False,
            "error": "current Cognito user is not authorized for Operator Dashboard START/STOP",
            "initiator": principal.get("email"),
            "groups": principal.get("groups") or [],
            "ad_hoc": True,
        }
    return principal, None

# XYZ-PILOT-156: user-facing START must not announce "ready" while the
# Exasol Personal cache warm-up is still running after a cold EC2 boot.
WARMUP_SERVICE_NAME = os.environ.get("WARMUP_SERVICE_NAME", "xyz-exasol-warmup.service")
WARMUP_LOG_PATH = os.environ.get("WARMUP_LOG_PATH", "/var/log/xyz-warmup.log")
WARMUP_SSM_ONLINE_TIMEOUT_S = int(os.environ.get("WARMUP_SSM_ONLINE_TIMEOUT_S", "180"))
WARMUP_SSM_COMMAND_TIMEOUT_S = int(os.environ.get("WARMUP_SSM_COMMAND_TIMEOUT_S", "660"))

# XYZ-PILOT-034 extension: scheduled STOP-only daily user monitor. This must run
# before the regular 24:00 CEST stop, but never for Cognito Operator Dashboard
# ad-hoc STOP calls.
DAILY_USER_MONITOR_ENABLED = os.environ.get("DAILY_USER_MONITOR_ENABLED", "1").strip().lower() not in {
    "0", "false", "no", "off",
}
DAILY_USER_MONITOR_LOOKBACK_HOURS = int(os.environ.get("DAILY_USER_MONITOR_LOOKBACK_HOURS", "26"))
DAILY_USER_MONITOR_PATH_PREFIX = os.environ.get(
    "DAILY_USER_MONITOR_PATH_PREFIX",
    "docs/60-evidence-inventory/daily-user-monitor",
).strip("/")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "<GITHUB_OWNER>/<PROJECT_SLUG>")
GITHUB_REF = os.environ.get("GITHUB_REF", "main")
GITHUB_TOKEN_SECRET_ID = os.environ.get("GITHUB_TOKEN_SECRET_ID", "xyz-pilot/github/service-token")
GITHUB_TOKEN_PARAM = os.environ.get("GITHUB_TOKEN_PARAM", "/xyz-pilot/github/service-token")
_GITHUB_TOKEN_CACHE: str | None = None
_GITHUB_TOKEN_WARNED: set[str] = set()


def lambda_handler(event: dict, context: Any) -> dict:
    # XYZ-PILOT-034 extension: operator-safe preview path for the scheduled STOP
    # daily user monitor. This path does not call start/stop, does not archive
    # to GitHub, and sends only to the explicit preview recipient.
    if isinstance(event, dict) and event.get("daily_user_monitor_dry_run") is True:
        recipient = str(event.get("recipient") or SES_FROM).strip()
        if not recipient or "@" not in recipient:
            return {"ok": False, "error": "dry-run recipient must be an email address"}
        return run_daily_user_monitor(dry_run=True, dry_run_recipients=[recipient])

    # Function URL invocations carry a `requestContext.http` block; EventBridge
    # cron invocations do not. Dispatch on that signal so the same Lambda
    # serves both the scheduled trigger and the ad-hoc dashboard button.
    if isinstance(event, dict) and event.get("requestContext", {}).get("http"):
        return handle_http(event)

    # Notification gating (per user instruction 2026-05-16):
    # Only send email on (1) scheduled EventBridge cron, (2) explicit ad-hoc
    # START via Function URL — handled in handle_http above. Any other
    # invocation (manual aws lambda invoke, console test, scheduled repaint
    # by an operator) MUST NOT send email; it still repaints the dashboard
    # so state stays accurate, just silently.
    is_scheduled = isinstance(event, dict) and event.get("source") == "aws.events"

    # XYZ-PILOT-077: extract a Cognito bearer token if the caller (Operator
    # Dashboard or chatbot admin sidebar) attached one. Lets handle_start
    # identify the human who clicked the button and emit an "ad-hoc start"
    # notification email to the team — different from the scheduled-cron
    # notification, with WHO + WHEN + a banner flagging it as outside the
    # 12:00 CEST Mon–Fri usual window.
    bearer_token = None
    if isinstance(event, dict):
        headers = event.get("headers") or {}
        if isinstance(headers, dict):
            # Headers may be lower- or mixed-case depending on caller.
            for k, v in headers.items():
                if k.lower() == "authorization" and isinstance(v, str) and v.lower().startswith("bearer "):
                    bearer_token = v[7:].strip() or None
                    break

    # XYZ-PILOT-173: optional test_mode flag in the event payload. When True,
    # send_email() narrows the recipient set to the restricted Cognito group
    # union (OPS_ONLY + XYZ_OPS) instead of the full subscriber union. The flag
    # has no effect on EC2 start/stop behavior itself — only notification routing.
    test_mode = bool(isinstance(event, dict) and event.get("test_mode") is True)
    log.info("invoked: action=%s instance=%s scheduled=%s ad_hoc_token=%s test_mode=%s",
             ACTION, EC2_INSTANCE_ID, is_scheduled, bool(bearer_token), test_mode)
    if ACTION == "start":
        return handle_start(notify=is_scheduled, bearer_token=bearer_token, test_mode=test_mode)
    elif ACTION == "stop":
        # XYZ-PILOT-080: mirror XYZ-PILOT-077 — ad-hoc STOP via Cognito-gated
        # UI also produces an [AD-HOC STOP] notification email naming WHO
        # initiated it. Scheduled cron stops are unchanged.
        return handle_stop(notify=is_scheduled, bearer_token=bearer_token, test_mode=test_mode)
    else:
        raise ValueError(f"unknown ACTION env: {ACTION!r}")


def handle_http(event: dict) -> dict:
    """Function URL handler — validates token and runs the action handler.

    Function URL = explicit ad-hoc START. Always sends email (notify=True).
    """
    qs = event.get("queryStringParameters") or {}
    supplied = qs.get("token", "")
    if not TRIGGER_TOKEN or supplied != TRIGGER_TOKEN:
        log.warning("function url invocation rejected · token mismatch")
        return {"statusCode": 401,
                "headers": {"content-type": "application/json"},
                "body": json.dumps({"error": "invalid or missing token"})}
    if ACTION != "start":
        return {"statusCode": 403,
                "headers": {"content-type": "application/json"},
                "body": json.dumps({"error": f"function url is start-only; this lambda is {ACTION!r}"})}
    log.info("function url invocation accepted · running handle_start (notify=True)")
    result = handle_start(notify=True)
    return {"statusCode": 200,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(result)}


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

def handle_start(notify: bool = False, bearer_token: str | None = None,
                 test_mode: bool = False) -> dict:
    # Holiday guard — only suppresses the SCHEDULED cron fire (notify=True
    # corresponds to source=aws.events). Manual invokes from the chatbot
    # / Operator Dashboard pass notify=False and are unaffected. Date is
    # evaluated in CEST so the calendar match aligns with the user's
    # local "today is a holiday" understanding.
    today_cest = (dt.datetime.utcnow() + dt.timedelta(hours=2)).date()
    if notify and today_cest in GERMAN_HOLIDAYS_NO_AUTO_START:
        log.info("scheduled start suppressed — %s is a German federal holiday "
                 "(no email, no EC2 start)", today_cest)
        return {"action": "skipped", "reason": f"holiday ({today_cest})",
                "notified": False}

    # XYZ-PILOT-077: identify the human who clicked the START button so the
    # extraordinary email can name them. Skips silently if the lookup
    # fails — the start itself still proceeds.
    principal, auth_error = _ad_hoc_principal_or_error("start", bearer_token)
    if auth_error:
        log.warning("ad-hoc start rejected · %s · groups=%s",
                    auth_error.get("initiator") or "unknown",
                    ",".join(auth_error.get("groups") or []) or "none")
        return auth_error
    initiator = str(principal.get("email")) if principal else None
    if initiator:
        log.info("ad-hoc start · initiator=%s · groups=%s",
                 initiator, ",".join(principal.get("groups") or []) or "none")

    is_ad_hoc = bool(initiator)

    state_before = current_state()
    log.info("start handler · state before: %s · notify: %s · ad_hoc: %s",
             state_before, notify, is_ad_hoc)
    cold_started = state_before != "running"
    if state_before != "running":
        ec2.start_instances(InstanceIds=[EC2_INSTANCE_ID])
        wait_until("running", timeout_s=300)
    # docker compose `restart: unless-stopped` brings the containers back on
    # its own, but cloudflared needs ~30 s to publish its new quick-tunnel URL
    log.info("instance running · waiting 60 s for docker stack to settle")
    time.sleep(60)

    tunnels = recapture_tunnel_urls()

    # XYZ-PILOT-038: also poll the named-tunnel MCP endpoint until it's
    # OAuth-gated (HTTP 401). The MCP container is brought up by
    # docker-compose's `restart: unless-stopped`, but cloudflared's
    # named tunnel + FastMCP's OIDC discovery against Cognito both need
    # a moment more than the bare 60 s above. Poll up to ~90 s.
    mcp = probe_mcp_health()
    for attempt in range(18):
        if mcp.get("healthy"):
            log.info("MCP healthy on attempt %d · %d ms · HTTP %s",
                     attempt, mcp["latency_ms"], mcp["code"])
            break
        log.info("MCP probe attempt %d: %s (code=%s)",
                 attempt, mcp["note"], mcp["code"])
        time.sleep(5)
        mcp = probe_mcp_health()
    else:
        log.warning("MCP never reached healthy state after 90 s · last: %s",
                    mcp.get("note"))

    dashboard_url = render_dashboard(state="running", tunnels=tunnels)

    # XYZ-PILOT-284: send the START notification PROMPTLY — as soon as the env is
    # running and tunnels/MCP/dashboard are ready — instead of holding it behind
    # the (sometimes multi-minute) Exasol warm-up. The old ordering ran
    # ensure_exasol_warmup() BEFORE this email, so a slow/failed warm-up delayed
    # the "started" notice; on 2026-06-23 it slipped ~10 min and collided with
    # an operator STOP, creating a false "un-initiated START" impression. Warm-up
    # now runs AFTER this email (below) and emits only a failure-only follow-up.
    if cold_started:
        warmup: dict[str, Any] = {
            "ok": None,
            "status": "in_progress",
            "note": "warm-up running in background after this notification",
        }
    else:
        warmup = {
            "ok": True,
            "status": "skipped",
            "note": "instance was already running; warm-up not re-run",
        }

    # XYZ-PILOT-077: email iff EITHER the scheduled cron fired (notify=True) OR a
    # Cognito-identified ad-hoc start. XYZ-PILOT-169 (dedup): also gate on
    # `cold_started` — only email if THIS invocation caused stopped→running.
    # A second email from an impatient re-click / operator race is duplicate
    # noise. Unauthenticated manual invokes (no bearer token) stay silent.
    notified = False
    if (notify or is_ad_hoc) and cold_started:
        send_email(state="running", tunnels=tunnels, dashboard_url=dashboard_url,
                   ad_hoc=is_ad_hoc, initiator=initiator, warmup=warmup,
                   test_mode=test_mode)
        notified = True
    elif notify or is_ad_hoc:
        log.info("start invoked but state was already 'running' — "
                 "skipping duplicate email (XYZ-PILOT-169 dedup) · "
                 "initiator=%s ad_hoc=%s notify=%s test_mode=%s",
                 initiator, is_ad_hoc, notify, test_mode)
    else:
        log.info("notify=False · skipping SES send (manual / repaint invocation, no bearer token)")

    # XYZ-PILOT-285 + XYZ-PILOT-284: run warm-up now that the prompt START email is
    # out. A clean warm-up needs no second email (success is the common case);
    # only a WARNING/failure sends a concise follow-up so the operator still
    # learns when the env came up cold. The returned `warmup` reflects the real
    # outcome for the dashboard / Lambda response.
    if cold_started:
        warmup = ensure_exasol_warmup()
        if notified and not warmup.get("ok"):
            send_email(state="running", tunnels=tunnels, dashboard_url=dashboard_url,
                       ad_hoc=is_ad_hoc, initiator=initiator, warmup=warmup,
                       test_mode=test_mode, warmup_followup=True)

    return {"action": "start", "state": "running", "tunnels": tunnels,
            "mcp": mcp, "warmup": warmup, "dashboard": dashboard_url,
            "notified": notified, "ad_hoc": is_ad_hoc,
            "initiator": initiator, "cold_started": cold_started,
            "test_mode": test_mode}


# ---------------------------------------------------------------------------
# Scheduled STOP daily user monitor
# ---------------------------------------------------------------------------

def _warn_github_token_once(key: str, message: str, *args: Any) -> None:
    if key in _GITHUB_TOKEN_WARNED:
        return
    log.warning(message, *args)
    _GITHUB_TOKEN_WARNED.add(key)


def _github_token_from_secret() -> str:
    """Return the GitHub PAT from Secrets Manager without logging it."""
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
            "daily user monitor GitHub token Secrets Manager lookup failed for %s: %s; falling back to SSM",
            GITHUB_TOKEN_SECRET_ID,
            type(exc).__name__,
        )
        return ""
    if not value:
        _warn_github_token_once(
            "secret_empty",
            "daily user monitor GitHub token secret %s returned an empty value; falling back to SSM",
            GITHUB_TOKEN_SECRET_ID,
        )
        return ""
    return value


def _github_token_from_ssm() -> str:
    """Return the GitHub PAT from SSM fallback without logging it."""
    try:
        resp = ssm.get_parameter(Name=GITHUB_TOKEN_PARAM, WithDecryption=True)
        return str(resp.get("Parameter", {}).get("Value") or "").strip()
    except Exception as exc:  # noqa: BLE001
        _warn_github_token_once(
            "ssm_lookup",
            "daily user monitor GitHub token SSM lookup failed for %s: %s",
            GITHUB_TOKEN_PARAM,
            type(exc).__name__,
        )
        return ""


def _github_token() -> str:
    global _GITHUB_TOKEN_CACHE
    if _GITHUB_TOKEN_CACHE is not None:
        return _GITHUB_TOKEN_CACHE
    value = _github_token_from_secret() or _github_token_from_ssm()
    if not value:
        raise RuntimeError("GitHub token unavailable from Secrets Manager and SSM fallback")
    _GITHUB_TOKEN_CACHE = value
    return value


def _github_contents_request(method: str, path: str, payload: dict[str, Any] | None = None,
                             expected_status: set[int] | None = None) -> dict[str, Any] | None:
    encoded_path = urllib.parse.quote(path, safe="/")
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{encoded_path}"
    if method == "GET":
        url += f"?ref={urllib.parse.quote(GITHUB_REF, safe='')}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {_github_token()}",
        "User-Agent": "xyz-pilot-scheduler-daily-user-monitor",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            if expected_status and response.status not in expected_status:
                raise RuntimeError(f"GitHub Contents API returned unexpected status {response.status}")
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        if method == "GET" and exc.code == 404:
            return None
        body_text = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"GitHub Contents API {method} failed: {exc.code} {exc.reason}; {body_text}") from exc


def _github_archive_daily_monitor(path: str, markdown: str, reviewed_date: dt.date) -> dict[str, Any]:
    existing = _github_contents_request("GET", path, expected_status={200})
    if existing:
        return {
            "ok": True,
            "status": "already_archived",
            "path": path,
            "html_url": existing.get("html_url"),
        }
    payload = {
        "message": (
            f"Archive daily XYZ user monitor for {reviewed_date.isoformat()}\n\n"
            "Co-Authored-By: <PILOT_NAME> Scheduler <<OPERATOR_EMAIL>>"
        ),
        "content": base64.b64encode(markdown.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_REF,
        "committer": {"name": "<PILOT_NAME> Scheduler", "email": "<OPERATOR_EMAIL>"},
    }
    created = _github_contents_request("PUT", path, payload, expected_status={200, 201}) or {}
    content = created.get("content") or {}
    commit = created.get("commit") or {}
    return {
        "ok": True,
        "status": "created",
        "path": path,
        "html_url": content.get("html_url"),
        "commit_sha": commit.get("sha"),
    }


def _daily_monitor_cognito_summary() -> dict[str, Any]:
    users: list[dict[str, Any]] = []
    pagination_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"UserPoolId": USER_POOL_ID, "Limit": 60}
        if pagination_token:
            kwargs["PaginationToken"] = pagination_token
        resp = cognito.list_users(**kwargs)
        for user in resp.get("Users", []):
            attrs = {
                str(a.get("Name")): str(a.get("Value") or "")
                for a in user.get("Attributes", [])
                if a.get("Name")
            }
            username = str(user.get("Username") or "")
            email = attrs.get("email", "").strip().lower()
            groups = _groups_for_principal(username=username, email=email)
            users.append({
                "username": username,
                "email": email,
                "enabled": bool(user.get("Enabled")),
                "status": str(user.get("UserStatus") or ""),
                "groups": groups,
                "created": str(user.get("UserCreateDate") or ""),
                "modified": str(user.get("UserLastModifiedDate") or ""),
            })
        pagination_token = resp.get("PaginationToken")
        if not pagination_token:
            break

    def in_group(group: str) -> list[str]:
        return sorted(
            [u["email"] for u in users if group in u.get("groups", []) and u.get("email")],
            key=str.lower,
        )

    confirmed = [u for u in users if u.get("enabled") and u.get("status") == "CONFIRMED"]
    xyz_domain = [u for u in confirmed if str(u.get("email", "")).endswith("@<CUSTOMER_DOMAIN>")]
    return {
        "ok": True,
        "user_pool_id": USER_POOL_ID,
        "total_users": len(users),
        "enabled_confirmed": len(confirmed),
        "customer_confirmed": len(xyz_domain),
        "groups": {group: in_group(group) for group in ("OPS_ONLY", "XYZ_OPS", "XYZ_PILOT", "XYZ_BIZ")},
        "customer_users": [
            {
                "email": u.get("email"),
                "status": u.get("status"),
                "groups": u.get("groups", []),
                "modified": u.get("modified"),
            }
            for u in sorted(xyz_domain, key=lambda x: str(x.get("email", "")))
        ],
    }


def _daily_monitor_ssm_command() -> str:
    lookback = max(1, min(72, DAILY_USER_MONITOR_LOOKBACK_HOURS))
    script = f"""
set -euo pipefail
python3 - <<'PY'
import datetime as dt
import json
import pathlib
import subprocess
import time

lookback_hours = {lookback}
now = time.time()
since_epoch = now - (lookback_hours * 3600)

def run(cmd, timeout=20):
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return {{"rc": cp.returncode, "stdout": cp.stdout, "stderr": cp.stderr}}
    except Exception as exc:
        return {{"rc": 99, "stdout": "", "stderr": f"{{type(exc).__name__}}: {{exc}}"}}

def safe_email_from_history_file(path):
    stem = path.stem
    if "_" not in stem:
        return stem
    local, domain = stem.rsplit("_", 1)
    return f"{{local}}@{{domain}}"

history = []
hist_dir = pathlib.Path("/opt/xyz-chatbot/history")
if hist_dir.exists():
    for path in sorted(hist_dir.glob("*.json")):
        try:
            stat = path.stat()
            entry_count = None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    entry_count = len(data)
                elif isinstance(data, dict):
                    for key in ("messages", "history", "turns"):
                        if isinstance(data.get(key), list):
                            entry_count = len(data[key])
                            break
                    if entry_count is None:
                        entry_count = len(data)
            except Exception:
                entry_count = None
            history.append({{
                "email": safe_email_from_history_file(path),
                "mtime_utc": dt.datetime.utcfromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat() + "Z",
                "age_hours": round((now - stat.st_mtime) / 3600, 2),
                "modified_in_window": stat.st_mtime >= since_epoch,
                "size_bytes": stat.st_size,
                "entry_count": entry_count,
            }})
        except Exception as exc:
            history.append({{"file": path.name, "error": f"{{type(exc).__name__}}: {{exc}}"}})

containers = {{}}
ps = run(["sudo", "docker", "ps", "--format", "{{{{.Names}}}}\\t{{{{.Status}}}}"])
if ps["rc"] == 0:
    for line in ps["stdout"].splitlines():
        parts = line.split("\\t", 1)
        if len(parts) == 2:
            containers[parts[0]] = parts[1]

service_candidates = {{
    "chatbot": ["xyz-pilot-chatbot", "chatbot", "xyz-chatbot"],
    "qbr_streamlit": ["xyz-pilot-qbr", "qbr", "services/streamlit-insights"],
    "grafana": ["xyz-pilot-grafana", "grafana"],
    "mcp": ["xyz-pilot-mcp", "mcp"],
}}

def resolve_container(names):
    for wanted in names:
        for actual in containers:
            if actual == wanted or wanted in actual:
                return actual
    return None

service_logs = {{}}
for service, names in service_candidates.items():
    container = resolve_container(names)
    if not container:
        service_logs[service] = {{"container": None, "available": False, "note": "container not found"}}
        continue
    result = run(["sudo", "docker", "logs", "--since", f"{{lookback_hours}}h", container], timeout=25)
    lines = (result["stdout"] + "\\n" + result["stderr"]).splitlines()
    lower = [line.lower() for line in lines]
    service_logs[service] = {{
        "container": container,
        "available": result["rc"] == 0,
        "line_count": len([line for line in lines if line.strip()]),
        "error_lines": sum(1 for line in lower if "error" in line or "exception" in line or "traceback" in line),
        "warning_lines": sum(1 for line in lower if "warn" in line),
        "http_markers": sum(1 for line in lower if " get " in line or " post " in line or "http/" in line),
        "tool_or_query_markers": sum(1 for line in lower if "tool" in line or "exasol_query" in line or "select " in line),
        "note": "" if result["rc"] == 0 else result["stderr"][:180],
    }}

print(json.dumps({{
    "ok": True,
    "host": pathlib.Path("/etc/hostname").read_text(encoding="utf-8").strip() if pathlib.Path("/etc/hostname").exists() else "",
    "lookback_hours": lookback_hours,
    "history": history,
    "containers": containers,
    "service_logs": service_logs,
}}, sort_keys=True))
PY
"""
    return "bash -lc " + shlex.quote(script)


def _run_daily_monitor_host_probe() -> dict[str, Any]:
    ssm_ready = wait_for_ssm_online(timeout_s=min(WARMUP_SSM_ONLINE_TIMEOUT_S, 90))
    if not ssm_ready.get("ok"):
        return {"ok": False, "status": "ssm_offline", "note": ssm_ready.get("note", "SSM not online")}
    try:
        resp = ssm.send_command(
            InstanceIds=[EC2_INSTANCE_ID],
            DocumentName="AWS-RunShellScript",
            Comment="<PILOT_NAME> scheduled STOP daily user activity monitor",
            Parameters={
                "commands": [_daily_monitor_ssm_command()],
                "executionTimeout": ["90"],
            },
            TimeoutSeconds=120,
        )
        command_id = resp["Command"]["CommandId"]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "send_command_failed", "note": f"{type(exc).__name__}: {str(exc)[:160]}"}

    deadline = time.time() + 115
    last_note = "Pending"
    while time.time() < deadline:
        try:
            inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=EC2_INSTANCE_ID)
            status = inv.get("Status", "Unknown")
            if status in {"Success", "Cancelled", "TimedOut", "Failed", "Cancelling"}:
                stdout = inv.get("StandardOutputContent", "")
                stderr = inv.get("StandardErrorContent", "")
                if status == "Success" and inv.get("ResponseCode") == 0:
                    try:
                        data = json.loads(stdout.strip())
                    except Exception as exc:  # noqa: BLE001
                        return {
                            "ok": False,
                            "status": "parse_failed",
                            "command_id": command_id,
                            "note": f"{type(exc).__name__}: {str(exc)[:120]}",
                            "stdout_tail": stdout[-500:],
                            "stderr_tail": stderr[-500:],
                        }
                    data["command_id"] = command_id
                    return data
                return {
                    "ok": False,
                    "status": status,
                    "command_id": command_id,
                    "response_code": inv.get("ResponseCode"),
                    "note": (stderr or stdout)[-500:],
                }
            last_note = status
        except Exception as exc:  # noqa: BLE001
            last_note = f"{type(exc).__name__}: {str(exc)[:120]}"
        time.sleep(5)
    return {"ok": False, "status": "timeout", "command_id": command_id, "note": last_note}


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_None observed._\n"
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(cell).replace("\n", " ") for cell in row) + " |")
    return "\n".join(out) + "\n"


def _render_daily_user_monitor_report(
    *,
    reviewed_date: dt.date,
    now_utc: dt.datetime,
    cognito_summary: dict[str, Any],
    host_summary: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    active_history = [
        h for h in host_summary.get("history", [])
        if h.get("modified_in_window")
    ] if host_summary.get("ok") else []
    service_logs = host_summary.get("service_logs", {}) if host_summary.get("ok") else {}
    customer_users = cognito_summary.get("customer_users", []) if cognito_summary.get("ok") else []

    extraordinary: list[str] = []
    errors_total = sum(int(v.get("error_lines", 0)) for v in service_logs.values() if isinstance(v, dict))
    if active_history:
        extraordinary.append(f"{len(active_history)} chatbot history file(s) changed in the monitor window.")
    if errors_total:
        extraordinary.append(f"{errors_total} error/exception log marker(s) counted across monitored containers.")
    if not active_history and not errors_total:
        extraordinary.append("No extraordinary user-activity marker detected from current metadata sources.")

    executive_summary = {
        "reviewed_date": reviewed_date.isoformat(),
        "total_cognito_users": cognito_summary.get("total_users"),
        "enabled_confirmed_users": cognito_summary.get("enabled_confirmed"),
        "customer_confirmed_users": cognito_summary.get("customer_confirmed"),
        "active_chatbot_histories": len(active_history),
        "monitored_service_log_error_markers": errors_total,
        "extraordinary": extraordinary,
    }

    lines = [
        f"# <PILOT_NAME> Daily User Activity Monitor — {reviewed_date.isoformat()}",
        "",
        f"- Generated UTC: `{now_utc.replace(microsecond=0).isoformat()}Z`",
        f"- Reviewed business day: `{reviewed_date.isoformat()}`",
        f"- Lookback window: `{DAILY_USER_MONITOR_LOOKBACK_HOURS}h` ending immediately before the scheduled 24:00 CEST STOP",
        f"- Source Lambda: `xyz-pilot-stop` scheduled EventBridge path only",
        f"- EC2 instance: `{EC2_INSTANCE_ID}`",
        "",
        "## Executive Summary",
        "",
        f"- Cognito users: `{cognito_summary.get('total_users', 'unknown')}` total, "
        f"`{cognito_summary.get('enabled_confirmed', 'unknown')}` enabled+confirmed.",
        f"- <CUSTOMER_NAME> confirmed users: `{cognito_summary.get('customer_confirmed', 'unknown')}`.",
        f"- Chatbot users with changed local history metadata in window: `{len(active_history)}`.",
        f"- Container log error/exception markers in window: `{errors_total}`.",
        "",
        "### Extraordinary Activity Markers",
        "",
    ]
    lines.extend([f"- {item}" for item in extraordinary])
    lines.extend([
        "",
        "## Cognito User Posture",
        "",
        _markdown_table(
            ["Group", "User emails"],
            [[group, ", ".join(emails) or "_none_"] for group, emails in (cognito_summary.get("groups") or {}).items()],
        ),
        "",
        "### <CUSTOMER_NAME> Confirmed Users",
        "",
        _markdown_table(
            ["Email", "Status", "Groups", "Last modified"],
            [[u.get("email", ""), u.get("status", ""), ", ".join(u.get("groups", [])), u.get("modified", "")]
             for u in customer_users],
        ),
        "",
        "## Chatbot Activity Metadata",
        "",
        "This section records metadata only. It does not archive prompt text, responses, tokens, cookies, or credentials.",
        "",
        _markdown_table(
            ["Email", "Modified UTC", "Age h", "Entries", "Bytes", "In window"],
            [[h.get("email", ""), h.get("mtime_utc", ""), h.get("age_hours", ""), h.get("entry_count", ""),
              h.get("size_bytes", ""), h.get("modified_in_window", False)]
             for h in sorted(host_summary.get("history", []), key=lambda x: str(x.get("email", "")))]
            if host_summary.get("ok") else [],
        ),
        "",
        "## Runtime Service Activity Markers",
        "",
        _markdown_table(
            ["Area", "Container", "Log lines", "HTTP markers", "Tool/query markers", "Warnings", "Errors", "Note"],
            [[area, data.get("container") or "_not found_", data.get("line_count", 0), data.get("http_markers", 0),
              data.get("tool_or_query_markers", 0), data.get("warning_lines", 0), data.get("error_lines", 0),
              data.get("note", "")]
             for area, data in sorted(service_logs.items())],
        ),
        "",
        "## Attribution Limits",
        "",
        "- QBR Streamlit and Grafana runtime logs currently provide service activity markers, not Cognito-user attribution.",
        "- Chatbot attribution is based on local per-user history file metadata only.",
        "- Direct Exasol SQL activity is not attributed by this monitor yet; add a DB-side audit query if that becomes a UAT requirement.",
        "- The monitor intentionally runs before EC2 shutdown so Docker/log metadata is still available.",
        "",
        "## Raw Metadata Snapshot",
        "",
        "```json",
        json.dumps({"cognito": cognito_summary, "host": host_summary}, indent=2, sort_keys=True, default=str),
        "```",
        "",
    ])
    return "\n".join(lines), executive_summary


def _send_daily_user_monitor_email(
    executive_summary: dict[str, Any],
    archive: dict[str, Any],
    *,
    recipients_override: list[str] | None = None,
    dry_run: bool = False,
    markdown_preview: str | None = None,
) -> dict[str, Any]:
    recipients = recipients_override or _warmup_warning_recipients()
    dry_run_label = "DRY-RUN " if dry_run else ""
    subject = (
        f"{executive_summary['reviewed_date'].replace('-', '.')} : "
        f"{dry_run_label}Daily <PILOT_NAME> user activity monitor"
    )
    archive_url = archive.get("html_url") or "(not available)"
    if dry_run:
        archive_url = "(dry-run preview only; no GitHub archive was created)"
    body = f"""<PILOT_NAME> — daily user activity monitor

Reviewed day: {executive_summary['reviewed_date']}
Archive path: {archive.get('path')}
Archive URL:  {archive_url}

Executive summary
  Cognito users:              {executive_summary.get('total_cognito_users')} total
  Enabled + confirmed users:  {executive_summary.get('enabled_confirmed_users')}
  <CUSTOMER_NAME> confirmed users:     {executive_summary.get('customer_confirmed_users')}
  Chatbot active histories:   {executive_summary.get('active_chatbot_histories')}
  Service error markers:      {executive_summary.get('monitored_service_log_error_markers')}

Extraordinary activity markers
""" + "\n".join(f"  - {item}" for item in executive_summary.get("extraordinary", [])) + f"""

Scope
  The production monitor is generated once per day by the regular 24:00 CEST
  STOP cycle before EC2 shutdown. Operator Dashboard ad-hoc STOP calls never
  generate it.
  The report archives metadata only: no prompt text, response text, tokens,
  cookies, passwords, or secret values.
  Dry-run mode reads the same metadata sources non-destructively, does not
  archive to GitHub, does not purge or drain logs, and does not stop EC2.

Confidentiality
  {CONFIDENTIAL_EMAIL_FOOTER}

— Sent automatically by the <PILOT_NAME> scheduler Lambda before scheduled STOP.
"""
    if markdown_preview:
        body += (
            "\n\n────────────────────────────────────────────────────────────────────\n"
            "Dry-run full Markdown report preview\n"
            "────────────────────────────────────────────────────────────────────\n\n"
            + markdown_preview
        )
    verified: set[str] = set()
    try:
        attrs = ses.get_identity_verification_attributes(Identities=recipients + [SES_FROM])
        for addr, info in attrs.get("VerificationAttributes", {}).items():
            if info.get("VerificationStatus") == "Success":
                verified.add(addr)
    except Exception as exc:  # noqa: BLE001
        log.warning("daily monitor could not check SES verification: %s", exc)
        verified = set(recipients + [SES_FROM])
    deliverable = [addr for addr in recipients if addr in verified]
    if SES_FROM not in verified:
        return {"ok": False, "status": "sender_unverified", "recipients": deliverable}
    if not deliverable:
        return {"ok": False, "status": "no_verified_ops_only_recipients", "recipients": []}
    ses.send_email(
        Source=SES_FROM,
        Destination={"ToAddresses": deliverable},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
        },
    )
    log.info("daily user monitor email sent to %s · subject: %s", deliverable, subject)
    return {"ok": True, "status": "sent", "recipients": deliverable, "subject": subject}


def _send_daily_user_monitor_failure_email(reviewed_date: dt.date, error: str) -> dict[str, Any]:
    recipients = _warmup_warning_recipients()
    subject = f"{reviewed_date.isoformat().replace('-', '.')} : Daily <PILOT_NAME> user activity monitor FAILED"
    body = f"""<PILOT_NAME> — daily user activity monitor failed

Reviewed day: {reviewed_date.isoformat()}
Failure: {error[:1000]}

The scheduled STOP sequence will continue so the environment is still brought
down for cost discipline. Operators should inspect the xyz-pilot-stop Lambda
CloudWatch logs and rerun the monitor manually if evidence is required.

Confidentiality
  {CONFIDENTIAL_EMAIL_FOOTER}

— Sent automatically by the <PILOT_NAME> scheduler Lambda before scheduled STOP.
"""
    try:
        if recipients:
            ses.send_email(
                Source=SES_FROM,
                Destination={"ToAddresses": recipients},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                },
            )
            return {"ok": True, "status": "sent", "recipients": recipients, "subject": subject}
    except Exception as exc:  # noqa: BLE001
        log.warning("daily monitor failure email failed: %s", type(exc).__name__)
    return {"ok": False, "status": "not_sent", "recipients": recipients}


def run_daily_user_monitor(
    *,
    dry_run: bool = False,
    dry_run_recipients: list[str] | None = None,
) -> dict[str, Any]:
    """Archive the daily user activity report and notify OPS_ONLY.

    Designed for the regular EventBridge STOP path only. The caller enforces
    that gate so ad-hoc Operator Dashboard STOPs never generate daily evidence.
    """
    if not DAILY_USER_MONITOR_ENABLED:
        return {"ok": True, "status": "disabled"}
    now_utc = dt.datetime.utcnow()
    now_cest = now_utc + dt.timedelta(hours=2)
    reviewed_date = now_cest.date()
    # At 24:00 CEST the "reviewed day" is the day that is ending, not the
    # just-started next day represented by UTC+2 after midnight.
    if now_cest.hour < 6:
        reviewed_date = reviewed_date - dt.timedelta(days=1)
    path = f"{DAILY_USER_MONITOR_PATH_PREFIX}/XYZ_PILOT_DAILY_USER_MONITOR_{reviewed_date.isoformat()}.md"
    existing = None if dry_run else _github_contents_request("GET", path, expected_status={200})
    if existing and not dry_run:
        log.info("daily user monitor already archived for %s at %s", reviewed_date, path)
        return {
            "ok": True,
            "status": "already_archived",
            "path": path,
            "html_url": existing.get("html_url"),
            "email": {"ok": True, "status": "skipped_duplicate"},
        }
    cognito_summary = _daily_monitor_cognito_summary()
    host_summary = _run_daily_monitor_host_probe()
    markdown, executive_summary = _render_daily_user_monitor_report(
        reviewed_date=reviewed_date,
        now_utc=now_utc,
        cognito_summary=cognito_summary,
        host_summary=host_summary,
    )
    if dry_run:
        archive = {
            "ok": True,
            "status": "dry_run_not_archived",
            "path": path,
            "html_url": None,
        }
    else:
        archive = _github_archive_daily_monitor(path, markdown, reviewed_date)
    email_result = _send_daily_user_monitor_email(
        executive_summary,
        archive,
        recipients_override=dry_run_recipients if dry_run else None,
        dry_run=dry_run,
        markdown_preview=markdown if dry_run else None,
    )
    archive["email"] = email_result
    archive["dry_run"] = dry_run
    return archive


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------

def handle_stop(notify: bool = False, bearer_token: str | None = None,
                test_mode: bool = False) -> dict:
    # XYZ-PILOT-080: identify the human who clicked the STOP button (mirrors
    # XYZ-PILOT-077's handle_start). When the bearer token resolves to a
    # Cognito user, force the notification email even though notify=False.
    principal, auth_error = _ad_hoc_principal_or_error("stop", bearer_token)
    if auth_error:
        log.warning("ad-hoc stop rejected · %s · groups=%s",
                    auth_error.get("initiator") or "unknown",
                    ",".join(auth_error.get("groups") or []) or "none")
        return auth_error
    initiator = str(principal.get("email")) if principal else None
    if initiator:
        log.info("ad-hoc stop · initiator=%s · groups=%s",
                 initiator, ",".join(principal.get("groups") or []) or "none")

    is_ad_hoc = bool(initiator)

    state_before = current_state()
    log.info("stop handler · state before: %s · notify: %s · ad_hoc: %s",
             state_before, notify, is_ad_hoc)
    transitioned = state_before == "running"
    # Capture URLs *before* stopping (they'll be the last-known values for the
    # "now stopping" email).
    tunnels: dict[str, str] | None = None
    if transitioned:
        try:
            tunnels = recapture_tunnel_urls()
        except Exception as exc:  # noqa: BLE001
            log.warning("URL recapture failed pre-stop: %s", exc)
        daily_monitor: dict[str, Any] | None = None
        if notify and not is_ad_hoc:
            try:
                daily_monitor = run_daily_user_monitor()
            except Exception as exc:  # noqa: BLE001
                now_cest = dt.datetime.utcnow() + dt.timedelta(hours=2)
                reviewed_date = now_cest.date()
                if now_cest.hour < 6:
                    reviewed_date = reviewed_date - dt.timedelta(days=1)
                log.exception("scheduled STOP daily user monitor failed before EC2 stop")
                daily_monitor = {
                    "ok": False,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    "failure_email": _send_daily_user_monitor_failure_email(
                        reviewed_date,
                        f"{type(exc).__name__}: {str(exc)}",
                    ),
                }
        ec2.stop_instances(InstanceIds=[EC2_INSTANCE_ID])
        wait_until("stopped", timeout_s=300)
    else:
        daily_monitor = None

    dashboard_url = render_dashboard(state="stopped", tunnels=tunnels)
    # XYZ-PILOT-080: same notify-or-ad_hoc gate as handle_start (XYZ-PILOT-077).
    # Unauthenticated raw `aws lambda invoke` with empty payload still falls
    # through silent — only Cognito-identified UI clicks generate the email.
    #
    # XYZ-PILOT-169 (dedup): also gate on `transitioned` — only email if THIS
    # invocation actually caused the running→stopped transition. If state was
    # already stopped (race between two operators clicking STOP within
    # seconds of each other; or impatient second click while the first
    # invocation was still in its post-stop tail), the previous invocation
    # already emailed when it brought the env down. A second email is
    # duplicate noise that erodes team trust.
    notified = False
    if (notify or is_ad_hoc) and transitioned:
        send_email(state="stopped", tunnels=tunnels, dashboard_url=dashboard_url,
                   ad_hoc=is_ad_hoc, initiator=initiator, test_mode=test_mode)
        notified = True
    elif notify or is_ad_hoc:
        log.info("stop invoked but state was already 'stopped' — "
                 "skipping duplicate email (XYZ-PILOT-169 dedup) · "
                 "initiator=%s ad_hoc=%s notify=%s test_mode=%s",
                 initiator, is_ad_hoc, notify, test_mode)
    else:
        log.info("notify=False · skipping SES send (manual / repaint invocation, no bearer token)")
    return {"action": "stop", "state": "stopped", "dashboard": dashboard_url,
            "notified": notified, "ad_hoc": is_ad_hoc,
            "initiator": initiator, "transitioned": transitioned,
            "test_mode": test_mode, "daily_user_monitor": daily_monitor}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def current_state() -> str:
    r = ec2.describe_instances(InstanceIds=[EC2_INSTANCE_ID])
    return r["Reservations"][0]["Instances"][0]["State"]["Name"]


PROBE_USER_AGENT = "xyz-pilot-status-probe/1.0 (+https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>)"


def probe_mcp_health(timeout: int = 5) -> dict:
    """Probe the named-tunnel MCP endpoint with an unauth initialize call.

    Expected healthy state: HTTP 401 (FastMCP auth gate active) OR 403
    (Cloudflare bot-mgmt deflecting the request before it reaches the
    container — still means the path is reachable + something is auth-gating
    it; container behind CF). Both mean "the URL is up, something is
    rejecting the unauth call" — exactly what we want.

    XYZ-PILOT-057 (2026-05-21): added User-Agent header (default Python urllib
    UA was being treated as bot by Cloudflare → 403). Also accept both 401
    and 403 as healthy so future bot-mgmt / FastMCP-upstream changes don't
    silently flip the dashboard red.

    Returns: {healthy: bool, code: int, latency_ms: int, note: str}
    """
    import urllib.request
    import urllib.error
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize",'
        b'"params":{"protocolVersion":"2024-11-05","capabilities":{},'
        b'"clientInfo":{"name":"dashboard-probe","version":"0.1"}}}'
    )
    req = urllib.request.Request(
        EXA_MCP_URL, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": PROBE_USER_AGENT,
        },
        data=body,
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ms = int((time.time() - t0) * 1000)
            return {"healthy": False, "code": r.status, "latency_ms": ms,
                    "note": "unexpected 2xx (auth not gating?)"}
    except urllib.error.HTTPError as exc:
        ms = int((time.time() - t0) * 1000)
        if exc.code in (401, 403):
            return {"healthy": True, "code": exc.code, "latency_ms": ms,
                    "note": f"OAuth-gated (HTTP {exc.code})"}
        return {"healthy": False, "code": exc.code, "latency_ms": ms,
                "note": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        ms = int((time.time() - t0) * 1000)
        return {"healthy": False, "code": 0, "latency_ms": ms,
                "note": str(exc)[:80]}


CHATBOT_URL = os.environ.get("CHATBOT_URL", "https://<PROJECT_SLUG>-chat.<PILOT_DOMAIN>")
CONFIDENTIAL_EMAIL_FOOTER = (
    "Confidential - <PILOT_NAME> - © Exasol AG · ESL © <SEMANTIC_LAYER_VENDOR> · "
    "for XYZ evaluation use only."
)


def probe_chatbot_health(timeout: int = 5) -> dict:
    """Probe the chatbot's Streamlit health endpoint.

    Streamlit serves /_stcore/health unauthenticated and returns plain text
    "ok" when the app is up. The chatbot's Cognito gate is only on the main
    page; the health endpoint is reachable for liveness checks.

    Returns: {healthy: bool, code: int, latency_ms: int, note: str}
    """
    import urllib.request
    import urllib.error
    url = f"{CHATBOT_URL.rstrip('/')}/_stcore/health"
    req = urllib.request.Request(
        url, method="GET",
        headers={
            "Accept": "text/plain",
            "User-Agent": PROBE_USER_AGENT,
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ms = int((time.time() - t0) * 1000)
            body = r.read(64).decode(errors="replace").strip()
            if r.status == 200 and body.lower() == "ok":
                return {"healthy": True, "code": 200, "latency_ms": ms,
                        "note": f"OK · {ms} ms"}
            return {"healthy": False, "code": r.status, "latency_ms": ms,
                    "note": f"HTTP {r.status} body={body[:40]!r}"}
    except urllib.error.HTTPError as exc:
        ms = int((time.time() - t0) * 1000)
        return {"healthy": False, "code": exc.code, "latency_ms": ms,
                "note": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        ms = int((time.time() - t0) * 1000)
        return {"healthy": False, "code": 0, "latency_ms": ms,
                "note": str(exc)[:80]}


def wait_until(target_state: str, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if current_state() == target_state:
            log.info("reached state: %s", target_state)
            return
        time.sleep(5)
    raise TimeoutError(f"instance did not reach {target_state!r} within {timeout_s} s")


def wait_for_ssm_online(timeout_s: int = WARMUP_SSM_ONLINE_TIMEOUT_S) -> dict[str, Any]:
    """Wait until the EC2 instance is visible to SSM Run Command."""
    deadline = time.time() + timeout_s
    last_note = "not checked"
    while time.time() < deadline:
        try:
            resp = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [EC2_INSTANCE_ID]}]
            )
            infos = resp.get("InstanceInformationList", [])
            if infos:
                info = infos[0]
                ping = info.get("PingStatus", "Unknown")
                last_note = f"SSM PingStatus={ping}"
                if ping == "Online":
                    return {"ok": True, "status": "online", "note": last_note}
            else:
                last_note = "instance not yet listed by SSM"
        except Exception as exc:  # noqa: BLE001
            last_note = f"{type(exc).__name__}: {str(exc)[:120]}"
        time.sleep(5)
    return {"ok": False, "status": "offline", "note": last_note}


def _warmup_guard_command() -> str:
    service = shlex.quote(WARMUP_SERVICE_NAME)
    log_path = shlex.quote(WARMUP_LOG_PATH)
    script = f"""
set -euo pipefail
SERVICE={service}
LOG={log_path}
echo warmup_guard_started=$(date -u --iso-8601=seconds)
echo warmup_guard_boot_id=$(cat /proc/sys/kernel/random/boot_id)
boot_epoch=$(date -d "$(uptime -s)" +%s)
log_epoch=0
if [ -e "$LOG" ]; then
  log_epoch=$(stat -c %Y "$LOG")
fi
if ! systemctl cat "$SERVICE" >/dev/null 2>&1; then
  echo "warmup_guard_error=missing_service:$SERVICE" >&2
  exit 40
fi
unit_result=$(systemctl show "$SERVICE" -p Result --value 2>/dev/null || true)
unit_status=$(systemctl show "$SERVICE" -p ExecMainStatus --value 2>/dev/null || true)
if [ "$unit_result" = "success" ] && [ "$unit_status" = "0" ] && \
   [ "$log_epoch" -ge "$boot_epoch" ] && sudo grep -q "xyz-warmup finished" "$LOG"; then
  echo warmup_guard_already_current=1
else
  echo warmup_guard_starting_service=1
  sudo systemctl start "$SERVICE"
fi
systemctl show "$SERVICE" -p Result -p ActiveState -p SubState \
  -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp --no-pager
if ! sudo test -s "$LOG"; then
  echo "warmup_guard_error=missing_log:$LOG" >&2
  exit 41
fi
if ! sudo grep -q "xyz-warmup finished" "$LOG"; then
  echo "warmup_guard_error=log_has_no_finished_marker" >&2
  sudo tail -n 40 "$LOG" >&2 || true
  exit 42
fi
if [ "$(systemctl show "$SERVICE" -p Result --value)" != "success" ]; then
  echo "warmup_guard_error=unit_result_not_success" >&2
  sudo tail -n 40 "$LOG" >&2 || true
  exit 43
fi
sudo tail -n 12 "$LOG"
echo warmup_guard_finished=$(date -u --iso-8601=seconds)
"""
    return "bash -lc " + shlex.quote(script)


def _warmup_summary(stdout: str, stderr: str) -> str:
    lines = (stdout + "\n" + stderr).splitlines()
    interesting = [
        line.strip()
        for line in lines
        if (
            "warmup_guard_" in line
            or "xyz-warmup finished" in line
            or "[warmup] done at" in line
            or line.startswith("Result=")
            or line.startswith("ExecMain")
        )
    ]
    return " | ".join(interesting[-8:])[:900]


def ensure_exasol_warmup() -> dict[str, Any]:
    """Run or wait for the EC2 warm-up unit before announcing START readiness."""
    started = time.time()
    ssm_ready = wait_for_ssm_online()
    if not ssm_ready.get("ok"):
        log.warning("warmup gate skipped: %s", ssm_ready)
        return {
            "ok": False,
            "status": "ssm_offline",
            "note": ssm_ready.get("note", "SSM not online"),
            "duration_s": int(time.time() - started),
        }

    try:
        resp = ssm.send_command(
            InstanceIds=[EC2_INSTANCE_ID],
            DocumentName="AWS-RunShellScript",
            Comment="XYZ-PILOT-156 start-path Exasol warm-up gate",
            Parameters={
                "commands": [_warmup_guard_command()],
                "executionTimeout": [str(WARMUP_SSM_COMMAND_TIMEOUT_S)],
            },
            TimeoutSeconds=WARMUP_SSM_COMMAND_TIMEOUT_S + 60,
        )
        command_id = resp["Command"]["CommandId"]
    except Exception as exc:  # noqa: BLE001
        log.warning("warmup gate send-command failed: %s", type(exc).__name__)
        return {
            "ok": False,
            "status": "send_command_failed",
            "note": f"{type(exc).__name__}: {str(exc)[:160]}",
            "duration_s": int(time.time() - started),
        }

    deadline = time.time() + WARMUP_SSM_COMMAND_TIMEOUT_S + 45
    last_status = "Pending"
    last_note = ""
    while time.time() < deadline:
        try:
            inv = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=EC2_INSTANCE_ID,
            )
            last_status = inv.get("Status", last_status)
            if last_status in {"Success", "Cancelled", "TimedOut", "Failed", "Cancelling"}:
                stdout = inv.get("StandardOutputContent", "")
                stderr = inv.get("StandardErrorContent", "")
                ok = last_status == "Success" and inv.get("ResponseCode") == 0
                summary = _warmup_summary(stdout, stderr)
                result = {
                    "ok": ok,
                    "status": last_status,
                    "command_id": command_id,
                    "response_code": inv.get("ResponseCode"),
                    "duration_s": int(time.time() - started),
                    "note": summary or (stderr or stdout)[-500:],
                }
                if ok:
                    log.info("warmup gate succeeded: %s", result)
                else:
                    log.warning("warmup gate did not succeed: %s", result)
                return result
            last_note = f"SSM status={last_status}"
        except Exception as exc:  # noqa: BLE001
            last_note = f"{type(exc).__name__}: {str(exc)[:120]}"
        time.sleep(10)

    log.warning("warmup gate timed out · command_id=%s · last=%s", command_id, last_note)
    return {
        "ok": False,
        "status": "timeout",
        "command_id": command_id,
        "duration_s": int(time.time() - started),
        "note": last_note,
    }


def recapture_tunnel_urls() -> dict[str, str]:
    """Return the stable named-tunnel URLs.

    XYZ-PILOT-025 / -020 Resolved 2026-05-20: Streamlit + Grafana migrated from
    rotating *.trycloudflare.com quick tunnels to named tunnels on the
    <PILOT_DOMAIN> zone. No scraping needed; the URLs are constants.
    """
    return {
        "streamlit": "https://<PROJECT_SLUG>-qbr.<PILOT_DOMAIN>",
        "grafana":   "https://<PROJECT_SLUG>-grafana.<PILOT_DOMAIN>",
    }


def render_dashboard(state: str, tunnels: dict[str, str] | None) -> str:
    """XYZ-PILOT-076: this used to render a full ServiceURI dashboard with
    EC2 state, badges, tunnel URLs, etc., publicly served over HTTP from
    the s3-website endpoint. That exposed EC2/AWS-account/service-URI
    inventory anonymously. Now the s3-website page is a thin meta-refresh
    redirect to the Cognito-gated Operator Dashboard (`dashboard.html`,
    HTTPS). All operational content is rendered live there from temp
    AWS creds the page mints via Cognito Identity Pool — same
    authenticated surface as the chatbot. `state` and `tunnels` are
    ignored intentionally; kept in the signature for caller stability.
    """
    _ = state, tunnels  # signature kept for back-compat with handle_start/stop
    html_page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title><PILOT_NAME> — moved to the Operator Dashboard</title>
<meta http-equiv="refresh" content="2; url={html.escape(OPERATOR_DASHBOARD)}">
<link rel="icon" href="{FAVICON_32}" sizes="32x32" type="image/png">
<link rel="icon" href="{FAVICON_192}" sizes="192x192" type="image/png">
<style>
  body  {{ font-family:-apple-system,BlinkMacSystemFont,sans-serif;
           text-align:center; padding:4em 1em; color:#222;
           max-width:640px; margin:auto; line-height:1.5; }}
  h2    {{ font-size:1.5em; margin-bottom:.3em; }}
  .lede {{ color:#666; margin-bottom:2em; }}
  .cta  {{ display:inline-block; padding:.7em 1.4em; background:#1AAB40;
           color:#fff; border-radius:4px; font-weight:600;
           text-decoration:none; }}
  .cta:hover {{ background:#168D36; }}
  .meta {{ color:#888; font-size:.85em; margin-top:2em; }}
</style>
</head><body>
<h2><PILOT_NAME> Dashboard moved</h2>
<p class="lede">All operational status, ServiceURIs, and ad-hoc
Start/Stop controls now live behind Cognito SSO on the Operator
Dashboard.</p>
<p><a class="cta" href="{html.escape(OPERATOR_DASHBOARD)}">▶ Open Operator Dashboard</a></p>
<p class="meta">Auto-redirecting in 2 seconds…<br>
If your browser doesn't redirect, click the button above.</p>
</body></html>"""

    s3.put_object(
        Bucket=STATUS_BUCKET,
        Key="index.html",
        Body=html_page.encode("utf-8"),
        ContentType="text/html; charset=utf-8",
        CacheControl="no-cache, must-revalidate",
    )
    return OPERATOR_DASHBOARD


def _render_dashboard_legacy_unused(state: str, tunnels: dict[str, str] | None) -> str:
    """Kept temporarily for reference during XYZ-PILOT-076 rollout — the
    previous full-content renderer that disclosed EC2 + account + tunnel
    inventory anonymously. Not called from anywhere; delete on next
    cleanup pass once the redirect-only design has soaked."""
    now_utc = dt.datetime.utcnow()
    now_cest = now_utc + dt.timedelta(hours=2)  # CEST in May
    rows = []

    def row(name: str, uri: str, status: str, badge_color: str) -> None:
        uri_html = f'<a href="{html.escape(uri)}">{html.escape(uri)}</a>' if uri else "<em>n/a (not running)</em>"
        rows.append(
            f"<tr><td><b>{html.escape(name)}</b></td><td>{uri_html}</td>"
            f"<td><span class='badge {badge_color}'>{html.escape(status)}</span></td></tr>"
        )

    is_running = (state == "running")
    badge = "ok" if is_running else "off"
    state_text = "RUNNING" if is_running else "STOPPED"

    row("Exasol DB", f"{EXASOL_DNS}:8563", state_text, badge)
    row("Exasol Admin UI", f"https://{EXASOL_DNS}:8443", state_text, badge)
    row("SSH", f"ssh ubuntu@{EXASOL_DNS}", state_text, badge)
    streamlit_uri = (tunnels or {}).get("streamlit", "")
    grafana_uri   = (tunnels or {}).get("grafana", "")
    row("Streamlit QBR (public)", streamlit_uri or "—", state_text, badge)
    row("Grafana (public)",       grafana_uri   or "—", state_text, badge)
    # XYZ-PILOT-035: fixed hostname via named Cloudflare tunnel.
    # XYZ-PILOT-037: live HTTP probe of the MCP endpoint so the dashboard
    # reflects the *actual* MCP server health, not just the EC2 state.
    # XYZ-PILOT-057 (2026-05-21): probe accepts 401|403 + adds User-Agent
    # so Cloudflare bot-mgmt doesn't flip the dashboard red on Lambda's
    # default urllib UA.
    # Skipped when EC2 is stopped (probe would always time out).
    if is_running:
        mcp = probe_mcp_health(timeout=5)
        mcp_badge = "ok" if mcp["healthy"] else "off"
        mcp_text = (f"OAUTH-GATED · {mcp['latency_ms']} ms"
                    if mcp["healthy"]
                    else f"DOWN · {mcp['note']}")
    else:
        mcp_text, mcp_badge = "STOPPED", "off"
    row("Exasol MCP (OAuth, fixed URL)", EXA_MCP_URL, mcp_text, mcp_badge)

    # XYZ-PILOT-056 (2026-05-21): chatbot status surfaced on the dashboard
    # so subscribers see whether https://<PROJECT_SLUG>-dashboard.<PILOT_DOMAIN>
    # is reachable. Skipped while EC2 is stopped — the chatbot lives on
    # the same EC2 and is necessarily down when EC2 is.
    if is_running:
        cb = probe_chatbot_health(timeout=5)
        cb_badge = "ok" if cb["healthy"] else "off"
        cb_text  = (f"RUNNING · {cb['latency_ms']} ms"
                    if cb["healthy"]
                    else f"DOWN · {cb['note']}")
    else:
        cb_text, cb_badge = "STOPPED", "off"
    row("<PILOT_NAME> Chatbot (Cognito SSO)", CHATBOT_URL, cb_text, cb_badge)
    # XYZ-PILOT-063 resolution: surface the Operator Dashboard alongside the
    # other ServiceURIs so users see they can self-serve Start/Stop here.
    row("<PILOT_NAME> Operator Dashboard (Cognito SSO)",
        OPERATOR_DASHBOARD, "RUNNING", "ok")

    # Ad-hoc START block — XYZ-PILOT-063 mitigation: the legacy in-page Start
    # button posted to a public Lambda Function URL that AWS's account-level
    # BPA enforcement (May 2026) now blocks with HTTP 403. Replaced with a
    # prominent link to the Cognito-gated Operator Dashboard so users can
    # self-serve a start even when the chatbot is offline (cold-start path).
    start_button = ""
    if not is_running:
        start_button = f"""
<div class="action">
  <p style="margin:0 0 .6em 0"><b>The <PILOT_NAME> environment is currently STOPPED.</b></p>
  <p style="margin:0 0 .9em 0">
    To start it now without waiting for the next 12:00 CEST cron, sign in to the
    Cognito-gated Operator Dashboard and click <b>▶ Start</b>:
  </p>
  <p style="margin:0">
    <a class="op-link" href="{html.escape(OPERATOR_DASHBOARD)}" target="_blank" rel="noopener">
      ▶ Open <PILOT_NAME> Operator Dashboard →
    </a>
  </p>
  <p style="margin:.7em 0 0;color:#555;font-size:.88em">
    Use the same Cognito subscriber credentials as for the chatbot.
    The button takes you off this page; come back here in ~2 minutes to see the
    updated tunnel URLs.
  </p>
</div>"""

    html_page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title><PILOT_NAME> · Status · Exasol environment on AWS</title>
<link rel="icon"             href="{FAVICON_32}"  sizes="32x32"  type="image/png">
<link rel="icon"             href="{FAVICON_192}" sizes="192x192" type="image/png">
<link rel="apple-touch-icon" href="{FAVICON_192}">
<style>
 body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 900px; margin: 2em auto; color:#222; }}
 h1 {{ font-size: 1.6em; margin-bottom: .2em; }}
 .meta {{ color: #888; margin-bottom: 1.5em; }}
 table {{ width: 100%; border-collapse: collapse; }}
 th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; }}
 .badge {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: .85em; color:#fff; }}
 .badge.ok  {{ background: #1AAB40; }}
 .badge.off {{ background: #888; }}
 .footer {{ margin-top: 2em; color: #888; font-size: .85em; }}
 a {{ color: #118DFF; text-decoration: none; }}
 a:hover {{ text-decoration: underline; }}
 .action {{ margin: 1.5em 0; padding: 1em 1.2em; background: #FFF6E5; border: 1px solid #F0C36D; border-radius: 6px; }}
 .action .op-link {{ display: inline-block; padding: 10px 18px; background: #1AAB40; color: #fff; border-radius: 4px; font-weight: 600; }}
 .action .op-link:hover {{ background: #168D36; text-decoration: none; }}
</style>
</head><body>
<h1><PILOT_NAME> — Exasol environment on AWS</h1>
<div class="meta">
  Status: <span class='badge {badge}'>{state_text}</span> ·
  Last update (UTC): {now_utc.strftime('%Y-%m-%d %H:%M:%S')} ·
  Last update (CEST): {now_cest.strftime('%Y-%m-%d %H:%M:%S')} ·
  EC2: {EC2_INSTANCE_ID}
</div>
{start_button}
<table>
<thead><tr><th>Service</th><th>ServiceURI</th><th>Availability</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
<div class="footer">
  Auto-published by the <PILOT_NAME> scheduler Lambda after every start/stop.
  Schedule: start 12:00 CEST Mon–Fri · stop 24:00 CEST every day.
  Confidential: © Exasol AG · ESL © <SEMANTIC_LAYER_VENDOR> · XYZ evaluation use only.
  Repository: <a href="https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>"><GITHUB_OWNER>/<PROJECT_SLUG></a>
</div>
</body></html>"""

    s3.put_object(
        Bucket=STATUS_BUCKET,
        Key="index.html",
        Body=html_page.encode("utf-8"),
        ContentType="text/html; charset=utf-8",
        CacheControl="no-cache, max-age=10",
    )
    return OPERATOR_DASHBOARD


def _format_warmup_for_email(warmup: dict[str, Any] | None) -> str:
    if not warmup:
        return "not checked"
    status = warmup.get("status", "unknown")
    # XYZ-PILOT-284: the prompt START email is sent while warm-up is still
    # running, so report that honestly rather than as a pass/fail.
    if status == "in_progress":
        return ("in progress — warm-up runs in the background; a follow-up "
                "email arrives only if it does not complete cleanly")
    duration = warmup.get("duration_s")
    duration_txt = f", {duration}s" if duration is not None else ""
    if warmup.get("ok"):
        return f"OK ({status}{duration_txt})"
    note = str(warmup.get("note", "")).replace("\n", " ")[:180]
    return f"WARNING ({status}{duration_txt}) {note}"


def send_email(state: str, tunnels: dict[str, str] | None, dashboard_url: str,
               ad_hoc: bool = False, initiator: str | None = None,
               warmup: dict[str, Any] | None = None,
               test_mode: bool = False,
               warmup_followup: bool = False) -> None:
    now_utc = dt.datetime.utcnow()
    now_cest = now_utc + dt.timedelta(hours=2)
    # XYZ-PILOT-077 + XYZ-PILOT-080: ad-hoc subject prefix + action word adapt
    # to the actual state (start = "started", stop = "stopped").
    # XYZ-PILOT-173: keep the internal test_mode flag for compatibility. Do not
    # add recipient-scope labels to the subject; routing is documented in the
    # body/logs and the normal subject stays stable for mail filters.
    ops_prefix = ""
    # XYZ-PILOT-284: a warm-up follow-up reuses this body (env is up, ServiceURIs
    # valid) but is flagged in the subject so it reads as a status follow-up,
    # not a duplicate START. Sent only when the post-start warm-up did not pass.
    warmup_tag = "[WARM-UP WARNING] " if warmup_followup else ""
    if ad_hoc:
        if state == "running":
            label, action_word = "AD-HOC START", "started"
        elif state == "stopped":
            label, action_word = "AD-HOC STOP", "stopped"
        else:
            label, action_word = "AD-HOC", "transitioned"
        subject = (now_cest.strftime("%Y.%m.%d.%H.%M")
                   + f" · {ops_prefix}{warmup_tag}[{label}] <PILOT_NAME> environment {action_word} by "
                   + (initiator or "unknown caller"))
    else:
        subject = (
            now_cest.strftime("%Y.%m.%d.%H.%M")
            + f" : {ops_prefix}{warmup_tag}Status <PILOT_NAME> Exasol environment on AWS"
        )
    streamlit_uri = (tunnels or {}).get("streamlit") or "(not running)"
    grafana_uri   = (tunnels or {}).get("grafana")   or "(not running)"

    # XYZ-PILOT-077 + XYZ-PILOT-080: banner header prepended to the body for
    # ad-hoc starts AND stops. Names WHO clicked the button + WHEN +
    # explains that 12:00 CEST Mon–Fri (for start) / 24:00 CEST daily (for
    # stop) is the usual cron window.
    ad_hoc_header = ""
    if ad_hoc:
        weekday = now_cest.strftime("%A")
        if state == "running":
            verb = "START"
            usual = "scheduled cron starts the env at 12:00 CEST Mon–Fri only"
        elif state == "stopped":
            verb = "STOP"
            usual = "scheduled cron stops the env at 24:00 CEST every day"
        else:
            verb = "ACTION"
            usual = "scheduled cron is the usual driver of state changes"
        ad_hoc_header = f"""
⚠ AD-HOC {verb} — outside the usual schedule.

  Initiator:    {initiator or '(unknown — bearer token lookup failed)'}
  Initiated at: {now_cest.strftime('%Y-%m-%d %H:%M:%S')} CEST  ({weekday})
                {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC
  Path:         Cognito-authenticated button (Operator Dashboard or
                chatbot admin sidebar) → boto3 lambda:Invoke

  Usual schedule: {usual}. Any {verb.lower()} outside that window arrives
  here. If this was unexpected, open the Operator Dashboard to inspect or
  reverse the change: {OPERATOR_DASHBOARD}

────────────────────────────────────────────────────────────────────

"""


    # Ad-hoc START / STOP block — points at the Cognito-gated Operator
    # Dashboard (XYZ-PILOT-063 resolution). The legacy unauthenticated
    # `curl -X POST '?token=…'` snippet was retired when AWS expanded
    # Block-Public-Access for Lambda Function URLs (May 2026); the new
    # path is Cognito SSO → Identity Pool temp creds → boto3 Lambda invoke.
    start_block = ""
    if state == "stopped":
        start_block = f"""
Ad-hoc START — restart the Pilot Environment now (XYZ-PILOT-063)
  The environment is currently STOPPED. To bring it up without
  waiting for the next 12:00 CEST cron:

    Open {OPERATOR_DASHBOARD}
    in your browser, sign in with your Pilot Cognito credentials,
    and click the green "▶ Start" button.

  End-to-end takes ~2 minutes. The button is disabled if the
  instance is already starting. A fresh status notification email
  will follow once the start completes with the new Streamlit +
  Grafana tunnel URLs.
"""
    elif state == "running":
        start_block = f"""
Ad-hoc STOP — bring the Pilot Environment down now
  Open {OPERATOR_DASHBOARD}
  in your browser, sign in with your Pilot Cognito credentials,
  and click the red "⏹ Stop" button. The next scheduled stop fires
  at 24:00 CEST.
"""

    ops_only_footer = ""
    if test_mode:
        ops_only_footer = f"""
Notification routing
  This real operational notification was restricted to the configured
  OPS_ONLY + XYZ_OPS recipient groups, excluding XYZ_BIZ. It is not a dry-run,
  test environment, or test-only status; the state and ServiceURIs above
  describe the live <PILOT_NAME> UAT environment.
"""

    # XYZ-PILOT-284: prepend a short note on warm-up follow-ups so the reader
    # understands this is a status follow-up to a START they already received,
    # not a second start.
    followup_note = ""
    if warmup_followup:
        followup_note = (
            "Follow-up to the START notification you just received: the env is "
            "running and the ServiceURIs below are valid, but the post-start "
            "Exasol warm-up did not complete cleanly (see Warm-up). The "
            "dashboards are usable; the first few queries may pay cold-cache "
            "latency until warm-up succeeds on a subsequent run.\n"
            "────────────────────────────────────────────────────────────────────\n"
        )

    body = f"""<PILOT_NAME> Exasol environment on AWS — status notification
{followup_note}{ad_hoc_header}
  State:        {state.upper()}
  Warm-up:      {_format_warmup_for_email(warmup)}
  Time (UTC):   {now_utc.strftime('%Y-%m-%d %H:%M:%S')}
  Time (CEST):  {now_cest.strftime('%Y-%m-%d %H:%M:%S')}
  EC2:          {EC2_INSTANCE_ID}

Status dashboard (live):
  {dashboard_url}

Current ServiceURIs

  Exasol DB:           {EXASOL_DNS}:8563
  Exasol Admin UI:     https://{EXASOL_DNS}:8443
  SSH:                 ssh ubuntu@{EXASOL_DNS}
  Streamlit QBR:       {streamlit_uri}
  Grafana:             {grafana_uri}
  Exasol MCP:          {EXA_MCP_URL}
                       Auth: AWS Cognito hosted UI ({COGNITO_HOSTED_UI})
  <PILOT_NAME> Chat:      {CHATBOT_URL}
                       Auth: AWS Cognito SSO (browser)
  Operator Dashboard:  {OPERATOR_DASHBOARD}
                       Auth: AWS Cognito SSO (PKCE) · Start/Stop buttons + live EC2 state
{start_block}
Schedule
  Start: 12:00 CEST Mon-Fri (EventBridge cron 0 10 ? * MON-FRI *)
  Stop:  24:00 CEST every day (EventBridge cron 0 22 * * ? *)
  Ad-hoc start: dashboard button / curl, available any time the env is stopped.

Cost discipline
  Stopped instance + attached Elastic IP: ~EUR 0.30 per weekend.
{ops_only_footer}

Confidentiality
  {CONFIDENTIAL_EMAIL_FOOTER}

— Sent automatically by the <PILOT_NAME> scheduler Lambda.
"""
    # XYZ-PILOT-162 follow-up: discover the broader candidate recipient list
    # from Cognito + SES_TO union BEFORE the verification check, so any
    # Cognito-onboarded subscriber that has SES verification = Success
    # automatically receives the START/STOP email without an operator
    # patching the env var.
    #
    # XYZ-PILOT-173: when test_mode=True, restrict to OPS_ONLY + XYZ_OPS
    # instead of the full subscriber union. Lets operators validate ad-hoc
    # flows without notifying the full UAT subscriber group.
    if warmup_followup:
        candidate_recipients = _warmup_warning_recipients()
        log.warning(
            "warm-up warning recipient set restricted to %s=%s "
            "(normal START/STOP union would have been: %s)",
            WARMUP_WARNING_GROUP,
            candidate_recipients,
            _all_candidate_recipients(),
        )
    elif test_mode:
        candidate_recipients = _restricted_notification_recipients()
        log.warning("[OPS-ONLY MODE] XYZ-PILOT-173 — recipient set restricted to groups %s=%s "
                    "(full Cognito union would have been: %s)",
                    ",".join(RESTRICTED_NOTIFY_INCLUDE_GROUPS),
                    candidate_recipients,
                    _all_candidate_recipients())
    else:
        candidate_recipients = _all_candidate_recipients()
    # SES sandbox mode: drop recipients that aren't verified yet so the whole
    # send doesn't get rejected for one bad address. Logs a warning per skip.
    verified = set()
    try:
        attrs = ses.get_identity_verification_attributes(Identities=candidate_recipients + [SES_FROM])
        for addr, info in attrs.get("VerificationAttributes", {}).items():
            if info.get("VerificationStatus") == "Success":
                verified.add(addr)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not check SES verification: %s", exc)
        verified = set(candidate_recipients + [SES_FROM])  # fall through and try anyway

    if SES_FROM not in verified:
        log.error("sender %s not verified, abort send", SES_FROM)
        return
    deliverable = [a for a in candidate_recipients if a in verified]
    skipped = [a for a in candidate_recipients if a not in verified]
    if skipped:
        log.warning("skipping unverified recipients in sandbox: %s", skipped)

    ses.send_email(
        Source=SES_FROM,
        Destination={"ToAddresses": deliverable},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
        },
    )
    log.info("email sent to %s · subject: %s", deliverable, subject)
