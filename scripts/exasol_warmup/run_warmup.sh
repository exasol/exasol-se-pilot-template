#!/usr/bin/env bash
# XYZ-PILOT-059 — wrapper for the Exasol warm-up script.
#
# Sources DB credentials from /opt/xyz-chatbot/mcp-internal.env (already
# present on EC2 since the chatbot's exasol_query tool needs it), activates
# the venv at /opt/xyz-warmup/venv, then runs warmup.py. Logs everything
# both to systemd journal (stdout/stderr) AND to a rolling file at
# /var/log/xyz-warmup.log for ad-hoc inspection.
#
# Called by systemd unit xyz-exasol-warmup.service at boot.

set -euo pipefail

LOG=/var/log/xyz-warmup.log

# Rotate previous log to .1 (one-deep, simplest possible scheme)
if [[ -f "$LOG" ]]; then
    mv -f "$LOG" "${LOG}.1"
fi

{
    echo "==== $(date -u --iso-8601=seconds) · xyz-warmup starting ===="

    if [[ ! -f /opt/xyz-chatbot/mcp-internal.env ]]; then
        echo "ERROR: /opt/xyz-chatbot/mcp-internal.env not found; chatbot stack must be deployed first."
        exit 3
    fi

    # Source DB creds — EXA_DSN / EXA_USER / EXA_PASSWORD live here.
    # `set -a` exports every var defined while sourcing.
    set -a
    # shellcheck source=/dev/null
    . /opt/xyz-chatbot/mcp-internal.env
    set +a

    # XYZ_BIZ warm-up cred (best-effort, additive) — XYZ-PILOT-291. Fetched from
    # the secret store (SSM SecureString /xyz-pilot/secrets/exasol-xyz-biz, the
    # same store the Operator Dashboard Password Retriever serves), NOT a
    # plaintext file on disk. The EC2 instance role has ssm:GetParameter on
    # /xyz-pilot/secrets/*. Best-effort: if the fetch fails/empty, EXA_BIZ_PASSWORD
    # stays unset and warmup.py skips the BIZ leg, so the demo-critical XYZ_DASH
    # warm-up is never affected. The value is held only in a local var that is
    # unset right after export to the python child (never written to disk/logs).
    biz_pw="$(aws ssm get-parameter --name /xyz-pilot/secrets/exasol-xyz-biz \
                  --with-decryption --query Parameter.Value --output text \
                  --region <AWS_REGION> 2>/dev/null || true)"
    if [[ -n "$biz_pw" && "$biz_pw" != "None" ]]; then
        export EXA_BIZ_USER="${EXA_BIZ_USER:-XYZ_BIZ}"
        export EXA_BIZ_PASSWORD="$biz_pw"
    fi
    unset biz_pw

    # Override DSN to localhost — the chatbot env uses host.docker.internal
    # which only resolves from inside a docker container.
    export EXA_DSN="127.0.0.1:8563"
    export XYZ_WARMUP_DYNAMIC_VIEWS="${XYZ_WARMUP_DYNAMIC_VIEWS:-1}"
    export XYZ_WARMUP_HEAVY_ANALYTICS="${XYZ_WARMUP_HEAVY_ANALYTICS:-0}"

    if [[ ! -x /opt/xyz-warmup/venv/bin/python ]]; then
        echo "ERROR: venv missing at /opt/xyz-warmup/venv. Run install.sh first."
        exit 4
    fi

    # XYZ-PILOT-291 Option B: stream warm-up progress to S3 so the Operator
    # Dashboard can poll it and show warm-up state in its log in real time.
    # warmup.py writes a small JSON to $XYZ_WARMUP_PROGRESS_FILE per query; a
    # bounded background loop ships it to S3 every ~3s. Fully best-effort —
    # every S3 call is `|| true`, so a publish failure NEVER affects the warm-up.
    # The "xyz-warmup finished" marker is still emitted ONLY on a clean (rc=0)
    # run and the unit exit code is warmup.py's, unchanged from before.
    export XYZ_WARMUP_PROGRESS_FILE="${XYZ_WARMUP_PROGRESS_FILE:-/tmp/xyz-warmup-progress.json}"
    XYZ_WARMUP_STATUS_BUCKET="${XYZ_WARMUP_STATUS_BUCKET:-xyz-pilot-status-<AWS_ACCOUNT_ID>}"
    _progress_stop=/tmp/xyz-warmup-progress.stop
    rm -f "$XYZ_WARMUP_PROGRESS_FILE" "$_progress_stop"
    _upload_progress() {
        [[ -f "$XYZ_WARMUP_PROGRESS_FILE" ]] && \
                aws s3 cp "$XYZ_WARMUP_PROGRESS_FILE" \
                    "s3://$XYZ_WARMUP_STATUS_BUCKET/warmup/progress.json" \
                    --content-type application/json --region "${AWS_REGION:?set AWS_REGION}" >/dev/null 2>&1 || true
    }
    ( for _ in $(seq 1 260); do
          if [[ -f "$_progress_stop" ]]; then break; fi
          _upload_progress
          sleep 3
      done ) &
    _progress_uploader_pid=$!

    # Disable set -e only around the warm-up so we can capture its exit code and
    # always stop the uploader + push the terminal state, then re-propagate rc.
    set +e
    /opt/xyz-warmup/venv/bin/python /opt/xyz-warmup/warmup.py
    _warmup_rc=$?
    set -e

    touch "$_progress_stop"
    kill "$_progress_uploader_pid" 2>/dev/null || true
    _upload_progress   # final push so the done/failed state lands in S3

    if [[ $_warmup_rc -eq 0 ]]; then
        echo "==== $(date -u --iso-8601=seconds) · xyz-warmup finished ===="
    else
        echo "==== $(date -u --iso-8601=seconds) · xyz-warmup FAILED (rc=$_warmup_rc) ===="
    fi
    exit "$_warmup_rc"
} 2>&1 | tee -a "$LOG"
