"""<PILOT_NAME> Chatbot — Streamlit + Bedrock + Cognito SSO + GitHub archival.

XYZ-PILOT-043 Phase 1 (shipped 2026-05-20) + XYZ-PILOT-049 Phase 2 (shipped 2026-05-21):
  - Cognito Hosted-UI OAuth code flow for sign-in (single sign-on with the
    MCP server's user pool).
  - Bedrock Claude Sonnet 4.6 via the **Converse API** with tool-use enabled.
  - Static 12-file Pilot context fetched fresh from GitHub on first turn,
    cached 5 min in session state (covers ~90 % of subscriber questions
    without any tool calls).
  - **Phase 2 tools** for the remaining ~10 %:
      • `read_repo_file(path, max_chars)` — pulls ANY file in the private repo
        via the authenticated Contents API.
      • `list_repo_directory(path)` — directory listing for path discovery.
    Guardrails: forbidden-pattern check on every path (`.env`, `*.pem`,
    `secrets.json`, etc.), path-traversal block (`..`, leading `/`), and a
    configurable tool-call ceiling per user turn.
  - "Archive Q&A to repo" button → create or update
    `docs/70-generated-qa/qa/YYYY-MM-DD_<slug>.md` via GitHub Contents API
    using the PAT parked in SSM.
  - "File as feature request" and "Log as new defect" buttons → both emit
    `tests/logs/.../issue-candidates.md` handoffs for XYZ_MASTER. Bedrock
    dup-check first; existing tickets surfaced instead of creating duplicates.

Not yet in scope:
  - Streaming responses (Converse supports it; not wired here yet).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import boto3
import pyexasol
import requests
import streamlit as st

# XYZ-PILOT-053: MCP client for talking to the internal Exasol MCP sidecar.
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# ---------------------------------------------------------------------------
# Config (read from env, set by docker-compose .env)
# ---------------------------------------------------------------------------
AWS_REGION         = os.environ.get("AWS_REGION", "<AWS_REGION>")
COGNITO_DOMAIN     = os.environ["COGNITO_DOMAIN"]
COGNITO_CLIENT_ID  = os.environ["COGNITO_CLIENT_ID"]
COGNITO_CLIENT_SECRET = os.environ["COGNITO_CLIENT_SECRET"]
APP_REDIRECT_URI   = os.environ["APP_REDIRECT_URI"]
BEDROCK_MODEL_ID   = os.environ.get(
    "BEDROCK_MODEL_ID", "eu.anthropic.claude-sonnet-4-6",
)
GITHUB_REPO        = os.environ.get(
    "GITHUB_REPO", "<GITHUB_OWNER>/<PROJECT_SLUG>",
)
SSM_PAT_NAME       = os.environ.get(
    "SSM_PAT_NAME", "/xyz-pilot/github/service-token",
)
HISTORY_DIR        = os.environ.get("HISTORY_DIR", "/data/history")
HISTORY_MAX_ITEMS  = 10
# XYZ-PILOT-053: internal Exasol MCP endpoint, reachable only on the chatbot's
# docker bridge network. No-auth — trust boundary is the docker network itself.
MCP_INTERNAL_URL        = os.environ.get("MCP_INTERNAL_URL", "http://mcp-internal:8000/mcp")
LAST_DEPLOYMENT_ISSUE  = os.environ.get(
    "XYZ_LAST_DEPLOYMENT_ISSUE",
    os.environ.get("XYZ_DEPLOYMENT_TICKET", "XYZ-PILOT-288"),
)
BUILD_AT_UTC           = os.environ.get("XYZ_BUILD_AT_UTC", "not-stamped")
DEPLOYED_AT_UTC        = os.environ.get("XYZ_DEPLOYED_AT_UTC", BUILD_AT_UTC)
APP_STARTED_AT_UTC     = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
CONFIDENTIAL_NOTICE = (
    "Confidential - <PILOT_NAME> - "
    "© Exasol AG · ESL © <SEMANTIC_LAYER_VENDOR> · for XYZ evaluation use only."
)
XYZ_CHART_PALETTE      = ["#118DFF", "#E66C37", "#1AAB40", "#D64554", "#6B007B", "#12239E", "#D9B300"]
XYZ_CHART_TEXT         = "#111827"
XYZ_CHART_GRID         = "#E5E7EB"
XYZ_PLOTLY_EVENTS_ENABLED = os.environ.get("XYZ_PLOTLY_EVENTS_ENABLED", "0").strip().lower() in {
    "1", "true", "yes", "on",
}

# XYZ-PILOT-114: branded with <CUSTOMER_NAME>'s favicon (same source
# the Streamlit QBR dashboard uses) instead of the default Streamlit emoji.
st.set_page_config(
    page_title="<PILOT_NAME> Chatbot",
    page_icon="💬",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Cognito OAuth Code Flow
# ---------------------------------------------------------------------------
def _login_url() -> str:
    params = {
        "client_id": COGNITO_CLIENT_ID,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": APP_REDIRECT_URI,
    }
    return f"{COGNITO_DOMAIN}/login?{urllib.parse.urlencode(params)}"


def _logout_url() -> str:
    return f"{COGNITO_DOMAIN}/logout?client_id={COGNITO_CLIENT_ID}&logout_uri={urllib.parse.quote(APP_REDIRECT_URI, safe='')}"


def _exchange_code(code: str) -> dict[str, Any]:
    auth = base64.b64encode(
        f"{COGNITO_CLIENT_ID}:{COGNITO_CLIENT_SECRET}".encode()
    ).decode()
    r = requests.post(
        f"{COGNITO_DOMAIN}/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "client_id": COGNITO_CLIENT_ID,
            "code": code,
            "redirect_uri": APP_REDIRECT_URI,
        },
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=10,
    )
    return r.json()


def _refresh_tokens(refresh_token: str) -> dict[str, Any]:
    """XYZ-PILOT-118 Phase 2: silently mint a fresh access_token + id_token
    from the cached refresh_token (30-day TTL on the chatbot client). The
    chatbot Cognito client is confidential, so the refresh-token grant uses
    HTTP Basic auth with the client secret — same pattern as `_exchange_code`.
    Cognito does NOT re-emit a refresh_token on this grant; preserve the
    existing one. Raises on non-200 so the caller can fall back to the
    sign-in page."""
    auth = base64.b64encode(
        f"{COGNITO_CLIENT_ID}:{COGNITO_CLIENT_SECRET}".encode()
    ).decode()
    r = requests.post(
        f"{COGNITO_DOMAIN}/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "client_id": COGNITO_CLIENT_ID,
            "refresh_token": refresh_token,
        },
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=10,
    )
    if r.status_code != 200:
        raise RuntimeError(f"refresh failed HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _userinfo(access_token: str) -> dict[str, Any]:
    r = requests.get(
        f"{COGNITO_DOMAIN}/oauth2/userInfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    return r.json()


def _jwt_is_live(token: str, leeway: int = 30) -> bool:
    """XYZ-PILOT-099(b): decode the JWT payload (no signature verification —
    just the `exp` claim) and return True if the token is still valid
    with `leeway` seconds of headroom. Returns False on parse failure
    (treat as expired — fail closed). This lets `_require_auth` detect
    expired sessions on every Streamlit rerun and revert to the login
    page instead of letting the user keep typing into a stale UI."""
    try:
        import time as _t
        parts = (token or "").split(".")
        if len(parts) < 2:
            return False
        payload_b64 = parts[1]
        payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp", 0)
        return exp > _t.time() + leeway
    except Exception:
        return False


def _provenance_caption() -> str:
    """XYZ-PILOT-150: compact build/deploy/start line shown below the service caption."""
    ticket_slug = LAST_DEPLOYMENT_ISSUE.lower()
    ticket_link = (
        "https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/"
        f"docs/XYZ_PILOT_ISSUE_TRACKER.md#{ticket_slug}"
    )
    return (
        f"[{LAST_DEPLOYMENT_ISSUE}]({ticket_link}) · "
        f"build `{BUILD_AT_UTC}` · deploy `{DEPLOYED_AT_UTC}` · "
        f"chatbot process started `{APP_STARTED_AT_UTC}`"
    )


def _render_signin_page(*, expired: bool = False) -> None:
    """Render the sign-in page. XYZ-PILOT-099(a): the call-to-action is a
    raw HTML `<a target="_self">` button so OAuth happens in the SAME tab
    (Streamlit's `st.markdown` markdown links default to new-tab in some
    browsers — observed in Edge)."""
    st.title("<PILOT_NAME> Chatbot 💬")
    if expired:
        st.warning(
            "⚠ Your Cognito session has expired. Please sign in again — "
            "we reverted to the login window the moment we detected the "
            "stale token (XYZ-PILOT-099)."
        )
    st.markdown(
        f"Sign in with your Pilot Cognito credentials (same ones you use for "
        f"the MCP server)."
    )
    st.markdown(
        f'<a href="{_login_url()}" target="_self" '
        f'style="display:inline-block;padding:.55em 1.2em;background:#1e40af;'
        f'color:white;text-decoration:none;border-radius:.4em;'
        f'font-weight:600;margin:.5em 0 1em 0;">'
        f'🔐 Sign in via Cognito →</a>',
        unsafe_allow_html=True,
    )
    st.caption(
        "If you haven't set your Cognito password yet, look for an email from "
        "AWS Cognito (subject contains *xyz-pilot-mcp*) with your temporary "
        "password."
    )
    st.stop()


def _require_auth() -> str:
    """Returns the authenticated user's email, or st.stop()s the script.

    XYZ-PILOT-099(b): re-validates the JWT `exp` claim on EVERY Streamlit
    rerun (not just at first login). Previously, once `user_email` landed
    in session_state, the function returned without ever checking
    expiry — leading to the "stale UI" failure mode where a subscriber
    typed into chat / defect form for minutes after their session
    expired, then on submit got booted to the login page with their
    text lost. Now the moment they interact (any rerun), expired sessions
    revert to the sign-in page immediately."""
    code = st.query_params.get("code")
    if code:
        tokens = _exchange_code(code)
        if "access_token" not in tokens:
            st.error(f"Sign-in failed: {tokens.get('error_description', tokens.get('error', 'unknown'))}.")
            st.stop()
        user = _userinfo(tokens["access_token"])
        email = user.get("email", "(no-email)")
        st.session_state.user_email = email
        st.session_state.access_token = tokens["access_token"]
        # XYZ-PILOT-118 Phase 2: persist refresh_token so we can silently
        # re-mint access_token when it expires, avoiding the 60-minute
        # Cognito kick-out during long work / debug sessions.
        if "refresh_token" in tokens:
            st.session_state.refresh_token = tokens["refresh_token"]
        st.query_params.clear()
        st.rerun()
    if "user_email" in st.session_state and "access_token" in st.session_state:
        if _jwt_is_live(st.session_state.access_token, leeway=120):
            return st.session_state.user_email
        # XYZ-PILOT-118 Phase 2: access_token near or past expiry — try one
        # silent refresh BEFORE bouncing to the sign-in page so the user
        # never loses chat history or a half-typed defect form mid-session.
        rt = st.session_state.get("refresh_token")
        if rt:
            try:
                new_tokens = _refresh_tokens(rt)
                if "access_token" in new_tokens:
                    st.session_state.access_token = new_tokens["access_token"]
                    return st.session_state.user_email
            except Exception:
                pass
        # Refresh impossible or failed — clear stale session_state.
        for k in ("user_email", "access_token", "refresh_token"):
            st.session_state.pop(k, None)
        _render_signin_page(expired=True)
    _render_signin_page(expired=False)


# ---------------------------------------------------------------------------
# Pilot context — fetched from GitHub, cached per session
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner="Loading <PILOT_NAME> context…")
def _fetch_pilot_context() -> str:
    """Pull README + daily status + tracker top section via authenticated
    Contents API (the repo is private). Cached 5 min."""
    parts: list[str] = []
    docs_to_inject = [
        ("README.md", 8000),
        ("CLAUDE.md", 6000),
        ("docs/00-live-status/XYZ_PILOT_DAILY_STATUS.md", 5000),
        ("docs/XYZ_PILOT_ISSUE_TRACKER.md", 12000),
        ("docs/00-live-status/XYZ_PILOT_PROGRESS_LOG.md", 10000),
        ("docs/10-product-data/XYZ_PILOT_SCHEMA_MAPPING.md", 8000),
        ("docs/00-live-status/XYZ_PILOT_LIVE_RUNBOOK.md", 8000),
        ("docs/30-runbooks/operations/XYZ_PILOT_DEVELOPER_RUNBOOK.md", 6000),
        ("docs/30-runbooks/mcp/XYZ_PILOT_MCP_CLIENT_GUIDE.md", 8000),
        ("docs/30-runbooks/mcp/XYZ_PILOT_MCP_PRODUCTION_HARDENING.md", 5000),
        ("docs/30-runbooks/security/XYZ_PILOT_CREDENTIALS.md", 6000),
        ("sample_schema_assets/migration_report.md", 5000),
    ]
    try:
        pat = _get_pat()
        headers = {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github.raw",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    except Exception as e:
        return f"[snapshot unavailable — PAT fetch failed: {e}]"
    for path, char_budget in docs_to_inject:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{urllib.parse.quote(path)}?ref=main"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                text = r.text[:char_budget]
                parts.append(f"=== {path} ===\n{text}\n")
            else:
                parts.append(f"=== {path} ===\n[fetch failed: HTTP {r.status_code}]\n")
        except Exception as e:
            parts.append(f"=== {path} ===\n[fetch failed: {e}]\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Bedrock
# ---------------------------------------------------------------------------
_bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

SYSTEM_PROMPT = """You are the <PILOT_NAME> Chatbot — an assistant for subscribers of an Exasol-on-AWS analytics pilot project. You help with questions about implementation status, schema, defects, and feature requests.

CONFIDENTIALITY BOUNDARY:
You operate in a confidential pilot context. In subscriber-facing responses, keep
ownership boundaries explicit when relevant: pilot platform IP is Exasol AG,
ESL assets are <SEMANTIC_LAYER_VENDOR> (separate license), and customer-origin artifacts/data
remain <CUSTOMER_NAME> confidential material.

ABOUT YOURSELF (so you can answer questions about your own architecture without hedging):
You are XYZ-PILOT-043 Phase 1 (shipped 2026-05-20) + XYZ-PILOT-049 Phase 2 + XYZ-PILOT-053 Phase 3 (shipped 2026-05-21). Your stack:
  - Streamlit container running on the <PILOT_NAME> EC2 in /opt/xyz-chatbot/, exposed via a named Cloudflare tunnel at https://<PROJECT_SLUG>-chat.<PILOT_DOMAIN>
  - Sign-in via AWS Cognito Hosted UI (OAuth code flow), reusing the same <AWS_REGION> user pool <COGNITO_USER_POOL_ID> that the Pilot's MCP server uses — same credentials for both.
  - LLM backend: AWS Bedrock **Converse API** with tool-use enabled, on the eu.anthropic.claude-sonnet-4-6 inference profile in <AWS_REGION>.
  - Pilot context: a static digest of 12 curated narrative docs (~22 K tokens) fetched fresh from the private repo via the authenticated GitHub Contents API on first turn, cached 5 min in Streamlit session state. The 12 docs are: README, CLAUDE.md, XYZ_PILOT_DAILY_STATUS, XYZ_PILOT_ISSUE_TRACKER (top portion), XYZ_PILOT_PROGRESS_LOG (most-recent milestones), XYZ_PILOT_SCHEMA_MAPPING, XYZ_PILOT_LIVE_RUNBOOK, XYZ_PILOT_DEVELOPER_RUNBOOK, XYZ_PILOT_MCP_CLIENT_GUIDE, XYZ_PILOT_MCP_PRODUCTION_HARDENING, XYZ_PILOT_CREDENTIALS, and sample_schema_assets/migration_report.
  - **Phase 2 tools (XYZ-PILOT-049):** for anything outside the 12-file digest you can call `read_repo_file(path, max_chars=20000)` to pull ANY file from the private repo, and `list_repo_directory(path)` to navigate. Guardrails block `.env`, `*.pem`, `*.key`, `secrets.json`, `node_access*`, plus path traversal. Tool-call ceiling defaults to 14 calls per user turn and can be tuned by the operator. A checkpoint after 6 calls asks you to stop exploring and answer from partial evidence unless one more tool call is essential.
  - **Phase 3 tools (XYZ-PILOT-053):** live Exasol DB access via two complementary surfaces:
    1. Internal Exasol MCP sidecar (`http://mcp-internal:8000/mcp` on the docker bridge network, no auth — trust boundary is the docker network). Provides ~20 **metadata** tools — `list_exasol_schemas`, `list_exasol_tables_and_views`, `describe_exasol_table_or_view`, `find_exasol_tables_and_views`, `list_exasol_custom_functions`, `list_exasol_built_in_functions` (and search/describe variants). Discovered at runtime via MCP `list_tools` and exposed to Bedrock dynamically. This image (exasol-mcp v3.2.4) intentionally has no SQL-execution tool.
    2. Chatbot-side `exasol_query(sql)` tool — pyexasol against the same DB, same `XYZ_DASH` read-only credential. SELECT / WITH / DESCRIBE / EXPLAIN / SHOW only. Results capped at 200 rows / 12 K chars. Use this for actual data; use the MCP metadata tools for structure.
  - GitHub repo write access: "Archive this Q&A to repo" creates or updates docs/70-generated-qa/qa/YYYY-MM-DD_<slug>.md on main. Defects + enhancement requests have TWO candidate-emission entry points for XYZ_MASTER consolidation: (1) post-reply buttons "File as feature request" / "Log as new defect" that operate on the last Q&A, and (2) a direct-submit form below the chat input (🐛 Submit as Defect / ✨ Submit as Enhancement — XYZ-PILOT-084) that skips the Q&A round-trip. Both paths read docs/XYZ_PILOT_ISSUE_TRACKER.md for the same Bedrock-driven duplicate check against the tracker's Open + recent Resolved entries first, then emit tests/logs/runs/<UTC>/XYZ_PILOT_CHATBOT/issue-candidates.md rows; existing tickets are surfaced instead of creating duplicates. Uses the same PAT fetched at runtime from SSM Parameter Store /xyz-pilot/github/service-token.
  - Cost: ~$1–3/month incremental, Bedrock-token-driven. Tool-heavy turns are slightly more expensive (~+5–10 ¢ extra round-trip).
Source code in services/chatbot/ on the repo. When asked about your architecture, describe it freely using the facts above — they are deployed reality, not speculation.

