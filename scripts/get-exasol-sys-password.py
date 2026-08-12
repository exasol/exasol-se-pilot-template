#!/usr/bin/env python3
"""<PILOT_NAME> — retrieve the current Exasol `sys` password via Cognito SSO.

Run this script on your own machine. It will:

  1. Open the <PILOT_NAME> Cognito Hosted UI in your default browser.
  2. Catch the OAuth callback on localhost:54321.
  3. Exchange the authorization code for ID + access tokens.
  4. Trade the Cognito ID token for temporary AWS credentials via the
     xyz_pilot_secret_retrievers Cognito Identity Pool.
  5. Invoke the password-retrieval Lambda (xyz-pilot-get-sys-password)
     with those temporary credentials. The Lambda re-validates your
     Cognito access token, checks the email allowlist, and returns
     the secret value from AWS Secrets Manager.
  6. Print the sys password (or copy to clipboard with --clipboard).

Nothing token-/password-related is persisted to disk by this script.
All sensitive material lives only in process memory.

Usage
=====
  python3 get-exasol-sys-password.py                 # print password
  python3 get-exasol-sys-password.py --clipboard     # copy via pbcopy/xclip
  python3 get-exasol-sys-password.py --json          # full JSON envelope

Prereqs
=======
- Python 3.9+, plus `boto3` (`pip install boto3` or `pip3 install boto3`).
- A confirmed <PILOT_NAME> Cognito account (you've completed the temp-password
  change after the chatbot invite).

Troubleshooting
===============
For ANY problem with this flow (script error, browser sign-in fails,
"403 not authorized", Lambda timeout, anything), please log it via the
<PILOT_NAME> chatbot at https://<PROJECT_SLUG>-chat.<PILOT_DOMAIN>/ using
the "🐛 Log as new defect" button — that writes a new XYZ-PILOT-NNN
entry straight into docs/XYZ_PILOT_ISSUE_TRACKER.md (single source of
truth for the Pilot's backlog). Do not reply per email — replies get
fragmented across inboxes; the tracker is what we triage from.

Quick-reference for common errors:
- "403 email not authorized" → your email isn't in the Lambda allowlist
  (env var ALLOWED_EMAILS on xyz-pilot-get-sys-password).
- "Browser doesn't open" → use --print-url instead.
- "Port 54321 in use" → another helper instance running; pass --port 54322.
- "Could not get credentials" → your Cognito session expired; re-run.
- "401 invalid or expired Cognito token" → access token didn't carry the
  `aws.cognito.signin.user.admin` scope; happens if you signed in via a
  client other than `xyz-pilot-chatbot-client`. Re-run this script.
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import os
import secrets as _secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser


class _DualStackTCPServer(socketserver.TCPServer):
    """Binds on IPv6 + IPv4 so the browser can reach us whether macOS resolves
    `localhost` to ::1 or 127.0.0.1."""
    address_family = socket.AF_INET6
    allow_reuse_address = True
    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()

# ─── <PILOT_NAME> Cognito wiring (production) ───────────────────────────────
COGNITO_REGION  = "<AWS_REGION>"
COGNITO_DOMAIN  = "https://<COGNITO_DOMAIN_PREFIX>.auth.<AWS_REGION>.amazoncognito.com"
USER_POOL_ID    = "<COGNITO_USER_POOL_ID>"
CLIENT_ID       = "3brunjmf3l8gj6fb093c7fuiu"           # xyz-pilot-chatbot-client (public OAuth client identifier)
IDENTITY_POOL_ID = "<AWS_REGION>:a90e805b-6ef1-4b29-9d5b-7e89d961660d"
LAMBDA_NAME     = "xyz-pilot-get-sys-password"
DEFAULT_PORT    = 54321
SCOPES          = "openid email profile aws.cognito.signin.user.admin"

# XYZ-PILOT-170: CLIENT_SECRET previously hardcoded here; moved to AWS SSM
# Parameter Store (SecureString) at /xyz-pilot/secrets/cognito-chatbot-client-secret.
# Resolution order: env-var override (for CI / quick local runs) → SSM fetch.
def _resolve_client_secret() -> str:
    env_val = os.environ.get("XYZ_PILOT_COGNITO_CHATBOT_CLIENT_SECRET")
    if env_val:
        return env_val
    try:
        import boto3
        ssm = boto3.client("ssm", region_name=COGNITO_REGION)
        resp = ssm.get_parameter(
            Name="/xyz-pilot/secrets/cognito-chatbot-client-secret",
            WithDecryption=True,
        )
        return resp["Parameter"]["Value"]
    except Exception as exc:  # noqa: BLE001
        sys.exit(
            f"ERROR: Cognito client secret unavailable.\n"
            f"  Tried env var XYZ_PILOT_COGNITO_CHATBOT_CLIENT_SECRET (unset)\n"
            f"  Tried SSM /xyz-pilot/secrets/cognito-chatbot-client-secret ({type(exc).__name__}: {exc})\n"
            f"  Quick fix: export XYZ_PILOT_COGNITO_CHATBOT_CLIENT_SECRET=\"$(\\\n"
            f"    AWS_PROFILE=default aws ssm get-parameter \\\n"
            f"    --name /xyz-pilot/secrets/cognito-chatbot-client-secret --with-decryption \\\n"
            f"    --region <AWS_REGION> --query Parameter.Value --output text)\"\n"
            f"  Per XYZ-PILOT-170 (Cognito CLIENT_SECRET de-hardcoding)."
        )

# NOTE: do NOT call _resolve_client_secret() at module-import — it would
# break `--help` when SSM is unreachable (e.g. on a laptop offline). Call
# it lazily at the OAuth token-exchange call site instead.


# ─── OAuth code flow against Cognito Hosted UI ───────────────────────────
def _login_url(port: int, state: str) -> str:
    # /oauth2/authorize is the canonical OIDC endpoint; /login is the Hosted-UI
    # wrapper and is stricter about scope parsing.
    # quote_via=quote forces `%20` for spaces (Cognito rejects `+` for the
    # aws.cognito.signin.user.admin scope on some pool configurations).
    return f"{COGNITO_DOMAIN}/oauth2/authorize?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id":     CLIENT_ID,
        "redirect_uri":  f"http://localhost:{port}/callback",
        "scope":         SCOPES,
        "state":         state,
    }, quote_via=urllib.parse.quote)


def _exchange_code(code: str, redirect_uri: str) -> dict:
    data = urllib.parse.urlencode({
        "grant_type":   "authorization_code",
        "code":         code,
        "redirect_uri": redirect_uri,
        "client_id":    CLIENT_ID,
    }).encode()
    basic = base64.b64encode(f"{CLIENT_ID}:{_resolve_client_secret()}".encode()).decode()
    req = urllib.request.Request(
        f"{COGNITO_DOMAIN}/oauth2/token", data=data, method="POST",
        headers={
            "Content-Type":  "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic}",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


class _CB(http.server.BaseHTTPRequestHandler):
    got: dict = {}
    def do_GET(self):  # noqa: N802
        p = urllib.parse.urlparse(self.path)
        if p.path != "/callback":
            self.send_error(404); return
        q = urllib.parse.parse_qs(p.query)
        _CB.got = {k: v[0] for k, v in q.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(
            b"<html><body style='font-family:sans-serif;text-align:center;padding-top:3em'>"
            b"<h2>Signed in. Return to your terminal.</h2></body></html>")
    def log_message(self, *_a, **_kw): pass


def _do_oauth(port: int, print_url: bool) -> dict:
    state = _secrets.token_urlsafe(16)
    url   = _login_url(port, state)
    if print_url:
        print("Open this URL in a browser and sign in:"); print(url)
    else:
        print("→ Opening Cognito sign-in in your browser …")
        webbrowser.open(url)
    print(f"→ Waiting for OAuth callback on http://localhost:{port}/callback …")

    srv = _DualStackTCPServer(("::", port), _CB)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        for _ in range(600):  # 10 min — enough time for first-time Hosted UI flows
            if _CB.got: break
            time.sleep(1)
        else:
            raise SystemExit("Timed out waiting for sign-in (10 min).")
    finally:
        srv.shutdown()

    got = _CB.got
    if "error" in got:
        raise SystemExit(f"OAuth error: {got.get('error')} — {got.get('error_description','')}")
    if got.get("state") != state:
        raise SystemExit("OAuth state mismatch — possible CSRF. Aborting.")
    code = got.get("code")
    if not code:
        raise SystemExit("OAuth callback missing code.")
    print("→ Exchanging code for tokens …")
    return _exchange_code(code, f"http://localhost:{port}/callback")


# ─── Identity Pool → temporary AWS credentials → Lambda invoke ───────────
def _retrieve_password(id_token: str, access_token: str) -> dict:
    try:
        import boto3
    except ModuleNotFoundError:
        raise SystemExit("Missing dependency `boto3`. Install with: pip3 install boto3")

    provider = f"cognito-idp.{COGNITO_REGION}.amazonaws.com/{USER_POOL_ID}"
    cidp = boto3.client("cognito-identity", region_name=COGNITO_REGION)

    print("→ Exchanging Cognito ID token for temporary AWS credentials …")
    ident = cidp.get_id(IdentityPoolId=IDENTITY_POOL_ID, Logins={provider: id_token})
    creds = cidp.get_credentials_for_identity(
        IdentityId=ident["IdentityId"], Logins={provider: id_token})["Credentials"]

    print("→ Invoking xyz-pilot-get-sys-password …")
    lam = boto3.client("lambda",
        aws_access_key_id     = creds["AccessKeyId"],
        aws_secret_access_key = creds["SecretKey"],
        aws_session_token     = creds["SessionToken"],
        region_name           = COGNITO_REGION,
    )
    resp = lam.invoke(
        FunctionName = LAMBDA_NAME,
        InvocationType = "RequestResponse",
        Payload = json.dumps({"headers": {"authorization": f"Bearer {access_token}"}}).encode(),
    )
    raw = resp["Payload"].read().decode()
    envelope = json.loads(raw)
    if envelope.get("statusCode") and envelope["statusCode"] >= 400:
        body = envelope.get("body", "{}")
        raise SystemExit(
            f"Lambda denied: HTTP {envelope['statusCode']} — {body}\n"
            "Please log this via the <PILOT_NAME> chatbot at "
            "https://<PROJECT_SLUG>-chat.<PILOT_DOMAIN> "
            "(use the '🐛 Log as new defect' button) — do not reply per email."
        )
    return json.loads(envelope["body"])


# ─── Clipboard helper ────────────────────────────────────────────────────
def _to_clipboard(value: str) -> str:
    for cmd in (["pbcopy"], ["xclip", "-selection", "clipboard"], ["wl-copy"]):
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            p.communicate(input=value.encode())
            if p.returncode == 0:
                return cmd[0]
        except FileNotFoundError:
            continue
    raise SystemExit("No clipboard tool (need pbcopy / xclip / wl-copy).")


# ─── Main ────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Retrieve the <PILOT_NAME> Exasol sys password via Cognito SSO.")
    ap.add_argument("--clipboard", action="store_true", help="Copy password to clipboard, don't print.")
    ap.add_argument("--json",      action="store_true", help="Print full Lambda response JSON.")
    ap.add_argument("--print-url", action="store_true", help="Print sign-in URL instead of opening browser.")
    ap.add_argument("--port",      type=int, default=DEFAULT_PORT, help="Local OAuth callback port.")
    args = ap.parse_args()

    tokens = _do_oauth(args.port, args.print_url)
    if "id_token" not in tokens or "access_token" not in tokens:
        raise SystemExit(f"Token exchange didn't return id+access tokens: {list(tokens)}")

    payload = _retrieve_password(tokens["id_token"], tokens["access_token"])

    if args.json:
        print(json.dumps(payload, indent=2)); return

    pw = payload.get("password")
    if not pw:
        raise SystemExit(f"Lambda response had no password field: {payload}")
    if args.clipboard:
        tool = _to_clipboard(pw)
        print(f"✓ Password copied to clipboard via {tool}.")
        print(f"  retrieved_by={payload.get('retrieved_by')}  version_id={payload.get('version_id')}")
    else:
        print(pw)


if __name__ == "__main__":
    main()
