"""<PILOT_NAME> — one-shot weekend recap email + tracker prune.

Triggered once by the `xyz-pilot-weekend-recap-oneshot` EventBridge rule at
2026-05-26 04:00 UTC = 06:00 CEST (cron(0 4 26 5 ? 2026)).

Flow:
  1. SES-sends the pre-composed weekend recap (Sat 2026-05-23 → Mon 2026-05-25)
     to every address in the SES_TO env var — same fanout as the regular
     daily-status Lambda.
  2. Reads docs/XYZ_PILOT_ISSUE_TRACKER.md via the GitHub contents API.
  3. Drops every row in the "Active issues" top table whose ID column is
     wrapped in `~~...~~` (struck-through = Resolved). The Full register
     section keeps every entry untouched for permanent reference.
  4. Commits the pruned tracker via GitHub PUT contents using the PAT
     retrieved from the Secrets Manager secret named by GITHUB_TOKEN_SECRET_ID.
     GITHUB_TOKEN_PARAM stays as a temporary SSM fallback.

The cron date-binding (year + month + day-of-month all fixed) means the
rule expires naturally after firing once. No teardown required.

Env vars required (reuses the `xyz-pilot-daily-status-role` execution role):
  SES_FROM      verified SES source address
  SES_TO        comma-separated recipient list (7 <PILOT_NAME> subscribers)
  GITHUB_REPO   owner/repo (e.g. `<GITHUB_OWNER>/<PROJECT_SLUG>`)
  GITHUB_TOKEN_SECRET_ID Secrets Manager secret name for the PAT
  GITHUB_TOKEN_PARAM     SSM SecureString fallback name for the PAT with
                         `contents:write` on the repo

Dry-run: invoke with payload `{"dry_run": true}` to validate the body +
the prune diff WITHOUT sending the email or committing.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from urllib import error, request

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

SES_FROM = os.environ["SES_FROM"]
SES_TO = [e.strip() for e in os.environ["SES_TO"].split(",") if e.strip()]
GITHUB_REPO = os.environ["GITHUB_REPO"]
GITHUB_TOKEN_SECRET_ID = os.environ.get("GITHUB_TOKEN_SECRET_ID", "xyz-pilot/github/service-token")
GITHUB_TOKEN_PARAM = os.environ.get("GITHUB_TOKEN_PARAM", "/xyz-pilot/chatbot/github-token")
AWS_REGION = os.environ.get("XYZ_AWS_REGION") or os.environ.get("AWS_REGION", "<AWS_REGION>")
_GITHUB_TOKEN_CACHE: str | None = None
_GITHUB_TOKEN_WARNED: set[str] = set()
CONFIDENTIAL_EMAIL_FOOTER = (
    "Confidential - <PILOT_NAME> - © Exasol AG · ESL © <SEMANTIC_LAYER_VENDOR> · "
    "for XYZ evaluation use only."
)

ses = boto3.client("ses", region_name=AWS_REGION)
secretsmanager = boto3.client("secretsmanager", region_name=AWS_REGION)
ssm = boto3.client("ssm", region_name=AWS_REGION)

SUBJECT = (
    "[<PILOT_NAME>] Weekend recap (extraordinary status) — Pfingsten 2026-05-23 → 2026-05-25"
)

BODY_TEXT = f"""<PILOT_NAME> — extraordinary weekend status recap
=================================================
Window: Sat 2026-05-23 → Mon 2026-05-25 (Pfingsten long weekend)

This is an extraordinary one-off recap of all tracker activity over the
long weekend. The regular daily-status email at 06:00 CEST today (Tue)
covers the routine 24 h window separately.

At-a-glance
-----------
   ▸ 11 tickets opened during the window (XYZ-PILOT-077 … XYZ-PILOT-087)
   ▸ 9 of them resolved same window (XYZ-PILOT-077, -079, -080, -081, -082, -083, -084, -086, -087)
   ▸ 2 carry over as Open: XYZ-PILOT-078 (per-service probes) + XYZ-PILOT-085 (compose service rename)
   ▸ + 1 pre-weekend Open continuing: XYZ-PILOT-066 (CLAUDE.md staleness sweep)

Tickets resolved during the weekend
-----------------------------------

XYZ-PILOT-077 — ad-hoc start emails with caller attribution
   Sat 2026-05-23 · filed + resolved same day
   Manual START via Operator Dashboard / chatbot ran silently; team had
   no email identifying who came up off-schedule. Lambda now extracts a
   Cognito Bearer token from the invoke payload, resolves caller email
   via cognito-idp:GetUser, and forces a [AD-HOC START] email with
   initiator + banner.
   https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-077