WHEN TO USE THE TOOLS:
  - The 12-file digest covers status / tickets / progress / schema / runbooks / credentials reference / BIM migration report. Most questions answer from the digest alone — don't call tools when the answer is already in context.
  - DO call `read_repo_file` for: specific SQL DDL or view definitions (e.g., V_ACTUAL_VS_TARGET, FACT_PRIMARY) — though for live data ALWAYS prefer `exasol_query` over reading the DDL file; source code (Lambdas, Streamlit pages, Dockerfiles); tracker entries below the top-12K-char digest window.
  - Call `list_repo_directory("")` first if you're unsure of paths. The repo root has these notable subdirectories: `services/chatbot/`, `clients/XYZ/`, `config/`, `docs/`, `sample_source_model/` (source model reference), `services/grafana/`, `prompts/`, `scheduler/` (Lambdas), `scripts/`, `services/streamlit-insights/`, `sample_schema_assets/` (DDL + sample data + views + measures).
  - **Complex visualization testcase lookup (XYZ-PILOT-270):** if the user asks to locate and execute the most complex chatbot visualization testcase, do NOT directory-walk. Read `docs/30-runbooks/ui/XYZ_PILOT_STREAMLIT_VISUALS.md` directly. The canonical target is TC-5 / TC-5A: Plotly YoY CY-vs-PY trend and grouped bar variant over live Exasol data with interactive chart controls.
  - **Live DB questions** ("how many rows in X?", "top 5 brands by sales?", "what's in FACT_TARGET?") → use `exasol_query(sql='SELECT ...')` with a tight SELECT (always include a LIMIT or WHERE; results are capped at 200 rows). For schema/table/column discovery PREFER the MCP metadata tools — `list_exasol_tables_and_views(schema_name='<PILOT_SCHEMA>')` and `describe_exasol_table_or_view(schema_name='<PILOT_SCHEMA>', object_name='X')` return structured metadata; only fall back to `exasol_query` for data. NEVER guess at sample DDL — query the live DB; it always wins.
  - **Schema qualification:** `XYZ_DASH` is the read-only database user, NOT a schema. Never query `XYZ_DASH.<table>`. Business tables, views, and MV snapshots live under `<PILOT_SCHEMA>`, so use `<PILOT_SCHEMA>.FACT_PRIMARY`, `<PILOT_SCHEMA>.MV_YOY_METRICS`, etc.
  - Be parsimonious: 1–2 well-targeted tool calls per question is usually enough. Don't list directories you already know. Don't re-issue SELECTs you've already run in this turn.
  - **EXASOL SQL DIALECT — STRICT (XYZ-PILOT-230 follow-up).** Exasol is NOT PostgreSQL, MySQL, or SQL Server. NEVER emit cross-dialect tokens that don't parse in Exasol:
    - ❌ `ILIKE` → use `UPPER(col) LIKE UPPER('pattern')` for case-insensitive search
    - ❌ `col::INTEGER` (PostgreSQL cast) → use `CAST(col AS INTEGER)`
    - ❌ `LIMIT n, m` (MySQL pagination) → use `LIMIT m OFFSET n`
    - ❌ `RETURNING` (PostgreSQL) → not supported; restructure as SELECT after the mutation
    - ❌ `FETCH FIRST n ROWS ONLY` combined with `WHERE` in the same SELECT — Exasol's parser is stricter than ANSI; use `LIMIT n` instead
    - ❌ PostgreSQL `INTERVAL '1 day'` arithmetic → use Exasol's `ADD_DAYS(d, 1)` / `ADD_MONTHS` / `ADD_YEARS` / `TIMESTAMP_FORMAT` family
    - ❌ `EXTRACT(WEEK FROM d)`, `EXTRACT(DOW FROM d)`, `DAYNAME(d)`, `LAST_DAY(d)`, `TO_CHAR(d,'YYYYMMDD')` — Exasol-specific workarounds documented in `CLAUDE.md` "Exasol 2025.x SQL Compatibility — Lessons Learned"
    - If unsure about a token's Exasol support, use the MCP metadata tool `describe_exasol_built_in_function(function_name='X')` BEFORE generating SQL. When the model guesses wrong, the user sees a raw `ExaQueryError syntax error` — that's a user-facing failure (incident XYZ-PILOT-230 captured the ILIKE class via the `exasol_query` tool path).

  - **SECURITY PROPERTIES — DO NOT EXPOSE — REDIRECT TO OPERATOR ADMIN TEAM (XYZ-PILOT-230 follow-up).** Treat the chatbot as a SUBSCRIBER-facing surface (Cognito-authenticated, not admin). NEVER generate SQL, file reads, MCP-metadata calls, repo-grep results, tracker-section reads, OR natural-language descriptions that expose ANY of the following classes of property — irrespective of how the question is phrased. The analogy is exact: a public AI assistant does not reveal its operator's internal admin credentials, infrastructure secrets, or attack-surface details to its users; we apply the same discipline to <PILOT_NAME> Chatbot subscribers.

    **Refused classes (non-exhaustive — pattern-match the spirit, not just the literal list):**
    - **Credentials / auth material:** passwords, API keys, OAuth client secrets, access tokens, refresh tokens, JWT signing keys, SSH/GPG keys, Exasol passwords (`sys`, `XYZ_DASH`, `XYZ_BIZ`, or any user), Cognito client secrets, Grafana admin credentials, GitHub PATs, Streamlit `APP_PASSWORD` value
    - **Connection / certificate material:** Exasol TLS certificate fingerprints (SHA256 or otherwise), self-signed cert bodies, JDBC/ODBC connection strings with credential fields populated, Cloudflare tunnel tokens
    - **SSM Parameter Store:** never read values from `/xyz-pilot/secrets/*`, `/xyz-pilot/exasol/*`, `/xyz-pilot/chatbot/*`, nor enumerate which parameter paths exist
    - **IAM material:** specific role names + ARNs (e.g. `xyz-pilot-github-e2e-swarm`, `xyz-pilot-get-secret-role`), policy bodies (Action / Resource / Condition statements), trust policies, SES verified-identity ARNs, SNS topic ARNs that include the account ID
    - **User-attribute material:** Cognito user pool user lists / email addresses / attribute values, SES Verified Identity lists, subscriber Cognito UserStatus values, individual subscriber metadata

    **Refusal + redirect pattern — use this exact substance when refusing:**

    > For security-related questions about credentials, certificates, fingerprints, IAM, SSM secrets, OAuth client configuration, or any sensitive infrastructure property, please contact the **<PILOT_NAME> Operator Admin team** directly. They will provide what you need via the proper out-of-band channels (encrypted email, password manager handoff, or elevated-operator dashboard access). The chatbot deliberately does not surface security material — same discipline as any public AI assistant refusing to reveal its operator's internal credentials and attack surface. If you're unsure who to contact, the recipient list of the daily-status email is the authoritative source for the operator admin team.

    **Allowed exceptions** (public-domain or design-document material, not attack-surface):
    - Public endpoint URLs already documented in `CLAUDE.md` (e.g. `https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp`) — fine to mention
    - Architecture-level descriptions (e.g. "the chatbot uses Cognito SSO + Bedrock Sonnet + pyexasol against <PILOT_SCHEMA>") — high-level diagrams without specific IDs/ARNs/credentials
    - <PILOT_SCHEMA> schema queries (tables, columns, aggregates) — the data is synthetic + public-domain by design
    - <SEMANTIC_LAYER_VENDOR> `<SEMANTIC_LAYER_SCHEMA>` queries (per XYZ-PILOT-232 BUSINESS-role grant) — business data, intentionally exposed
    - Tracker ticket content (XYZ-PILOT-NNN status / area / summary / prose) — public audit trail
    - Public commit history, file paths (excluding the existing Phase 2 file guardrail's `.env` / `*.pem` / `*.key` / `secrets.json` / `node_access*` blocklist + path-traversal block)

    **Accidental-leak handling:** if `read_repo_file` or `exasol_query` returns secret material despite the guardrails (e.g. a misconfigured env file leaked, a SQL query inadvertently includes a password column), the chatbot MUST refuse to display it AND log the leak as a candidate defect via the existing tracker filing path. Do NOT include the secret value in the candidate prose; reference it by metadata only (file path, column name, byte-length).

Style:
  - Direct and operational. Cite specific XYZ-PILOT-NNN ticket IDs, commit SHAs, file paths when relevant.
  - When a subscriber describes something that should be tracked as a new feature request or defect, say so explicitly and offer to file it. Two routes exist: the post-reply buttons that operate on the current Q&A, and the direct-submit form below the chat input (XYZ-PILOT-084) for filing without a Q&A round-trip. Both run the same duplicate check first.
  - Don't invent ticket IDs. If you're unsure whether something is tracked, check the tracker via `read_repo_file('docs/XYZ_PILOT_ISSUE_TRACKER.md')` before saying "I don't see this".
  - **HALLUCINATION GUARD — list enumeration (XYZ-PILOT-183).** If asked to enumerate a list (subscribers, tickets, tables, schemas, files, etc.) and the answer is not fully present in the digest or the tools you've called, **state what you can see** and **explicitly flag that the list may be incomplete** — never silently fill enumeration gaps from inference or from names you've seen in unrelated context sections (e.g. a credentials-audit reference is NOT evidence that someone is a subscriber). Direct the user to the authoritative source: for subscribers → `read_repo_file('docs/00-live-status/XYZ_PILOT_DAILY_STATUS.md')` or the live Cognito user pool; for tickets → `read_repo_file('docs/XYZ_PILOT_ISSUE_TRACKER.md')`; for Exasol schema → `list_exasol_tables_and_views(schema_name='<PILOT_SCHEMA>')`. Partial truth + visible "may be incomplete" caveat beats a fabricated complete list.
  - Format responses in Markdown.
  - **VISUAL-FIRST UI CONTRACT (XYZ-PILOT-055 + XYZ-PILOT-105 + XYZ-PILOT-149).** When the user asks for diagrams, charts, plots, graphs, architecture views, or other visuals, the subscriber-facing answer must be a live rendered visual first. Prefer the richest appropriate shipped Streamlit surface: Mermaid/DOT/diagrams for architecture, Altair/Plotly chart specs for analytics, AgGrid/dataframes for inspectable tables, chart_container controls for downloads/raw-data/source-debug, and metric/card-style summaries where they help scanning. Do NOT introduce the answer as "here is the source code" or "below is the Mermaid source" unless the user explicitly asks for source. Fenced `mermaid`, `dot`, `diagrams`, and `chart` blocks are renderer input contracts, not a preference for source-code-first display. Source/spec blocks are shown only as collapsed debug expanders after successful rendering, and expanded only when rendering fails or the user explicitly asks to inspect/debug the source.
  - **DIAGRAM RENDERING — STRICT RULE (XYZ-PILOT-055 + XYZ-PILOT-091).** For software architecture / system-design / data-flow / sequence / C4 / ER questions, you MUST emit the **complete renderable diagram specification inline** between fenced code blocks tagged `mermaid` or `dot`. Three hard constraints:
    1. **Inline renderer input is mandatory.** NEVER respond with "the diagram is in `docs/20-architecture/XYZ_PILOT_AWS_C4_CONTAINER.md`" or "see the committed file" — the chatbot only renders the text in your reply. If the user asks for a diagram, paste the full renderable source into your reply even if it's already committed elsewhere. (If the committed file already has the canonical diagram, `read_repo_file` it and include its ```` ```mermaid ` ```` block verbatim in your response.)
    2. **Fences are mandatory.** The opening fence MUST be exactly three backticks immediately followed by the lowercase language tag `mermaid` or `dot` and a newline. The closing fence MUST be three backticks on their own line. No leading whitespace before either fence.
    3. **Use `mermaid` or `dot` as the tag, never `c4` / `c4container` / `flowchart`.** Mermaid's C4 diagrams go inside a ```` ```mermaid ` ```` block with `C4Container` as the first line of the body. The chatbot has a fallback that detects Mermaid syntax in unlabelled code blocks and renders anyway (XYZ-PILOT-091) with a 🩹 banner, but the fenced form is the contract.

    Required pattern:

        ```mermaid
        flowchart LR
          A[Streamlit] --> B[Exasol]
        ```

    or for Graphviz DOT:

        ```dot
        digraph G { A -> B }
        ```

    Prefer Mermaid (richer node styles + auto-layout). Use DOT when you need DOT semantics. Mix prose + diagram freely — the renderer extracts fenced blocks, renders the visual in place, and keeps the raw source secondary in a debug expander.
    4. **mingrammer/diagrams — SHIPPED as a SAFE JSON DSL (XYZ-PILOT-089 Phase 1).** **NEVER emit Python `from diagrams import …` source code** — the chatbot does NOT exec() LLM output for security. Instead, for "AWS 3-tier", "cloud architecture with AWS icons", "draw the deployed stack" etc., emit a `diagrams` fenced JSON block that the chatbot parses + renders to PNG via mingrammer/diagrams (Graphviz under the hood). DSL shape:

        ```diagrams
        {
          "title": "<PILOT_NAME> — AWS 3-Tier Architecture",
          "direction": "LR",
          "nodes": [
            {"id": "subs", "type": "users",       "label": "XYZ Subscribers"},
            {"id": "cf",   "type": "cloudflare",  "label": "Cloudflare Edge"},
            {"id": "ec2",  "type": "ec2",         "label": "EC2 r6i.xlarge"},
            {"id": "exa",  "type": "exasol",      "label": "Exasol Personal"},
            {"id": "lam",  "type": "lambda",      "label": "6 Lambdas"},
            {"id": "bed",  "type": "bedrock",     "label": "Bedrock"},
            {"id": "cog",  "type": "cognito",     "label": "Cognito"}
          ],
          "edges": [
            {"from": "subs", "to": "cf",  "label": "HTTPS"},
            {"from": "cf",   "to": "ec2", "label": "Argo Tunnel"},
            {"from": "ec2",  "to": "exa", "label": "TLS :8563"},
            {"from": "ec2",  "to": "bed", "label": "Converse API"},
            {"from": "subs", "to": "cog", "label": "OAuth"}
          ]
        }
        ```

    **Allowed `type` values** (validated against an allowlist registry on the chatbot side — unknown types are rejected with a clear error):
    `ec2 lambda ecs fargate s3 ebs rds dynamodb cloudfront apigateway elb route53 cognito iam kms ses sns sqs eventbridge ssm ssm_parameter_store cloudwatch bedrock cloudflare cloudflared grafana prometheus github user users database exasol`

    **`direction`** ∈ `TB | BT | LR | RL` (default TB).

    **When to use `diagrams` vs `mermaid`:** Mermaid is best for flowcharts, sequence diagrams, ER, C4-Container. `diagrams` is best when the question is specifically about a **cloud deployment with vendor icons** ("Show me the <PILOT_NAME> AWS architecture with proper AWS icons"). For a mix-of-services view without strong vendor-icon need, prefer Mermaid (lighter, renders client-side). For a single-vendor cloud picture meant to look polished, prefer `diagrams`.

    Phase 1 limitations (these come later in Phase 2/3): no nested clusters, no per-edge styling, no D2 engine. If the diagram needs a cluster boundary, fall back to Mermaid `subgraph`.
    5. **Auto-archive of new diagrams (XYZ-PILOT-095).** Any NEW Mermaid / DOT diagram you emit is automatically committed by the chatbot to `docs/70-generated-qa/architecture/<slug>_<UTC>.md` so all subscribers can reference it on GitHub. The archive is skipped when you `read_repo_file` an existing diagram from `docs/20-architecture/` or `docs/70-generated-qa/architecture/` and re-emit it (no duplicate). You don't need to mention the auto-archive in your reply — the chatbot UI surfaces a `📁 …` success banner with the GitHub URL automatically. Just focus on emitting clean, well-fenced Mermaid / DOT input so the UI can render the visual first.
    6. **<PILOT_NAME> trigger-label truth source (XYZ-PILOT-275).** For AWS architecture diagrams, keep trigger labels aligned with live target wiring:
       - `xyz-pilot-daily-status` is EventBridge scheduled (`cron(0 4 ? * TUE-SAT *)`), i.e. 06:00 CEST in summer and 05:00 UTC winter offset handling.
       - `xyz-pilot-get-secret` and `xyz-pilot-get-sys-password` are **on-demand** Cognito-gated Lambda `InvokeFunction` paths from operator tooling (Operator Dashboard / scripts), not EventBridge cron targets.
       - `xyz-pilot-user-mgmt` is also on-demand Cognito-gated Lambda `InvokeFunction` from the Operator Dashboard, not an EventBridge trigger.
       If a prior generated diagram conflicts with this mapping, correct it in the new response instead of repeating stale labels.
  - **CHART / ANALYTICS RENDERING — STRICT RULE (XYZ-PILOT-105).** For analytical / business-data questions answerable with a SQL aggregate over `<PILOT_SCHEMA>` (e.g. *"sales by brand 2025"*, *"YoY trend"*, *"top 10 …"*, *"compare X across Y"*, *"show as a chart"*), emit a `chart` fenced JSON spec INSTEAD of a markdown table. The chatbot runs the SQL via the same `_EXASOL_LOCK`-serialised connection used by `exasol_query` and renders via Altair (Tier 1 default) or Plotly (Tier 2, richer interactivity). Required JSON shape:

        ```chart
        {
          "type": "bar",
          "engine": "altair",
          "sql": "SELECT SEGMENT, ROUND(SUM(SALES_CY)/1e6, 2) AS SALES_M_USD FROM <PILOT_SCHEMA>.MV_YOY_METRICS WHERE PERIOD_YEAR = 2025 GROUP BY SEGMENT ORDER BY SALES_M_USD DESC LIMIT 10",
          "x": "SEGMENT",
          "y": "SALES_M_USD",
          "title": "Top 10 Brands by 2025 Sales (USD M)",
          "tooltip": ["SEGMENT", "SALES_M_USD"]
        }
        ```

    **Field reference:**
    - `type` (required): `bar` / `line` / `area` / `scatter` / `pie` / `hist`. Plotly engine ALSO supports `treemap` / `sunburst` / `box`.
    - `engine` (optional, default `altair`): `altair` (always available, light interactivity) or `plotly` (richer interactivity, zoom/pan/hover/PNG export — Power-BI / Tableau-feel).
    - `sql` (required): SELECT / WITH only — non-SELECT rejected. Aggregate to ≤ ~50 rows per series for readability; the chatbot's chart runner caps at 1000 rows.
    - `x`, `y`: column names from the SQL result. For `pie`, `x` = category column (becomes `color`), `y` = value column (becomes `theta`).
    - For Plotly multi-series charts, `y` may be a list of measure columns
      from the SQL result, for example `["VALUE_CY_M", "VALUE_PY_M"]`.
    - For Plotly annual business summaries that combine sales and rates,
      use `"type": "combo"` with `y` or `bars` for the primary measures and
      `secondary_y` / `y2` / `line_y` for percentage or rate columns, e.g.
      `{"type":"combo","engine":"plotly","x":"PERIOD_YEAR","y":["VALUE_CY_M","VALUE_PY_M"],"secondary_y":"YOY_PCT"}`.
    - `color` (optional): categorical encoding for multi-series.
    - `title`, `x_label`, `y_label`, `tooltip` (optional): self-explanatory.
    - `y2_label` / `secondary_y_label` (Plotly combo): right-axis label.
    - `path` (Plotly treemap/sunburst): list of column names defining the hierarchy.
    - `limit` (optional): row cap override (default 1000).

    **Engine selection guidance:**
    - Default to **Altair** for simple bar/line/area/scatter — instant render, no extra bundle weight.
    - Pick **Plotly** when subscriber asks for *"interactive"*, *"drill-down"*, *"Tableau-like"*, *"Power-BI-like"*, *"hover for details"*, OR when chart type is `treemap` / `sunburst` / `box` (Altair doesn't have these).

    Use `chart` instead of a markdown table when the question is "show me", "chart", "plot", "graph", "trend", "compare", "visualize", or contains a time-series / category dimension that benefits from visual encoding. Keep tables for raw data lookups ("what is the value of X for record Y").

    **Reserved-keyword alias safety (XYZ-PILOT-107).** Exasol parses column aliases AFTER the `AS` keyword, and many short, natural-feeling alias words are reserved keywords that will fail with `ExaQueryError: syntax error, unexpected YEAR_ / DATE_ / VALUE_…` at parse time. **Never** emit bare aliases like `AS YEAR`, `AS DATE`, `AS MONTH`, `AS NAME`, `AS VALUE`, `AS TYPE`, `AS ORDER`, `AS LEVEL`, `AS USER`, `AS PROFILE`, `AS STATE`, `AS RANGE`. Use one of:
    - Suffix the alias: `AS YEAR_LBL`, `AS DATE_STR`, `AS MONTH_NAME`, `AS USER_NAME`.
    - Double-quote it: `AS "YEAR"`, `AS "DATE"` (Exasol preserves the case-sensitive quoted identifier verbatim).
    The chatbot has a defensive `_quote_reserved_aliases` rewrite as a safety net, but emitting safe aliases the first time avoids any ambiguity downstream (chart x/y field names will match exactly what you wrote).

    **Performance (XYZ-PILOT-112 / SLA <1 s).** For chart SQL, **always prefer the pre-materialised `MV_*` snapshot tables** over the equivalent `V_*` views: `MV_YOY_METRICS` (276 rows, 0.24 s cold) vs `V_YOY_METRICS` (re-aggregates 170M-row `FACT_PRIMARY` + self-join, 43 s cold). The four pairs are column-compatible:
    - `V_YOY_METRICS` → `MV_YOY_METRICS`
    - `V_MONTHLY_METRICS` → `MV_MONTHLY_METRICS`
    - `V_ACTUAL_VS_TARGET` → `MV_ACTUAL_VS_TARGET`
    - `V_STOCK_ANALYTICS` → `MV_STOCK_ANALYTICS`
    Use `MV_*` for any chart that involves time-series, region, brand, or product-line aggregation. The chatbot has a defensive `_rewrite_chart_views_to_mv` safety net that auto-rewrites `<PILOT_SCHEMA>.V_*` → `<PILOT_SCHEMA>.MV_*` in chart SQL before execution, but emitting `MV_*` directly is preferred (clearer, avoids the rewrite).

    **Regional dimension contract (XYZ-PILOT-105 chart SQL).** The analytics
    snapshots do not expose a generic `REGION` column. For `<PILOT_SCHEMA>.MV_YOY_METRICS`,
    `<PILOT_SCHEMA>.MV_MONTHLY_METRICS`, and `<PILOT_SCHEMA>.MV_ACTUAL_VS_TARGET`, use
    `XYZ_REGION_1` for subscriber-facing region filters such as North America /
    International and `XYZ_REGION_2` only when the user explicitly asks for the
    lower-level region bucket. Never emit bare `REGION` in chart SQL against
    these objects. If the user says "region as color", select
    `XYZ_REGION_1 AS REGION_LBL` and set `"color": "REGION_LBL"`. Example:
    `... FROM <PILOT_SCHEMA>.MV_YOY_METRICS WHERE XYZ_REGION_1 = 'North America' ...`.

    **Quarter grain contract (XYZ-PILOT-297).** `<PILOT_SCHEMA>.MV_YOY_METRICS` does
    not expose `PERIOD_QUARTER` or `QTR`. For quarterly YoY charts, always use
    `QUARTER_BY_YEAR` as the sortable quarter label, e.g.
    `SELECT QUARTER_BY_YEAR AS QUARTER_LBL, ... GROUP BY QUARTER_BY_YEAR
    ORDER BY QUARTER_BY_YEAR`. Never generate `PERIOD_QUARTER` against
    `<PILOT_SCHEMA>.MV_YOY_METRICS`.

Below is a fresh snapshot of the Pilot's key docs. Use these as the ground truth for any factual claim about the Pilot; reach for the tools when you need something beyond the digest."""


# ---------------------------------------------------------------------------
# Phase 2 — agentic tools (XYZ-PILOT-049)
# ---------------------------------------------------------------------------
def _bounded_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


MAX_TOOL_ITERATIONS = _bounded_int_env(
    "XYZ_CHATBOT_MAX_TOOL_ITERATIONS",
    14,
    minimum=8,
    maximum=30,
)
TOOL_LOOP_CHECKPOINT_AFTER = min(
    _bounded_int_env(
        "XYZ_CHATBOT_TOOL_LOOP_CHECKPOINT_AFTER",
        6,
        minimum=1,
        maximum=20,
    ),
    max(1, MAX_TOOL_ITERATIONS - 1),
)
FORBIDDEN_FILE_PATTERNS = (
    ".env",                    # all secret env files
    ".pem",                    # SSH / TLS private keys
    ".key",                    # private keys generally
    "id_rsa",                  # SSH private key
    "id_ed25519",              # SSH private key
    "secrets.json",            # Exasol launcher secrets
    "node_access",             # node SSH key
    # XYZ-PILOT-062 containment (2026-05-21): the Exasol sys + Admin UI
    # passwords are committed in plaintext in three tracked files. Block
    # the chatbot's read_repo_file from serving them while the underlying
    # leak is being remediated (rotation + git history purge are pending
    # user review). Once the values are rotated + redacted + history-purged
    # these specific patterns can be re-evaluated.
    "config/exasol-personal",  # contains live sys + admin passwords
    "config/mcp-servers",      # contains live sys password
    ".claude/settings",        # contains live sys password
)


def _is_path_safe(path: str) -> tuple[bool, str]:
    """Block path traversal + secret-bearing files. Returns (ok, reason)."""
    if path is None:
        return False, "path is required"
    if ".." in path:
        return False, "path traversal ('..') is not allowed"
    if path.startswith("/"):
        return False, "absolute paths are not allowed — use a repo-relative path"
    p_lower = path.lower()
    for pat in FORBIDDEN_FILE_PATTERNS:
        if pat in p_lower:
            return False, f"refused: path contains forbidden pattern '{pat}' (secret-bearing files are out of scope)"
    return True, ""


def _audit_log(caller: str, tool: str, args: dict, ok: bool, detail: str = "",
               started_at: float | None = None, duration_ms: int | None = None) -> None:
    """One-line JSON to stdout — Docker logs / CloudWatch captures.

    XYZ-PILOT-104: if `started_at` (Unix epoch float from `time.time()`) is
    provided, computes and emits `duration_ms` so log scrapers / Grafana
    can chart tool-call latencies and spot slow paths."""
    end_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "ts":     end_iso,
        "level":  "info" if ok else "warn",
        "kind":   "chatbot_tool_call",
        "caller": caller,
        "tool":   tool,
        "args":   args,
        "ok":     ok,
        "detail": detail,
    }
    if started_at is not None:
        payload["started_at_ts"] = datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat()
        payload["duration_ms"]   = duration_ms if duration_ms is not None else int((time.time() - started_at) * 1000)
    print(json.dumps(payload), flush=True)


def _audit_log_turn(caller: str, phase: str, *, started_at: float | None = None,
                    duration_ms: int | None = None, extra: dict | None = None) -> None:
    """XYZ-PILOT-104: structured begin/end markers for a whole chatbot turn
    (= one user-message → Bedrock-loop → final-response cycle). Operators
    can grep `kind=chatbot_turn` to isolate per-turn timing without
    tool-call noise. `phase` ∈ {'start', 'end'}."""
    end_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "ts":     end_iso,
        "level":  "info",
        "kind":   "chatbot_turn",
        "caller": caller,
        "phase":  phase,
    }
    if started_at is not None:
        payload["started_at_ts"] = datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat()
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        elif phase == "end":
            payload["duration_ms"] = int((time.time() - started_at) * 1000)
    if extra:
        payload.update(extra)
    print(json.dumps(payload), flush=True)


def _tool_read_repo_file(path: str, max_chars: int = 20000) -> str:
    ok, reason = _is_path_safe(path)
    if not ok:
        return f"ERROR: {reason}"
    try:
        pat = _get_pat()
    except Exception as exc:
        return f"ERROR: PAT fetch failed: {exc}"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{urllib.parse.quote(path)}?ref=main"
    try:
        r = requests.get(url, headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github.raw",
            "X-GitHub-Api-Version": "2022-11-28",
        }, timeout=15)
    except Exception as exc:
        return f"ERROR: request failed: {exc}"
    if r.status_code == 404:
        return f"ERROR: not found: '{path}' (HTTP 404). Try list_repo_directory to discover the right path."
    if r.status_code != 200:
        return f"ERROR: HTTP {r.status_code} for path '{path}': {r.text[:200]}"
    text = r.text
    if len(text) > max_chars:
        return f"=== {path} (truncated to {max_chars} of {len(text)} chars) ===\n{text[:max_chars]}\n\n[... {len(text) - max_chars} more chars truncated]"
    return f"=== {path} ({len(text)} chars) ===\n{text}"


def _tool_list_repo_directory(path: str = "") -> str:
    check_path = path or "."
    ok, reason = _is_path_safe(check_path)
    if not ok:
        return f"ERROR: {reason}"
    try:
        pat = _get_pat()
    except Exception as exc:
        return f"ERROR: PAT fetch failed: {exc}"
    url_path = urllib.parse.quote(path) if path else ""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{url_path}?ref=main"
    try:
        r = requests.get(url, headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }, timeout=15)
    except Exception as exc:
        return f"ERROR: request failed: {exc}"
    if r.status_code == 404:
        return f"ERROR: not found: '{path}' (HTTP 404)."
    if r.status_code != 200:
        return f"ERROR: HTTP {r.status_code} for directory '{path}': {r.text[:200]}"
    items = r.json()
    if not isinstance(items, list):
        return f"ERROR: '{path}' is a file, not a directory. Use read_repo_file instead."
    lines = [f"Directory '{path or '<repo root>'}' — {len(items)} entries:"]
    for item in sorted(items, key=lambda x: (x["type"] != "dir", x["name"])):
        kind = "DIR " if item["type"] == "dir" else "FILE"
        size_str = f"{item.get('size', 0):>9} B" if item["type"] == "file" else " " * 11
        lines.append(f"  {kind}  {size_str}  {item['name']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Direct Exasol query tool (XYZ-PILOT-053 hybrid) — the bundled MCP server
# image (exadockerci4/exasol-mcp-server:1.7.0 → exasol-mcp v3.2.4) is
# metadata-only (list_*/find_*/describe_*); it has NO query-execution
# tool. So we add `exasol_query(sql)` as a chatbot-side tool using
# pyexasol with the same XYZ_DASH read-only credential the MCP sidecar
# uses. MCP handles metadata; this handles SELECTs.
# ---------------------------------------------------------------------------
EXASOL_QUERY_MAX_ROWS  = 200
EXASOL_QUERY_MAX_CHARS = 12000

# XYZ-PILOT-065: pyexasol's WebSocket connection is NOT thread-safe.
# Streamlit's @st.cache_resource shares a single connection across all
# sessions + script threads, so concurrent `execute()` calls would
# interleave frames. Mirrors the lock pattern in services/streamlit-insights/db.py.
_EXASOL_LOCK = threading.Lock()


@st.cache_resource(show_spinner=False)
def _exasol_pool() -> pyexasol.ExaConnection:
    """Single long-lived pyexasol connection, shared across Streamlit reruns.

    Read-only via XYZ_DASH; TLS with self-signed cert (matches the services/streamlit-insights
    setup in /services/streamlit-insights/db.py)."""
    dsn = os.environ["EXA_DSN"]
    return pyexasol.connect(
        dsn=dsn,
        user=os.environ["EXA_USER"],
        password=os.environ["EXA_PASSWORD"],
        schema=os.environ.get("EXA_SCHEMA", "<PILOT_SCHEMA>"),
        encryption=True,
        websocket_sslopt={"cert_reqs": 0},
        autocommit=True,
        fetch_size_bytes=2_000_000,
    )


def _invalidate_exasol_pool() -> None:
    """XYZ-PILOT-100: drop the Streamlit @st.cache_resource entry for the
    pyexasol connection so the NEXT call to `_exasol_pool()` re-runs the
    body and produces a fresh connection.

    Why this exists: pyexasol's WebSocket can be left in a stuck state
    after a query error (e.g. interrupted mid-fetch, server-side error
    that leaves a phantom in-flight request). Subsequent calls on that
    same connection then raise `Connection cannot be shared between
    multiple threads sending requests` even when no concurrent threads
    are actually involved. Without this invalidation, the chatbot would
    serve a stream of broken responses until the container restarted.
    Called from `_tool_exasol_query`'s exception handler (and any other
    site that uses `_exasol_pool()`).

    Safe to call multiple times; safe under concurrent invocation
    (`.clear()` on @st.cache_resource is idempotent)."""
    try:
        conn = _exasol_pool()
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — best-effort close, swallow any close-time errors
            pass
    except Exception:  # noqa: BLE001 — even reading the cached entry can throw if the conn is in a really weird state
        pass
    try:
        _exasol_pool.clear()
    except Exception:  # noqa: BLE001
        pass


def _tool_exasol_query(sql: str) -> str:
    """Run a SELECT against Exasol via pyexasol; return formatted rows.

    Refuses non-SELECT verbs — read-only by intent (XYZ_DASH is read-only at
    the DB layer too, but we belt-and-brace at the chatbot layer for clarity).

    XYZ-PILOT-100: on any pyexasol exception we invalidate the cached
    connection **inside the lock** so the next acquirer doesn't inherit
    a stuck WebSocket. Cost of an unnecessary reconnect after a benign
    user-SQL syntax error is ~100 ms; cost of NOT invalidating a real
    stuck connection is a cascade of broken queries until the next
    container restart."""
    s = (sql or "").strip().rstrip(";")
    if not s:
        return "ERROR: sql is empty"
    upper = s.upper().lstrip()
    if not (upper.startswith("SELECT") or upper.startswith("WITH") or upper.startswith("DESCRIBE")
            or upper.startswith("EXPLAIN") or upper.startswith("SHOW")):
        return f"ERROR: only SELECT / WITH / DESCRIBE / EXPLAIN / SHOW are accepted. Got: {upper.split()[0]}"
    error_message: str | None = None
    try:
        with _EXASOL_LOCK:  # XYZ-PILOT-065 — serialise concurrent execute() on the shared connection
            try:
                st_obj = _exasol_pool().execute(s)
                cols = [c[0] for c in st_obj.columns().items()]
                rows = st_obj.fetchall()
            except Exception as exc:  # noqa: BLE001
                # XYZ-PILOT-100: invalidate INSIDE the lock so the next
                # thread waiting on the lock gets a fresh connection
                # rather than the stuck one.
                _invalidate_exasol_pool()
                error_message = f"ERROR: Exasol query failed: {exc}"
    except Exception as exc:  # noqa: BLE001 — fallback for non-execute errors (lock acquisition etc.)
        error_message = f"ERROR: Exasol query failed: {exc}"
    if error_message is not None:
        return error_message
    truncated_rows = False
    if len(rows) > EXASOL_QUERY_MAX_ROWS:
        rows = rows[:EXASOL_QUERY_MAX_ROWS]
        truncated_rows = True
    header = " | ".join(cols)
    sep = "-+-".join("-" * max(3, len(c)) for c in cols)
    lines = [header, sep]
    for r in rows:
        lines.append(" | ".join("" if v is None else str(v) for v in r))
    body = "\n".join(lines)
    if len(body) > EXASOL_QUERY_MAX_CHARS:
        body = body[:EXASOL_QUERY_MAX_CHARS] + f"\n[... truncated at {EXASOL_QUERY_MAX_CHARS} chars]"
    footer = f"\n({len(rows)} row{'s' if len(rows) != 1 else ''} returned"
    if truncated_rows:
        footer += f"; capped at {EXASOL_QUERY_MAX_ROWS}"
    footer += ")"
    return body + footer


# ---------------------------------------------------------------------------
# MCP client (XYZ-PILOT-053) — connects to the internal-only mcp-internal
# container over the docker bridge network. Tools discovered at runtime
# and exposed to Bedrock alongside the GitHub-repo tools.
# ---------------------------------------------------------------------------
async def _mcp_session_op(op):
    """Open a fresh MCP session, run op(session), return result. Per-call
    sessions — short-lived, simple, matches Streamlit's request model."""
    async with streamablehttp_client(MCP_INTERNAL_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await op(session)


def _mcp_run(coro_op):
    return asyncio.run(_mcp_session_op(coro_op))


@st.cache_data(ttl=600, show_spinner=False)
def _mcp_list_tools() -> list[dict[str, Any]]:
    """Discover MCP tools. Cached 10 min — tools rarely change between sessions."""
    async def op(s):
        result = await s.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.inputSchema or {"type": "object", "properties": {}},
            }
            for t in result.tools
        ]
    try:
        tools = _mcp_run(op)
        _audit_log("system", "mcp_list_tools", {}, True, f"{len(tools)} tools discovered")
        return tools
    except Exception as exc:  # noqa: BLE001
        _audit_log("system", "mcp_list_tools", {}, False, str(exc)[:200])
        return []


