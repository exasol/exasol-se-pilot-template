"""<PILOT_NAME> — start/stop scheduler Lambda (target account/<AWS_REGION>) with rich OPS-ONLY status email.

Issues the EC2 lifecycle call and (for STOP, or an explicit notify) emails the OPS-ONLY
recipients the full status-notification (matching the established source format,
repointed to <AWS_REGION>). START is silent at boot (NOTIFY_ON_START=false) — the START
status email is sent only after the post-start E2E health check passes, via the
`notify` action ({"action":"notify","state":"STARTED"}).

actions: {"action":"start"|"stop"|"notify","state":<label for notify>}
env: EC2_INSTANCE_ID, OPS_RECIPIENTS (comma-sep), SES_FROM, NOTIFY_ON_START(=false)
"""
import os
import datetime
import boto3

EC2_DNS = "<EXASOL_HOSTNAME>"
STATUS_BUCKET = "xyz-pilot-status-<AWS_ACCOUNT_ID>"
COGNITO_DOMAIN = "https://<COGNITO_DOMAIN_PREFIX>.auth.<AWS_REGION>.amazoncognito.com"
DASHBOARD = os.environ.get("STATUS_DASHBOARD_URL", "https://<PROJECT_SLUG>-dashboard.<PILOT_DOMAIN>")
# Point the "Status dashboard (live)" link directly at the dashboard.html REST URL
# (target account) — bypasses the index.html meta-refresh redirect entirely, so the email flow
# never routes through a redirect page that a browser could have cached pointing at the source account.
STATUS_WEBSITE = DASHBOARD


def _body(state, instance_id):
    now = datetime.datetime.utcnow()
    utc = now.strftime("%Y-%m-%d %H:%M:%S")
    cest = (now + datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    adhoc = ""
    if state == "STOPPED":
        adhoc = (
            "\nAd-hoc START — restart the Pilot Environment now\n"
            "  The environment is currently STOPPED. To bring it up without\n"
            "  waiting for the next 12:00 CEST cron:\n\n"
            "    Open %s\n"
            "    in your browser, sign in with your Pilot Cognito credentials,\n"
            "    and click the green \"▶ Start\" button.\n\n"
            "  End-to-end takes ~2 minutes. A status email follows once the start\n"
            "  completes and all services are verified healthy.\n" % DASHBOARD
        )
    return (
        "<PILOT_NAME> Exasol environment on AWS — status notification\n\n"
        "  State:        %s\n"
        "  Time (UTC):   %s\n"
        "  Time (CEST):  %s\n"
        "  EC2:          %s\n\n"
        "Status dashboard (live):\n  %s\n\n"
        "Current ServiceURIs\n\n"
        "  Exasol DB:           %s:8563\n"
        "  Exasol Admin UI:     https://%s:8443\n\n"
        "  SSH:                 ssh ubuntu@%s\n"
        "  Streamlit QBR:       https://<PROJECT_SLUG>-qbr.<PILOT_DOMAIN>\n\n"
        "  Grafana:             https://<PROJECT_SLUG>-grafana.<PILOT_DOMAIN>\n"
        "  Exasol MCP:          https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp\n"
        "                       Auth: AWS Cognito hosted UI (%s)\n"
        "  <PILOT_NAME> Chat:      https://<PROJECT_SLUG>-chat.<PILOT_DOMAIN>\n\n"
        "                       Auth: AWS Cognito SSO (browser)\n"
        "  Operator Dashboard:  %s\n"
        "                       Auth: AWS Cognito SSO (PKCE) · Start/Stop buttons + live EC2 state\n"
        "%s\n"
        "Schedule\n"
        "  Start: 12:00 CEST Mon-Fri (EventBridge cron 0 10 ? * MON-FRI *)\n"
        "  Stop:  24:00 CEST every day (EventBridge cron 0 22 * * ? *)\n"
        "  Ad-hoc start: dashboard button, available any time the env is stopped.\n\n"
        "Cost discipline\n"
        "  Stopped instance + attached Elastic IP: ~EUR 0.30 per weekend.\n\n"
        "— Sent automatically by the <PILOT_NAME> scheduler Lambda (<AWS_REGION>).\n"
        % (state, utc, cest, instance_id, STATUS_WEBSITE,
           EC2_DNS, EC2_DNS, EC2_DNS, COGNITO_DOMAIN, DASHBOARD, adhoc)
    )


def _notify(state, instance_id):
    recips = [e.strip() for e in os.environ.get("OPS_RECIPIENTS", "").split(",") if e.strip()]
    sender = os.environ.get("SES_FROM", "").strip()
    if not recips or not sender:
        return "skipped (no OPS_RECIPIENTS/SES_FROM)"
    cest = (datetime.datetime.utcnow() + datetime.timedelta(hours=2)).strftime("%Y.%m.%d.%H.%M")
    subject = "%s [%s] : Status <PILOT_NAME> Exasol environment on AWS" % (cest, state)
    try:
        boto3.client("sesv2", region_name=os.environ.get("AWS_REGION", "<AWS_REGION>")).send_email(
            FromEmailAddress=sender,
            Destination={"ToAddresses": recips},
            Content={"Simple": {"Subject": {"Data": subject},
                                "Body": {"Text": {"Data": _body(state, instance_id)}}}},
        )
        return "notified: " + ", ".join(recips)
    except Exception as e:
        return "notify-failed: " + str(e)[:160]


def lambda_handler(event, context):
    event = event or {}
    action = str(event.get("action") or os.environ.get("ACTION", "")).strip().lower()
    instance_id = os.environ["EC2_INSTANCE_ID"]
    ec2 = boto3.client("ec2", region_name=os.environ.get("AWS_REGION", "<AWS_REGION>"))

    # notify-only: send the rich status email for a given state (used by the post-E2E START gate)
    if action == "notify":
        state = str(event.get("state") or "RUNNING").strip().upper()
        notify = _notify(state, instance_id)
        print("action=notify state=%s notify=%s" % (state, notify))
        return {"action": "notify", "state": state, "notify": notify}

    state = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]["State"]["Name"]
    if action == "start":
        result = "start_issued" if state in ("stopped", "stopping") else "noop (already %s)" % state
        if state in ("stopped", "stopping"):
            ec2.start_instances(InstanceIds=[instance_id])
    elif action == "stop":
        result = "stop_issued" if state == "running" else "noop (already %s)" % state
        if state == "running":
            ec2.stop_instances(InstanceIds=[instance_id])
    else:
        raise ValueError("unknown action %r (expected start|stop|notify)" % action)

    # STOP notifies (rich); START is silent unless NOTIFY_ON_START (START status email
    # is sent post-E2E via the notify action).
    notify_start = os.environ.get("NOTIFY_ON_START", "false").strip().lower() in ("1", "true", "yes")
    if action == "stop":
        notify = _notify("STOPPED", instance_id)
    elif action == "start" and notify_start:
        notify = _notify("STARTED", instance_id)
    else:
        notify = "suppressed (START notified only after E2E health check passes)"
    print("action=%s prior_state=%s result=%s notify=%s" % (action, state, result, notify))
    return {"action": action, "instance": instance_id, "prior_state": state, "result": result, "notify": notify}