XYZ-PILOT-080 — ad-hoc stop emails (mirror of XYZ-PILOT-077 for STOP)
   Sat 2026-05-23 · filed + resolved same day
   `handle_stop` now accepts bearer_token, same Cognito-extraction
   pattern, [AD-HOC STOP] subject + banner explaining the 24:00 CEST
   scheduled cron.
   https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-080

XYZ-PILOT-079 — Streamlit cold-filter regression (scope-expanded)
   Sat 2026-05-23 filed; Mon 2026-05-25 resolved
   Originally filed as observability gap (warmup silent-fail mode after
   XYZ-PILOT-075). Re-opened Mon when first-filter latency reproduced
   post-reboot. Root cause: warmup primed only MV_* materialized tables
   but NOT the <PILOT_SCHEMA> V_* views. Streamlit's Data tab runs a dynamic
   COUNT(*) UNION ALL over every view on every render. Cold-cache
   V_MONTHLY_METRICS alone was 43.4 s. Fix: `warmup.py` now runs
   individual COUNT(*) per view (primes column blocks) + the exact same
   UNION ALL Streamlit issues (primes result cache). Warm-cache 2nd run
   = 579 ms wall, vs 173,149 ms cold → 300× speedup. Boot cost absorbed
   inside `xyz-exasol-warmup.service` (TimeoutStartSec=600).
   https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-079

XYZ-PILOT-081 — generic Cognito-gated password retrieval
   Mon 2026-05-25 · filed + resolved same day
   New Lambda `xyz-pilot-get-secret` + IAM role + CLI helper +
   Operator-Dashboard dropdown card. 6 secrets exposed via dropdown
   (exasol-sys, exasol-xyz-dash, grafana-admin, streamlit-app-password,
   exasol-proxy-token, exasol-admin-ui, github-pat). Cognito-gated +
   email-allowlisted + 30 s auto-hide.
   https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-081

XYZ-PILOT-082 — Grafana CVE patching (CVE-2026-27876, -33376, -33377)
   Mon 2026-05-25 · filed + resolved same day
   Bumped `grafana/grafana:11.6.11` → `11.6.14` (security patch within
   the 11.6.x line, no panel-JSON migration). Container recreated, all
   datasources + provisioned dashboards intact, tunnel still
   reachable.
   https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-082

XYZ-PILOT-083 — Exasol Personal Admin UI password rotation
   Mon 2026-05-25 · filed + resolved same day
   Three iterations to find the right control-plane path:
     v1 — wrongly assumed `c4` runs on macOS (dead end)
     v2 — assumed `c4` lives inside `exasol shell container` (verified:
          no c4 in that container; dead end)
     v3 — correct path: `exasol shell host` puts you on EC2 as ubuntu
          where c4 IS at /home/ubuntu/.local/bin/c4. Run
          `c4 local reconf --ccc-adminui-admin-password-hash …`. Apply
          step: `c4 local reconf` only writes STORED config; the only
          way to make the Admin UI process pick up the new password on
          Personal Edition is to reboot the EC2 host. Executed live;
          browser sign-in with the new password user-verified.
   https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-083

XYZ-PILOT-084 — chatbot direct-submit Defect/ER form
   Mon 2026-05-25 · filed + resolved same day
   Subscribers can now file tracker entries without a Q&A round-trip
   first. Three layout iterations same-day; final v3 wraps the form in
   `st.expander(..., expanded=False)` so it collapses to a single thin
   clickable bar just above the pinned chat_input. Same Bedrock dup-
   check + `_log_to_tracker(kind, …)` orchestrator as the post-reply
   buttons. Result rendered with GitHub anchor link to the tracker
   entry + commit URL.
   https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-084

XYZ-PILOT-086 — this very email + the tracker auto-prune that follows it
   Mon 2026-05-25 · filed + resolved same day (early evening)
   New one-shot Lambda `xyz-pilot-weekend-recap-oneshot` + EventBridge
   rule `cron(0 4 26 5 ? 2026)` (date-bound, single firing). Sends this
   recap to the SES_TO fanout, then prunes every struck-through
   Resolved row from the Active-issues top table of XYZ_PILOT_ISSUE_TRACKER.md
   via the GitHub PUT contents API. Reuses the existing
   `xyz-pilot-scheduler` IAM role. Dry-run validated 37 prune rows + 7
   recipients before scheduling.
   https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-086

XYZ-PILOT-087 — preserve user's submitted text on chatbot duplicate-check
   Mon 2026-05-25 · filed + resolved same day (early evening)
   Follow-up to XYZ-PILOT-084: with `clear_on_submit=True`, the carefully
   written description was lost when the duplicate-check identified an
   existing ticket. Now on the duplicate path the chatbot renders the
   user's original text inside an expanded `st.expander("📋 Your
   original submission — preserved")` with `st.code(text)` formatting,
   so it can be copy-pasted into another channel or refined and
   re-submitted if the user disagrees with the dup-check judgement.
   No change on the "new ticket filed" path (text-clear there is fine
   because the entry is now in the tracker).
   https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-087