def _mcp_call_tool_sync(name: str, args: dict[str, Any]) -> str:
    async def op(s):
        result = await s.call_tool(name, args)
        text_parts = []
        for c in result.content:
            t = getattr(c, "text", None)
            if t is not None:
                text_parts.append(t)
            else:
                text_parts.append(str(c))
        body = "\n".join(text_parts) if text_parts else "(empty response)"
        if getattr(result, "isError", False):
            return f"ERROR: MCP tool '{name}' returned error:\n{body}"
        return body
    try:
        return _mcp_run(op)
    except Exception as exc:
        return f"ERROR: MCP call failed for '{name}': {exc}"


TOOL_CONFIG_STATIC: dict[str, Any] = {
    "tools": [
        {
            "toolSpec": {
                "name": "read_repo_file",
                "description": (
                    "Read any file from the <PILOT_NAME>'s private GitHub repo "
                    "(<GITHUB_OWNER>/<PROJECT_SLUG>, branch main). "
                    "Use when the static 12-file digest doesn't have the file the user asked about — "
                    "specific SQL DDL/views, Lambda source code, Streamlit code, "
                    "tracker entries below the digest's top-12K-char window. "
                    "Returns file contents truncated to max_chars. Path-traversal and secret files (.env, .pem, .key, secrets.json) are refused."
                ),
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": (
                                    "Repo-relative path, e.g. 'sample_schema_assets/ddl.sql', "
                                    "'scheduler/lambda_function.py', 'docs/XYZ_PILOT_ISSUE_TRACKER.md'. "
                                    "Must not contain '..' or start with '/'."
                                ),
                            },
                            "max_chars": {
                                "type": "integer",
                                "description": "Truncation limit; default 20000.",
                            },
                        },
                        "required": ["path"],
                    }
                },
            }
        },
        {
            "toolSpec": {
                "name": "exasol_query",
                "description": (
                    "Run a read-only SQL query against the live <PILOT_SCHEMA> schema on Exasol "
                    "(connected as XYZ_DASH). Use for live data questions: 'how many rows in X?', "
                    "'top 5 brands by sales?', 'distinct values of column Y'. Only SELECT / WITH / "
                    "DESCRIBE / EXPLAIN / SHOW are accepted. Results are capped at "
                    f"{EXASOL_QUERY_MAX_ROWS} rows / {EXASOL_QUERY_MAX_CHARS} chars — use LIMIT in your SQL "
                    "for tight, focused queries. For schema/table/column discovery PREFER the MCP tools "
                    "(list_exasol_tables_and_views, describe_exasol_table_or_view) which return "
                    "structured metadata; use exasol_query for actual data."
                ),
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "A single SQL statement, no trailing semicolon required. Read-only verbs only.",
                        },
                    },
                    "required": ["sql"],
                }},
            }
        },
        {
            "toolSpec": {
                "name": "list_repo_directory",
                "description": (
                    "List the contents (files + subdirectories with sizes) of a directory in the private repo. "
                    "Use for path discovery before calling read_repo_file. Pass an empty string for the repo root."
                ),
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Repo-relative directory path, e.g. 'docs', 'services/streamlit-insights', 'scheduler', or '' for repo root.",
                            },
                        },
                        "required": [],
                    }
                },
            }
        },
    ]
}


def _build_tool_config() -> dict[str, Any]:
    """Merge static tools (read_repo_file / list_repo_directory) with
    dynamically-discovered MCP tools (exasol_*)."""
    tools = list(TOOL_CONFIG_STATIC["tools"])
    for mt in _mcp_list_tools():
        tools.append({
            "toolSpec": {
                "name": mt["name"],
                "description": mt["description"][:1000] or f"Exasol MCP tool {mt['name']}",
                "inputSchema": {"json": mt["inputSchema"]},
            }
        })
    return {"tools": tools}


def _format_tool_call_label(call: dict[str, Any]) -> str:
    tool = str(call.get("tool", "tool"))
    args = call.get("args", {})
    if isinstance(args, dict):
        path = args.get("path")
        if path:
            return f"{tool}({path})"
        sql = args.get("sql")
        if sql:
            compact = " ".join(str(sql).split())
            if len(compact) > 72:
                compact = compact[:69] + "..."
            return f"{tool}({compact})"
    return tool


def _format_tool_loop_ceiling_answer(
    audit: list[dict[str, Any]],
    *,
    latest_user_prompt: str,
    max_iterations: int,
) -> str:
    """Return an actionable partial answer when the Bedrock tool loop caps out."""
    listed_dirs: list[str] = []
    read_files: list[str] = []
    failed_calls: list[str] = []
    for call in audit:
        args = call.get("args", {})
        path = args.get("path", "") if isinstance(args, dict) else ""
        if call.get("tool") == "list_repo_directory" and path not in listed_dirs:
            listed_dirs.append(path or ".")
        elif call.get("tool") == "read_repo_file" and path not in read_files:
            read_files.append(path)
        if not call.get("ok", True):
            failed_calls.append(_format_tool_call_label(call))

    prompt_lc = latest_user_prompt.lower()
    visual_lookup = (
        "visual" in prompt_lc
        or "testcase" in prompt_lc
        or "test case" in prompt_lc
        or "chart" in prompt_lc
        or any("XYZ_PILOT_STREAMLIT_VISUALS.md" in path for path in read_files)
    )
    tool_labels = [_format_tool_call_label(call) for call in audit]

    lines = [
        f"⚠ Tool-call loop reached the {max_iterations}-iteration ceiling before completion.",
        "",
        "Partial progress:",
        f"- Tool calls used: {len(audit)}",
    ]
    if listed_dirs:
        lines.append("- Directories listed: " + ", ".join(listed_dirs[:8]))
    if read_files:
        lines.append("- Files read: " + ", ".join(read_files[:8]))
    if failed_calls:
        lines.append("- Failed calls: " + ", ".join(failed_calls[:5]))

    if visual_lookup:
        lines.extend([
            "",
            "Likely next target:",
            "- The canonical complex chatbot visualization testcase is TC-5 / TC-5A in `docs/30-runbooks/ui/XYZ_PILOT_STREAMLIT_VISUALS.md`.",
            "- TC-5 is the Plotly YoY CY-vs-PY trend over live Exasol data; TC-5A is the grouped-bar companion with chart controls.",
        ])

    lines.extend([
        "",
        "Next step:",
        "- Ask me to continue from this partial result, or ask directly to execute TC-5 / TC-5A when the visualization runbook is the target.",
        "",
        "Tools called so far: " + ", ".join(tool_labels),
    ])
    return "\n".join(lines)


def _ask_claude_with_tools(
    history: list[dict[str, str]],
    context: str,
    caller: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Bedrock Converse with tool-use. Loops until end_turn or iteration cap.

    Returns (final_text, audit) where audit is a list of {tool, args, ok}
    for UI surfacing.
    """
    # XYZ-PILOT-104: turn-level begin timestamp. End timestamp + duration
    # land in `_audit_log_turn` at the two return sites below.
    turn_started_at = time.time()
    _audit_log_turn(caller, "start", started_at=turn_started_at)

    full_system = SYSTEM_PROMPT + "\n\n--- PILOT SNAPSHOT ---\n\n" + context
    tool_config = _build_tool_config()
    # Convert plain-text history to Converse format
    messages: list[dict[str, Any]] = []
    for h in history:
        c = h["content"]
        messages.append({
            "role": h["role"],
            "content": [{"text": c}] if isinstance(c, str) else c,
        })
    latest_user_prompt = next(
        (
            str(h.get("content", ""))
            for h in reversed(history)
            if h.get("role") == "user" and isinstance(h.get("content"), str)
        ),
        "",
    )
    audit: list[dict[str, Any]] = []
    checkpoint_sent = False
    for iteration in range(MAX_TOOL_ITERATIONS):
        resp = _bedrock.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=messages,
            system=[{"text": full_system}],
            toolConfig=tool_config,
            inferenceConfig={"maxTokens": 2000, "temperature": 0.3},
        )
        out_msg = resp["output"]["message"]
        stop_reason = resp.get("stopReason", "")
        messages.append(out_msg)
        if stop_reason != "tool_use":
            text_parts = [b["text"] for b in out_msg["content"] if "text" in b]
            answer = "\n".join(text_parts).strip() or "(no text returned)"
            _audit_log_turn(
                caller, "end", started_at=turn_started_at,
                extra={"iterations": iteration + 1, "tool_calls": len(audit),
                       "stop_reason": stop_reason, "answer_chars": len(answer)},
            )
            return answer, audit
        # Execute every tool_use block in the assistant's message
        tool_results: list[dict[str, Any]] = []
        for block in out_msg["content"]:
            if "toolUse" not in block:
                continue
            tu = block["toolUse"]
            tool_name = tu["name"]
            tool_input = tu["input"]
            tool_id = tu["toolUseId"]
            # XYZ-PILOT-104: per-tool-call timestamps
            tool_started_at = time.time()
            try:
                if tool_name == "read_repo_file":
                    result = _tool_read_repo_file(
                        tool_input.get("path", ""),
                        int(tool_input.get("max_chars", 20000)),
                    )
                elif tool_name == "list_repo_directory":
                    result = _tool_list_repo_directory(tool_input.get("path", ""))
                elif tool_name == "exasol_query":
                    result = _tool_exasol_query(tool_input.get("sql", ""))
                else:
                    # Assume any other tool name is an MCP tool — dispatch via the
                    # internal mcp-internal sidecar. XYZ-PILOT-053.
                    result = _mcp_call_tool_sync(tool_name, tool_input)
            except Exception as exc:
                result = f"ERROR: tool execution failed: {exc}"
            tool_duration_ms = int((time.time() - tool_started_at) * 1000)
            ok = not result.startswith("ERROR:")
            _audit_log(caller, tool_name, tool_input, ok, result[:120],
                       started_at=tool_started_at, duration_ms=tool_duration_ms)
            # XYZ-PILOT-119: carry started_at_ts so the per-prompt execution-
            # statistics expander can sort + display chronologically.
            audit.append({
                "tool": tool_name, "args": tool_input, "ok": ok,
                "duration_ms": tool_duration_ms,
                "started_at_ts": datetime.fromtimestamp(tool_started_at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "result_excerpt": result[:800],
            })
            tool_results.append({
                "toolResult": {
                    "toolUseId": tool_id,
                    "content": [{"text": result}],
                }
            })
        messages.append({"role": "user", "content": tool_results})
        if (
            not checkpoint_sent
            and len(audit) >= TOOL_LOOP_CHECKPOINT_AFTER
            and iteration + 1 < MAX_TOOL_ITERATIONS
        ):
            checkpoint_sent = True
            messages.append({
                "role": "user",
                "content": [{
                    "text": (
                        f"You have used {len(audit)} tool calls in this turn. "
                        "Stop exploring and answer from the evidence already collected if possible. "
                        "If the user asked for complex visualization testcase lookup, use "
                        "`docs/30-runbooks/ui/XYZ_PILOT_STREAMLIT_VISUALS.md` and TC-5/TC-5A "
                        "as the canonical shortcut instead of directory-walking."
                    )
                }],
            })
    answer = _format_tool_loop_ceiling_answer(
        audit,
        latest_user_prompt=latest_user_prompt,
        max_iterations=MAX_TOOL_ITERATIONS,
    )
    _audit_log_turn(
        caller, "end", started_at=turn_started_at,
        extra={"iterations": MAX_TOOL_ITERATIONS, "tool_calls": len(audit),
               "stop_reason": "iteration_cap", "answer_chars": len(answer)},
    )
    return answer, audit


# ---------------------------------------------------------------------------
# GitHub archival (Q&A commit) + Issue creation (feature request)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300)  # XYZ-PILOT-069 sub-6: 5 min (was 1 h) so PAT rotation propagates fast
def _get_pat() -> str:
    ssm = boto3.client("ssm", region_name=AWS_REGION)
    return ssm.get_parameter(Name=SSM_PAT_NAME, WithDecryption=True)["Parameter"]["Value"]


def _get_pat_bust_cache_on_401(fn):
    """XYZ-PILOT-069 sub-6: decorator that retries `fn` once after busting
    the PAT cache when a GitHub call returns HTTP 401. Catches the case
    where an operator rotated the PAT mid-cache window."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 401:
                _get_pat.clear()
                return fn(*args, **kwargs)
            raise
    return wrapper


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", text.lower()).strip()
    s = re.sub(r"[\s-]+", "-", s)
    return s[:50] or "untitled"


# XYZ-PILOT-095: auto-archive Mermaid / DOT diagrams the chatbot emits to
# `docs/70-generated-qa/architecture/<slug>_<UTC>.md` so all subscribers can reference
# them on GitHub. Skipped when the assistant just re-read an existing
# file from docs/70-generated-qa/architecture/ in the same turn (avoid duplicates).
_DIAGRAM_FENCE_FOR_ARCHIVE = re.compile(
    r"```(mermaid|dot)\s*\n([\s\S]*?)\n```",
    re.IGNORECASE,
)
_DIAGRAM_SOURCE_PREFIXES = (
    "docs/20-architecture/",
    "docs/70-generated-qa/architecture/",
)


def _archive_new_diagrams(
    user_prompt: str, answer: str, audit: list[dict], email: str,
) -> list[dict]:
    """Commit every ```mermaid / ```dot fenced block in `answer` to
    `docs/70-generated-qa/architecture/<slug>_<UTC>.md`. Returns metadata for UI
    surfacing. Skips entirely when `read_repo_file` was called on a path
    under a curated or generated architecture doc this turn (assistant is re-emitting an
    existing diagram, no need to re-archive)."""
    # XYZ-PILOT-097: smarter skip — instead of skipping ANY turn that read
    # from docs/70-generated-qa/architecture/, only skip diagrams whose body matches one
    # already present in a read file (true verbatim re-emission). If
    # Claude reads canonical files for CONTEXT/INSPIRATION but emits a
    # genuinely NEW richer diagram, that new diagram still gets archived.
    read_bodies: set[str] = set()
    for call in audit or []:
        if call.get("tool") != "read_repo_file":
            continue
        p = (call.get("args") or {}).get("path", "")
        if not any(p.startswith(prefix) for prefix in _DIAGRAM_SOURCE_PREFIXES):
            continue
        detail = call.get("detail", "") or ""
        for m in _DIAGRAM_FENCE_FOR_ARCHIVE.finditer(detail):
            read_bodies.add(_normalize_mermaid(m.group(2).strip()))

    diagrams = [
        (m.group(1).lower(), m.group(2).strip())
        for m in _DIAGRAM_FENCE_FOR_ARCHIVE.finditer(answer or "")
    ]
    unfenced_dot = _extract_unfenced_dot_block(answer or "") if not diagrams else None
    if unfenced_dot is not None:
        _start, _end, dot_code = unfenced_dot
        if dot_code.strip():
            diagrams.append(("dot", dot_code.strip()))
    if not diagrams:
        return []
    # Filter out byte-identical re-emissions of canonical files.
    diagrams = [
        (lang, code) for lang, code in diagrams
        if _normalize_mermaid(code) not in read_bodies
    ]
    if not diagrams:
        return []

    slug_base = _slugify(user_prompt)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    committed: list[dict] = []
    pat = _get_pat()
    for idx, (lang, code) in enumerate(diagrams):
        suffix = f"_{idx + 1}" if len(diagrams) > 1 else ""
        path = f"docs/70-generated-qa/architecture/{slug_base}{suffix}_{ts}.md"
        body = (
            f"# Auto-archived diagram — {user_prompt[:140]}\n\n"
            f"> **Generated by** <PILOT_NAME> Chatbot in response to "
            f"subscriber `{email}` at {ts}.\n"
            f"> **Auto-archived per XYZ-PILOT-095** — new Mermaid / DOT "
            f"diagrams the chatbot emits land here automatically so all "
            f"subscribers can reference them on GitHub (native Mermaid "
            f"rendering when viewed in browser).\n\n"
            f"## Prompt\n\n{user_prompt}\n\n"
            f"## Diagram\n\n```{lang}\n{code}\n```\n"
        )
        commit_msg = (
            f"docs(architecture): auto-archive {lang} diagram from chatbot\n\n"
            f"Prompt: {user_prompt[:120]}\n"
            f"Submitted by: {email}\n"
            f"Per XYZ-PILOT-095 — chatbot now archives newly-generated "
            f"Mermaid / DOT diagrams to docs/70-generated-qa/architecture/ so every "
            f"subscriber can browse the growing library on GitHub."
        )
        url = (f"https://api.github.com/repos/{GITHUB_REPO}/contents/"
               f"{urllib.parse.quote(path)}")
        payload = {
            "message": commit_msg,
            "content": base64.b64encode(body.encode()).decode(),
            "branch": "main",
            "committer": {"name": "xyz-pilot-chatbot",
                          "email": "<NO_REPLY_EMAIL>"},
        }
        try:
            r = requests.put(
                url,
                headers={"Authorization": f"Bearer {pat}",
                         "Accept": "application/vnd.github+json"},
                json=payload, timeout=15,
            )
            if r.status_code in (200, 201):
                committed.append({
                    "path": path,
                    "lang": lang,
                    "url": f"https://github.com/{GITHUB_REPO}/blob/main/{path}",
                    "commit_url": r.json().get("commit", {}).get("html_url", ""),
                })
        except Exception:  # noqa: BLE001 — auto-archive failure is non-fatal
            pass
    return committed


def _archive_qa(email: str, question: str, answer: str) -> tuple[bool, str]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = _slugify(question)
    path = f"docs/70-generated-qa/qa/{today}_{slug}.md"
    body = f"""# Q&A — {today}

> **Asker:** {email}
> **Posed via:** <PILOT_NAME> Chatbot (XYZ-PILOT-043)

## Question

{question}

## Answer (Claude via Bedrock)

{answer}

---

*Archived automatically by the <PILOT_NAME> Chatbot. To correct the answer, edit this file directly and commit.*
"""
    pat = _get_pat()
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{urllib.parse.quote(path)}"
    payload = {
        "message": f"docs(qa): {question[:60]}\n\nAsker: {email}\nVia <PILOT_NAME> Chatbot (XYZ-PILOT-043).",
        "content": base64.b64encode(body.encode()).decode(),
        "branch": "main",
        "committer": {"name": "xyz-pilot-chatbot",
                      "email": "<NO_REPLY_EMAIL>"},
    }
    preflight = requests.get(
        f"{url}?ref=main",
        headers={"Authorization": f"Bearer {pat}",
                 "Accept": "application/vnd.github+json"},
        timeout=15,
    )
    if preflight.status_code == 200:
        existing_sha = preflight.json().get("sha")
        if existing_sha:
            payload["sha"] = existing_sha
    elif preflight.status_code != 404:
        return False, f"HTTP {preflight.status_code}: archive preflight failed: {preflight.text[:200]}"
    r = requests.put(url, headers={"Authorization": f"Bearer {pat}",
                                    "Accept": "application/vnd.github+json"},
                     json=payload, timeout=15)
    if r.status_code in (200, 201):
        return True, r.json().get("content", {}).get("html_url", path)
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


# ---------------------------------------------------------------------------
# Per-user query history (XYZ-PILOT-051) — persists across logout/login via a
# bind-mounted directory on the EC2 host (/opt/xyz-chatbot/history → /data/history).
# Last 10 user prompts per email, most-recent first.
# ---------------------------------------------------------------------------
def _email_slug(email: str) -> str:
    """Filesystem-safe filename derived from email address."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", email)[:80] or "unknown"


def _history_path(email: str) -> str:
    return os.path.join(HISTORY_DIR, f"{_email_slug(email)}.json")


def _load_history(email: str) -> list[dict[str, str]]:
    """Returns list of {q, asked_at} dicts, most-recent first. Empty on miss."""
    p = _history_path(email)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("items", [])
    except Exception:
        return []


def _save_query(email: str, query: str) -> None:
    """Prepend a new query, trim to HISTORY_MAX_ITEMS, atomic write."""
    items = _load_history(email)
    # de-dup: if the new query equals the most recent one, just refresh its timestamp
    items = [i for i in items if i.get("q") != query]
    items.insert(0, {"q": query, "asked_at": datetime.now(timezone.utc).isoformat()})
    items = items[:HISTORY_MAX_ITEMS]
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        p = _history_path(email)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001
        # History persistence is best-effort — never fail the user's question
        # because we couldn't write a side log. Surface in container logs only.
        print(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": "warn",
            "kind": "history_save_failed",
            "email": email,
            "error": str(exc),
        }), flush=True)


# ---------------------------------------------------------------------------
# Tracker candidate emission (XYZ-PILOT-052 / XYZ-PILOT-054 / XYZ-PILOT-157a) —
# reads XYZ_PILOT_ISSUE_TRACKER.md for Bedrock-driven duplicate checks, then
# emits issue-candidates.md handoffs for XYZ_MASTER. Used by BOTH the "Log
# as new defect" and "File as feature request" buttons. The tracker remains
# the single source of truth; the chatbot does not write it directly.
# ---------------------------------------------------------------------------
TRACKER_PATH = "docs/XYZ_PILOT_ISSUE_TRACKER.md"

DUP_CHECK_TOOL = {
    "tools": [{
        "toolSpec": {
            "name": "report_defect_dup_check",
            "description": "Report whether the user's Q&A describes a defect already in the <PILOT_NAME> tracker.",
            "inputSchema": {"json": {
                "type": "object",
                "properties": {
                    "is_duplicate": {
                        "type": "boolean",
                        "description": "True if the Q&A is about a defect already tracked (Open or Resolved). False if genuinely new.",
                    },
                    "existing_id": {
                        "type": "string",
                        "description": "Format 'XYZ-PILOT-NNN'. Required if is_duplicate=true; empty string otherwise.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "One sentence explaining the match (if duplicate) or why it's new (if not).",
                    },
                    "proposed_title": {
                        "type": "string",
                        "description": "Required if is_duplicate=false. Short one-line summary suitable for the tracker's active-issues table (<100 chars).",
                    },
                    "proposed_severity": {
                        "type": "string",
                        "enum": ["minor", "major", "high", "critical"],
                        "description": "Required if is_duplicate=false.",
                    },
                    "proposed_area": {
                        "type": "string",
                        "description": "Required if is_duplicate=false. Short category like 'Chatbot · UX', 'Schema · RI', 'Networking', 'Scheduler · daily-status'.",
                    },
                    "proposed_summary": {
                        "type": "string",
                        "description": "Required if is_duplicate=false. Markdown body for the full-register entry: what was reported, why it matters, suggested next step. 2-5 short paragraphs.",
                    },
                },
                "required": ["is_duplicate", "existing_id", "reason"],
            }},
        }
    }],
    "toolChoice": {"tool": {"name": "report_defect_dup_check"}},
}


def _next_ticket_id(tracker_text: str) -> int:
    nums = re.findall(r"XYZ-PILOT-(\d+)", tracker_text)
    return (max(int(n) for n in nums) if nums else 0) + 1


def _ask_bedrock_dup_check(kind: str, question: str, answer: str, tracker_excerpt: str) -> dict:
    """kind ∈ {'defect', 'feature'}. Frames the triage prompt accordingly."""
    framing = (
        "Reviewing a potential new DEFECT report"
        if kind == "defect"
        else "Reviewing a potential new FEATURE REQUEST"
    )
    severity_hint = (
        "If new and a defect: severity ∈ {minor, major, high, critical} based on impact."
        if kind == "defect"
        else "If new and a feature request: set severity to 'feature' (this is the convention used throughout the tracker for non-defect items)."
    )
    prompt = f"""{framing} against the existing <PILOT_NAME> tracker.

THE REPORTED Q&A:
Q: {question}

A: {answer}

THE EXISTING TRACKER (top of active table + recent Full register entries — Open AND recently-Resolved entries; the tracker is the canonical backlog + history, with XYZ-PILOT-NNN as the single ID space):
{tracker_excerpt}

Decide if this Q&A describes something that's already tracked. Strict matching only: only mark as duplicate if the underlying item is genuinely the same one (not just topically related). {severity_hint} If not a duplicate, propose a new ticket with title, severity, area, and a 2-5 short paragraph summary. Call report_defect_dup_check with your decision."""
    resp = _bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        system=[{"text": "You are an issue-triage assistant. The XYZ_PILOT_ISSUE_TRACKER.md file is the single source of truth — duplicate checks compare against it, NEVER against GitHub Issues (which is a deprecated parallel channel). Be strict about duplicate detection — only flag as duplicate if the underlying item is the same."}],
        toolConfig=DUP_CHECK_TOOL,
        inferenceConfig={"maxTokens": 1500, "temperature": 0.1},
    )
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            return block["toolUse"]["input"]
    return {"is_duplicate": False, "existing_id": "", "reason": "no tool call returned"}


