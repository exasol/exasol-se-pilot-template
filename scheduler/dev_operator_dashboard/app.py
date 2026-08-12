"""<PILOT_NAME> DEV Operator Dashboard — reduced backend (4 panels).

Cognito-token-gated (server-side JWKS validation). Uses the node instance role
for AWS calls. NO XYZ_MASTER / swarm panels (UAT-only). Panels: Start/Stop,
Service URIs, Service-Password Retrieval, New-User Onboarding.
"""
import os, json
import boto3, jwt
from jwt import PyJWKClient
from flask import Flask, request, jsonify, send_from_directory

REGION = os.environ.get("AWS_REGION", "<AWS_REGION>")
POOL = os.environ["COGNITO_POOL_ID"]
CLIENT = os.environ["COGNITO_CLIENT_ID"]
INSTANCE = os.environ["EC2_INSTANCE_ID"]
SCHED = os.environ.get("SCHEDULER_FN", "xyz-pilot-scheduler")
ISS = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
_jwks = PyJWKClient(f"{ISS}/.well-known/jwks.json")

app = Flask(__name__, static_folder="static")
ec2 = boto3.client("ec2", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
idp = boto3.client("cognito-idp", region_name=REGION)

SERVICE_URIS = [
    {"label": "Streamlit QBR", "url": "https://<PROJECT_SLUG>-qbr.<PILOT_DOMAIN>"},
    {"label": "Grafana", "url": "https://<PROJECT_SLUG>-grafana.<PILOT_DOMAIN>"},
    {"label": "<PILOT_NAME> Chatbot", "url": "https://<PROJECT_SLUG>-chat.<PILOT_DOMAIN>"},
    {"label": "Public Exasol MCP", "url": "https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp"},
    {"label": "Operator Dashboard", "url": "https://<PROJECT_SLUG>-dashboard.<PILOT_DOMAIN>"},
]
# Service-password sources read from the node .env files mounted read-only at /secrets.
SECRET_SRC = [
    ("Streamlit QBR — APP_PASSWORD", "/secrets/qbr.env", "APP_PASSWORD"),
    ("Grafana — admin password", "/secrets/grafana.env", "GRAFANA_ADMIN_PASSWORD"),
    ("exasol_proxy — bearer token", "/secrets/grafana.env", "PROXY_TOKEN"),
    ("Exasol DB — XYZ_DASH", "/secrets/mcp.env", "EXA_PASSWORD"),
]


def _auth():
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        raise PermissionError("missing bearer token")
    tok = h[7:]
    key = _jwks.get_signing_key_from_jwt(tok).key
    claims = jwt.decode(tok, key, algorithms=["RS256"], issuer=ISS,
                        audience=CLIENT, options={"require": ["exp", "iss", "aud"]})
    return claims.get("email") or claims.get("cognito:username") or "user"


def _guard():
    try:
        return _auth(), None
    except Exception:
        return None, (jsonify(error="unauthorized"), 401)


def _read_env(path, key):
    try:
        with open(path) as f:
            for line in f:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/api/config")
def config():
    # non-secret values the page needs to start the Cognito PKCE flow
    return jsonify(domain=os.environ["COGNITO_DOMAIN"], clientId=CLIENT,
                   redirect=os.environ["DASHBOARD_URL"])


@app.get("/api/uris")
def uris():
    who, err = _guard()
    if err:
        return err
    return jsonify(SERVICE_URIS)


@app.get("/api/status")
def status():
    who, err = _guard()
    if err:
        return err
    r = ec2.describe_instances(InstanceIds=[INSTANCE])
    st = r["Reservations"][0]["Instances"][0]["State"]["Name"]
    return jsonify(instance=INSTANCE, state=st)


@app.post("/api/start")
def start():
    return _action("start")


@app.post("/api/stop")
def stop():
    return _action("stop")


def _action(a):
    who, err = _guard()
    if err:
        return err
    lam.invoke(FunctionName=SCHED, InvocationType="Event",
               Payload=json.dumps({"action": a}).encode())
    return jsonify(ok=True, action=a, by=who)


@app.get("/api/secrets")
def secrets():
    who, err = _guard()
    if err:
        return err
    out = [{"label": lbl, "value": _read_env(p, k) or "(not found)"} for lbl, p, k in SECRET_SRC]
    return jsonify(out)


@app.post("/api/onboard")
def onboard():
    who, err = _guard()
    if err:
        return err
    email = ((request.json or {}).get("email") or "").strip()
    if "@" not in email:
        return jsonify(error="valid email required"), 400
    try:
        idp.admin_create_user(
            UserPoolId=POOL, Username=email,
            UserAttributes=[{"Name": "email", "Value": email},
                            {"Name": "email_verified", "Value": "true"}],
            MessageAction="SUPPRESS")
        return jsonify(ok=True, email=email,
                       note="created (FORCE_CHANGE_PASSWORD) — operator sets password via admin-set-user-password")
    except idp.exceptions.UsernameExistsException:
        return jsonify(ok=True, email=email, note="already exists")
    except Exception as e:  # noqa
        return jsonify(error=str(e)), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