Still open at end of weekend
----------------------------

XYZ-PILOT-078 — Operator Dashboard per-service live HTTP probes
   Filed Fri 2026-05-22 (pre-weekend). Browser-side fetch() against
   `/_stcore/health`, `/api/health`, `/mcp` hits CORS — most service
   endpoints don't emit `Access-Control-Allow-Origin: *`. Server-side
   probe Lambda needed.
   https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-078

XYZ-PILOT-085 — rename docker-compose service `chatbot` → `xyz-chatbot`
   Filed Mon 2026-05-25 by <OPERATOR_NAME> Minor follow-up — the service key
   doesn't match the `xyz-*` prefix every other container uses
   (`xyz-grafana`, `xyz-mcp-internal`, …). Cost one failed
   `docker compose build` during the XYZ-PILOT-084 deploy when the
   container_name was mistakenly used as service key.
   https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/blob/main/docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-085

Deployments live on EC2 <EC2_INSTANCE_ID> over the weekend
------------------------------------------------------------
   • Grafana 11.6.11 → 11.6.14 (CVE patch)
   • Chatbot rebuilt 4× across XYZ-PILOT-081 / -084 v1 / v2 / v3
   • warmup.py — added V_* view priming step (300× filter-change speedup)
   • Exasol Admin UI password rotated + EC2 rebooted to apply
   • 4 new SSM SecureString secrets:
       /xyz-pilot/secrets/grafana-admin
       /xyz-pilot/secrets/streamlit-app-password
       /xyz-pilot/secrets/exasol-xyz-dash
       /xyz-pilot/secrets/exasol-proxy-token
   • 1 new IAM role: xyz-pilot-get-secret-role
   • 1 new Lambda: xyz-pilot-get-secret
   • Operator Dashboard: new "🔑 Service Password Retrieval" card

Tracker hygiene after this email
--------------------------------
The "Active issues" top table is being pruned of all struck-through
Resolved rows as part of this extraordinary recap (executed by this
same Lambda right after the email is sent). The Full register section
below stays untouched as the permanent reference.

---
Composed 2026-05-25 evening, sent automatically by the
`xyz-pilot-weekend-recap-oneshot` Lambda + EventBridge rule at
04:00 UTC = 06:00 CEST 2026-05-26. The cron expression
`cron(0 4 26 5 ? 2026)` is date-bound to that single firing.