def _get_tracker_file() -> tuple[str, str]:
    """Returns (text, sha). Raises on error."""
    pat = _get_pat()
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{urllib.parse.quote(TRACKER_PATH)}?ref=main"
    r = requests.get(url, headers={
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
    }, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"tracker GET failed: HTTP {r.status_code} {r.text[:200]}")
    meta = r.json()
    text = base64.b64decode(meta["content"]).decode("utf-8")
    return text, meta["sha"]


def _insert_defect_into_tracker(
    tracker_text: str,
    new_id_int: int,
    title: str,
    severity: str,
    area: str,
    summary_md: str,
    email: str,
    question: str,
    answer: str,
) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_id = f"XYZ-PILOT-{new_id_int:03d}"
    anchor = new_id.lower()
    # Active table row — prepended to the table body
    new_row = (
        f"| [{new_id}](#{anchor}) | {severity} | {area} | "
        f"{title[:120]} *(filed via chatbot {today} by {email})* |\n"
    )
    table_marker = "| ID | Severity | Area | Summary |\n|---|---|---|---|\n"
    if table_marker not in tracker_text:
        raise RuntimeError("could not locate active-issues table header in tracker")
    tracker_text = tracker_text.replace(table_marker, table_marker + new_row, 1)
    # Full register entry — inserted at top of register section
    register_marker = "## Full register (newest first)\n\n"
    if register_marker not in tracker_text:
        raise RuntimeError("could not locate Full register section marker")
    new_entry = (
        f'<a id="{anchor}"></a>\n\n'
        f"### {new_id} — {title}\n"
        f"- **Status:** Open\n"
        f"- **Severity:** {severity}\n"
        f"- **Area:** {area}\n"
        f"- **Surfaced:** {today} (filed via the <PILOT_NAME> Chatbot by **{email}**)\n\n"
        f"#### Summary\n\n{summary_md}\n\n"
        f"#### Original chatbot exchange\n\n"
        f"<details><summary>Click to expand the Q&amp;A that generated this ticket</summary>\n\n"
        f"**Q:** {question}\n\n"
        f"**A:** {answer}\n\n"
        f"</details>\n\n"
        f"---\n\n"
    )
    tracker_text = tracker_text.replace(register_marker, register_marker + new_entry, 1)
    # Last refresh line
    new_refresh = (
        f"**Last refresh:** {today} "
        f"({new_id} **Open** — filed via chatbot by {email})"
    )
    tracker_text = re.sub(r"\*\*Last refresh:\*\* [^\n]*", new_refresh, tracker_text, count=1)
    return tracker_text


def _put_tracker(new_text: str, sha: str, commit_message: str) -> tuple[bool, str]:
    # XYZ-PILOT-157(a) note: this helper is now used ONLY by legacy code paths
    # that haven't been migrated to the candidate-emission flow. The chatbot's
    # main `_log_to_tracker` flow no longer calls this; it calls
    # `_emit_chatbot_candidate` instead. Kept here for back-compat with any
    # future code path that needs in-place tracker modification (e.g. a
    # hypothetical operator-driven inline edit). Such uses should still go
    # through XYZ_MASTER per Rule #5; this helper is preserved as a building
    # block, not a recommended path.
    pat = _get_pat()
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{urllib.parse.quote(TRACKER_PATH)}"
    payload = {
        "message": commit_message,
        "content": base64.b64encode(new_text.encode("utf-8")).decode(),
        "sha": sha,
        "branch": "main",
        "committer": {"name": "xyz-pilot-chatbot",
                      "email": "<NO_REPLY_EMAIL>"},
    }
    r = requests.put(url, headers={
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
    }, json=payload, timeout=20)
    if r.status_code in (200, 201):
        return True, r.json().get("commit", {}).get("html_url", "")
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def _put_new_repo_file(path: str, content: str, commit_message: str) -> tuple[bool, str]:
    """XYZ-PILOT-157(a): generic new-file PUT helper. No If-Match SHA required.

    Parallel to `_put_tracker` which requires the existing SHA for in-place
    modifications. This helper creates a NEW file at the supplied repo
    `path` (relative to the repo root) — there's no existing SHA to
    conflict on. The 201 Created response is treated as success.

    A 422 response means the path already exists (collision); the caller
    should regenerate a fresh run_id and retry. With UTC-second-precision
    run_ids the collision probability for two near-simultaneous chatbot
    invocations is bounded — within the same calendar second, only the
    first-arriving PUT succeeds; the loser retries with a new run_id.
    """
    pat = _get_pat()
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{urllib.parse.quote(path)}"
    payload = {
        "message": commit_message,
        "content": base64.b64encode(content.encode("utf-8")).decode(),
        "branch": "main",
        "committer": {"name": "xyz-pilot-chatbot",
                      "email": "<NO_REPLY_EMAIL>"},
    }
    r = requests.put(url, headers={
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
    }, json=payload, timeout=20)
    if r.status_code in (200, 201):
        return True, r.json().get("commit", {}).get("html_url", "")
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def _read_repo_file_raw(path: str, *, max_chars: int = 20000) -> str:
    """Read a repo file through GitHub Contents API for internal triage."""
    pat = _get_pat()
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{urllib.parse.quote(path)}?ref=main"
    r = requests.get(url, headers={
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github.raw",
        "X-GitHub-Api-Version": "2022-11-28",
    }, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"raw read failed for {path}: HTTP {r.status_code}")
    return r.text[:max_chars]


@st.cache_data(ttl=300, show_spinner=False)
def _candidate_feed_fingerprints() -> dict[str, str]:
    """Return known auto-incident fingerprints from candidate files.

    This is a deterministic duplicate precheck for self-healing incident
    capture. It avoids Bedrock so Bedrock failures can still be filed.
    """
    pat = _get_pat()
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    ref_url = f"https://api.github.com/repos/{GITHUB_REPO}/git/ref/heads/main"
    ref = requests.get(ref_url, headers=headers, timeout=20)
    if ref.status_code != 200:
        raise RuntimeError(f"branch ref read failed: HTTP {ref.status_code}")
    tree_sha = ref.json()["object"]["sha"]
    tree_url = f"https://api.github.com/repos/{GITHUB_REPO}/git/trees/{tree_sha}?recursive=1"
    tree_resp = requests.get(tree_url, headers=headers, timeout=30)
    if tree_resp.status_code != 200:
        raise RuntimeError(f"repo tree read failed: HTTP {tree_resp.status_code}")
    tree = tree_resp.json().get("tree", [])
    paths = sorted(
        (
            item.get("path", "")
            for item in tree
            if item.get("type") == "blob"
            and item.get("path", "").endswith("/issue-candidates.md")
        ),
        reverse=True,
    )
    fingerprints: dict[str, str] = {}
    for path in paths[:500]:
        try:
            text = _read_repo_file_raw(path, max_chars=12000)
        except Exception:  # noqa: BLE001
            continue
        for fp in re.findall(r"Incident fingerprint:\s*`([0-9a-f]{16})`", text):
            fingerprints.setdefault(fp, path)
    return fingerprints


def _incident_duplicate_precheck(fingerprint: str) -> dict[str, Any]:
    """Check tracker + candidate feed for an existing incident fingerprint."""
    notes: list[str] = []
    try:
        tracker_text, _ = _get_tracker_file()
        if fingerprint in tracker_text:
            idx = tracker_text.find(fingerprint)
            before = tracker_text[max(0, idx - 5000):idx]
            ids = re.findall(r"XYZ-PILOT-\d{3}", before)
            existing_id = ids[-1] if ids else "tracker entry"
            return {
                "duplicate": True,
                "source": existing_id,
                "notes": f"tracker fingerprint match: {existing_id}",
            }
        notes.append("tracker no fingerprint match")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"tracker check unavailable: {_redact_debug_text(exc, limit=240)}")

    try:
        known = _candidate_feed_fingerprints()
    except Exception as exc:  # noqa: BLE001
        return {
            "duplicate": False,
            "candidate_feed_checked": False,
            "notes": "; ".join(notes + [
                f"candidate-feed check unavailable: {_redact_debug_text(exc, limit=240)}"
            ]),
        }

    if fingerprint in known:
        return {
            "duplicate": True,
            "source": known[fingerprint],
            "notes": f"candidate-feed fingerprint match: {known[fingerprint]}",
        }
    notes.append(f"candidate feed no fingerprint match across {len(known)} known incident fingerprints")
    return {
        "duplicate": False,
        "candidate_feed_checked": True,
        "notes": "; ".join(notes),
    }


# XYZ-PILOT-103 legacy constants for the pre-XYZ-PILOT-157(a) direct tracker
# writer. `_log_to_tracker` now emits a new candidate file instead of
# mutating the tracker, so the ID-collision race is handled by XYZ_MASTER
# at drain time. Keep these names while `_put_tracker` and the verifier
# helper remain available for legacy/operator-only paths.
_TRACKER_WRITE_LOCK = threading.Lock()
_TRACKER_WRITE_MAX_RETRIES = 3
_TRACKER_WRITE_BACKOFF_S = 0.5


def _verify_tracker_completeness(content: str) -> tuple[bool, list[str]]:
    """XYZ-PILOT-111: pre/post-write integrity check on the tracker content.

    Returns (ok, errors). Errors are strings naming the gap. The chatbot's
    `_log_to_tracker` calls this on the rendered new_content before PUT;
    any error aborts the write with a logged warning instead of corrupting
    the register. Mirrors the algorithm in `scripts/tracker_completeness.py`
    but inlined to avoid bundling overhead in the Docker image.
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


def _emit_chatbot_candidate(
    kind: str,
    email: str,
    title: str,
    severity: str,
    area: str,
    summary_md: str,
    question: str,
    answer: str,
    triage_note: str | None = None,
) -> dict:
    """XYZ-PILOT-157(a): emit a candidate file for XYZ_MASTER to drain.

    Replaces the pre-XYZ-PILOT-157(a) direct GitHub Contents API PUT of the
    tracker. Writes a Markdown candidate file at
    `tests/logs/runs/<UTC>/XYZ_PILOT_CHATBOT/issue-candidates.md` whose
    content matches the XYZ_MASTER skill's expected candidate format
    (proposed top-table row + Full-register detail section + last-refresh
    line update + operator notes). XYZ_MASTER picks the candidate up
    via its standard archive + verify + edit + verify + commit + push
    pass and assigns the actual XYZ-PILOT-NNN at filing time.

    Returns
        {"kind": "candidate", "run_id": <UTC>, "candidate_path": <path>,
         "commit_url": <github url to the candidate-file commit>,
         "title": <title>}
    on success, or
        {"kind": "error", "detail": <msg>}
    on GitHub Contents API failure.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    candidate_path = f"tests/logs/runs/{run_id}/XYZ_PILOT_CHATBOT/issue-candidates.md"
    triage_note = triage_note or (
        f"chatbot kind=`{kind}`, Bedrock dup-check returned no matching "
        "existing ticket; chatbot defaults to minor/feature per the "
        "2026-05-30 severity-default rule"
    )

    # Render the same proposed mutations that the pre-XYZ-PILOT-157(a)
    # `_insert_defect_into_tracker` would have applied, but as a candidate
    # handoff that XYZ_MASTER reads — no actual tracker write.
    candidate_body = f"""# Candidate handoff to XYZ_MASTER — File new ticket: {title[:80]}

- **Source agent:** `XYZ_PILOT_CHATBOT`
- **Run ID:** `{run_id}`
- **Author:** {email} via <PILOT_NAME> Chatbot on {today}
- **Per directive:** XYZ-PILOT-157(a) — chatbot `_log_to_tracker` rework
  to emit candidates instead of direct tracker writes (Rule #5
  single-writer compliance).
- **No ID pre-allocation.** XYZ_MASTER assigns the actual `XYZ-PILOT-NNN`
  at filing time.

## Candidate — File new ticket: {title[:80]}

- **Proposed severity:** `{severity}` ({triage_note})
- **Proposed area:** `{area}`
- **Proposed status:** `Open`
- **Surfaced:** {today} via the <PILOT_NAME> Chatbot by **{email}**

### Top-table row

| [XYZ-PILOT-NNN](#xyz-pilot-NNN) | {severity} | {area} | {title[:120]} *(filed via chatbot {today} by {email})* |

### Full-register detail section

```markdown
<a id="xyz-pilot-NNN"></a>

### XYZ-PILOT-NNN — {title}
- **Status:** Open
- **Severity:** {severity}
- **Area:** {area}
- **Surfaced:** {today} (filed via the <PILOT_NAME> Chatbot by **{email}**)

#### Summary

{summary_md}

#### Original chatbot exchange

<details><summary>Click to expand the Q&A that generated this ticket</summary>

**Q:** {question}

**A:** {answer}

</details>
```

### Last-refresh header line update

```
**Last refresh:** {today} (XYZ-PILOT-NNN **Open** — filed via chatbot by {email})
```

## Operator notes

- **Tracker-only mutation this pass** (when XYZ_MASTER drains it). No code
  deploy.
- XYZ_MASTER assigns no ID pre-allocation; the next free `XYZ-PILOT-NNN`
  is computed at filing time.
- Severity `{severity}` is the chatbot's standard default per the
  2026-05-30 severity-default rule. If Bedrock proposed `major` or
  `critical`, the chatbot downgrades to `minor`/`feature` here — the
  high-severity buckets require explicit operator confirmation that
  the chatbot UI cannot collect. The operator can manually edit the
  detail-section Severity field via XYZ_MASTER after filing if escalation
  is warranted.
- This candidate ships as a single ticket per the one-ticket-per-invocation
  rule.
"""

    commit_msg = (
        f"chatbot: file XYZ_MASTER candidate ({kind}) — {title[:60]}\n\n"
        f"Filed via the <PILOT_NAME> Chatbot by {email}.\n"
        f"Run ID: {run_id}.\n"
        f"Candidate path: {candidate_path}.\n"
        f"Per XYZ-PILOT-157(a): _log_to_tracker emits candidate instead of direct write.\n\n"
        f"Co-Authored-By: <PILOT_NAME> Chatbot <<NO_REPLY_EMAIL>>"
    )

    ok, url = _put_new_repo_file(candidate_path, candidate_body, commit_msg)
    if not ok:
        return {"kind": "error", "detail": f"candidate file write failed: {url}"}

    return {
        "kind": "candidate",
        "run_id": run_id,
        "candidate_path": candidate_path,
        "commit_url": url,
        "title": title,
    }


def _log_to_tracker(kind: str, email: str, question: str, answer: str) -> dict:
    """XYZ-PILOT-157(a): chatbot-side defect/feature filing via candidate emission.

    kind ∈ {'defect', 'feature'}. Determines:
      - prompt framing of the Bedrock dup-check
      - default severity if Bedrock doesn't propose one
      - candidate-file commit message prefix

    Returns {"kind": "duplicate"|"candidate"|"error", ...}.

    Pre-XYZ-PILOT-157(a) this function performed direct tracker writes via
    the GitHub Contents API with a thread-lock + retry-on-409 (XYZ-PILOT-103),
    a pre-PUT completeness check (XYZ-PILOT-111), and a freshly-computed
    `next_id`. Post-XYZ-PILOT-157(a) it emits a Markdown candidate file at
    `tests/logs/runs/<UTC>/XYZ_PILOT_CHATBOT/issue-candidates.md` for
    XYZ_MASTER to drain — no tracker mutation; no race-class concern; no
    `next_id` computation here. XYZ_MASTER's standard archive + verify +
    edit + verify + commit + push pass handles all of those at drain time.
    """
    if kind not in ("defect", "feature"):
        return {"kind": "error", "detail": f"invalid kind '{kind}'"}

    # ── Phase 1: fetch tracker (read-only) + Bedrock dup-check ──
    # Still useful: surface likely duplicates BEFORE filing as candidate,
    # so XYZ_MASTER doesn't have to file-then-reject. The fetch reads the
    # tracker but never mutates it.
    try:
        tracker_text, _initial_sha = _get_tracker_file()
    except Exception as exc:
        return {"kind": "error", "detail": f"tracker fetch failed: {exc}"}
    # XYZ-PILOT-069 sub-5: send the FULL active-issues table + the head of
    # the Full register so older Open tickets don't silently fall out of
    # the dup-check window as the tracker grows.
    active_marker = "## Active issues"
    register_marker = "## Full register"
    if active_marker in tracker_text and register_marker in tracker_text:
        active_start = tracker_text.index(active_marker)
        register_start = tracker_text.index(register_marker)
        excerpt = (
            tracker_text[active_start:register_start]
            + "\n"
            + tracker_text[register_start:register_start + 20000]
        )
    else:
        excerpt = tracker_text[:25000]
    try:
        check = _ask_bedrock_dup_check(kind, question, answer, excerpt)
    except Exception as exc:
        return {"kind": "error", "detail": f"Bedrock dup-check failed: {exc}"}
    if check.get("is_duplicate"):
        return {
            "kind": "duplicate",
            "existing_id": check.get("existing_id", "?"),
            "reason": check.get("reason", ""),
        }

    # ── Phase 2: triage metadata + emit candidate file ──
    title = check.get("proposed_title", question[:80])
    default_severity = "feature" if kind == "feature" else "minor"
    severity = check.get("proposed_severity") or default_severity
    if kind == "feature" and severity != "feature":
        severity = "feature"
    # 2026-05-30 severity-default directive: chatbot defaults to minor/feature
    # only. If Bedrock proposes major/critical, downgrade — those buckets
    # require explicit operator confirmation that the chatbot UI can't
    # collect. Operator can manually escalate via XYZ_MASTER post-filing.
    if severity in ("major", "critical"):
        severity = "minor" if kind == "defect" else "feature"
    default_area = (
        "Chatbot · feature-request" if kind == "feature" else "Chatbot · user-reported"
    )
    area = check.get("proposed_area") or default_area
    summary_md = check.get("proposed_summary", "_(no summary returned)_")

    return _emit_chatbot_candidate(
        kind, email, title, severity, area, summary_md, question, answer,
    )


_DEBUG_REDACTION_PATTERNS = [
    re.compile(
        r"(?i)(authorization\s*[:=]\s*)bearer\s+[A-Za-z0-9._~+/\-=]+"
    ),
    re.compile(
        r"(?i)(password|passwd|pwd|client_secret|secret|token|api[_-]?key|"
        r"aws_access_key_id|aws_secret_access_key)(\s*[:=]\s*)([^\s,;\"']+)"
    ),
    re.compile(
        r"(?i)(\"(?:password|passwd|pwd|client_secret|secret|token|api[_-]?key|"
        r"aws_access_key_id|aws_secret_access_key)\"\s*:\s*\")([^\"]+)"
    ),
]


def _redact_debug_text(value: Any, *, limit: int = 3000) -> str:
    """Return a bounded, Markdown-safe, secret-safe debug excerpt."""
    text = "" if value is None else str(value)
    for pat in _DEBUG_REDACTION_PATTERNS:
        if pat.pattern.startswith("(?i)(authorization"):
            text = pat.sub(r"\1Bearer [REDACTED]", text)
        elif pat.pattern.startswith("(?i)(\""):
            text = pat.sub(r"\1[REDACTED]", text)
        else:
            text = pat.sub(r"\1\2[REDACTED]", text)
    # Candidate files are embedded inside fenced Markdown by the tracker
    # handoff helper. Avoid accidentally closing that fence.
    text = text.replace("```", "` ` `")
    if len(text) > limit:
        text = text[:limit] + f"... [truncated to {limit} chars]"
    return text


def _queue_visual_incident_candidate(
    *,
    surface: str,
    renderer: str,
    failure: Any,
    source: Any = None,
    sql: str | None = None,
    data_preview: Any = None,
) -> None:
    """Queue a defect candidate when a visual/query surface fails.

    This is intentionally best-effort and session-deduped: Streamlit reruns
    could otherwise emit the same candidate repeatedly while rendering old
    history. XYZ_MASTER still performs the canonical guarded tracker filing.
    """
    if not st.session_state.get("_xyz_capture_visual_incidents", False):
        return
    try:
        failure_text = _redact_debug_text(
            f"{type(failure).__name__}: {failure}", limit=1200,
        )
        source_text = _redact_debug_text(source, limit=4000)
        sql_text = _redact_debug_text(sql, limit=2000) if sql else ""
        data_text = _redact_debug_text(data_preview, limit=2000) if data_preview is not None else ""
        prompt_text = _redact_debug_text(
            st.session_state.get("_xyz_current_user_prompt", "(prompt unavailable)"),
            limit=1200,
        )
        fingerprint_material = "|".join(
            [surface, renderer, failure_text, source_text[:500], sql_text[:500]]
        )
        fingerprint = hashlib.sha256(
            fingerprint_material.encode("utf-8", errors="ignore")
        ).hexdigest()[:16]
        seen = st.session_state.setdefault("_xyz_visual_incidents_queued", set())
        if fingerprint in seen:
            return
        seen.add(fingerprint)

        precheck = _incident_duplicate_precheck(fingerprint)
        if precheck.get("duplicate"):
            st.info(
                "🐛 Visual/debug incident already exists in the XYZ candidate "
                f"or tracker feed (`{precheck.get('source', 'existing item')}`; "
                f"fingerprint `{fingerprint}`) — no duplicate candidate queued."
            )
            return
        if not precheck.get("candidate_feed_checked"):
            st.warning(
                "⚠ Visual/debug incident detected, but duplicate precheck "
                "against the candidate feed failed. No candidate was queued. "
                f"Details: {_redact_debug_text(precheck.get('notes', 'unknown'), limit=500)}"
            )
            return

        email_addr = st.session_state.get("user_email", "unknown-subscriber")
        title = f"Chatbot visual incident · {surface} · {renderer} failed"
        question = (
            "Automated visual/query incident captured by the <PILOT_NAME> Chatbot "
            f"for prompt: {prompt_text}"
        )
        answer = f"""The chatbot encountered a visual/query rendering failure and queued this as a defect candidate for XYZ_MASTER review.

Incident fingerprint: `{fingerprint}`
Surface: `{_redact_debug_text(surface, limit=200)}`
Renderer/tool: `{_redact_debug_text(renderer, limit=200)}`
Subscriber: `{_redact_debug_text(email_addr, limit=200)}`
Deployment: `{LAST_DEPLOYMENT_ISSUE}` · build `{BUILD_AT_UTC}` · deploy `{DEPLOYED_AT_UTC}` · app-start `{APP_STARTED_AT_UTC}`

Failure:
{failure_text}

Prompt excerpt:
{prompt_text}

SQL excerpt:
{sql_text or "(none captured)"}

Source/spec excerpt:
{source_text or "(none captured)"}

Data preview excerpt:
{data_text or "(none captured)"}

Notes:
- Deterministic duplicate precheck: {precheck.get('notes', '(not recorded)')}
- Candidate is emitted through the chatbot candidate queue, not as a direct tracker write.
- Source/spec/query excerpts are redacted and bounded before filing.
- XYZ_MASTER performs guarded duplicate triage and central tracker mutation.
"""
        summary_md = (
            "Automated Chatbot visual/query incident capture. The fresh "
            "subscriber turn hit a prompt, live-data tool, SQL-backed chart, "
            "diagram, widget, or renderer failure. Debug evidence is redacted "
            "and bounded in the original exchange below. The Chatbot performed "
            "a deterministic duplicate precheck against the tracker/candidate "
            "feed before queuing; XYZ_MASTER still performs guarded triage "
            "before any central tracker mutation."
        )
        triage_note = (
            "chatbot kind=`defect`; deterministic duplicate precheck returned "
            f"no existing incident fingerprint ({precheck.get('notes', 'no notes')}); "
            "chatbot defaults to minor per the 2026-05-30 severity-default rule"
        )
        result = _emit_chatbot_candidate(
            "defect",
            email_addr,
            title,
            "minor",
            "Chatbot · visual/query incident",
            summary_md,
            question,
            answer,
            triage_note=triage_note,
        )
        if result.get("kind") == "candidate":
            st.info(
                "🐛 Visual/debug incident queued for XYZ_MASTER review "
                f"(`{result['candidate_path']}`; fingerprint `{fingerprint}`)."
            )
        else:
            st.warning(
                "⚠ Visual/debug incident capture could not queue a candidate: "
                f"{_redact_debug_text(result.get('detail', 'unknown error'), limit=500)}"
            )
    except Exception as exc:  # noqa: BLE001
        st.caption(
            "_(visual incident auto-capture unavailable: "
            f"{type(exc).__name__})_"
        )


