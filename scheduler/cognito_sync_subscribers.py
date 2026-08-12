"""<PILOT_NAME> — Cognito subscriber sync (XYZ-PILOT-039).

Runs on a recurring EventBridge schedule. For each candidate email in the
CANDIDATE_EMAILS env var:

  - if a Cognito user already exists in the pool          → skip
  - if SES verification status is not "Success"           → skip
  - otherwise                                              → admin_create_user
    (no MessageAction; Cognito sends the pool's configured HTML invite)

Idempotent: re-runs are safe. The Lambda becomes a no-op once every
candidate is either already in the pool or unverified-failed. Operator
can delete the EventBridge rule once the candidate set is empty.

This Lambda is the "B half" of the SES-verified→Cognito-onboarded
handshake for the <PILOT_NAME>:

  Operator runs `aws ses verify-email-identity --email-address X`
       │
       ▼
  Subscriber clicks the AWS verification link within 24 h TTL
       │
       ▼
  SES flips status to Success
       │
       ▼   ◄── this Lambda observes that on its next tick
  Cognito admin_create_user(X) fires
       │
       ▼
  Subscriber gets the (now HTML-formatted) Pilot invite with the
  hosted-UI URL + temporary password (XYZ-PILOT-036's lesson applied).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

USER_POOL_ID = os.environ["USER_POOL_ID"]
CANDIDATE_EMAILS = [
    e.strip() for e in os.environ["CANDIDATE_EMAILS"].split(",") if e.strip()
]

cognito = boto3.client("cognito-idp", region_name="eu-central-1")
ses = boto3.client("ses", region_name="eu-central-1")


def _ses_verified(emails: list[str]) -> set[str]:
    if not emails:
        return set()
    attrs = ses.get_identity_verification_attributes(Identities=emails)
    return {
        addr
        for addr, info in attrs.get("VerificationAttributes", {}).items()
        if info.get("VerificationStatus") == "Success"
    }


def _cognito_exists(email: str) -> bool:
    """XYZ-PILOT-069 sub-4: catch ClientError (TooManyRequestsException etc.)
    and log+treat-as-exists rather than propagating, so a throttle on user
    N doesn't abort processing of users N+1, N+2, … with no resume marker.
    """
    try:
        cognito.admin_get_user(UserPoolId=USER_POOL_ID, Username=email)
        return True
    except cognito.exceptions.UserNotFoundException:
        return False
    except Exception as exc:  # noqa: BLE001  (ClientError, EndpointConnectionError, etc.)
        log.warning("cognito lookup for %s failed (%s) — treating as exists; will retry next hour",
                    email, type(exc).__name__)
        return True


def lambda_handler(event: dict, context: Any) -> dict:
    log.info("sync · pool=%s · candidates=%d", USER_POOL_ID, len(CANDIDATE_EMAILS))
    verified = _ses_verified(CANDIDATE_EMAILS)
    log.info("ses verified subset: %s", sorted(verified))

    results = []
    for email in CANDIDATE_EMAILS:
        if _cognito_exists(email):
            results.append({"email": email, "action": "skip", "reason": "already in pool"})
            continue
        if email not in verified:
            results.append({"email": email, "action": "skip", "reason": "ses not verified yet"})
            continue
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
            results.append({"email": email, "action": "created"})
            log.info("created %s · invite email queued by Cognito", email)
        except Exception as exc:  # noqa: BLE001
            results.append({"email": email, "action": "error", "error": str(exc)})
            log.warning("create %s failed: %s", email, exc)

    return {"candidates": len(CANDIDATE_EMAILS), "results": results}
