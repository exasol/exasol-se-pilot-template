"""<PILOT_NAME> onboarding-confirmation watchdog.

XYZ-PILOT-209 expands the earlier SNS-only watchdog into a daily business-day
onboarding watchdog. It checks Cognito first-login, SES verification, and the
major/critical-resolution SNS topic at 13:00 CEST on operational weekdays.

Fresh AWS confirmation messages are reissued only when the previous request is
missing or its TTL has expired, unless an operator invokes the Lambda with
``{"force": true}``. Durable state is stored in S3 so the Operator Dashboard can
show reminder timestamps, TTL expiry, likely unsubscribe signals, and explicit
deactivation/opt-out trails.

EventBridge schedule:
  cron(0 11 ? * MON-FRI *)  -- 13:00 CEST during the Pilot DEV window
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import os
from typing import Any

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "<AWS_REGION>")
ACCOUNT_ID = os.environ.get("XYZ_AWS_ACCOUNT_ID", "<AWS_ACCOUNT_ID>")
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
REJECTION_TOPIC_ARN = os.environ.get("REJECTION_TOPIC_ARN", SNS_TOPIC_ARN)
SES_FROM = os.environ.get("SES_FROM", "<OPERATOR_EMAIL>")
OPERATOR_NOTIFY_TO = "<OPERATOR_EMAIL>,<OPS_USER_2_EMAIL>,<OPS_USER_3_EMAIL>"
WATCHDOG_CC = [e.strip().lower() for e in os.environ.get(
    "WATCHDOG_CC", OPERATOR_NOTIFY_TO
).split(",") if e.strip()]
REJECTION_NOTIFY_TO = [e.strip().lower() for e in os.environ.get(
    "REJECTION_NOTIFY_TO",
    OPERATOR_NOTIFY_TO,
).split(",") if e.strip()]
EXPECTED_SNS_EMAILS = [e.strip().lower() for e in os.environ.get(
    "EXPECTED_SNS_EMAILS",
    "<OPERATOR_EMAIL>,<OPS_USER_2_EMAIL>,<OPS_USER_3_EMAIL>",
).split(",") if e.strip()]
USER_POOL_ID = os.environ.get("USER_POOL_ID", "<COGNITO_USER_POOL_ID>")
STATE_BUCKET = os.environ.get("STATE_BUCKET", f"xyz-pilot-status-{ACCOUNT_ID}")
STATE_KEY = os.environ.get("STATE_KEY", "sns-watchdog/state.json")

SNS_TTL_HOURS = int(os.environ.get("SNS_TTL_HOURS", "72"))
SES_VERIFICATION_TTL_HOURS = int(os.environ.get("SES_VERIFICATION_TTL_HOURS", "24"))
COGNITO_TEMP_PASSWORD_TTL_DAYS = int(os.environ.get("COGNITO_TEMP_PASSWORD_TTL_DAYS", "7"))
OP_WINDOW = "12:00 CEST until 24:00 CEST on <PILOT_NAME> operational business days"

sns = boto3.client("sns", region_name=REGION)
ses = boto3.client("ses", region_name=REGION)
cognito = boto3.client("cognito-idp", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)

ACTION_LABELS = {
    "cognito": "Cognito first login / temporary-password invite",
    "ses": "SES email identity verification",
    "sns": "AWS SNS major/critical-resolution notification subscription",
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_utc(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _same_utc_date(value: str | None, now: dt.datetime) -> bool:
    parsed = _parse_iso(value)
    return bool(parsed and parsed.astimezone(dt.timezone.utc).date() == now.date())


def _first_name(email: str) -> str:
    local = email.split("@", 1)[0]
    token = local.replace("_", ".").replace("-", ".").split(".", 1)[0]
    return token[:1].upper() + token[1:] if token else "there"


def _state_template() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "state_source": "xyz-pilot-sns-confirmation-watchdog",
        "updated_at_utc": None,
        "users": {},
    }


def _load_state() -> dict[str, Any]:
    try:
        obj = s3.get_object(Bucket=STATE_BUCKET, Key=STATE_KEY)
        state = json.loads(obj["Body"].read().decode("utf-8"))
        if isinstance(state, dict) and isinstance(state.get("users"), dict):
            return state
    except s3.exceptions.NoSuchKey:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("state load failed: %s", type(exc).__name__)
    return _state_template()


def _save_state(state: dict[str, Any]) -> None:
    state["schema_version"] = 2
    state["state_source"] = "xyz-pilot-sns-confirmation-watchdog"
    state["updated_at_utc"] = _iso(_now())
    s3.put_object(
        Bucket=STATE_BUCKET,
        Key=STATE_KEY,
        Body=json.dumps(state, indent=2, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )


def _user_state(state: dict[str, Any], email: str) -> dict[str, Any]:
    users = state.setdefault("users", {})
    return users.setdefault(email.lower(), {"email": email.lower()})


def _parse_deactivated_users() -> dict[str, dict[str, Any]]:
    """Return operator-maintained explicit opt-outs/deactivations.

    Preferred env format:

      DEACTIVATED_USERS_JSON='{
        "user@example.com": {
          "reason": "Declined <PILOT_NAME> tester participation by email",
          "source": "operator",
          "recorded_at_utc": "2026-06-03T10:00:00Z",
          "denied": ["cognito", "ses", "sns"]
        }
      }'

    ``DEACTIVATED_EMAILS`` remains as a low-detail CSV fallback.
    """
    out: dict[str, dict[str, Any]] = {}
    raw = os.environ.get("DEACTIVATED_USERS_JSON", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for email, meta in parsed.items():
                    if not isinstance(email, str) or "@" not in email:
                        continue
                    if not isinstance(meta, dict):
                        meta = {"reason": str(meta)}
                    denied = meta.get("denied") or ["cognito", "ses", "sns"]
                    e = email.strip().lower()
                    out[e] = {
                        "email": e,
                        "status": "deactivated",
                        "source": meta.get("source", "operator"),
                        "reason": meta.get("reason", "User declined <PILOT_NAME> tester participation"),
                        "recorded_at_utc": meta.get("recorded_at_utc") or _iso(_now()),
                        "denied": [str(x) for x in denied],
                    }
        except json.JSONDecodeError as exc:
            log.warning("DEACTIVATED_USERS_JSON ignored: %s", exc)
    for email in [e.strip().lower() for e in os.environ.get("DEACTIVATED_EMAILS", "").split(",") if e.strip()]:
        out.setdefault(email, {
            "email": email,
            "status": "deactivated",
            "source": "operator",
            "reason": "User declined <PILOT_NAME> tester participation",
            "recorded_at_utc": _iso(_now()),
            "denied": ["cognito", "ses", "sns"],
        })
    return out


def _list_cognito_users() -> dict[str, dict[str, Any]]:
    users: dict[str, dict[str, Any]] = {}
    token = None
    while True:
        kwargs: dict[str, Any] = {"UserPoolId": USER_POOL_ID, "Limit": 60}
        if token:
            kwargs["PaginationToken"] = token
        resp = cognito.list_users(**kwargs)
        for user in resp.get("Users", []):
            email = None
            for attr in user.get("Attributes", []):
                if attr.get("Name") == "email":
                    email = attr.get("Value", "").strip().lower()
                    break
            if email:
                users[email] = user
        token = resp.get("PaginationToken")
        if not token:
            break
    return users


def _ses_statuses(emails: list[str]) -> dict[str, str]:
    if not emails:
        return {}
    resp = ses.get_identity_verification_attributes(Identities=sorted(set(emails)))
    attrs = resp.get("VerificationAttributes", {})
    return {
        email: attrs.get(email, {}).get("VerificationStatus", "NotVerified")
        for email in emails
    }


def _sns_statuses() -> dict[str, dict[str, Any]]:
    status: dict[str, dict[str, Any]] = {}
    token = None
    while True:
        kwargs: dict[str, Any] = {"TopicArn": SNS_TOPIC_ARN}
        if token:
            kwargs["NextToken"] = token
        resp = sns.list_subscriptions_by_topic(**kwargs)
        for sub in resp.get("Subscriptions", []):
            if sub.get("Protocol") != "email":
                continue
            email = sub.get("Endpoint", "").strip().lower()
            if not email:
                continue
            sub_status = "pending" if sub.get("SubscriptionArn") == "PendingConfirmation" else "confirmed"
            rec = status.setdefault(email, {
                "status": sub_status,
                "subscription_arns": [],
                "pending_count": 0,
                "confirmed_count": 0,
            })
            rec["subscription_arns"].append(sub.get("SubscriptionArn", ""))
            rec["pending_count" if sub_status == "pending" else "confirmed_count"] += 1
            if sub_status == "confirmed":
                rec["status"] = "confirmed"
            elif rec["status"] != "confirmed":
                rec["status"] = "pending"
        token = resp.get("NextToken")
        if not token:
            break
    return status


def _ttl_for(action: str, now: dt.datetime) -> dt.datetime:
    if action == "cognito":
        return now + dt.timedelta(days=COGNITO_TEMP_PASSWORD_TTL_DAYS)
    if action == "ses":
        return now + dt.timedelta(hours=SES_VERIFICATION_TTL_HOURS)
    return now + dt.timedelta(hours=SNS_TTL_HOURS)


def _should_reissue(user_state: dict[str, Any], action: str, now: dt.datetime, force: bool) -> bool:
    if force:
        return True
    expires = _parse_iso((user_state.get(action) or {}).get("expires_at_utc"))
    return expires is None or expires <= now


def _record_action(
    user_state: dict[str, Any],
    action: str,
    status: str,
    now: dt.datetime,
    *,
    expires_at: dt.datetime | None = None,
    detail: str | None = None,
    message_id: str | None = None,
    arn: str | None = None,
) -> None:
    rec = user_state.setdefault(action, {})
    rec.update({"status": status, "last_checked_at_utc": _iso(now)})
    if status in {"pending", "reissued"}:
        rec["last_reissued_at_utc"] = _iso(now)
        rec["last_sent_at_utc"] = _iso(now)
        rec["sent_count"] = int(rec.get("sent_count") or 0) + 1
    if expires_at:
        rec["expires_at_utc"] = _iso(expires_at)
    if detail:
        rec["detail"] = detail
    if message_id:
        rec["ses_message_id"] = message_id
    if arn:
        rec["subscription_arn"] = arn


# XYZ-PILOT-264 — durable per-user throttle: send at most one watchdog awareness/
# reminder email per user per AWARENESS_MIN_INTERVAL_DAYS (default 7), regardless
# of which EventBridge rule / mode / account triggers the fire. This is the
# code-level guarantee behind the operator's "max one reminder per user on
# Monday" directive; it survives a rule being re-enabled or a second environment
# ever running again. `force` (event force=True) bypasses it for manual operator
# runs. The unified stamp is user_state["last_awareness_email_utc"].
AWARENESS_MIN_INTERVAL_DAYS = int(os.environ.get("AWARENESS_MIN_INTERVAL_DAYS", "7"))


def _awareness_recently_sent(user_state: dict[str, Any], now: dt.datetime, force: bool) -> bool:
    if force:
        return False
    last = _parse_iso(user_state.get("last_awareness_email_utc"))
    return last is not None and (now - last) < dt.timedelta(days=AWARENESS_MIN_INTERVAL_DAYS)


def _stamp_awareness_sent(user_state: dict[str, Any], now: dt.datetime) -> None:
    user_state["last_awareness_email_utc"] = _iso(now)


def _send_awareness(email: str, actions: list[dict[str, Any]], now: dt.datetime) -> str:
    name = html.escape(_first_name(email))
    earliest_expiry = min(_parse_iso(a.get("expires_at_utc")) or now for a in actions)
    items = []
    for action in actions:
        label = ACTION_LABELS.get(action["action"], action["action"])
        expiry = _parse_iso(action.get("expires_at_utc"))
        expiry_txt = _fmt_utc(expiry) if expiry else "see AWS email"
        items.append(
            "<li>"
            f"<b>{html.escape(label)}</b><br>"
            "Please find the related AWS email and complete the requested action. "
            f"Current TTL expiry: <b>{html.escape(expiry_txt)}</b>."
            "</li>"
        )
    subject = (
        "[<PILOT_NAME>] Action needed: complete Pilot access confirmations "
        f"(next TTL expires {_fmt_utc(earliest_expiry)})"
    )
    body = (
        "<div style='font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
        "color:#1f2937;max-width:760px;'>"
        "<h2 style='margin:0 0 12px 0;color:#003366;'>"
        "Action needed: complete your <PILOT_NAME> access confirmations</h2>"
        f"<p>Hi {name},</p>"
        "<p>Thank you for taking the time to test the deployed <PILOT_NAME> services, "
        "including the <b><PILOT_NAME> Chatbot with Exasol MCP Server</b> and the "
        "<b><PILOT_NAME> QBR Dashboard</b>. We appreciate the feedback from the tester "
        "team because it helps harden the POC before broader UAT.</p>"
        "<p>The Pilot still shows one or more outstanding AWS confirmation actions "
        "for your account. Fresh AWS confirmation messages have been issued for the "
        "items below where the prior request was missing or expired:</p>"
        f"<ul>{''.join(items)}</ul>"
        "<div style='background:#EFF6FF;border-left:4px solid #2563EB;"
        "padding:12px 16px;border-radius:4px;margin:16px 0;'>"
        "<b><PILOT_NAME> E2E operating window</b><br>"
        f"The current DEV E2E environment is normally operated from <b>{OP_WINDOW}</b>. "
        "Outside that window the EC2-backed services may be stopped by schedule, "
        "although an operator can still perform ad-hoc starts when needed.</div>"
        "<div style='background:#FEF3C7;border-left:4px solid #F59E0B;"
        "padding:12px 16px;border-radius:4px;margin:16px 0;'>"
        "<b>What to look for in your inbox</b><br>"
        "Cognito temporary-password and SES verification messages are sent by AWS. "
        "SNS subscription confirmation messages come from "
        "<code>no-reply@sns.amazonaws.com</code> with a subject similar to "
        "<b>AWS Notification - Subscription Confirmation</b>. Please also check "
        "your spam or quarantine folders.</div>"
        "<p><b>If you do not want to participate in the <PILOT_NAME> tester team</b>, "
        "reply to this email and state that clearly. The operator will deactivate "
        "your Pilot account and persist the reason in the Operator Dashboard "
        "individual status trail so no further reminder loop keeps chasing you.</p>"
        "<p style='margin-top:18px;color:#6B7280;font-size:.9em;'>"
        f"-- <PILOT_NAME> operations · {_fmt_utc(now)} · "
        "<code>xyz-pilot-sns-confirmation-watchdog</code> · "
        f"CC: {html.escape(', '.join(WATCHDOG_CC))}</p></div>"
    )
    resp = ses.send_email(
        Source=SES_FROM,
        Destination={"ToAddresses": [email], "CcAddresses": list(WATCHDOG_CC)},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Html": {"Data": body, "Charset": "UTF-8"}},
        },
    )
    return resp.get("MessageId", "")


def _send_rejection_notification(email: str, deactivation: dict[str, Any], now: dt.datetime) -> dict[str, str]:
    reason = deactivation.get("reason", "User declined <PILOT_NAME> tester participation")
    denied = ", ".join(deactivation.get("denied") or ["cognito", "ses", "sns"])
    subject = f"<PILOT_NAME> subscriber opt-out recorded: {email}"
    text = (
        f"<PILOT_NAME> subscriber opt-out/deactivation recorded\n\n"
        f"User: {email}\n"
        f"Reason: {reason}\n"
        f"Source: {deactivation.get('source', 'operator')}\n"
        f"Recorded at: {deactivation.get('recorded_at_utc', _iso(now))}\n"
        f"Denied / deactivated scopes: {denied}\n\n"
        "This detail is persisted in the <PILOT_NAME> onboarding watchdog state and "
        "is visible in the Operator Dashboard New User Onboarding Facility "
        "individual status trail."
    )
    sns_msg_id = ""
    if REJECTION_TOPIC_ARN:
        sns_resp = sns.publish(TopicArn=REJECTION_TOPIC_ARN, Subject=subject[:100], Message=text)
        sns_msg_id = sns_resp.get("MessageId", "")
    ses_resp = ses.send_email(
        Source=SES_FROM,
        Destination={"ToAddresses": REJECTION_NOTIFY_TO},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": text, "Charset": "UTF-8"}},
        },
    )
    return {"sns_message_id": sns_msg_id, "ses_message_id": ses_resp.get("MessageId", "")}


def _send_weekly_pending_reminder(email: str, actions: list[dict[str, Any]], now: dt.datetime) -> str:
    name = html.escape(_first_name(email))
    items = []
    for action in actions:
        label = ACTION_LABELS.get(action["action"], action["action"])
        expiry = _parse_iso(action.get("expires_at_utc"))
        expiry_txt = _fmt_utc(expiry) if expiry else "not issued yet / see latest AWS email"
        items.append(
            "<li>"
            f"<b>{html.escape(label)}</b><br>"
            f"Current status: {html.escape(action.get('status', 'pending'))}. "
            f"Latest known TTL expiry: <b>{html.escape(expiry_txt)}</b>. "
            f"{html.escape(action.get('detail', 'Please complete the matching AWS confirmation action.'))}"
            "</li>"
        )
    subject = "[<PILOT_NAME>] Weekly reminder: complete pending Pilot access confirmations"
    body = (
        "<div style='font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
        "color:#1f2937;max-width:760px;'>"
        "<h2 style='margin:0 0 12px 0;color:#003366;'>"
        "Weekly reminder: pending <PILOT_NAME> access confirmations</h2>"
        f"<p>Hi {name},</p>"
        "<p>This is the weekly Monday 15:00 CEST <PILOT_NAME> onboarding reminder. "
        "Your account still has one or more pending access-confirmation actions.</p>"
        "<p><b>Why this matters:</b> completing these AWS confirmation actions lets "
        "you fully test the deployed <PILOT_NAME> services, including the "
        "<b><PILOT_NAME> Chatbot with Exasol MCP Server</b> and the "
        "<b><PILOT_NAME> QBR Dashboard</b>. We appreciate your time and feedback as "
        "part of the tester team.</p>"
        f"<ul>{''.join(items)}</ul>"
        "<div style='background:#EFF6FF;border-left:4px solid #2563EB;"
        "padding:12px 16px;border-radius:4px;margin:16px 0;'>"
        "<b><PILOT_NAME> E2E operating window</b><br>"
        f"The current DEV E2E environment is normally operated from <b>{OP_WINDOW}</b>. "
        "The best time to test chatbot/QBR flows is inside that window.</div>"
        "<div style='background:#FEF3C7;border-left:4px solid #F59E0B;"
        "padding:12px 16px;border-radius:4px;margin:16px 0;'>"
        "<b>How to complete the actions</b><br>"
        "Search your mailbox and spam/quarantine folders for recent AWS messages. "
        "SNS subscription confirmations are sent by <code>no-reply@sns.amazonaws.com</code> "
        "with a subject similar to <b>AWS Notification - Subscription Confirmation</b>. "
        "Cognito temporary-password and SES verification messages are also AWS-generated. "
        "Click the confirmation or first-login link before its TTL expires. Earlier "
        "<PILOT_NAME> onboarding attempts for <OPS_USER_2> and Madeleine showed that these "
        "AWS-generated confirmations can end up in junk mail or corporate quarantine, "
        "so please check those folders explicitly.</div>"
        "<p>The 13:00 CEST weekday watchdog separately reissues expired AWS confirmation "
        "requests when the prior TTL has elapsed. This 15:00 CEST Monday message is an "
        "explanatory heads-up only; it does not create extra AWS confirmation requests.</p>"
        "<p><b>If you do not want to participate in the <PILOT_NAME> tester team</b>, "
        "reply to this email and state that clearly. The operator will deactivate your "
        "Pilot account and record the reason in the Operator Dashboard status trail.</p>"
        "<p style='margin-top:18px;color:#6B7280;font-size:.9em;'>"
        f"-- <PILOT_NAME> operations · {_fmt_utc(now)} · "
        "weekly pending-user reminder · "
        f"CC: {html.escape(', '.join(WATCHDOG_CC))}</p></div>"
    )
    resp = ses.send_email(
        Source=SES_FROM,
        Destination={"ToAddresses": [email], "CcAddresses": list(WATCHDOG_CC)},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Html": {"Data": body, "Charset": "UTF-8"}},
        },
    )
    return resp.get("MessageId", "")


def _disable_cognito_user(email: str) -> str:
    cognito.admin_disable_user(UserPoolId=USER_POOL_ID, Username=email)
    return "admin_disable_user"


def lambda_handler(event: dict, context: Any) -> dict:
    event = event or {}
    dry_run = bool(event.get("dry_run", False))
    force = bool(event.get("force", False))
    force_rejection_notice = bool(event.get("force_rejection_notice", False))
    mode = str(event.get("mode") or event.get("action") or "ttl_reissue")
    weekly_pending_reminder = mode == "weekly_pending_reminder"
    now = _now()

    log.info("onboarding watchdog fire · mode=%s dry_run=%s force=%s state=s3://%s/%s",
             mode, dry_run, force, STATE_BUCKET, STATE_KEY)

    state = _load_state()
    deactivated_cfg = _parse_deactivated_users()
    cognito_users = _list_cognito_users()
    all_email_set = set(cognito_users) | set(EXPECTED_SNS_EMAILS) | set(deactivated_cfg)
    ses_status = _ses_statuses(sorted(all_email_set))
    sns_status = _sns_statuses()

    results: dict[str, Any] = {
        "action": "checked",
        "dry_run": dry_run,
        "force": force,
        "mode": mode,
        "checked_at_utc": _iso(now),
        "state": f"s3://{STATE_BUCKET}/{STATE_KEY}",
        "schedule": "cron(0 11 ? * MON-FRI *)",
        "operating_window": OP_WINDOW,
        "reissued": [],
        "weekly_reminders": [],
        "waiting": [],
        "confirmed": [],
        "deactivated": [],
        "rejection_notifications": [],
        "errors": [],
    }
    state_changed = False

    for email in sorted(all_email_set):
        user_state = _user_state(state, email)
        user_state["email"] = email
        user_state["last_checked_at_utc"] = _iso(now)

        user = cognito_users.get(email)
        c_status = user.get("UserStatus") if user else "not-in-cognito"
        c_enabled = bool(user.get("Enabled", True)) if user else False
        s_status = ses_status.get(email, "NotVerified")
        sns_rec = sns_status.get(email, {"status": "missing"})
        current_sns_status = sns_rec.get("status", "missing")

        deactivation = deactivated_cfg.get(email)
        if deactivation or (user and not c_enabled):
            if not deactivation:
                deactivation = {
                    "email": email,
                    "status": "deactivated",
                    "source": "cognito",
                    "reason": "Cognito user is disabled",
                    "recorded_at_utc": _iso(now),
                    "denied": [],
                }
            user_state["deactivation"] = deactivation
            for action in ("cognito", "ses", "sns"):
                _record_action(user_state, action, "deactivated", now, detail=deactivation.get("reason"))
            if user and c_enabled:
                if dry_run:
                    disable_step = "would-disable-cognito"
                else:
                    try:
                        disable_step = _disable_cognito_user(email)
                    except Exception as exc:  # noqa: BLE001
                        disable_step = f"disable-failed:{type(exc).__name__}"
                        results["errors"].append({"email": email, "step": "admin_disable_user", "error": str(exc)[:180]})
            else:
                disable_step = "already-disabled-or-not-in-cognito"

            notice = user_state.setdefault("deactivation_notice", {})
            if force_rejection_notice or not notice.get("sent_at_utc"):
                if dry_run:
                    notice_result = {"sns_message_id": "dry-run", "ses_message_id": "dry-run"}
                else:
                    try:
                        notice_result = _send_rejection_notification(email, deactivation, now)
                    except Exception as exc:  # noqa: BLE001
                        notice_result = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
                        results["errors"].append({"email": email, "step": "rejection_notice", "error": notice_result["error"]})
                notice.update({
                    "sent_at_utc": _iso(now),
                    "recipients": REJECTION_NOTIFY_TO,
                    "sns_topic_arn": REJECTION_TOPIC_ARN,
                    **notice_result,
                })
                results["rejection_notifications"].append({"email": email, **notice_result})
            results["deactivated"].append({
                "email": email,
                "reason": deactivation.get("reason"),
                "source": deactivation.get("source"),
                "denied": deactivation.get("denied", []),
                "recorded_at_utc": deactivation.get("recorded_at_utc"),
                "step": disable_step,
            })
            state_changed = True
            continue

        reissued_for_email: list[dict[str, Any]] = []

        if weekly_pending_reminder:
            pending_for_email: list[dict[str, Any]] = []
            if user and c_enabled and c_status != "CONFIRMED":
                state_rec = user_state.get("cognito") or {}
                pending_for_email.append({
                    "action": "cognito",
                    "status": c_status,
                    "expires_at_utc": state_rec.get("expires_at_utc"),
                    "detail": "Complete the Cognito first-login / temporary-password flow.",
                })
            if user and c_enabled and s_status != "Success":
                state_rec = user_state.get("ses") or {}
                pending_for_email.append({
                    "action": "ses",
                    "status": s_status,
                    "expires_at_utc": state_rec.get("expires_at_utc"),
                    "detail": "Click the SES email identity verification link.",
                })
            if email in EXPECTED_SNS_EMAILS and current_sns_status != "confirmed":
                state_rec = user_state.get("sns") or {}
                pending_for_email.append({
                    "action": "sns",
                    "status": current_sns_status,
                    "expires_at_utc": state_rec.get("expires_at_utc"),
                    "detail": "Click the AWS SNS subscription confirmation link.",
                })

            if not pending_for_email:
                results["confirmed"].append({"email": email})
                continue

            weekly_state = user_state.setdefault("weekly_reminder", {})
            if _awareness_recently_sent(user_state, now, force):
                results["waiting"].append({"email": email, "action": "weekly_reminder", "detail": f"throttled: awareness email sent within {AWARENESS_MIN_INTERVAL_DAYS}d"})
                continue

            if dry_run:
                msg_id = "dry-run"
            else:
                try:
                    msg_id = _send_weekly_pending_reminder(email, pending_for_email, now)
                except Exception as exc:  # noqa: BLE001
                    msg_id = ""
                    results["errors"].append({"email": email, "step": "weekly_pending_reminder", "error": str(exc)[:180]})
            weekly_state.update({
                "last_sent_at_utc": _iso(now),
                "message_id": msg_id,
                "actions": pending_for_email,
                "cc": WATCHDOG_CC,
            })
            if not dry_run and msg_id:
                _stamp_awareness_sent(user_state, now)
            results["weekly_reminders"].append({"email": email, "actions": pending_for_email, "message_id": msg_id})
            state_changed = True
            continue

        if user and c_enabled and c_status != "CONFIRMED":
            if _should_reissue(user_state, "cognito", now, force):
                expires = _ttl_for("cognito", now)
                if dry_run:
                    detail = "would resend Cognito temporary-password invite"
                else:
                    try:
                        cognito.admin_create_user(
                            UserPoolId=USER_POOL_ID,
                            Username=email,
                            MessageAction="RESEND",
                            DesiredDeliveryMediums=["EMAIL"],
                        )
                        detail = "Cognito temporary-password invite resent"
                    except Exception as exc:  # noqa: BLE001
                        detail = f"{type(exc).__name__}: {str(exc)[:140]}"
                        results["errors"].append({"email": email, "step": "cognito_resend", "error": detail})
                _record_action(user_state, "cognito", "pending", now, expires_at=expires, detail=detail)
                reissued_for_email.append({"action": "cognito", "expires_at_utc": _iso(expires), "detail": detail})
                state_changed = True
            else:
                results["waiting"].append({"email": email, "action": "cognito", "expires_at_utc": (user_state.get("cognito") or {}).get("expires_at_utc")})
        elif user:
            _record_action(user_state, "cognito", "confirmed", now, detail=f"Cognito={c_status}")

        if user and c_enabled and s_status != "Success":
            if _should_reissue(user_state, "ses", now, force):
                expires = _ttl_for("ses", now)
                if dry_run:
                    detail = "would resend SES verify-email-identity"
                else:
                    try:
                        ses.verify_email_identity(EmailAddress=email)
                        detail = "SES verification email resent"
                    except Exception as exc:  # noqa: BLE001
                        detail = f"{type(exc).__name__}: {str(exc)[:140]}"
                        results["errors"].append({"email": email, "step": "ses_verify", "error": detail})
                _record_action(user_state, "ses", "pending", now, expires_at=expires, detail=detail)
                reissued_for_email.append({"action": "ses", "expires_at_utc": _iso(expires), "detail": detail})
                state_changed = True
            else:
                results["waiting"].append({"email": email, "action": "ses", "expires_at_utc": (user_state.get("ses") or {}).get("expires_at_utc")})
        elif user:
            _record_action(user_state, "ses", "confirmed", now, detail=f"SES={s_status}")

        if email in EXPECTED_SNS_EMAILS:
            prev_sns = user_state.get("sns") or {}
            if current_sns_status == "missing" and prev_sns.get("status") == "confirmed":
                prev_sns.update({
                    "status": "missing_after_confirmed",
                    "possible_unsubscribe_at_utc": _iso(now),
                    "detail": "SNS subscription missing after prior confirmed state; operator should review whether the user unsubscribed.",
                })
                user_state["sns"] = prev_sns
                state_changed = True

            if current_sns_status != "confirmed":
                if _should_reissue(user_state, "sns", now, force):
                    expires = _ttl_for("sns", now)
                    if dry_run:
                        detail = f"would subscribe to SNS topic ({current_sns_status})"
                        sub_arn = ""
                    else:
                        try:
                            resp = sns.subscribe(
                                TopicArn=SNS_TOPIC_ARN,
                                Protocol="email",
                                Endpoint=email,
                                ReturnSubscriptionArn=True,
                            )
                            sub_arn = resp.get("SubscriptionArn", "")
                            detail = f"SNS confirmation email issued ({current_sns_status})"
                        except Exception as exc:  # noqa: BLE001
                            sub_arn = ""
                            detail = f"{type(exc).__name__}: {str(exc)[:140]}"
                            results["errors"].append({"email": email, "step": "sns_subscribe", "error": detail})
                    _record_action(user_state, "sns", "pending", now, expires_at=expires, detail=detail, arn=sub_arn)
                    reissued_for_email.append({"action": "sns", "expires_at_utc": _iso(expires), "detail": detail})
                    state_changed = True
                else:
                    results["waiting"].append({"email": email, "action": "sns", "expires_at_utc": (user_state.get("sns") or {}).get("expires_at_utc")})
            else:
                _record_action(user_state, "sns", "confirmed", now, detail="SNS subscription confirmed")

        if reissued_for_email:
            if _awareness_recently_sent(user_state, now, force):
                # AWS confirmations were already reissued above (TTL-governed); we
                # throttle only OUR awareness email to >= AWARENESS_MIN_INTERVAL_DAYS.
                results["waiting"].append({"email": email, "action": "awareness_email", "detail": f"throttled: awareness email sent within {AWARENESS_MIN_INTERVAL_DAYS}d (AWS confirmations still reissued)"})
            else:
                if dry_run:
                    msg_id = "dry-run"
                else:
                    try:
                        msg_id = _send_awareness(email, reissued_for_email, now)
                    except Exception as exc:  # noqa: BLE001
                        msg_id = ""
                        results["errors"].append({"email": email, "step": "ses_awareness", "error": str(exc)[:180]})
                user_state["reminder"] = {
                    "last_sent_at_utc": _iso(now),
                    "message_id": msg_id,
                    "actions": reissued_for_email,
                    "cc": WATCHDOG_CC,
                }
                if not dry_run and msg_id:
                    _stamp_awareness_sent(user_state, now)
                results["reissued"].append({"email": email, "actions": reissued_for_email, "awareness_message_id": msg_id})
            state_changed = True
        elif (
            (not user or c_status == "CONFIRMED")
            and (not user or s_status == "Success")
            and (email not in EXPECTED_SNS_EMAILS or current_sns_status == "confirmed")
        ):
            results["confirmed"].append({"email": email})

    if state_changed and not dry_run:
        _save_state(state)
    elif dry_run:
        results["state_write"] = "skipped-dry-run"

    return results
