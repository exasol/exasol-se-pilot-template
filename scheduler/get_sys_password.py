"""<PILOT_NAME> — Cognito-gated retrieval of the Exasol sys password.

Follow-up Lambda for XYZ-PILOT-062 (sys password rotation). Lets the seven
<PILOT_NAME> Cognito subscribers fetch the current sys password from AWS
Secrets Manager without needing IAM credentials in account <AWS_ACCOUNT_ID>.

Flow
====
1. Caller signs into the Cognito Hosted UI (same pool as chatbot + MCP),
   captures an access token via the OAuth code grant.
2. Caller POSTs to this Lambda's Function URL with:
       Authorization: Bearer <access_token>
3. The Lambda revalidates the token against Cognito's /oauth2/userInfo
   endpoint and extracts the caller's email.
4. If the email is in ALLOWED_EMAILS, the Lambda fetches the secret value
   and returns it as JSON.
5. Every attempt (allow OR deny) is logged to CloudWatch with the
   outcome and the caller email. The secret value itself is NEVER logged.

Audit channels
==============
- CloudWatch Logs `/aws/lambda/<PROJECT_SLUG>-get-sys-password`: one INFO/WARN
  line per call with email + outcome + (on success) the secret VersionId.
- CloudTrail (Secrets Manager): every `GetSecretValue` is logged with
  the Lambda role's principal — independent second source.

Updating the allowlist
======================
  aws lambda update-function-configuration \
      --function-name <PROJECT_SLUG>-get-sys-password \
      --environment "Variables={COGNITO_DOMAIN=...,SECRET_NAME=...,ALLOWED_EMAILS=a@x,b@y}" \
      --region eu-central-1
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

# COGNITO_DOMAIN kept for backwards compatibility but no longer used — token
# validation now goes through cognito-idp.GetUser (works for tokens from any
# auth flow, including SRP/admin which lack OAuth scopes).
SECRET_NAME = os.environ.get("SECRET_NAME", "<PROJECT_SLUG>/exasol-sys")
ALLOWED_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ALLOWED_EMAILS", "").split(",")
    if e.strip()
}

sm = boto3.client("secretsmanager", region_name="eu-central-1")
cidp = boto3.client("cognito-idp", region_name="eu-central-1")


def _resp(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _bearer(event: dict[str, Any]) -> str | None:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def _email_from_token(access_token: str) -> str | None:
    """Validate access token via Cognito GetUser and return the email claim.

    GetUser is auth'd by the AccessToken parameter itself — the Lambda's
    IAM role needs no cognito-idp permissions. Works for tokens minted by
    SRP, admin, refresh, or OAuth flows.
    """
    try:
        resp = cidp.get_user(AccessToken=access_token)
    except cidp.exceptions.NotAuthorizedException:
        log.warning("GetUser rejected token (NotAuthorized)")
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("GetUser error: %s", type(e).__name__)
        return None
    for attr in resp.get("UserAttributes", []):
        if attr.get("Name") == "email":
            return (attr.get("Value") or "").strip().lower() or None
    return None


def lambda_handler(event: dict, context: Any) -> dict[str, Any]:
    token = _bearer(event)
    if not token:
        log.warning("DENY · no bearer token")
        return _resp(401, {"error": "missing or malformed Authorization header"})

    email = _email_from_token(token)
    if not email:
        log.warning("DENY · token invalid or missing email")
        return _resp(401, {"error": "invalid or expired Cognito token"})

    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        log.warning("DENY · email=%s not in allowlist", email)
        return _resp(403, {"error": "email not authorized for this secret"})

    try:
        sv = sm.get_secret_value(SecretId=SECRET_NAME)
    except Exception as e:  # noqa: BLE001
        log.error("FAIL · email=%s · %s", email, type(e).__name__)
        return _resp(500, {"error": "secret retrieval failed"})

    version = (sv.get("VersionId") or "")[:8] or "?"
    log.info("OK · email=%s · secret=%s · version=%s", email, SECRET_NAME, version)
    return _resp(200, {
        "secret_name": SECRET_NAME,
        "password": sv["SecretString"],
        "version_id": sv.get("VersionId"),
        "retrieved_by": email,
    })