Confidentiality
---------------
{CONFIDENTIAL_EMAIL_FOOTER}
"""


def _send_email() -> str:
    resp = ses.send_email(
        Source=SES_FROM,
        Destination={"ToAddresses": SES_TO},
        Message={
            "Subject": {"Data": SUBJECT, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": BODY_TEXT, "Charset": "UTF-8"}},
        },
    )
    msg_id = resp["MessageId"]
    log.info("SES MessageId=%s sent to %s recipients", msg_id, len(SES_TO))
    return msg_id


def _warn_github_token_once(key: str, message: str, *args: object) -> None:
    if key in _GITHUB_TOKEN_WARNED:
        return
    log.warning(message, *args)
    _GITHUB_TOKEN_WARNED.add(key)


def _github_token_from_secret() -> str:
    """Return the GitHub PAT from Secrets Manager without logging the value."""
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
    """Return the GitHub PAT from SSM fallback without logging the value."""
    if _GITHUB_TOKEN_CACHE is not None:
        return _GITHUB_TOKEN_CACHE
    try:
        resp = ssm.get_parameter(Name=GITHUB_TOKEN_PARAM, WithDecryption=True)
        value = str(resp.get("Parameter", {}).get("Value") or "").strip()
    except Exception as exc:  # noqa: BLE001 - caller surfaces the prune failure.
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


def _gh_headers(content_type: str | None = None) -> dict[str, str]:
    token = _github_token()
    if not token:
        raise RuntimeError("GitHub token unavailable from Secrets Manager or SSM fallback")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "xyz-pilot-weekend-recap-oneshot",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _gh_get(path: str) -> dict:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/{path}"
    req = request.Request(url, headers=_gh_headers())
    with request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _gh_contents_text(content: dict) -> str:
    """Decode a Contents-API response, falling back to download_url for files
    > 1 MB. The Contents API returns encoding="none" + content="" past 1 MB;
    the tracker crossed that on 2026-06-08 (~1.13 MB), so a direct base64
    decode yields "" and the prune below would abort on the missing
    `## Full register` marker. Mirrors XYZ-PILOT-237 / XYZ-PILOT-245.
    """
    if content.get("encoding") == "base64" and content.get("content"):
        return base64.b64decode(content["content"]).decode("utf-8")
    download_url = content.get("download_url")
    if not download_url:
        raise RuntimeError(
            f"Contents API empty content + no download_url "
            f"(size={content.get('size')}, encoding={content.get('encoding')})")
    with request.urlopen(
        request.Request(download_url, headers={"User-Agent": "xyz-pilot-weekend-recap-oneshot"}),
        timeout=30,
    ) as raw:
        return raw.read().decode("utf-8")


def _gh_put(path: str, content_b64: str, sha: str, message: str) -> dict:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/{path}"
    body = json.dumps({
        "message": message,
        "content": content_b64,
        "sha": sha,
        "branch": "main",
    }).encode()
    req = request.Request(url, data=body, method="PUT", headers=_gh_headers("application/json"))
    with request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _prune_tracker(retries: int = 4, dry_run: bool = False) -> int:
    """Remove every struck-through Resolved row from the Active-issues top
    table. Retries on HTTP 409 (concurrent writer). Returns row count
    removed. When dry_run=True, computes the diff but doesn't commit.

    XYZ-PILOT-157 sub-task (b) note (2026-05-29): this one-shot Lambda
    already fired on 2026-05-26 via `cron(0 4 26 5 ? 2026)` and the
    EventBridge rule expired with it; the function below is therefore
    dead-code on the deployed side. If this Lambda is ever re-scheduled,
    rewrite it to emit prune candidates under
    `tests/logs/runs/<run-id>/XYZ_PILOT_WEEKEND_RECAP/issue-candidates.md`
    via the GitHub Contents API instead of mutating the tracker directly,
    matching the daily-status `_emit_prune_candidates_to_repo` helper.
    The single-writer + one-ticket-per-invocation rules in CLAUDE.md
    Rule #5 forbid any new direct-PUT writers.
    """
    path = "contents/docs/XYZ_PILOT_ISSUE_TRACKER.md"
    for attempt in range(1, retries + 1):
        try:
            file_info = _gh_get(path)
            sha = file_info["sha"]
            text = _gh_contents_text(file_info)  # XYZ-PILOT-245 sibling: >1MB download_url fallback

            register_marker = "## Full register"
            if register_marker not in text:
                raise RuntimeError("`## Full register` marker missing — aborting prune")
            split_at = text.index(register_marker)
            head, register = text[:split_at], text[split_at:]

            kept_lines = []
            removed = 0
            for line in head.split("\n"):
                if re.match(r"\| ~~\[XYZ-PILOT-\d+\]", line):
                    removed += 1
                    continue
                kept_lines.append(line)
            new_head = "\n".join(kept_lines)
            new_text = new_head + register

            if removed == 0:
                log.info("PRUNE no-op — nothing struck-through in top table")
                return 0
            if dry_run:
                log.info("PRUNE dry-run — would remove %d rows (not committed)", removed)
                return removed

            commit_msg = (
                f"docs(tracker): weekend recap auto-prune — removed {removed} "
                f"struck-through Resolved rows from Active-issues table\n\n"
                f"Triggered by xyz-pilot-weekend-recap-oneshot Lambda + EventBridge "
                f"cron(0 4 26 5 ? 2026). The Full register section is unchanged "
                f"and remains the permanent reference for every entry."
            )
            new_b64 = base64.b64encode(new_text.encode("utf-8")).decode("ascii")
            result = _gh_put(path, new_b64, sha, commit_msg)
            log.info("PRUNE ok — removed=%d, commit_sha=%s",
                     removed, result["commit"]["sha"])
            return removed
        except error.HTTPError as e:
            if e.code == 409 and attempt < retries:
                log.warning("PRUNE 409 conflict, retry %d/%d", attempt, retries)
                continue
            raise


def lambda_handler(event: dict, _context) -> dict:
    dry_run = bool((event or {}).get("dry_run"))

    msg_id = None
    if not dry_run:
        try:
            msg_id = _send_email()
        except Exception as exc:
            log.error("SES send failed: %s", exc)
            raise
    else:
        log.info("DRY-RUN — skipping SES send")

    try:
        removed = _prune_tracker(dry_run=dry_run)
    except Exception as exc:
        log.error("Tracker prune failed: %s", exc)
        return {
            "status": "email_sent_prune_failed" if msg_id else "prune_failed",
            "ses_message_id": msg_id,
            "error": str(exc),
        }

    return {
        "status": "ok" if not dry_run else "dry_run_ok",
        "ses_message_id": msg_id,
        "ses_recipients": len(SES_TO),
        "removed_rows": removed,
    }