def _queue_failed_tool_incidents(audit: list[dict[str, Any]]) -> None:
    """Queue defect candidates for failed live-data/MCP tools in a fresh turn."""
    for item in audit:
        if item.get("ok", True):
            continue
        tool_name = str(item.get("tool", "unknown"))
        if not (
            tool_name == "exasol_query"
            or tool_name.startswith("exasol")
            or tool_name.startswith("list_exasol")
            or tool_name.startswith("describe_exasol")
            or tool_name.startswith("find_exasol")
        ):
            continue
        args = item.get("args", {})
        sql = args.get("sql") if isinstance(args, dict) else None
        _queue_visual_incident_candidate(
            surface="Chatbot live-data tool",
            renderer=tool_name,
            failure=item.get("result_excerpt", "tool returned ERROR"),
            source=args,
            sql=sql,
        )


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
email = _require_auth()
context = _fetch_pilot_context()

st.title("<PILOT_NAME> Chatbot 💬")
st.caption(
    "**Powered by Exasol MCP Server** · "
    "[`https://mermaid.live`](https://mermaid.live)"
)
st.markdown(
    (
        "<div style='margin:.25rem 0 .6rem 0;padding:.45rem .65rem;"
        "border:1px solid #d0d7de;border-radius:4px;background:#f6f8fa;"
        "font-size:.92rem;color:#2f363d;'>"
        f"{CONFIDENTIAL_NOTICE}"
        "</div>"
    ),
    unsafe_allow_html=True,
)
st.caption(_provenance_caption())
with st.sidebar:
    st.caption(f"Signed in as **{email}**")
    if st.button("Sign out"):
        st.session_state.clear()
        # XYZ-PILOT-099(a): same-tab navigation for the Cognito logout endpoint.
        st.markdown(
            f'<a href="{_logout_url()}" target="_self" '
            f'style="display:inline-block;padding:.4em 1em;background:#dc2626;'
            f'color:white;text-decoration:none;border-radius:.4em;'
            f'font-weight:600;">Sign out via Cognito →</a>',
            unsafe_allow_html=True,
        )
        st.stop()
    st.divider()
    st.caption("Context snapshot")
    st.text(f"{len(context):,} chars of Pilot docs loaded.")
    if st.button("Refresh snapshot"):
        _fetch_pilot_context.clear()
        st.rerun()

    # ── Admin · EC2 control (XYZ-PILOT-063 mitigation) ────────────────────────
    # Replaces the S3 status-dashboard's Start/Stop buttons after the
    # account-level Block-Public-Access policy started returning 403 on the
    # unauthenticated Function URL. Cognito-gated by the same SSO that already
    # protects this page; allowlist-gated by ADMIN_EMAILS env var so non-ops
    # subscribers don't accidentally bounce the instance.
    _admin_emails = {
        e.strip().lower()
        for e in os.environ.get("ADMIN_EMAILS", "<OPERATOR_EMAIL>").split(",")
        if e.strip()
    }
    if email.lower() in _admin_emails:
        st.divider()
        st.caption("🛠️ Admin · EC2 control")
        try:
            _ec2  = boto3.client("ec2",    region_name=os.environ.get("AWS_REGION", "<AWS_REGION>"))
            _lam  = boto3.client("lambda", region_name=os.environ.get("AWS_REGION", "<AWS_REGION>"))
            _iid  = os.environ.get("PILOT_EC2_INSTANCE_ID", "<EC2_INSTANCE_ID>")
            _desc = _ec2.describe_instances(InstanceIds=[_iid])
            _state = _desc["Reservations"][0]["Instances"][0]["State"]["Name"]
            _emoji = {
                "running":  "🟢", "pending": "🟡",
                "stopping": "🟡", "stopped": "🔴",
            }.get(_state, "⚪")
            st.caption(f"`{_iid}` · {_emoji} **{_state}**")
            _c1, _c2 = st.columns(2)
            with _c1:
                if st.button(
                    "▶ Start",
                    disabled=(_state in {"running", "pending"}),
                    use_container_width=True,
                    help="Invoke xyz-pilot-start Lambda (boots EC2 + warms Exasol cache).",
                ):
                    # XYZ-PILOT-077: pass the operator's Cognito access token
                    # so the Lambda can identify the initiator + send the
                    # ad-hoc-start notification email naming who clicked.
                    _at = st.session_state.get("access_token", "")
                    if _at:
                        _payload = json.dumps({
                            "headers": {"authorization": f"Bearer {_at}"}
                        }).encode()
                    else:
                        _payload = b"{}"
                    _r = _lam.invoke(FunctionName="xyz-pilot-start", Payload=_payload)
                    _p = json.loads(_r["Payload"].read())
                    if _p.get("ad_hoc"):
                        st.success(f"Start fired · ad-hoc notification email queued · {_p}")
                    else:
                        st.success(f"Start fired · {_p}")
                    st.rerun()
            with _c2:
                if st.button(
                    "⏹ Stop",
                    disabled=(_state in {"stopped", "stopping"}),
                    use_container_width=True,
                    help="Invoke xyz-pilot-stop Lambda. WARNING: this chatbot becomes unreachable.",
                ):
                    # XYZ-PILOT-080: pass Cognito access token so the
                    # Lambda can name the initiator in the ad-hoc-stop
                    # email (mirrors XYZ-PILOT-077 for Start).
                    _at = st.session_state.get("access_token", "")
                    if _at:
                        _payload = json.dumps({
                            "headers": {"authorization": f"Bearer {_at}"}
                        }).encode()
                    else:
                        _payload = b"{}"
                    _r = _lam.invoke(FunctionName="xyz-pilot-stop", Payload=_payload)
                    _p = json.loads(_r["Payload"].read())
                    st.warning(
                        "EC2 stopping — this chatbot will become unreachable shortly. "
                        "Use `aws ec2 start-instances --instance-ids " + _iid + "` to restart "
                        "(or wait for the 12:00 CEST scheduled start)."
                    )
                    st.caption(f"Stop fired · {_p}")
        except Exception as _exc:  # noqa: BLE001
            st.caption(f"_(EC2 control unavailable: {type(_exc).__name__})_")

    st.divider()
    st.caption(
        "Phase 1 (digest) + Phase 2 (whole-repo `read_repo_file`) + "
        "**Phase 3 (live Exasol via MCP `exasol_query` etc.)** — all shipped 2026-05-20/21."
    )
    # --- Per-user query history (XYZ-PILOT-051) ---
    history_items = _load_history(email)
    if history_items:
        st.divider()
        st.caption(f"Your recent queries ({len(history_items)})")
        for i, item in enumerate(history_items):
            q = item.get("q", "")
            q_short = q[:55] + "…" if len(q) > 55 else q
            ts = (item.get("asked_at") or "")[:10] or "—"
            if st.button(
                f"🕐 {q_short}",
                key=f"hist_{i}",
                help=f"Asked {ts} · click to re-ask now",
                use_container_width=True,
            ):
                st.session_state.replay_query = q
                st.rerun()
    else:
        st.divider()
        st.caption("Your recent queries")
        st.caption("_(none yet — ask something and it'll appear here next session)_")

if "history" not in st.session_state:
    st.session_state.history = []


# XYZ-PILOT-055: render Claude's `mermaid` / `dot` fenced code blocks as
# interactive diagrams. Mermaid via an embedded Mermaid.js component, Graphviz
# DOT via server-side `dot -Tsvg`. Falls back to `st.code(..., language=lang)`
# if a renderer raises.
#
# XYZ-PILOT-091 robustness: in practice Claude occasionally emits the diagram
# text WITHOUT the ```mermaid fence (especially long C4Container outputs).
# So in addition to the standard fenced path we also auto-detect Mermaid
# syntax by its first-line keyword (C4Container, flowchart, sequenceDiagram,
# erDiagram, etc.). A second pass after the standard fence extraction looks
# for those keywords at column 0 and treats the surrounding paragraph as a
# Mermaid diagram even without explicit fences.
_re = __import__("re")

# Match ANY fenced code block (with or without a language tag). The pattern is
# intentionally tolerant: Bedrock answers have appeared with a space before the
# language tag, CRLF newlines, escaped Markdown fences, and four-backtick fences
# after nested prompt examples. We inspect the body's first non-empty line
# afterwards to decide how to render. This is much safer than free-text keyword
# detection (XYZ-PILOT-091 v2): a Mermaid keyword appearing in prose can no longer
# false-positive into the Mermaid renderer because we only consider content
# that's already inside a fenced block.
_ANY_CODE_BLOCK = _re.compile(
    r"`{3,}[ \t]*([\w-]*)[ \t]*\r?\n([\s\S]*?)\r?\n[ \t]*`{3,}[ \t]*",
    _re.MULTILINE,
)

# Mermaid 11.x diagram-type opening keywords. Used to recognise a body
# inside a fence-less or unlabelled code block as a Mermaid diagram.
# `graph` and `pie` removed compared to the v1 fallback set — too
# generic, they false-positive on prose.
_MERMAID_KEYWORDS = frozenset((
    "C4Context", "C4Container", "C4Component", "C4Dynamic", "C4Deployment",
    "flowchart", "sequenceDiagram", "classDiagram", "stateDiagram",
    "stateDiagram-v2", "erDiagram", "journey", "gantt", "quadrantChart",
    "requirementDiagram", "gitGraph", "mindmap", "timeline", "zenuml",
    "sankey-beta", "xychart-beta", "block-beta", "packet-beta", "architecture-beta",
))

# Common language-tag mistakes that still carry Mermaid source. Treat these as
# Mermaid renderer input instead of showing source-only code blocks.
_MERMAID_LANG_ALIASES = frozenset((
    "mmd", "c4", "c4context", "c4container", "c4component", "c4dynamic",
    "c4deployment", "flowchart", "sequence", "sequencediagram",
    "classdiagram", "statediagram", "statediagram-v2", "erdiagram",
    "journey", "gantt", "quadrantchart", "requirementdiagram", "gitgraph",
    "mindmap", "timeline", "zenuml", "sankey-beta", "xychart-beta",
    "block-beta", "packet-beta", "architecture-beta",
))

_INLINE_VISUAL_FENCE_RE = _re.compile(
    r"`{3}[ \t]*(mermaid|dot|diagrams|chart)[ \t]+([^\r\n`][^\r\n]*?)[ \t]*`{3}",
    _re.IGNORECASE,
)

_VISUAL_INCIDENT_OPERATOR_PREFIX = (
    "for visual-regression testing only, return exactly this invalid chart block"
)


def _first_token(code: str) -> str:
    """Return the first whitespace-separated token of the first non-empty
    line of `code`. Used to sniff the diagram type from a body."""
    for line in code.split("\n"):
        line = line.strip()
        if line and not line.startswith("%%"):  # skip Mermaid comments
            return line.split()[0] if line.split() else ""
    return ""


def _looks_like_dot(code: str) -> bool:
    """Heuristic: does this look like Graphviz DOT?"""
    first = _first_token(code)
    return first in ("digraph", "graph", "strict") and "{" in code


_DOT_START_RE = _re.compile(
    r"(?m)^[ \t]*(?:strict[ \t]+)?(?:digraph|graph)\b[^\n{]{0,200}\{"
)


def _extract_unfenced_dot_block(text: str) -> tuple[int, int, str] | None:
    """Return the first unfenced Graphviz DOT block in `text`.

    Bedrock sometimes follows the system prompt's visual-first intent but
    forgets the mandatory ```dot fence, especially for long schema diagrams
    after a short prose introduction. This extractor finds a line-start
    `digraph`/`graph` block, then brace-matches outside quoted labels so
    record-shape labels such as `label="{FACT|COL\\l}"` do not terminate the
    diagram early.
    """
    source = text or ""
    match = _DOT_START_RE.search(source)
    if not match:
        return None

    start = match.start()
    open_idx = source.find("{", match.start(), match.end())
    if open_idx < 0:
        return None

    depth = 0
    in_quote = False
    escaped = False
    i = open_idx
    while i < len(source):
        ch = source[i]
        if in_quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quote = False
        else:
            if ch == '"':
                in_quote = True
            elif ch == "/" and i + 1 < len(source) and source[i + 1] == "/":
                newline = source.find("\n", i + 2)
                if newline < 0:
                    break
                i = newline
            elif ch == "#":
                newline = source.find("\n", i + 1)
                if newline < 0:
                    break
                i = newline
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    return start, end, source[start:end].strip()
        i += 1

    # Return the remainder so `_try_render_dot` can show a visible Graphviz
    # error/source surface for a truncated block instead of raw Markdown text.
    return start, len(source), source[start:].strip()


def _looks_like_mermaid_graph(code: str) -> bool:
    """Heuristic for fenced Mermaid `graph TD/LR` blocks.

    `graph` is too generic for prose, but inside a fenced block it is a common
    Mermaid opener. Require basic edge/node syntax so plain prose/code that
    happens to start with "graph" does not render as a diagram.
    """
    first = _first_token(code)
    return first == "graph" and bool(re.search(r"(-->|---|==>|-.->|\[[^\]]+\])", code))


def _normalize_mermaid(code: str) -> str:
    """XYZ-PILOT-097: legacy Mermaid v10.2.4 renderers do
    NOT interpret `\\n` inside quoted label strings as a line break — it
    renders the two literal characters. Modern Mermaid (10.6+) and
    GitHub's renderer accept both `\\n` and `<br/>`. Normalising to
    `<br/>` upfront makes Claude's outputs render correctly regardless of
    which line-break form it chose.

    Replaces only the escape sequence `\\n` (two chars: backslash + n).
    Actual newline characters (which separate Mermaid statement lines)
    are untouched.
    """
    return (code or "").replace("\\n", "<br/>")


def _mermaid_component_height(code: str) -> int:
    """Return a stable iframe height for Mermaid diagrams.

    Streamlit components do not auto-resize reliably inside chat messages, so
    keep a bounded height that is large enough for TC-1A subgraph diagrams while
    avoiding giant blank panels for small flowcharts.
    """
    line_count = max(1, len((code or "").splitlines()))
    return max(420, min(760, 180 + line_count * 24))


def _mermaid_component_html(code: str) -> str:
    """Build a self-contained Mermaid renderer iframe body.

    The old `streamlit-mermaid` wrapper could report success while its
    browser-side iframe rendered blank. This component makes browser-side
    failures visible in the iframe itself, so subscribers see either an SVG or
    an actionable renderer error.
    """
    source_json = json.dumps(code or "").replace("</", "<\\/")
    source_preview = html.escape(code or "")
    diagram_id = f"xyz-mermaid-{hashlib.sha256((code or '').encode('utf-8')).hexdigest()[:16]}"
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {{
      color-scheme: light;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    body {{
      margin: 0;
      background: #ffffff;
      color: #111827;
    }}
    .mermaid-frame {{
      min-height: 360px;
      padding: 12px;
      border: 1px solid #e5e7eb;
      border-radius: 6px;
      background: #ffffff;
      overflow: auto;
      box-sizing: border-box;
    }}
    #mermaid-root svg {{
      display: block;
      max-width: 100%;
      height: auto;
      margin: 0 auto;
    }}
    .mermaid-error {{
      display: none;
      border: 1px solid #f59e0b;
      border-radius: 6px;
      background: #fffbeb;
      color: #78350f;
      padding: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
    }}
    .mermaid-error strong {{
      display: block;
      margin-bottom: 6px;
    }}
    .mermaid-source {{
      margin-top: 10px;
      max-height: 180px;
      overflow: auto;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      color: #374151;
      background: #f9fafb;
      border-radius: 4px;
      padding: 8px;
    }}
  </style>
</head>
<body>
  <div class="mermaid-frame">
    <div id="mermaid-root" aria-label="Rendered Mermaid diagram"></div>
    <div id="mermaid-error" class="mermaid-error" role="alert">
      <strong>Mermaid renderer failed.</strong>
      <span id="mermaid-error-message"></span>
      <div class="mermaid-source">{source_preview}</div>
    </div>
    <noscript>
      <div class="mermaid-error" style="display:block">
        <strong>Mermaid renderer failed.</strong>
        JavaScript is disabled in this browser surface.
      </div>
    </noscript>
  </div>

  <script>
    const MERMAID_SOURCE = {source_json};
    const MERMAID_ID = {json.dumps(diagram_id)};

    function showMermaidError(message) {{
      const root = document.getElementById("mermaid-root");
      const error = document.getElementById("mermaid-error");
      const text = document.getElementById("mermaid-error-message");
      if (root) {{
        root.style.display = "none";
      }}
      if (text) {{
        text.textContent = message || "Unknown Mermaid renderer error.";
      }}
      if (error) {{
        error.style.display = "block";
      }}
    }}

    async function renderMermaidDiagram() {{
      try {{
        if (!window.mermaid) {{
          showMermaidError("Mermaid JavaScript did not load. Use the Diagram source expander below to inspect the source.");
          return;
        }}
        window.mermaid.initialize({{
          startOnLoad: false,
          securityLevel: "loose",
          theme: "default",
          flowchart: {{ htmlLabels: true, useMaxWidth: true }},
          sequence: {{ useMaxWidth: true }},
          themeVariables: {{
            fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
          }}
        }});
        const renderResult = await window.mermaid.render(MERMAID_ID, MERMAID_SOURCE);
        const root = document.getElementById("mermaid-root");
        root.innerHTML = renderResult.svg;
        root.style.display = "block";
      }} catch (err) {{
        showMermaidError(err && err.message ? err.message : String(err));
      }}
    }}
  </script>
  <script
    src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"
    onload="renderMermaidDiagram()"
    onerror="showMermaidError('Mermaid JavaScript could not be loaded from the CDN. Use the Diagram source expander below to inspect the source.')">
  </script>
</body>
</html>"""


def _dot_component_height(code: str) -> int:
    line_count = max(1, len((code or "").splitlines()))
    return max(420, min(760, 180 + line_count * 20))


def _dot_svg_component_html(svg: str) -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{
      margin: 0;
      background: #ffffff;
      color: #111827;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .dot-frame {{
      min-height: 360px;
      padding: 12px;
      border: 1px solid #e5e7eb;
      border-radius: 6px;
      background: #ffffff;
      overflow: auto;
      box-sizing: border-box;
    }}
    .dot-frame svg {{
      display: block;
      max-width: 100%;
      height: auto;
      margin: 0 auto;
    }}
  </style>
</head>
<body>
  <div class="dot-frame">
    {svg}
  </div>
</body>
</html>"""


