"""<PILOT_NAME> — Cognito invite blast (XYZ-PILOT-035 final step).

Resends the AWS Cognito hosted-UI invite email to every pre-seeded user
in the Pilot's user pool. Fired once via EventBridge at 24:00 CEST on
the date the operator schedules; the rule deletes itself after the
firing window passes (cron with explicit year).

Idempotent against double-invocation: `admin_create_user` with
`MessageAction=RESEND` works only for users still in
FORCE_CHANGE_PASSWORD; users who have already logged in raise
NotAuthorizedException, which we swallow and log.

Dry-run by default. The EventBridge target supplies `{"send": true}`
in its Input.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

USER_POOL_ID = os.environ["USER_POOL_ID"]
EMAILS = [e.strip() for e in os.environ["EMAILS"].split(",") if e.strip()]

cognito = boto3.client("cognito-idp", region_name="eu-central-1")


def lambda_handler(event: dict, context: Any) -> dict:
    send_real = isinstance(event, dict) and bool(event.get("send"))
    log.info(
        "invite blast · send_real=%s · pool=%s · users=%d",
        send_real, USER_POOL_ID, len(EMAILS),
    )

    results = []
    for email in EMAILS:
        if not send_real:
            results.append({"email": email, "mode": "dry-run", "action": "would-resend"})
            continue
        try:
            cognito.admin_create_user(
                UserPoolId=USER_POOL_ID,
                Username=email,
                MessageAction="RESEND",
            )
            results.append({"email": email, "mode": "live", "action": "sent"})
            log.info("resend → %s · ok", email)
        except cognito.exceptions.UserNotFoundException as exc:
            results.append({"email": email, "mode": "live", "action": "not-found",
                            "error": str(exc)})
            log.warning("resend → %s · user not in pool", email)
        except Exception as exc:  # noqa: BLE001
            results.append({"email": email, "mode": "live", "action": "error",
                            "error": str(exc)})
            log.warning("resend → %s · failed: %s", email, exc)

    return {"send_real": send_real, "results": results}