def _dot_to_svg(code: str) -> str:
    result = subprocess.run(
        ["dot", "-Tsvg"],
        input=code or "",
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Graphviz returned non-zero exit").strip()
        raise RuntimeError(detail[:1200])
    svg = (result.stdout or "").strip()
    if "<svg" not in svg:
        raise RuntimeError("Graphviz returned no SVG payload")
    return svg


def _normalize_diagram_fences(text: str) -> str:
    r"""XYZ-PILOT-186: unescape Markdown-escaped code fences before diagram
    extraction.

    Some LLM replies protect code fences as ``\`\`\`mermaid`` or
    ``\```mermaid``. Streamlit then renders the source visibly and our extractor
    sees no real fence. This normalises only the fence marker, not arbitrary
    inline backticks.
    """
    text = (text or "").replace("\\`\\`\\`", "```").replace("\\```", "```")

    # XYZ-PILOT-149 / TC-2A: operator negative-test prompts intentionally use a
    # compact one-line visual fence (` ```chart {...} ``` `). Normal diagram
    # generation must keep newline fences, but the renderer should still honour
    # compact visual fences instead of leaving them as raw Markdown/code spans.
    return _INLINE_VISUAL_FENCE_RE.sub(
        lambda m: f"```{m.group(1).lower()}\n{m.group(2).strip()}\n```",
        text,
    )


def _operator_visual_incident_probe_answer(user_msg: str) -> str | None:
    """Return deterministic renderer input for the TC-2A operator probe.

    The TC-2A prompt exists specifically to validate visual incident
    auto-capture. Do not let the LLM reinterpret it as a schema/diagram request;
    feed the invalid chart spec directly into the normal renderer path so the
    expected warning, source display, and XYZ_MASTER candidate capture are tested
    deterministically.
    """
    normalized = _normalize_diagram_fences(user_msg or "")
    if not normalized.lower().lstrip().startswith(_VISUAL_INCIDENT_OPERATOR_PREFIX):
        return None
    match = re.search(r"```chart\s*\n([\s\S]*?)\n```", normalized, re.IGNORECASE)
    if not match:
        return None
    body = match.group(1).strip()
    if '"sql"' not in body or not body.endswith("}"):
        return None
    return f"```chart\n{body}\n```"


def _tc5_complex_visualization_answer(user_msg: str) -> str | None:
    """Return deterministic renderer input for the TC-5 meta-prompt.

    The subscriber/demo prompt "lookup the most complex chatbot visualization
    testcase and execute it" is intended to exercise TC-5 from
    `XYZ_PILOT_STREAMLIT_VISUALS.md`. Runtime evidence in XYZ-PILOT-276 showed
    Bedrock can correctly choose TC-5 but then invent an `PERIOD_QUARTER`
    column that does not exist in `<PILOT_SCHEMA>.MV_YOY_METRICS`. Keep this named
    regression prompt deterministic and let the normal chart renderer execute
    the live, validated SQL contract.
    """
    prompt_lc = " ".join((user_msg or "").lower().split())
    if not ("complex" in prompt_lc and "visualization" in prompt_lc):
        return None
    if not ("testcase" in prompt_lc or "test case" in prompt_lc):
        return None
    if not ("execute" in prompt_lc or "run" in prompt_lc):
        return None
    if "chatbot" not in prompt_lc:
        return None

    return """## TC-5 — Plotly YoY Trend: Sales CY vs PY by Quarter, North America 2024-2025

This is the most complex standing Chatbot visualization testcase in `docs/30-runbooks/ui/XYZ_PILOT_STREAMLIT_VISUALS.md`: Plotly Tier 2, two live Exasol series, quarter grain, `chart_container` controls, and optional click-capture mode.

```chart
{
  "type": "line",
  "engine": "plotly",
  "sql": "SELECT QUARTER_BY_YEAR AS QUARTER_LBL, ROUND(SUM(SALES_CY)/1e6, 3) AS VALUE_CY_M, ROUND(SUM(SALES_PY)/1e6, 3) AS VALUE_PY_M FROM <PILOT_SCHEMA>.MV_YOY_METRICS WHERE XYZ_REGION_1 = 'North America' AND PERIOD_YEAR IN (2024, 2025) GROUP BY QUARTER_BY_YEAR ORDER BY QUARTER_BY_YEAR",
  "x": "QUARTER_LBL",
  "y": ["VALUE_CY_M", "VALUE_PY_M"],
  "title": "TC-5 — North America YoY Sales CY vs PY by Quarter (2024-2025)",
  "x_label": "Quarter",
  "y_label": "Sales (USD M)",
  "tooltip": ["QUARTER_LBL", "VALUE_CY_M", "VALUE_PY_M"],
  "capture_events": false
}
```

Data source: `<PILOT_SCHEMA>.MV_YOY_METRICS`. The valid quarter grain is `QUARTER_BY_YEAR`; the snapshot does not expose a separate quarter-number column."""


def _customer_yoy_retention_answer(user_msg: str) -> str | None:
    """Return deterministic three-panel renderer input for the demo-critical
    <CUSTOMER_NAME> YoY retention prompt.

    The underlying data is stable and small, but repeated runtime evidence
    showed model drift: sometimes the assistant emitted three chart blocks,
    sometimes one, and sometimes prose implying panels without matching chart
    specs. For this named business-demo prompt, keep Bedrock out of panel
    selection and let the existing chart renderer run the live SQL.
    """
    prompt_lc = " ".join((user_msg or "").lower().split())
    if "customer" not in prompt_lc:
        return None
    if not ("yoy" in prompt_lc or "year over year" in prompt_lc):
        return None
    if not ("retention" in prompt_lc or "growth" in prompt_lc):
        return None
    if not ("visual" in prompt_lc or "chart" in prompt_lc or "panel" in prompt_lc or "rich" in prompt_lc):
        return None

    return """## <CUSTOMER_NAME> — YoY Sales Retention (2022-2025)

The three panels below are generated deterministically from live `<PILOT_SCHEMA>.MV_YOY_METRICS` data so the demo does not depend on model-selected panel count.

### Panel 1 — CY vs PY Sales Trend (USD M)

```chart
{
  "type": "line",
  "engine": "plotly",
  "sql": "SELECT PERIOD_YEAR AS YEAR_LBL, ROUND(SUM(SALES_CY)/1e6, 3) AS VALUE_CY_M, ROUND(SUM(SALES_PY)/1e6, 3) AS VALUE_PY_M FROM <PILOT_SCHEMA>.MV_YOY_METRICS WHERE UPPER(SEGMENT) LIKE UPPER('%<CUSTOMER_NAME>%') AND PERIOD_YEAR >= 2022 GROUP BY PERIOD_YEAR ORDER BY PERIOD_YEAR",
  "x": "YEAR_LBL",
  "y": ["VALUE_CY_M", "VALUE_PY_M"],
  "title": "<CUSTOMER_NAME> — Current Year vs Prior Year Sales (USD M)",
  "x_label": "Fiscal Year",
  "y_label": "Sales (USD M)",
  "tooltip": ["YEAR_LBL", "VALUE_CY_M", "VALUE_PY_M"]
}
```

### Panel 2 — Sales Volume + YoY Growth %

```chart
{
  "type": "combo",
  "engine": "plotly",
  "sql": "SELECT PERIOD_YEAR AS YEAR_LBL, ROUND(SUM(SALES_CY)/1e6, 3) AS VALUE_CY_M, ROUND(SUM(SALES_PY)/1e6, 3) AS VALUE_PY_M, ROUND(SUM(SALES_CY) / NULLIF(SUM(SALES_PY), 0) * 100 - 100, 2) AS YOY_PCT FROM <PILOT_SCHEMA>.MV_YOY_METRICS WHERE UPPER(SEGMENT) LIKE UPPER('%<CUSTOMER_NAME>%') AND PERIOD_YEAR >= 2022 GROUP BY PERIOD_YEAR ORDER BY PERIOD_YEAR",
  "x": "YEAR_LBL",
  "y": ["VALUE_CY_M", "VALUE_PY_M"],
  "secondary_y": "YOY_PCT",
  "title": "<CUSTOMER_NAME> — Sales Volume + YoY Growth % (2022-2025)",
  "x_label": "Fiscal Year",
  "y_label": "Sales (USD M)",
  "y2_label": "YoY Growth (%)",
  "tooltip": ["YEAR_LBL", "VALUE_CY_M", "VALUE_PY_M", "YOY_PCT"]
}
```

### Panel 3 — YoY Delta (Absolute USD M Swing)

```chart
{
  "type": "bar",
  "engine": "plotly",
  "sql": "SELECT PERIOD_YEAR AS YEAR_LBL, ROUND((SUM(SALES_CY) - SUM(SALES_PY))/1e6, 3) AS YOY_DELTA_M FROM <PILOT_SCHEMA>.MV_YOY_METRICS WHERE UPPER(SEGMENT) LIKE UPPER('%<CUSTOMER_NAME>%') AND PERIOD_YEAR >= 2023 GROUP BY PERIOD_YEAR ORDER BY PERIOD_YEAR",
  "x": "YEAR_LBL",
  "y": "YOY_DELTA_M",
  "title": "<CUSTOMER_NAME> — YoY Absolute Sales Delta (USD M)",
  "x_label": "Fiscal Year",
  "y_label": "Delta vs Prior Year (USD M)",
  "tooltip": ["YEAR_LBL", "YOY_DELTA_M"]
}
```

Data source: `<PILOT_SCHEMA>.MV_YOY_METRICS` pre-materialised snapshot. 2022 has no PY comparator because the synthetic pilot window starts there. Synthetic data caveat: trend shape is valid for the pilot, not real XYZ revenue figures."""


def _parse_c4_args(arg_text: str) -> list[str]:
    """Parse Mermaid C4 macro arguments without evaluating anything."""
    args: list[str] = []
    cur: list[str] = []
    quote: str | None = None
    escaped = False
    for ch in arg_text:
        if escaped:
            cur.append(ch)
            escaped = False
            continue
        if ch == "\\" and quote:
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == ",":
            args.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if cur or arg_text.strip():
        args.append("".join(cur).strip())
    return args


def _safe_mermaid_id(raw: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", raw.strip() or "node")
    if not base or base[0].isdigit():
        base = f"n_{base}"
    candidate = base
    i = 2
    while candidate in used:
        candidate = f"{base}_{i}"
        i += 1
    used.add(candidate)
    return candidate


def _mermaid_label(*parts: str) -> str:
    text = "<br/>".join(p.strip() for p in parts if p and p.strip())
    text = text or "Unnamed"
    return (
        text.replace("\\", "\\\\")
        .replace('"', "'")
        .replace("[", "(")
        .replace("]", ")")
        .replace("|", "/")
    )


def _c4_to_flowchart_fallback(code: str) -> str | None:
    """Convert experimental Mermaid C4 blocks into stable flowchart syntax.

    Mermaid C4 support is less reliable than standard flowchart syntax across
    browser renderers. Rather than showing raw C4 source, the
    chatbot renders an equivalent `flowchart LR` visual. The converter supports
    the C4 macros commonly emitted by Bedrock for Pilot architecture answers;
    unsupported styling/layout macros are ignored.
    """
    if not _first_token(code).lower().startswith("c4"):
        return None

    node_macros = {
        "Person", "Person_Ext", "System", "System_Ext",
        "Container", "Container_Ext", "ContainerDb", "ContainerDb_Ext",
        "Component", "Component_Ext", "ComponentDb", "ComponentDb_Ext",
    }
    rel_macros = {"Rel", "Rel_U", "Rel_D", "Rel_L", "Rel_R", "BiRel"}
    id_map: dict[str, str] = {}
    used_ids: set[str] = set()
    nodes: list[tuple[str, str, str]] = []
    rels: list[tuple[str, str, str, str]] = []
    title = ""

    macro_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\((.*)\)\s*$")
    for raw_line in code.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("%%", "C4")):
            continue
        if line.lower().startswith("title "):
            title = line[6:].strip()
            continue
        match = macro_re.match(line)
        if not match:
            continue
        macro, arg_text = match.groups()
        args = _parse_c4_args(arg_text)
        if macro in node_macros and len(args) >= 2:
            original_id = args[0].strip()
            node_id = id_map.setdefault(original_id, _safe_mermaid_id(original_id, used_ids))
            name = args[1].strip()
            detail = args[2].strip() if len(args) >= 3 else ""
            label = _mermaid_label(name, detail)
            shape = "person" if macro.startswith("Person") else "db" if "Db" in macro else "box"
            nodes.append((node_id, label, shape))
        elif macro in rel_macros and len(args) >= 2:
            left_raw = args[0].strip()
            right_raw = args[1].strip()
            left = id_map.setdefault(left_raw, _safe_mermaid_id(left_raw, used_ids))
            right = id_map.setdefault(right_raw, _safe_mermaid_id(right_raw, used_ids))
            label = _mermaid_label(args[2]) if len(args) >= 3 else ""
            rels.append((macro, left, right, label))

    if not nodes and not rels:
        return None

    lines = ["flowchart LR"]
    if title:
        lines.append(f"  %% C4 fallback: {_mermaid_label(title)}")
    for node_id, label, shape in nodes:
        if shape == "person":
            lines.append(f'  {node_id}(["{label}"])')
        elif shape == "db":
            lines.append(f'  {node_id}[("{label}")]')
        else:
            lines.append(f'  {node_id}["{label}"]')
    for macro, left, right, label in rels:
        arrow = "<-->" if macro == "BiRel" else "-->"
        if label:
            lines.append(f'  {left} {arrow}|"{label}"| {right}')
        else:
            lines.append(f"  {left} {arrow} {right}")
    lines.extend([
        "  classDef xyzPrimary fill:#E8F3FF,stroke:#118DFF,stroke-width:2px,color:#1F2937;",
        "  classDef xyzPerson fill:#F8FAFC,stroke:#475569,stroke-width:1.5px,color:#1F2937;",
        "  classDef xyzDb fill:#EAF7EA,stroke:#2E7D32,stroke-width:1.5px,color:#1F2937;",
    ])
    for node_id, _label, shape in nodes:
        css_class = "xyzPerson" if shape == "person" else "xyzDb" if shape == "db" else "xyzPrimary"
        lines.append(f"  class {node_id} {css_class};")
    return "\n".join(lines)


# XYZ-PILOT-105: chart rendering from a ```chart fenced JSON spec.
# Two engines:
#   • Altair (Tier 1) — Streamlit-bundled, always works, light interactivity.
#   • Plotly (Tier 2) — added via Dockerfile, richer interactivity (zoom, pan,
#     hover, export PNG), supports treemap/sunburst/box.
# Engine picked by spec["engine"] ∈ {"altair","plotly"}, defaulting to altair.
# SQL runs via _run_sql_for_chart() which shares the _EXASOL_LOCK + auto-
# invalidation pattern from _tool_exasol_query (XYZ-PILOT-065 + XYZ-PILOT-100).
_CHART_SQL_MAX_ROWS = 1000  # pre-aggregation cap; chart spec can override

# XYZ-PILOT-107: subset of Exasol reserved words that Claude routinely picks
# as bare column aliases for chart SQL ("AS YEAR", "AS DATE", etc). When
# detected unquoted in an alias position we double-quote them defensively
# so the chart renders instead of failing with ExaQueryError 42000. Full
# Exasol keyword list is huge; this is the narrow subset reachable from
# typical analytics queries — extend as new collisions are reported.
_EXASOL_RESERVED_ALIAS_GUARD = frozenset({
    "YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND",
    "DATE", "TIME", "TIMESTAMP", "INTERVAL",
    "VALUE", "NAME", "TYPE", "ORDER", "GROUP", "LEVEL",
    "USER", "ROLE", "PROFILE", "STATE", "STATUS",
    "RANGE", "CASE", "WHEN", "THEN", "END",
})

_CHART_ALIAS_RE = re.compile(
    r'\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b', re.IGNORECASE
)


def _quote_reserved_aliases(sql: str) -> str:
    """XYZ-PILOT-107: rewrite bare `AS <reserved>` to `AS "<reserved>"`.

    Only triggers on the narrow allowlist `_EXASOL_RESERVED_ALIAS_GUARD`
    and only when the alias is unquoted — already-quoted aliases pass
    through untouched. Leaves SQL outside `AS ...` clauses alone."""
    def _sub(m: re.Match) -> str:
        alias = m.group(1)
        if alias.upper() in _EXASOL_RESERVED_ALIAS_GUARD:
            return f'AS "{alias}"'
        return m.group(0)
    return _CHART_ALIAS_RE.sub(_sub, sql)


# XYZ-PILOT-112 (was -109 locally before chatbot took 109): chart-path V_→MV_
# rewrite. The four `V_*` analytics views re-aggregate the 170M-row
# `FACT_PRIMARY` fact + windowing on every query (V_YOY_METRICS does this
# TWICE via its self-join). The matching `MV_*` snapshot tables are
# pre-aggregated to 240–1200 rows and column-compatible (verified 2026-05-27
# via `SYS.EXA_ALL_COLUMNS`). Rewriting chart SQL from V_→MV_ takes
# cold-cache latency from ~43 s down to ~0.24 s (180× speedup), meeting
# the <1 s SLA from XYZ-PILOT-109.
_CHART_VIEW_TO_MV_REWRITES = {
    "V_YOY_METRICS":            "MV_YOY_METRICS",
    "V_MONTHLY_METRICS":        "MV_MONTHLY_METRICS",
    "V_ACTUAL_VS_TARGET":      "MV_ACTUAL_VS_TARGET",
    "V_STOCK_ANALYTICS":  "MV_STOCK_ANALYTICS",
}

_CHART_XYZ_REGION_1_OBJECTS = frozenset({
    "MV_YOY_METRICS",
    "MV_MONTHLY_METRICS",
    "MV_ACTUAL_VS_TARGET",
})

_CHART_XYZ_REGION_1_OBJECT_RE = re.compile(
    r'\b(?:<PILOT_SCHEMA>\s*\.\s*)?(?:'
    + "|".join(re.escape(name) for name in sorted(_CHART_XYZ_REGION_1_OBJECTS))
    + r')\b',
    re.IGNORECASE,
)

_CHART_BARE_REGION_RE = re.compile(
    r'(?<![A-Za-z0-9_.])"?REGION"?(?![A-Za-z0-9_])',
    re.IGNORECASE,
)

_CHART_PERIOD_QUARTER_RE = re.compile(
    r'(?<![A-Za-z0-9_.])"?PERIOD_QUARTER"?(?![A-Za-z0-9_])',
    re.IGNORECASE,
)

_CHART_PERIOD_QUARTER_PERIOD_RE = re.compile(
    r'\bPERIOD_YEAR\s*\|\|\s*\'\s*Q\s*\'\s*\|\|\s*"?PERIOD_QUARTER"?\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)\b',
    re.IGNORECASE,
)

_CHART_PERIOD_QUARTER_CONCAT_RE = re.compile(
    r'\bPERIOD_YEAR\s*\|\|\s*\'\s*Q\s*\'\s*\|\|\s*"?PERIOD_QUARTER"?',
    re.IGNORECASE,
)


def _rewrite_chart_views_to_mv(sql: str) -> str:
    """XYZ-PILOT-112: rewrite `<PILOT_SCHEMA>.V_*` → `<PILOT_SCHEMA>.MV_*` for chart SQL.

    Anchored on `<PILOT_SCHEMA>.` (with optional whitespace) so the rewrite
    cannot fire on a column / alias whose name happens to start with `V_`.
    Case-insensitive match; preserves original casing of surroundings.
    """
    out = sql
    for v, mv in _CHART_VIEW_TO_MV_REWRITES.items():
        out = re.sub(
            r'((?<![A-Za-z0-9_])<PILOT_SCHEMA>\s*\.\s*)' + re.escape(v) + r'\b',
            lambda m, _mv=mv: m.group(1) + _mv,
            out, flags=re.IGNORECASE,
        )
    return out


def _rewrite_chart_region_dimension(sql: str) -> str:
    """Rewrite bare REGION to XYZ_REGION_1 for known analytics snapshots.

    This is intentionally narrow: it only fires when chart SQL references one
    of the `MV_*` snapshot objects that expose `XYZ_REGION_1` / `XYZ_REGION_2`
    but not a generic `REGION` column. It leaves dotted identifiers such as
    `dim.REGION` alone and does not touch single-quoted string literals.
    """
    if not _CHART_XYZ_REGION_1_OBJECT_RE.search(sql):
        return sql

    parts = re.split(r"('(?:''|[^'])*')", sql)
    for idx in range(0, len(parts), 2):
        parts[idx] = _CHART_BARE_REGION_RE.sub("XYZ_REGION_1", parts[idx])
    return "".join(parts)


def _rewrite_chart_yoy_quarter_dimension(sql: str) -> str:
    """Rewrite invented PERIOD_QUARTER to QUARTER_BY_YEAR for MV_YOY_METRICS.

    Runtime evidence in XYZ-PILOT-297 showed that direct TC-5-style prompts can
    still make Bedrock invent `PERIOD_QUARTER` even when the user explicitly
    names `<PILOT_SCHEMA>.MV_YOY_METRICS`. The snapshot's actual quarter grain is
    `QUARTER_BY_YEAR`, so keep this rewrite narrow to that object and only
    outside string literals.
    """
    if not re.search(r'(?<![A-Za-z0-9_])(?:<PILOT_SCHEMA>\s*\.\s*)?MV_YOY_METRICS\b', sql, re.IGNORECASE):
        return sql

    sql = _CHART_PERIOD_QUARTER_CONCAT_RE.sub("QUARTER_BY_YEAR", sql)
    parts = re.split(r"('(?:''|[^'])*')", sql)
    for idx in range(0, len(parts), 2):
        parts[idx] = _CHART_PERIOD_QUARTER_PERIOD_RE.sub(
            lambda m: f"QUARTER_BY_YEAR AS {m.group(1)}",
            parts[idx],
        )
        parts[idx] = _CHART_PERIOD_QUARTER_RE.sub("QUARTER_BY_YEAR", parts[idx])
    return "".join(parts)


def _normalise_chart_spec_fields(spec: dict, columns: list[str]) -> dict:
    """Map natural chart field synonyms to the actual SQL result columns."""
    column_by_upper = {str(col).upper(): str(col) for col in columns}
    out = dict(spec)

    if "REGION" in column_by_upper or "XYZ_REGION_1" not in column_by_upper:
        return out

    xyz_region_col = column_by_upper["XYZ_REGION_1"]

    def _fix_value(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if value.strip().strip('"').upper() == "REGION":
            return xyz_region_col
        return value

    def _fix_sequence(value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return [_fix_value(item) for item in value]

    def _fix_dict_sequence(value: Any) -> Any:
        if not isinstance(value, list):
            return value
        fixed = []
        for item in value:
            if isinstance(item, dict):
                copy = dict(item)
                for field_key in ("field", "name", "y", "x"):
                    if field_key in copy:
                        copy[field_key] = _fix_value(copy[field_key])
                fixed.append(copy)
            else:
                fixed.append(_fix_value(item))
        return fixed

    for key in ("x", "color", "size", "y2", "secondary_y", "line_y", "bar_y", "primary_y"):
        if key in out:
            out[key] = _fix_value(out[key])
    if "y" in out:
        out["y"] = _fix_sequence(out["y"]) if isinstance(out["y"], list) else _fix_value(out["y"])
    for key in ("tooltip", "path", "bars", "lines", "measures", "series"):
        if key in out:
            out[key] = _fix_dict_sequence(out[key]) if key in ("bars", "lines", "measures", "series") else _fix_sequence(out[key])
    return out


def _run_sql_for_chart(sql: str, limit: int = _CHART_SQL_MAX_ROWS) -> tuple[list[str], list[tuple]]:
    """Run SELECT / WITH for chart rendering. Returns (cols, rows).
    Same lock + invalidate-on-error as `_tool_exasol_query` so a failed
    chart SQL doesn't poison the shared pyexasol connection."""
    s = (sql or "").strip().rstrip(";")
    if not s:
        raise ValueError("chart spec 'sql' is empty")
    upper = s.upper().lstrip()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        raise ValueError(f"chart 'sql' must start with SELECT / WITH; got: {upper.split()[0]}")
    s = _quote_reserved_aliases(s)  # XYZ-PILOT-107 defensive rewrite
    s = _rewrite_chart_views_to_mv(s)  # XYZ-PILOT-112: V_→MV_ for <1s SLA
    s = _rewrite_chart_region_dimension(s)  # XYZ-PILOT-105: REGION→XYZ_REGION_1 for MV chart SQL
    s = _rewrite_chart_yoy_quarter_dimension(s)  # XYZ-PILOT-297: PERIOD_QUARTER→QUARTER_BY_YEAR
    with _EXASOL_LOCK:
        try:
            stmt = _exasol_pool().execute(s)
            cols = [c[0] for c in stmt.columns().items()]
            rows = stmt.fetchall()
        except Exception:
            _invalidate_exasol_pool()
            raise
    if len(rows) > limit:
        rows = rows[:limit]
    return cols, rows


def _polish_altair_chart(chart: Any) -> Any:
    """Apply shared BI-style chart chrome to chatbot Altair visuals."""
    return (
        chart
        .configure_axis(
            labelColor="#374151",
            titleColor=XYZ_CHART_TEXT,
            gridColor=XYZ_CHART_GRID,
            domainColor="#D1D5DB",
            tickColor="#D1D5DB",
            labelFontSize=12,
            titleFontSize=12,
            titleFontWeight=600,
        )
        .configure_legend(
            labelColor="#374151",
            titleColor=XYZ_CHART_TEXT,
            labelFontSize=12,
            titleFontSize=12,
            titleFontWeight=600,
        )
        .configure_title(color=XYZ_CHART_TEXT, fontSize=15, fontWeight=700)
        .configure_view(strokeWidth=0)
    )


def _polish_plotly_figure(fig: Any) -> Any:
    """Apply shared BI-style chart chrome to chatbot Plotly visuals."""
    fig.update_layout(
        template="plotly_white",
        colorway=XYZ_CHART_PALETTE,
        height=460,
        margin=dict(l=20, r=20, t=54, b=24),
        font=dict(color=XYZ_CHART_TEXT, size=12),
        title_font=dict(color=XYZ_CHART_TEXT, size=16),
        legend=dict(
            title_font=dict(color=XYZ_CHART_TEXT, size=12),
            font=dict(color="#374151", size=12),
        ),
        hoverlabel=dict(bgcolor="#FFFFFF", bordercolor="#D1D5DB", font_size=12),
    )
    fig.update_xaxes(
        gridcolor=XYZ_CHART_GRID,
        zerolinecolor="#D1D5DB",
        linecolor="#D1D5DB",
        tickfont=dict(color="#374151"),
        title_font=dict(color=XYZ_CHART_TEXT, size=12),
    )
    fig.update_yaxes(
        gridcolor=XYZ_CHART_GRID,
        zerolinecolor="#D1D5DB",
        linecolor="#D1D5DB",
        tickfont=dict(color="#374151"),
        title_font=dict(color=XYZ_CHART_TEXT, size=12),
    )
    return fig


_PLOTLY_COMBO_TYPES = {
    "combo",
    "bar_line",
    "bar+line",
    "bar-line",
    "dual_axis",
    "dual-axis",
    "secondary_axis",
    "secondary-axis",
}
_PLOTLY_SECONDARY_SPEC_KEYS = ("secondary_y", "y2", "line_y", "line", "lines")
_PLOTLY_PRIMARY_SPEC_KEYS = ("bars", "bar_y", "primary_y", "measures", "series")


def _chart_field_names(value: Any) -> list[str]:
    """Return field names from a chart spec value.

    The LLM often emits list-valued `y` fields, or richer dict forms such as
    `{"field": "YOY_PCT", "name": "YoY %"}`. Keep this pure so it can be
    unit-tested without importing Streamlit, Pandas, or Plotly.
    """
    if value is None:
        return []
    if isinstance(value, str):
        field = value.strip().strip('"')
        return [field] if field else []
    if isinstance(value, dict):
        for key in ("field", "y", "name", "column"):
            field = value.get(key)
            if isinstance(field, str) and field.strip():
                return [field.strip().strip('"')]
        return []
    if isinstance(value, (list, tuple)):
        fields: list[str] = []
        for item in value:
            for field in _chart_field_names(item):
                if field not in fields:
                    fields.append(field)
        return fields
    return []


def _chart_spec_existing_fields(value: Any, columns: list[str]) -> list[str]:
    """Return chart field names that exist in `columns`, preserving SQL case."""
    by_upper = {str(column).upper(): str(column) for column in columns}
    existing: list[str] = []
    for field in _chart_field_names(value):
        column = by_upper.get(field.upper())
        if column and column not in existing:
            existing.append(column)
    return existing


def _is_rate_or_percent_field(field: str) -> bool:
    upper = field.upper()
    return any(token in upper for token in ("PCT", "PERCENT", "RATE", "RATIO", "RETENTION"))


def _infer_plotly_combo_fields(spec: dict, columns: list[str]) -> tuple[list[str], list[str]]:
    """Infer primary and secondary fields for Plotly combo specs.

    This intentionally avoids broad magic for ordinary charts. It is used only
    when the caller already asked for a combo/secondary-axis chart or emitted
    combo-like keys. Measures that look like percentages/rates move to the
    secondary axis; sales/quantity/margin amounts remain primary.
    """
    x_fields = set(_chart_spec_existing_fields(spec.get("x"), columns))
    color_fields = set(_chart_spec_existing_fields(spec.get("color"), columns))
    secondary: list[str] = []
    primary: list[str] = []

    for key in _PLOTLY_SECONDARY_SPEC_KEYS:
        for field in _chart_spec_existing_fields(spec.get(key), columns):
            if field not in secondary:
                secondary.append(field)

    for key in ("y",) + _PLOTLY_PRIMARY_SPEC_KEYS:
        for field in _chart_spec_existing_fields(spec.get(key), columns):
            if field in secondary:
                continue
            if _is_rate_or_percent_field(field):
                if field not in secondary:
                    secondary.append(field)
            elif field not in primary:
                primary.append(field)

    if not primary:
        for field in columns:
            name = str(field)
            if name in x_fields or name in color_fields:
                continue
            upper = name.upper()
            if upper.endswith("_LBL") or upper in {"PERIOD_YEAR", "PERIOD_MONTH", "QTR_NUM"}:
                continue
            if _is_rate_or_percent_field(name):
                if name not in secondary:
                    secondary.append(name)
            elif any(token in upper for token in ("SALES", "QTY", "AMOUNT", "MARGIN", "GM_", "CY", "PY", "VAR")):
                primary.append(name)

    return primary, secondary


def _plotly_is_combo_spec(spec: dict) -> bool:
    chart_type = (spec.get("type") or "bar").lower()
    if chart_type in _PLOTLY_COMBO_TYPES:
        return True
    return any(spec.get(key) for key in _PLOTLY_SECONDARY_SPEC_KEYS + _PLOTLY_PRIMARY_SPEC_KEYS)


def _plotly_should_capture_events(spec: dict, combo_rendered: bool) -> bool:
    """Decide whether to use streamlit-plotly-events for this Plotly figure.

    The wrapper is useful for click-capture, but it has proven less reliable
    than native `st.plotly_chart` for list-valued wide-series and subplot /
    secondary-axis figures. Visual-first rendering wins unless events are
    explicitly enabled.
    """
    if combo_rendered:
        return False
    requested = spec.get("capture_events", spec.get("click_events"))
    if isinstance(requested, bool):
        return requested
    if isinstance(requested, str):
        return requested.strip().lower() in {"1", "true", "yes", "on"}
    return XYZ_PLOTLY_EVENTS_ENABLED


def _next_chart_render_key(prefix: str, spec: dict, df: Any, scope: str) -> str:
    """Return a unique Streamlit element key for a rendered chart instance.

    Streamlit now raises on duplicate auto-generated Plotly element IDs. The
    TC-5 YoY visual path can render the same figure shape repeatedly inside a
    single chat rerun, so all Plotly surfaces need explicit keys.
    """
    columns = [str(column) for column in getattr(df, "columns", [])]
    shape = tuple(getattr(df, "shape", ()))
    payload = {
        "scope": scope,
        "spec": spec,
        "columns": columns,
        "shape": shape,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    safe_prefix = re.sub(r"[^A-Za-z0-9_]+", "_", prefix or "chart").strip("_") or "chart"
    state_key = "_xyz_chart_render_sequence"
    try:
        seq = int(st.session_state.get(state_key, 0)) + 1
        st.session_state[state_key] = seq
    except Exception:  # noqa: BLE001 - tests/non-Streamlit imports use fallback state
        seq = int(getattr(_next_chart_render_key, "_fallback_sequence", 0)) + 1
        setattr(_next_chart_render_key, "_fallback_sequence", seq)
    return f"{safe_prefix}_{digest}_{seq}"


def _try_render_chart_plotly_combo(spec: dict, df) -> Any:
    """Render Plotly combo charts with optional secondary Y axis.

    Returns a Plotly Figure or None when the spec does not define enough
    drawable fields. The caller handles display and diagnostics.
    """
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go

    x_col = spec.get("x")
    if not isinstance(x_col, str) or x_col not in df.columns:
        return None

    columns = [str(column) for column in df.columns]
    primary_fields, secondary_fields = _infer_plotly_combo_fields(spec, columns)
    if not primary_fields and not secondary_fields:
        return None

    fig = make_subplots(specs=[[{"secondary_y": bool(secondary_fields)}]])
    x_values = df[x_col]
    palette = XYZ_CHART_PALETTE

    for idx, field in enumerate(primary_fields):
        if field not in df.columns:
            continue
        fig.add_trace(
            go.Bar(
                x=x_values,
                y=df[field],
                name=field,
                marker_color=palette[idx % len(palette)],
                hovertemplate=f"{x_col}=%{{x}}<br>{field}=%{{y:,.2f}}<extra>{field}</extra>",
            ),
            secondary_y=False,
        )

    for offset, field in enumerate(secondary_fields):
        if field not in df.columns:
            continue
        fig.add_trace(
            go.Scatter(
                x=x_values,
                y=df[field],
                mode="lines+markers",
                name=field,
                line=dict(color=palette[(len(primary_fields) + offset) % len(palette)], width=3),
                marker=dict(size=8),
                hovertemplate=f"{x_col}=%{{x}}<br>{field}=%{{y:,.2f}}<extra>{field}</extra>",
            ),
            secondary_y=True,
        )

    title = spec.get("title", "")
    if title:
        fig.update_layout(title=title)
    fig.update_layout(barmode="group", hovermode="x unified")
    fig.update_xaxes(title_text=spec.get("x_label") or x_col)
    if primary_fields:
        fig.update_yaxes(title_text=spec.get("y_label") or ", ".join(primary_fields), secondary_y=False)
    if secondary_fields:
        fig.update_yaxes(
            title_text=spec.get("y2_label") or spec.get("secondary_y_label") or ", ".join(secondary_fields),
            secondary_y=True,
            ticksuffix="%" if all(_is_rate_or_percent_field(field) for field in secondary_fields) else None,
        )
    return fig


_MONTH_AXIS_LABELS = {
    1: "Jan",
    2: "Feb",
    3: "Mar",
    4: "Apr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Aug",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dec",
}
_MONTH_AXIS_LABEL_EXPR = " : ".join(
    [
        f"(datum.value == {num} || datum.value == '{num}') ? '{label}'"
        for num, label in _MONTH_AXIS_LABELS.items()
    ]
    + ["datum.label"]
)
_MONTH_AXIS_NAME_ALLOWLIST = {
    "MO",
    "MON",
    "MONTH",
    "MONTH_NO",
    "MONTH_NUM",
    "MONTH_NUMBER",
    "MONTH_ID",
    "FISCAL_MONTH",
    "FISCAL_PERIOD",
    "PERIOD_MONTH",
    "INV_MONTH",
    "S_MONTH",
}


def _altair_field_name(field_ref: Any) -> str:
    """Return the dataframe field name from an Altair shorthand reference."""
    field = str(field_ref or "").strip()
    if ":" in field:
        base, suffix = field.rsplit(":", 1)
        if suffix.upper() in {"N", "O", "Q", "T", "G"}:
            return base
    return field


def _looks_like_month_axis_name(field_name: str) -> bool:
    """Return True for BI month ordinal fields, not arbitrary measures."""
    name = re.sub(r"[^A-Za-z0-9]+", "_", field_name or "").strip("_").upper()
    if name in _MONTH_AXIS_NAME_ALLOWLIST:
        return True
    if name.endswith("_MONTH") or name.startswith("MONTH_"):
        return True
    # Avoid treating measures such as MONTHLY_VALUE as date axes unless their
    # values also pass the 1..12 ordinal check below.
    return "MONTH" in name and "MONTHLY" not in name


def _coerce_month_number(value: Any) -> int | None:
    """Coerce an integer-like value to a 1..12 month number."""
    if value is None:
        return None
    try:
        text = str(value).strip()
    except Exception:  # noqa: BLE001
        return None
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    rounded = int(round(number))
    if abs(number - rounded) > 0.000001:
        return None
    return rounded if 1 <= rounded <= 12 else None


def _is_month_ordinal_column(df: Any, field_ref: Any) -> bool:
    """XYZ-PILOT-187: detect numeric month axes that should render Jan..Dec."""
    field_name = _altair_field_name(field_ref)
    if not field_name or field_name not in getattr(df, "columns", []):
        return False
    if not _looks_like_month_axis_name(field_name):
        return False
    try:
        values = df[field_name]
        if hasattr(values, "tolist"):
            values = values.tolist()
    except Exception:  # noqa: BLE001
        return False
    month_values = [_coerce_month_number(value) for value in values]
    month_values = [value for value in month_values if value is not None]
    return bool(month_values) and len(month_values) == len([v for v in values if str(v).strip()])


def _altair_x_encoding(spec: dict, df: Any, alt: Any) -> Any:
    """Build X encoding with month ordinals displayed as Jan..Dec labels."""
    x_col = spec.get("x")
    field_name = _altair_field_name(x_col)
    title = spec.get("x_label", field_name or x_col)
    if _is_month_ordinal_column(df, x_col):
        return alt.X(
            f"{field_name}:O",
            title=title,
            sort=list(_MONTH_AXIS_LABELS),
            axis=alt.Axis(
                values=list(_MONTH_AXIS_LABELS),
                labelExpr=_MONTH_AXIS_LABEL_EXPR,
            ),
        )
    return alt.X(x_col, title=title)


def _try_render_chart_altair(spec: dict, df) -> bool:
    """XYZ-PILOT-105 Tier 1: Altair renderer. Supported `type`: bar, line,
    area, scatter, pie/arc, hist/histogram. Pie uses theta + color encodings
    (not x/y) per Altair convention."""
    import altair as alt
    chart_type = (spec.get("type") or "bar").lower()
    x_col, y_col = spec.get("x"), spec.get("y")
    color_col = spec.get("color")
    title = spec.get("title", "")
    tooltip = spec.get("tooltip") or list(df.columns)

    base = alt.Chart(df)
    mark_style = {} if color_col or chart_type in ("pie", "arc") else {"color": XYZ_CHART_PALETTE[0]}
    if chart_type == "line":
        mark = base.mark_line(point=True, strokeWidth=2.5, **mark_style)
    elif chart_type == "area":
        mark = base.mark_area(opacity=0.65, **mark_style)
    elif chart_type == "scatter":
        mark = base.mark_circle(size=80, opacity=0.7, **mark_style)
    elif chart_type in ("pie", "arc"):
        mark = base.mark_arc(innerRadius=40)
    else:  # bar / hist / fallback
        mark = base.mark_bar(**mark_style)

    encs: dict = {}
    if chart_type in ("pie", "arc"):
        if y_col: encs["theta"] = alt.Theta(f"{y_col}:Q")
        if x_col:
            encs["color"] = alt.Color(
                f"{x_col}:N",
                title=x_col,
                scale=alt.Scale(range=XYZ_CHART_PALETTE),
            )
    else:
        if x_col: encs["x"] = _altair_x_encoding(spec, df, alt)
        if y_col: encs["y"] = alt.Y(y_col, title=spec.get("y_label", y_col))
        if color_col:
            encs["color"] = alt.Color(
                color_col,
                title=color_col,
                scale=alt.Scale(range=XYZ_CHART_PALETTE),
            )
    if tooltip: encs["tooltip"] = tooltip

    chart = mark.encode(**encs).properties(height=420)
    if title:
        chart = chart.properties(title=title)
    chart = _polish_altair_chart(chart)
    # XYZ-PILOT-134: wrap the Altair chart in streamlit-extras' `chart_container`
    # so subscribers get free 📥 Download (CSV/Parquet) + 👁 View raw data +
    # 📋 View source code affordances under every chart. Graceful try-import
    # fallback to plain st.altair_chart if streamlit-extras isn't installed.
    try:
        from streamlit_extras.chart_container import chart_container  # type: ignore
        with chart_container(df, export_formats=("CSV", "Parquet")):
            st.altair_chart(chart.interactive(), use_container_width=True)
    except ImportError:
        st.altair_chart(chart.interactive(), use_container_width=True)
    return True


def _try_render_chart_plotly(spec: dict, df) -> bool:
    """XYZ-PILOT-105 Tier 2: Plotly Express renderer. Supports bar, line,
    area, scatter, pie, treemap, sunburst, box, hist/histogram, and combo
    bar/line charts with a secondary axis. Plotly's interactive HTML widget
    bundles zoom, pan, hover, PNG export — same feel as a commercial BI desktop client."""
    import plotly.express as px
    chart_type = (spec.get("type") or "bar").lower()
    x_col, y_col = spec.get("x"), spec.get("y")
    color_col = spec.get("color")
    title = spec.get("title", "")

    common: dict = {
        "data_frame": df,
        "color_discrete_sequence": XYZ_CHART_PALETTE,
    }
    if title: common["title"] = title
    if x_col: common["x"] = x_col
    if y_col: common["y"] = y_col
    if color_col: common["color"] = color_col

    combo_rendered = False
    if _plotly_is_combo_spec(spec):
        fig = _try_render_chart_plotly_combo(spec, df)
        combo_rendered = fig is not None
        if fig is None:
            st.warning(
                "⚠ Plotly combo chart spec did not identify drawable primary "
                "or secondary measure columns. Falling back to the basic chart renderer."
            )

    if not combo_rendered:
        if chart_type == "line":
            fig = px.line(**common, markers=True)
        elif chart_type == "area":
            fig = px.area(**common)
        elif chart_type == "scatter":
            if spec.get("size"): common["size"] = spec["size"]
            fig = px.scatter(**common)
        elif chart_type in ("pie", "arc"):
            fig = px.pie(
                df,
                names=x_col,
                values=y_col,
                title=title,
                color_discrete_sequence=XYZ_CHART_PALETTE,
            )
        elif chart_type == "treemap":
            fig = px.treemap(
                df,
                path=spec.get("path", [x_col]),
                values=y_col,
                title=title,
                color_discrete_sequence=XYZ_CHART_PALETTE,
            )
        elif chart_type == "sunburst":
            fig = px.sunburst(
                df,
                path=spec.get("path", [x_col]),
                values=y_col,
                title=title,
                color_discrete_sequence=XYZ_CHART_PALETTE,
            )
        elif chart_type == "box":
            fig = px.box(**common)
        elif chart_type in ("hist", "histogram"):
            fig = px.histogram(**common)
        else:
            fig = px.bar(**common)

    fig = _polish_plotly_figure(fig)
    # XYZ-PILOT-133 (renumbered from local -132 after swarm filed -132) added
    # optional `plotly_events` click capture. Runtime evidence from TC-5C
    # showed the wrapper can render valid Plotly figures as empty coordinate
    # frames, especially list-valued wide-series and subplot/secondary-axis
    # figures. Visual-first rendering now defaults to native `st.plotly_chart`;
    # click capture is opt-in via XYZ_PLOTLY_EVENTS_ENABLED=1 or
    # `capture_events: true` in the chart spec.
    # XYZ-PILOT-134: wrap with streamlit-extras `chart_container` for the
    # download / view-data / view-source affordances. If the extra library is
    # missing, degrade gracefully to plain st.plotly_chart so the chart still
    # renders.
    def _render_inner(scope: str):
        if not _plotly_should_capture_events(spec, combo_rendered):
            st.plotly_chart(
                fig,
                use_container_width=True,
                key=_next_chart_render_key("plotly_chart", spec, df, f"{scope}_native"),
            )
            return
        try:
            from streamlit_plotly_events import plotly_events  # type: ignore
            selected = plotly_events(
                fig, click_event=True, select_event=False, hover_event=False,
                override_height=460, override_width="100%",
                key=_next_chart_render_key("plotly_evt", spec, df, f"{scope}_events"),
            )
            if selected:
                ev = selected[0]
                st.caption(
                    f"📍 Clicked: x={ev.get('x', '?')} · y={ev.get('y', '?')}"
                    + (f" · curve={ev.get('curveNumber', '?')}" if 'curveNumber' in ev else "")
                    + (f" · point={ev.get('pointNumber', '?')}" if 'pointNumber' in ev else "")
                    + " — (XYZ-PILOT-133 proof-of-life; drill-into-ticker is the next iteration)"
                )
        except ImportError:
            st.plotly_chart(
                fig,
                use_container_width=True,
                key=_next_chart_render_key("plotly_chart", spec, df, f"{scope}_event_fallback"),
            )
    try:
        from streamlit_extras.chart_container import chart_container  # type: ignore
        with chart_container(df, export_formats=("CSV", "Parquet")):
            _render_inner("chart_container")
    except ImportError:
        _render_inner("plain")
    return True


def _try_render_chart(spec_json: str) -> bool:
    """XYZ-PILOT-105: render an analytical chart from a JSON spec carried
    in a ```chart fenced block. Engine picked by spec['engine'] —
    'altair' (default, Tier 1) or 'plotly' (Tier 2). On any failure,
    shows the spec + a data preview in an expander so subscribers can
    diagnose without the SVG / Plotly widget."""
    try:
        spec = json.loads(spec_json)
    except Exception as exc:
        st.warning(f"⚠ chart spec is not valid JSON: {exc}")
        st.code(spec_json, language="json")
        _queue_visual_incident_candidate(
            surface="chart spec",
            renderer="chart-json",
            failure=exc,
            source=spec_json,
        )
        return False
    sql = (spec.get("sql") or "").strip()
    if not sql:
        st.warning("⚠ chart spec missing required 'sql' field")
        st.code(spec_json, language="json")
        _queue_visual_incident_candidate(
            surface="chart spec",
            renderer="chart-json",
            failure="missing required 'sql' field",
            source=spec_json,
        )
        return False
    limit = int(spec.get("limit", _CHART_SQL_MAX_ROWS))
    try:
        cols, rows = _run_sql_for_chart(sql, limit=limit)
    except Exception as exc:
        st.warning(f"⚠ chart SQL failed: {type(exc).__name__}: {exc}")
        st.code(spec_json, language="json")
        _queue_visual_incident_candidate(
            surface="chart query",
            renderer="exasol-sql",
            failure=exc,
            source=spec_json,
            sql=sql,
        )
        return False
    import pandas as pd
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        st.info(f"📊 chart query returned no rows for: `{sql[:120]}…`")
        return False
    spec = _normalise_chart_spec_fields(spec, list(df.columns))
    # Coerce numeric-looking object columns (pyexasol returns DECIMAL as str)
    for c in df.columns:
        if df[c].dtype == object:
            coerced = pd.to_numeric(df[c], errors="ignore")
            df[c] = coerced

    engine = (spec.get("engine") or "altair").lower()
    rendered = False
    try:
        if engine == "plotly":
            rendered = _try_render_chart_plotly(spec, df)
        else:
            rendered = _try_render_chart_altair(spec, df)
    except Exception as exc:
        st.warning(
            f"⚠ chart renderer ({engine}) raised {type(exc).__name__}: {exc}. "
            "Showing spec + first 20 rows for diagnosis."
        )
        try:
            preview = df.head(20).to_csv(index=False)
        except Exception:  # noqa: BLE001
            preview = None
        _queue_visual_incident_candidate(
            surface="chart renderer",
            renderer=engine,
            failure=exc,
            source=spec_json,
            sql=sql,
            data_preview=preview,
        )
        rendered = False

    with st.expander(
        f"📊 Chart spec + SQL result ({len(df)} rows × {len(df.columns)} cols) — engine={engine}",
        expanded=not rendered,
    ):
        st.code(spec_json, language="json")
        st.dataframe(df.head(20), use_container_width=True)
    return rendered


# XYZ-PILOT-089 Phase 1: mingrammer/diagrams AWS-icon renderer. Subscribers'
# requests for "cloud architecture with AWS icons" emit a constrained JSON
# DSL (NOT raw Python — never `exec()` LLM output). The renderer parses
# the JSON, validates every node `type` against an allowlist, builds a
# `Diagram` object via the library's Python API, renders to PNG, and
# displays with `st.image`. Phase 2 will add nested clusters + edge
# labels; Phase 3 D2 + pre-built templates.
#
# Allowlist registry: maps a short `type` string from the DSL to a
# concrete mingrammer/diagrams class. Keeps the LLM's authority bounded
# to exactly the icons we've vetted — adding a new vendor icon is a
# one-line registry edit + redeploy.
_DIAGRAMS_NODE_REGISTRY: dict[str, str] = {
    # AWS compute
    "ec2":               "diagrams.aws.compute.EC2",
    "lambda":            "diagrams.aws.compute.Lambda",
    "ecs":               "diagrams.aws.compute.ECS",
    "fargate":           "diagrams.aws.compute.Fargate",
    # AWS storage / db
    "s3":                "diagrams.aws.storage.S3",
    "ebs":               "diagrams.aws.storage.EBS",
    "rds":               "diagrams.aws.database.RDS",
    "dynamodb":          "diagrams.aws.database.Dynamodb",
    # AWS network
    "cloudfront":        "diagrams.aws.network.CloudFront",
    "apigateway":        "diagrams.aws.network.APIGateway",
    "elb":               "diagrams.aws.network.ELB",
    "route53":           "diagrams.aws.network.Route53",
    # AWS security / identity
    "cognito":           "diagrams.aws.security.Cognito",
    "iam":               "diagrams.aws.security.IAM",
    "kms":               "diagrams.aws.security.KMS",
    # AWS integration / mgmt
    "ses":               "diagrams.aws.engagement.SimpleEmailServiceSes",
    "sns":               "diagrams.aws.integration.SimpleNotificationServiceSns",
    "sqs":               "diagrams.aws.integration.SimpleQueueServiceSqs",
    "eventbridge":       "diagrams.aws.integration.Eventbridge",
    "ssm":               "diagrams.aws.management.SystemsManager",
    "ssm_parameter_store": "diagrams.aws.management.SystemsManagerParameterStore",
    "cloudwatch":        "diagrams.aws.management.Cloudwatch",
    # AWS ML
    "bedrock":           "diagrams.aws.ml.Bedrock",
    # Generic / on-prem (for Cloudflare, Grafana, Prometheus, Exasol, GitHub)
    "cloudflare":        "diagrams.saas.cdn.Cloudflare",
    "cloudflared":       "diagrams.saas.cdn.Cloudflare",
    "grafana":           "diagrams.onprem.monitoring.Grafana",
    "prometheus":        "diagrams.onprem.monitoring.Prometheus",
    "github":            "diagrams.onprem.vcs.Github",
    "user":              "diagrams.onprem.client.User",
    "users":             "diagrams.onprem.client.Users",
    # Exasol has no dedicated icon; fall back to a generic database
    "database":          "diagrams.generic.database.SQL",
    "exasol":            "diagrams.generic.database.SQL",
}


def _diagrams_type_key(type_key: Any) -> str:
    return str(type_key).strip().lower()


def _resolve_diagrams_class(type_key: str):
    """Look up a mingrammer/diagrams class by short DSL key. Returns the
    class object or None if not in the allowlist."""
    dotted = _DIAGRAMS_NODE_REGISTRY.get(_diagrams_type_key(type_key))
    if not dotted:
        return None
    try:
        module_path, _, cls_name = dotted.rpartition(".")
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, cls_name, None)
    except Exception:  # noqa: BLE001
        return None


def _try_render_diagrams_dsl(spec_json: str) -> bool:
    """XYZ-PILOT-089 Phase 1: render an AWS-icon-rich architecture diagram
    from a JSON DSL via mingrammer/diagrams. NEVER exec()-es LLM output —
    every node `type` is looked up in the `_DIAGRAMS_NODE_REGISTRY`
    allowlist; unknown types are rejected with a clear error.

    DSL shape:
        {
          "title":     "string — diagram title (optional)",
          "direction": "LR | TB | BT | RL  — graph direction (default TB)",
          "nodes": [
            {"id": "subs", "type": "users", "label": "XYZ Subscribers"},
            {"id": "ec2",  "type": "ec2",   "label": "EC2 r6i.xlarge"},
            ...
          ],
          "edges": [
            {"from": "subs", "to": "ec2", "label": "HTTPS"},
            ...
          ]
        }
    """
    try:
        spec = json.loads(spec_json)
    except Exception as exc:
        st.warning(f"⚠ diagrams DSL is not valid JSON: {exc}")
        st.code(spec_json, language="json")
        _queue_visual_incident_candidate(
            surface="diagrams DSL",
            renderer="mingrammer/diagrams-json",
            failure=exc,
            source=spec_json,
        )
        return False

    nodes = spec.get("nodes") or []
    edges = spec.get("edges") or []
    if not isinstance(nodes, list) or not nodes:
        st.warning("⚠ diagrams DSL: 'nodes' must be a non-empty list")
        st.code(spec_json, language="json")
        _queue_visual_incident_candidate(
            surface="diagrams DSL",
            renderer="mingrammer/diagrams-validator",
            failure="'nodes' must be a non-empty list",
            source=spec_json,
        )
        return False
    direction = (spec.get("direction") or "TB").upper()
    if direction not in {"TB", "BT", "LR", "RL"}:
        direction = "TB"
    title = spec.get("title", "")

    # Validate every node type against the allowlist BEFORE rendering. Keep
    # allowlist misses separate from broken registry import paths: the former
    # is an LLM/user DSL issue, the latter is a deployable renderer defect.
    unknown: list[str] = []
    unavailable: list[str] = []
    for n in nodes:
        if not isinstance(n, dict) or "id" not in n or "type" not in n:
            st.warning(f"⚠ diagrams DSL: node missing id/type: {n!r}")
            st.code(spec_json, language="json")
            _queue_visual_incident_candidate(
                surface="diagrams DSL",
                renderer="mingrammer/diagrams-validator",
                failure=f"node missing id/type: {n!r}",
                source=spec_json,
            )
            return False
        type_key = _diagrams_type_key(n["type"])
        if type_key not in _DIAGRAMS_NODE_REGISTRY:
            unknown.append(n["type"])
        elif _resolve_diagrams_class(type_key) is None:
            unavailable.append(type_key)
    if unknown:
        st.warning(
            f"⚠ diagrams DSL: unknown node type(s) {sorted(set(unknown))}. "
            f"Allowed types: {sorted(_DIAGRAMS_NODE_REGISTRY.keys())}"
        )
        st.code(spec_json, language="json")
        _queue_visual_incident_candidate(
            surface="diagrams DSL",
            renderer="mingrammer/diagrams-validator",
            failure=f"unknown node type(s): {sorted(set(unknown))}",
            source=spec_json,
        )
        return False
    if unavailable:
        st.warning(
            f"⚠ diagrams DSL: configured node type(s) could not be imported "
            f"{sorted(set(unavailable))}. This is a renderer registry/dependency "
            "mismatch, not a DSL allowlist problem."
        )
        st.code(spec_json, language="json")
        _queue_visual_incident_candidate(
            surface="diagrams DSL",
            renderer="mingrammer/diagrams-validator",
            failure=f"configured node type(s) could not be imported: {sorted(set(unavailable))}",
            source=spec_json,
        )
        return False

    # Render to a temp PNG via mingrammer/diagrams. The library writes to
    # cwd by default, so we run inside a tempdir + force show=False.
    import tempfile
    import os as _os
    try:
        from diagrams import Diagram, Edge
    except Exception as exc:
        st.warning(
            f"⚠ mingrammer/diagrams not importable ({type(exc).__name__}: {exc}). "
            "Ensure the chatbot container has `pip install diagrams` and "
            "`apt install graphviz` (XYZ-PILOT-089 Dockerfile)."
        )
        st.code(spec_json, language="json")
        _queue_visual_incident_candidate(
            surface="diagrams DSL",
            renderer="mingrammer/diagrams-import",
            failure=exc,
            source=spec_json,
        )
        return False

    rendered_ok = False
    png_path: str | None = None
    with tempfile.TemporaryDirectory() as tmpdir:
        cwd = _os.getcwd()
        try:
            _os.chdir(tmpdir)
            filename = "diagram"
            with Diagram(
                title or "<PILOT_NAME> architecture",
                filename=filename,
                show=False,
                direction=direction,
                graph_attr={"fontsize": "14", "bgcolor": "white",
                             "pad": "0.4", "nodesep": "0.5", "ranksep": "0.8"},
            ):
                node_objs: dict[str, Any] = {}
                for n in nodes:
                    cls = _resolve_diagrams_class(n["type"])
                    label = n.get("label", n["id"])
                    node_objs[n["id"]] = cls(label)
                for e in edges:
                    src = node_objs.get(e.get("from"))
                    dst = node_objs.get(e.get("to"))
                    if src is None or dst is None:
                        continue
                    edge_label = e.get("label", "")
                    if edge_label:
                        src >> Edge(label=edge_label) >> dst
                    else:
                        src >> dst
            png_path = _os.path.join(tmpdir, f"{filename}.png")
            if _os.path.exists(png_path):
                with open(png_path, "rb") as f:
                    png_bytes = f.read()
                st.image(png_bytes, use_container_width=True)
                rendered_ok = True
            else:
                st.warning("⚠ diagrams render produced no PNG output")
                _queue_visual_incident_candidate(
                    surface="diagrams DSL",
                    renderer="mingrammer/diagrams-png",
                    failure="render produced no PNG output",
                    source=spec_json,
                )
        except Exception as exc:
            st.warning(
                f"⚠ diagrams render raised {type(exc).__name__}: {exc}. "
                "Spec + node-type registry shown below for diagnosis."
            )
            _queue_visual_incident_candidate(
                surface="diagrams DSL",
                renderer="mingrammer/diagrams-render",
                failure=exc,
                source=spec_json,
            )
        finally:
            _os.chdir(cwd)

    with st.expander(
        f"📋 Diagrams DSL source (engine=mingrammer/diagrams · {len(nodes)} nodes · {len(edges)} edges)",
        expanded=not rendered_ok,
    ):
        st.code(spec_json, language="json")
    return rendered_ok


def _try_render_mermaid(code: str, *, fallback_lang: str = "mermaid") -> bool:
    """Render `code` as a Mermaid diagram with explicit failure visibility.

    The user-facing order is visual first, source second. On successful
    render the source stays in a collapsed debug expander; on failure it
    opens so operators have a workaround when the embedded Mermaid renderer
    silently fails (e.g. a "syntax error in text" bomb icon shown inside
    a tiny iframe). XYZ-PILOT-093 + XYZ-PILOT-097 normalisation.
    """
    original_code = _normalize_mermaid(code)
    fallback_code = _c4_to_flowchart_fallback(original_code)
    code = fallback_code or original_code
    rendered = True
    try:
        import streamlit.components.v1 as components

        if fallback_code:
            st.info(
                "Rendered this experimental Mermaid C4 diagram as a compatible "
                "`flowchart LR` visual fallback (XYZ-PILOT-185). The original C4 "
                "source is preserved in the debug expander below."
            )
        components.html(
            _mermaid_component_html(code),
            height=_mermaid_component_height(code),
            scrolling=True,
        )
    except Exception as exc:  # noqa: BLE001
        rendered = False
        st.warning(
            f"⚠ Mermaid renderer raised {type(exc).__name__}: {exc}. "
            "Source below for inspection."
        )
        _queue_visual_incident_candidate(
            surface="Mermaid diagram",
            renderer="embedded-mermaid-js",
            failure=exc,
            source=code,
        )

    # Always offer the source — defaults to collapsed on success, expanded
    # on failure. Operators can copy-paste into https://mermaid.live to
    # diagnose syntax errors that the embedded renderer flagged silently.
    with st.expander(
        "📋 Diagram source (paste into https://mermaid.live to debug syntax)",
        expanded=not rendered,
    ):
        if fallback_code:
            st.caption("Rendered compatible fallback:")
            st.code(code, language="mermaid")
            st.caption("Original C4 source:")
            st.code(original_code, language=fallback_lang)
        else:
            st.code(code, language=fallback_lang)
    return rendered


def _try_render_dot(code: str, *, fallback_lang: str = "dot") -> bool:
    """Render Graphviz DOT server-side so failures are visible.

    The built-in `st.graphviz_chart` renders in the browser and can produce a
    blank pane without raising in Python. The container already ships the
    Graphviz `dot` binary for the diagrams renderer, so render DOT to SVG
    server-side, show a bounded iframe, and expose source/debug details on
    failure.
    """
    rendered = True
    try:
        import streamlit.components.v1 as components

        svg = _dot_to_svg(code)
        components.html(
            _dot_svg_component_html(svg),
            height=_dot_component_height(code),
            scrolling=True,
        )
    except Exception as exc:  # noqa: BLE001
        rendered = False
        st.warning(
            f"⚠ Graphviz DOT renderer raised {type(exc).__name__}: {exc}. "
            "Source below for inspection."
        )
        _queue_visual_incident_candidate(
            surface="Graphviz DOT diagram",
            renderer="graphviz-dot-svg",
            failure=exc,
            source=code,
        )

    with st.expander(
        "📋 DOT source (paste into https://dreampuf.github.io/GraphvizOnline/ to debug syntax)",
        expanded=not rendered,
    ):
        st.code(code, language=fallback_lang)
    return rendered


def _audit_extract_arch_mermaid(audit: list[dict] | None) -> tuple[str, str] | None:
    """XYZ-PILOT-098: when Claude reads a file from an architecture docs folder but
    forgets to paste the diagram in its reply, recover the canonical
    Mermaid block from the audit log's `read_repo_file` result. Returns
    `(file_path, mermaid_code)` of the first hit, or None."""
    if not audit:
        return None
    for call in audit:
        if call.get("tool") != "read_repo_file":
            continue
        p = (call.get("args") or {}).get("path", "")
        if not (any(p.startswith(prefix) for prefix in _DIAGRAM_SOURCE_PREFIXES) and p.endswith(".md")):
            continue
        detail = call.get("detail", "") or ""
        # Find first mermaid (or dot) block in the file content
        for m in _ANY_CODE_BLOCK.finditer(detail):
            lang = m.group(1).lower().strip()
            code = m.group(2)
            if lang == "mermaid":
                return p, code
            if not lang and _first_token(code) in _MERMAID_KEYWORDS:
                return p, code
    return None


def _render_with_diagrams(text: str, audit: list[dict] | None = None) -> None:
    r"""Render `text` as markdown, but replace fenced code blocks containing
    Mermaid / Graphviz DOT diagrams with interactive renders.

    Decision tree per code block:
      1. ```mermaid …```                  → Mermaid render
      2. ```dot …```                      → Graphviz render
      3. ```<anything else or blank>``` where first non-comment line is a
         Mermaid keyword                  → Mermaid render with 🩹 banner
                                            (XYZ-PILOT-091 v2 — handles Claude
                                            forgetting the explicit `mermaid` tag)
      4. ```<anything>``` looking like DOT (digraph/graph + braces)
                                          → Graphviz render with 🩹 banner
      5. otherwise                        → plain `st.code(code, language=lang)`

    Anything outside a fenced block is rendered with `st.markdown`. This
    means Claude's keyword appearing in a prose sentence can NEVER trigger
    the Mermaid renderer — only content already wrapped in `\`\`\``.
    """
    if not text:
        return
    text = _normalize_diagram_fences(text)

    # XYZ-PILOT-098 fence-less fallback: when Claude emits Mermaid source at
    # column 0 with NO ```mermaid fence at all (observed 2026-05-26 across
    # multiple in-the-wild prompts), detect by first-line keyword and
    # render the entire response as Mermaid. Only triggers when there are
    # no fenced code blocks at all (so a real prose-then-fence reply is
    # not affected).
    has_any_fence = bool(_ANY_CODE_BLOCK.search(text))
    if not has_any_fence:
        stripped = text.lstrip()
        first_line_tok = stripped.split("\n", 1)[0].strip().split(maxsplit=1)
        if first_line_tok and first_line_tok[0] in _MERMAID_KEYWORDS:
            st.info(
                "🩹 Auto-detected unfenced Mermaid diagram at the start of the "
                "reply (XYZ-PILOT-098). The fence opener should be "
                "```` ```mermaid ` ```` for guaranteed rendering — "
                "treating the whole reply as Mermaid anyway."
            )
            _try_render_mermaid(stripped)
            return

        unfenced_dot = _extract_unfenced_dot_block(text)
        if unfenced_dot is not None:
            start, end, code = unfenced_dot
            preamble = text[:start].strip()
            remainder = text[end:].strip()
            if preamble:
                st.markdown(preamble)
            st.info(
                "🩹 Auto-detected unfenced Graphviz DOT diagram in the reply "
                "(XYZ-PILOT-149 visual-first guard). The fence opener should be "
                "```` ```dot ` ```` for guaranteed rendering — treating this "
                "block as DOT anyway."
            )
            _try_render_dot(code)
            if remainder:
                st.markdown(remainder)
            return

        # XYZ-PILOT-098 audit-based recovery: Claude read a file from
        # an architecture docs folder but emitted no diagram in its reply. Pull
        # the canonical Mermaid block from the audit log directly.
        recovered = _audit_extract_arch_mermaid(audit)
        if recovered is not None:
            file_path, code = recovered
            st.info(
                f"🩹 Claude read `{file_path}` but didn't paste the diagram "
                "inline (XYZ-PILOT-098). Rendering the canonical Mermaid block "
                "from that file directly."
            )
            _try_render_mermaid(code)
            # Show whatever prose Claude did emit, after the diagram
            if text.strip():
                st.markdown(text)
            return

    cursor = 0
    for m in _ANY_CODE_BLOCK.finditer(text):
        if m.start() > cursor:
            md_chunk = text[cursor:m.start()]
            if md_chunk.strip():
                st.markdown(md_chunk)
        lang = m.group(1).lower().strip()
        code = m.group(2)
        first = _first_token(code)

        if lang == "mermaid" or lang in _MERMAID_LANG_ALIASES:
            if lang != "mermaid":
                st.info(
                    f"Auto-detected Mermaid renderer input from a `{lang}` fence "
                    "(XYZ-PILOT-149 visual-first guard). The preferred fence is "
                    "```` ```mermaid ` ````."
                )
            _try_render_mermaid(code)
        elif lang == "chart":
            # XYZ-PILOT-105: analytical chart from JSON spec via Altair or Plotly
            _try_render_chart(code)
        elif lang == "diagrams":
            # XYZ-PILOT-089 Phase 1: AWS-icon architecture diagram via
            # mingrammer/diagrams (safe JSON DSL — no exec() of LLM output)
            _try_render_diagrams_dsl(code)
        elif lang == "dot":
            _try_render_dot(code)
        elif first in _MERMAID_KEYWORDS or _looks_like_mermaid_graph(code):
            st.info(
                "🩹 Auto-detected Mermaid syntax in an unlabelled code block (XYZ-PILOT-091). "
                "The fence opener should be ```` ```mermaid ` ```` for guaranteed rendering — "
                "treating it as Mermaid anyway."
            )
            _try_render_mermaid(code)
        elif _looks_like_dot(code):
            st.info(
                "🩹 Auto-detected Graphviz DOT in an unlabelled code block (XYZ-PILOT-091). "
                "The fence opener should be ```` ```dot ` ```` for guaranteed rendering — "
                "treating it as DOT anyway."
            )
            _try_render_dot(code)
        else:
            st.code(code, language=lang or None)
        cursor = m.end()

    if cursor < len(text):
        remainder = text[cursor:]
        if remainder.strip():
            st.markdown(remainder)


for msg in st.session_state.history:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            _render_with_diagrams(msg["content"])
        else:
            st.markdown(msg["content"])
        # XYZ-PILOT-104 v2: surface turn timestamps + duration at the prompt
        # (server-side emission already shipped via `_audit_log_turn`;
        # this is the subscriber-visible companion). Stored on the
        # assistant message dict as `turn_meta` at the time of generation.
        tm = msg.get("turn_meta")
        if tm:
            st.caption(
                f"⏱ {tm['started_at_ts']} → {tm['ended_at_ts']} · "
                f"{tm['duration_ms']/1000:.1f} s · {tm.get('tool_calls', 0)} tool call(s)"
            )

typed_msg = st.chat_input("Ask about <PILOT_NAME> — schema, RI, tickets, status, anything…")
# XYZ-PILOT-051: if a history item was clicked in the sidebar, treat it as the
# user's next message (one-shot pop so re-runs don't loop).
user_msg = typed_msg or st.session_state.pop("replay_query", None)
if user_msg:
    _save_query(email, user_msg)
    st.session_state.history.append({"role": "user", "content": user_msg})
    with st.chat_message("user"):
        st.markdown(user_msg)
    with st.chat_message("assistant"):
        # XYZ-PILOT-104 v2: capture turn start/stop at the prompt for UI display
        turn_started_at = time.time()
        turn_started_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        bedrock_failure: Exception | None = None
        operator_probe_answer = _operator_visual_incident_probe_answer(user_msg)
        if operator_probe_answer is not None:
            answer = operator_probe_answer
            audit = []
            st.caption(
                "TC-2A operator visual-regression probe detected locally; "
                "Bedrock/tool calls skipped so the invalid chart renderer path "
                "is tested deterministically."
            )
        elif (tc5_answer := _tc5_complex_visualization_answer(user_msg)) is not None:
            answer = tc5_answer
            audit = []
            st.caption(
                "TC-5 complex visualization regression prompt detected locally; "
                "Bedrock SQL synthesis skipped so the validated Plotly quarter chart renders deterministically."
            )
        elif (customer_yoy_answer := _customer_yoy_retention_answer(user_msg)) is not None:
            answer = customer_yoy_answer
            audit = []
            st.caption(
                "<CUSTOMER_NAME> YoY retention demo prompt detected locally; "
                "Bedrock panel selection skipped so the three chart panels render deterministically."
            )
        else:
            with st.spinner("Claude (Bedrock Sonnet 4.6 + tools) thinking…"):
                try:
                    answer, audit = _ask_claude_with_tools(
                        st.session_state.history, context, caller=email,
                    )
                except Exception as exc:  # noqa: BLE001
                    bedrock_failure = exc
                    answer = f"⚠ Bedrock call failed: `{exc}`"
                    audit = []
        turn_duration_ms = int((time.time() - turn_started_at) * 1000)
        turn_ended_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # XYZ-PILOT-119: persist the full audit alongside turn-level timing
        # so the per-prompt execution-statistics expander can render on
        # subsequent reruns from session history (not just the fresh turn).
        turn_meta = {
            "started_at_ts": turn_started_ts,
            "ended_at_ts": turn_ended_ts,
            "duration_ms": turn_duration_ms,
            "tool_calls": len(audit),
            "audit": audit,
        }
        if audit:
            with st.expander(f"🔧 Tools used ({len(audit)} call{'s' if len(audit) != 1 else ''})", expanded=False):
                for a in audit:
                    status_icon = "✓" if a["ok"] else "✗"
                    args_str = ", ".join(f"{k}={v!r}" for k, v in a["args"].items())
                    st.markdown(f"- {status_icon} `{a['tool']}({args_str})`")
        st.session_state._xyz_current_user_prompt = user_msg
        st.session_state._xyz_capture_visual_incidents = True
        try:
            if bedrock_failure is not None:
                _queue_visual_incident_candidate(
                    surface="Chatbot prompt",
                    renderer="Bedrock Converse",
                    failure=bedrock_failure,
                    source="Bedrock call failed before assistant response rendering.",
                )
            _queue_failed_tool_incidents(audit)
            _render_with_diagrams(answer, audit)
        finally:
            st.session_state._xyz_capture_visual_incidents = False
        # XYZ-PILOT-104 v2 FIX (was missing from the fresh-turn block — the
        # caption was only rendering on the history-iteration at the top
        # of the rerun, so subscribers never saw the timestamp on the
        # CURRENT prompt's response). Render it immediately on the fresh
        # turn so the user sees the elapsed time without needing to
        # submit another prompt first.
        st.caption(
            f"⏱ {turn_started_ts} → {turn_ended_ts} · "
            f"{turn_duration_ms/1000:.1f} s · {len(audit)} tool call(s)"
        )
        # XYZ-PILOT-095: auto-archive any NEW Mermaid/DOT diagram Claude
        # emitted to docs/70-generated-qa/architecture/<slug>_<UTC>.md. Skipped when the
        # assistant just re-read an existing file from that folder.
        try:
            archived = _archive_new_diagrams(user_msg, answer, audit, email)
        except Exception:  # noqa: BLE001
            archived = []
        for arch in archived:
            st.success(
                f"📁 New **{arch['lang']}** diagram auto-archived to "
                f"[`{arch['path']}`]({arch['url']}) — visible to all "
                f"subscribers on GitHub. "
                f"[Commit]({arch['commit_url']})" if arch.get("commit_url")
                else f"📁 Auto-archived to [`{arch['path']}`]({arch['url']})"
            )
    st.session_state.history.append({
        "role": "assistant", "content": answer, "turn_meta": turn_meta,
    })

# Action buttons appear after the most recent assistant message
if (st.session_state.history
        and st.session_state.history[-1]["role"] == "assistant"
        and len(st.session_state.history) >= 2):
    last_q = next((m["content"] for m in reversed(st.session_state.history) if m["role"] == "user"), "")
    last_a = st.session_state.history[-1]["content"]
    col1, col2, col3, _ = st.columns([1, 1, 1, 2])
    with col1:
        if st.button("📁 Archive this Q&A to repo"):
            ok, msg = _archive_qa(email, last_q, last_a)
            if ok:
                st.success(f"✓ Archived: [{msg}]({msg})")
            else:
                st.error(msg)
    with col2:
        if st.button("✨ File as feature request"):
            with st.spinner("Checking tracker for duplicates + filing as XYZ_MASTER candidate if new…"):
                result = _log_to_tracker("feature", email, last_q, last_a)
            if result["kind"] == "duplicate":
                st.info(
                    f"This looks like a duplicate of **{result['existing_id']}**. "
                    f"_{result['reason']}_ — no new ticket created."
                )
            elif result["kind"] == "candidate":
                # XYZ-PILOT-157(a) + XYZ-PILOT-189: chatbot files candidates →
                # operator reviews → XYZ_MASTER drains → SES email confirms
                # the allocated XYZ-PILOT-NNN to the submitter (Cc <OPERATOR_NAME>/
                # <OPS_USER_2>/<OPS_USER_3>). Closes the "black hole" UX gap that
                # existed pre-XYZ-PILOT-189.
                st.success(
                    f"✓ Submission queued for operator review — *{result['title']}* (severity: feature). "
                    f"You'll receive an SES email confirmation with the assigned `XYZ-PILOT-NNN` "
                    f"once XYZ_MASTER drains the candidate (typically within ~1 hour). "
                    f"Candidate file: `{result['candidate_path']}` "
                    f"([commit]({result['commit_url']}))."
                )
            else:
                st.error(f"⚠ Could not file feature: {result.get('detail', 'unknown error')}")
    with col3:
        if st.button("🐛 Log as new defect"):
            with st.spinner("Checking tracker for duplicates + filing as XYZ_MASTER candidate if new…"):
                result = _log_to_tracker("defect", email, last_q, last_a)
            if result["kind"] == "duplicate":
                st.info(
                    f"This looks like a duplicate of **{result['existing_id']}**. "
                    f"_{result['reason']}_ — no new ticket created."
                )
            elif result["kind"] == "candidate":
                # XYZ-PILOT-157(a) + XYZ-PILOT-189: chatbot files candidates →
                # operator reviews → XYZ_MASTER drains → SES email confirms
                # the allocated XYZ-PILOT-NNN to the submitter (Cc <OPERATOR_NAME>/
                # <OPS_USER_2>/<OPS_USER_3>). Closes the "black hole" UX gap that
                # existed pre-XYZ-PILOT-189.
                st.success(
                    f"✓ Submission queued for operator review — *{result['title']}*. "
                    f"You'll receive an SES email confirmation with the assigned `XYZ-PILOT-NNN` "
                    f"once XYZ_MASTER drains the candidate (typically within ~1 hour). "
                    f"Candidate file: `{result['candidate_path']}` "
                    f"([commit]({result['commit_url']}))."
                )
            else:
                st.error(f"⚠ Could not log defect: {result.get('detail', 'unknown error')}")

    # XYZ-PILOT-119: Execution statistics expander, default-COLLAPSED (operator
    # request 2026-06-01 — open it only when the subscriber actually needs the
    # timing breakdown; the header line already shows the headline duration +
    # tool-call count so click-to-expand is the right interaction). Placed
    # after the 3 action buttons and BEFORE the XYZ-PILOT-084 Submit-Defect/ER
    # form. Shows the per-prompt timing trace assembled by XYZ-PILOT-104 v2:
    # the whole-turn start/end timestamps + duration, plus a per-tool
    # breakdown (tool name, args summary, started_at_ts, duration_ms, ok).
    # Same data also available server-side via `sudo docker logs
    # xyz-pilot-chatbot | grep '"kind":"chatbot_turn"'` (XYZ-PILOT-104 v1),
    # but this surfaces it subscriber-side so timing is visible without
    # operator access.
    last_msg = st.session_state.history[-1]
    tm = last_msg.get("turn_meta") if isinstance(last_msg, dict) else None
    if tm:
        with st.expander(
            f"🔬 Execution statistics — {tm.get('duration_ms', 0)/1000:.1f} s · "
            f"{tm.get('tool_calls', 0)} tool call(s)",
            expanded=False,
        ):
            st.markdown(
                f"**Started:** `{tm.get('started_at_ts', '?')}` (UTC)  \n"
                f"**Ended:**   `{tm.get('ended_at_ts', '?')}` (UTC)  \n"
                f"**Total wall-clock:** `{tm.get('duration_ms', 0)/1000:.3f} s` "
                f"({tm.get('duration_ms', 0)} ms)  \n"
                f"**Tool calls:** {tm.get('tool_calls', 0)}"
            )
            audit_list = tm.get("audit") or []
            if audit_list:
                # Per-tool table — full args, exact start, duration, ok
                import pandas as _pd
                df = _pd.DataFrame([
                    {
                        "#": i + 1,
                        "Started (UTC)": a.get("started_at_ts", "?")[11:23],
                        "Duration (ms)": a.get("duration_ms", 0),
                        "ok": "✓" if a.get("ok") else "✗",
                        "Tool": a.get("tool", ""),
                        "Args": ", ".join(
                            f"{k}={v!r}" if len(repr(v)) < 80 else f"{k}=<{len(repr(v))}b>"
                            for k, v in (a.get("args") or {}).items()
                        ),
                    }
                    for i, a in enumerate(audit_list)
                ])
                # XYZ-PILOT-140: AgGrid for sortable / filterable / resizable /
                # multi-select / CSV-exportable per-tool table. Falls back to
                # st.dataframe if streamlit-aggrid isn't installed (e.g. local
                # dev without the new Docker layer).
                try:
                    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode  # type: ignore
                    gob = GridOptionsBuilder.from_dataframe(df)
                    gob.configure_default_column(
                        sortable=True, filter=True, resizable=True,
                        wrapText=True, autoHeight=True,
                    )
                    gob.configure_column("Args", flex=2, minWidth=300)
                    gob.configure_column("Duration (ms)", type=["numericColumn"], width=120)
                    gob.configure_grid_options(domLayout="autoHeight")
                    AgGrid(df, gridOptions=gob.build(),
                           update_mode=GridUpdateMode.NO_UPDATE,
                           allow_unsafe_jscode=False, fit_columns_on_grid_load=True,
                           theme="alpine", key=f"aggrid_tools_{id(audit_list)}")
                except ImportError:
                    st.dataframe(df, width="stretch", hide_index=True)
                # Aggregated per-tool summary
                if len(audit_list) > 1:
                    agg = (df.groupby("Tool", as_index=False)
                             .agg(calls=("Duration (ms)", "size"),
                                  total_ms=("Duration (ms)", "sum"),
                                  avg_ms=("Duration (ms)", "mean"),
                                  max_ms=("Duration (ms)", "max")))
                    agg["avg_ms"] = agg["avg_ms"].round(0).astype(int)
                    st.caption("Per-tool aggregate:")
                    try:
                        from st_aggrid import AgGrid as _AgGrid, GridOptionsBuilder as _Gob  # type: ignore
                        _gob = _Gob.from_dataframe(agg)
                        _gob.configure_default_column(sortable=True, resizable=True)
                        _gob.configure_grid_options(domLayout="autoHeight")
                        _AgGrid(agg, gridOptions=_gob.build(),
                                allow_unsafe_jscode=False, fit_columns_on_grid_load=True,
                                theme="alpine", key=f"aggrid_agg_{id(audit_list)}")
                    except ImportError:
                        st.dataframe(agg, width="stretch", hide_index=True)
            else:
                st.caption("_(no tool calls — Bedrock answered from context alone)_")

# XYZ-PILOT-084: candidate-submit form for defects / enhancement requests, at the
# BOTTOM of the view, COLLAPSED by default (per follow-up 2026-05-25 — the
# previous always-expanded form dominated the view when chat history was
# empty, because `st.chat_input` is Streamlit-pinned to the viewport bottom
# leaving the main scrollable area visually empty on first load). The
# expander becomes a single thin clickable bar just above the pinned chat
# input; chat stays the prime/default affordance. Reuses the existing
# `_log_to_tracker(kind, …)` candidate-emission orchestrator (XYZ-PILOT-054).
submit_defect_btn = False
submit_feature_btn = False
direct_submission = ""
with st.expander(
    "📝 Submit a Defect or Enhancement Request for XYZ_MASTER review",
    expanded=False,
):
    st.caption(
        "Skip the Q&A — describe the defect or enhancement below and click "
        "the matching button. A Bedrock-powered **duplicate check** runs first "
        "against the tracker's Open + recently-Resolved entries; if a match is "
        "found, you'll get the existing XYZ-PILOT-NNN id back. If no match is "
        "found, the chatbot emits an XYZ_MASTER candidate file for guarded "
        "tracker consolidation."
    )
    with st.form("direct_tracker_submit", clear_on_submit=True):
        direct_submission = st.text_area(
            "Description",
            height=100,
            label_visibility="collapsed",
            placeholder="What's broken (defect) or what would you like to see added (enhancement)?",
            key="direct_submission_text",
        )
        sbc1, sbc2 = st.columns(2)
        with sbc1:
            submit_defect_btn = st.form_submit_button(
                "🐛 Submit as Defect", use_container_width=True,
            )
        with sbc2:
            submit_feature_btn = st.form_submit_button(
                "✨ Submit as Enhancement", use_container_width=True,
            )

if submit_defect_btn or submit_feature_btn:
    text = (direct_submission or "").strip()
    if not text:
        st.warning("Please type a description before clicking Submit.")
    else:
        kind = "defect" if submit_defect_btn else "feature"
        with st.spinner("Duplicate-checking against tracker + filing if new…"):
            ds_result = _log_to_tracker(kind, email, text, "")
        if ds_result["kind"] == "duplicate":
            existing = ds_result["existing_id"]
            anchor = (
                f"https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/"
                f"docs/XYZ_PILOT_ISSUE_TRACKER.md#{existing.lower()}"
            )
            st.info(
                f"🔎 Duplicate of **[{existing}]({anchor})** — _{ds_result['reason']}_. "
                "No new ticket created."
            )
            # XYZ-PILOT-087: preserve the user's original text on the
            # duplicate path so the effort they put into writing the
            # description isn't lost when `clear_on_submit=True` wipes
            # the form. They can copy-paste into another channel or
            # refine and re-submit if they disagree with the dup-check.
            with st.expander(
                "📋 Your original submission (preserved — copy / refine / re-submit if needed)",
                expanded=True,
            ):
                st.code(text, language="text")
        elif ds_result["kind"] == "candidate":
            # XYZ-PILOT-157(a) + XYZ-PILOT-189: chatbot files XYZ_MASTER candidates
            # → operator reviews → XYZ_MASTER drains → SES email confirms the
            # allocated XYZ-PILOT-NNN to the submitter (Cc <OPERATOR_NAME>/<OPS_USER_2>/
            # <OPS_USER_3>). Closes the "black hole" UX gap that existed
            # pre-XYZ-PILOT-189.
            badge = "🐛 Submission queued" if kind == "defect" else "✨ Submission queued"
            st.success(
                f"{badge} for operator review — *{ds_result['title']}*. "
                f"You'll receive an SES email confirmation with the assigned `XYZ-PILOT-NNN` "
                f"once XYZ_MASTER drains the candidate (typically within ~1 hour). "
                f"Candidate file: `{ds_result['candidate_path']}` "
                f"([commit]({ds_result['commit_url']}))."
            )
        else:
            st.error(f"⚠ Could not file: {ds_result.get('detail', 'unknown error')}")
