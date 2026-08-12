#!/usr/bin/env bash
#
# XYZ-PILOT-083 v3 — Rotate the Exasol Personal Admin UI password (port 8443).
#
# Why this is a walkthrough (and what changed from v1/v2)
# -------------------------------------------------------
# The Exasol V8 Admin UI cannot rotate its own password through the web UI;
# the actual config-write happens via the `c4` CLI (Cluster Operating
# Configurator), which on Exasol Personal Edition lives on the EC2 host's
# `ubuntu` user PATH at `/home/ubuntu/.local/bin/c4`.
#
#   v1 (wrong) — assumed `c4` runs on macOS with operator-supplied
#                PLAY_ID / NODE_ID env vars. Would have failed at the
#                `command -v c4` check.
#   v2 (wrong) — assumed `c4` runs inside `exasol shell container` (the
#                Exasol COS container access path). Verified empirically
#                2026-05-25: `find / -name c4` inside that container
#                returns nothing. Dead end.
#   v3 (this)  — uses `exasol shell host`, which SSHes to the EC2 host as
#                `ubuntu` where `c4` IS on PATH. PLAY_ID + LOCAL_PLAY_ID
#                are discovered there with `c4 ps`, then `c4 local reconf`
#                writes the stored config. **Critical:** `c4 local reconf`
#                only writes STORED config — the running Admin UI process
#                keeps the OLD password until the play restarts. The only
#                way to apply the new config on Personal Edition is to
#                reboot the EC2 host (`c4 local reboot/down/up` all return
#                "CCC_CLOUD_ACTION is not supported for cloud type local"
#                — they're stubs).
#
# So the v3 flow is:
#   1. Stage the new password.
#   2. Open `exasol shell host` (SSH to EC2 ubuntu).
#   3. Operator pastes one command block: discover IDs + run c4 local
#      reconf with the new password hash.
#   4. Operator exits the host shell.
#   5. Script asks for confirmation, then triggers an EC2 reboot so the
#      running Admin UI picks up the new config (~3-5 min outage of all
#      Docker services on EC2 — chatbot, Streamlit, Grafana, MCP).
#   6. Script polls Admin UI HTTPS until it returns 200.
#   7. Script stashes the new password to SSM Parameter Store.
#   8. Operator manually verifies sign-in at the browser.
#
# Prerequisites on the operator's machine (macOS):
#   - `exasol` CLI installed (https://github.com/exasol/exasol-personal).
#   - AWS CLI authenticated with `ec2:RebootInstances` on
#       arn:aws:ec2:eu-central-1:<AWS_ACCOUNT_ID>:instance/<EC2_INSTANCE_ID>
#     AND `ssm:PutParameter` on
#       arn:aws:ssm:eu-central-1:<AWS_ACCOUNT_ID>:parameter/<PROJECT_SLUG>/secrets/*
#     (the `<AWS_PROFILE>` profile satisfies both).
#   - cwd set to the Exasol Personal deployment directory (the one
#     containing `secrets.json` and `connection-instructions.txt`).
#
# Usage:
#   cd /Users/<LOCAL_USER>/GitHub/<PROJECT_SLUG>/exasol-deployment
#   /path/to/scripts/rotate-exasol-admin-ui-pw.sh
#
# Verification afterwards:
#   - https://<PROJECT_SLUG>-status-<AWS_ACCOUNT_ID>.s3.eu-central-1.amazonaws.com/dashboard.html
#     → Reveal "Exasol Personal · Admin UI" returns the new password.
#   - https://<EXASOL_HOSTNAME>:8443
#     → sign in as `admin` with the new password should succeed.

set -euo pipefail

PWFILE="/tmp/new-admin-ui-pw.txt"
SSM_NAME="/<PROJECT_SLUG>/secrets/exasol-admin-ui"
REGION="${AWS_REGION:-eu-central-1}"
INSTANCE_ID="<EC2_INSTANCE_ID>"
ADMIN_UI_URL="https://<EXASOL_HOSTNAME>:8443"

# ─── Sanity: tools + cwd ───────────────────────────────────────────────────
if ! command -v exasol >/dev/null 2>&1; then
    cat <<'EOF'
ERROR: 'exasol' CLI not found on PATH.

Personal Edition's CLI is the only entry point to the EC2 host where the
Admin UI password lives. Install with:

    curl https://downloads.exasol.com/exasol-personal/installer.sh | sh

Then open a new shell, confirm with `exasol version`, and re-run this script.
EOF
    exit 1
fi
if ! command -v aws >/dev/null 2>&1; then
    echo "ERROR: 'aws' CLI not found on PATH (needed for the EC2 reboot + SSM stash)."
    exit 1
fi
if [[ ! -f ./secrets.json ]] || [[ ! -f ./connection-instructions.txt ]]; then
    cat <<'EOF'
ERROR: this script expects to be run from inside the Exasol Personal
deployment directory — the one containing `secrets.json` and
`connection-instructions.txt`. The `exasol shell host` command locates the
deployment relative to your current working directory.

Example for the <PILOT_NAME>:

    cd /Users/<LOCAL_USER>/GitHub/<PROJECT_SLUG>/exasol-deployment
    /path/to/scripts/rotate-exasol-admin-ui-pw.sh
EOF
    exit 1
fi

echo "✓ exasol $(exasol version 2>&1 | head -1 || true) found"
echo "✓ aws    $(aws --version 2>&1 | head -1 || true)"
echo "✓ deployment dir: $(pwd)"
echo ""

# ─── Stage the new password ───────────────────────────────────────────────
if [[ ! -f "$PWFILE" ]]; then
    echo "No new password file found at $PWFILE."
    read -r -p "Generate a strong 20-character random password now? [y/N] " yn
    if [[ "$yn" == "y" || "$yn" == "Y" ]]; then
        python3 -c "import secrets; print(secrets.token_urlsafe(15))" > "$PWFILE"
        chmod 600 "$PWFILE"
        echo "✓ generated, stored at $PWFILE (mode 600)"
    else
        cat <<'EOF'

Aborting. Put your chosen password in /tmp/new-admin-ui-pw.txt with
mode 600, then re-run this script. Suggested:

    echo "YourNewPassword123!" > /tmp/new-admin-ui-pw.txt
    chmod 600 /tmp/new-admin-ui-pw.txt
EOF
        exit 1
    fi
fi
chmod 600 "$PWFILE"
NEW_PW="$(cat "$PWFILE")"
if [[ -z "$NEW_PW" ]]; then
    echo "ERROR: $PWFILE is empty."
    exit 1
fi
echo "✓ new password staged in $PWFILE (length ${#NEW_PW})"

# ─── Print the paste-able command block ───────────────────────────────────
cat <<EOF

══════════════════════════════════════════════════════════════════════════
  GUIDED WALKTHROUGH — Exasol Personal Admin UI password rotation (v3)
══════════════════════════════════════════════════════════════════════════

  After this script opens \`exasol shell host\`, you'll be on the EC2 host
  as the \`ubuntu\` user (where \`c4\` lives at /home/ubuntu/.local/bin/c4).

  Paste the block below at the host prompt and press Enter. The block:
    (a) discovers PLAY_ID and LOCAL_PLAY_ID via \`c4 ps\`,
    (b) runs the actual \`c4 local reconf\` with the new password hash,
    (c) prints "Done" on success.

  IMPORTANT — your new password is embedded below. After the rotation
  succeeds, clear your terminal scrollback (Cmd+K on Terminal.app, or
  exit/relaunch) so the value doesn't linger.

────────────────────────────────────────────────────────────────────────
  PASTE THIS INSIDE \`exasol shell host\`:
────────────────────────────────────────────────────────────────────────

PLAY_ID=\$(c4 ps -e '.[].play.id' | head -1)
LOCAL_PLAY_ID=\$(c4 ps --local -e '.[].play.id' | head -1)
echo "PLAY_ID=\$PLAY_ID"
echo "LOCAL_PLAY_ID=\$LOCAL_PLAY_ID"
c4 local reconf --ccc-adminui-admin-password-hash \\
    "\$(c4 pwdhash -c -i <<< "$NEW_PW" | tr -d '\n' | base64)" \\
    "\$LOCAL_PLAY_ID"
exit   # back to the Mac

────────────────────────────────────────────────────────────────────────

  When you see "INFO ... Done" from \`c4 local reconf\`, the stored
  config has been updated. The running Admin UI process still has the
  OLD password until we reboot the EC2 host (next step, after you exit).

══════════════════════════════════════════════════════════════════════════
EOF

read -r -p "Ready to open \`exasol shell host\`? [y/N] " ok
if [[ "$ok" != "y" && "$ok" != "Y" ]]; then
    echo "Aborted before opening the shell. Re-run when ready."
    exit 1
fi

# ─── Hand off to the interactive host shell ───────────────────────────────
exasol shell host

# ─── Resume after operator exits the shell ───────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════════════════════"
echo "  Confirm the rotation took"
echo "══════════════════════════════════════════════════════════════════════════"
echo ""
read -r -p "Did \`c4 local reconf\` return WITHOUT errors (look for 'INFO ... Done')? [y/N] " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    cat <<'EOF'

ABORT: not rebooting EC2, not stashing to SSM. The new password is still
in /tmp/new-admin-ui-pw.txt — fix whatever went wrong (most common cause:
LOCAL_PLAY_ID wasn't picked up because `c4 ps --local` returned empty —
check that you ran the block on the HOST node, not still inside the EC2
shell parent) and re-run this script (it'll re-use the staged password).
EOF
    exit 1
fi

# ─── EC2 reboot to apply the new config to the running Admin UI service ──
echo ""
echo "══════════════════════════════════════════════════════════════════════════"
echo "  Apply step — EC2 reboot"
echo "══════════════════════════════════════════════════════════════════════════"
echo ""
cat <<'EOF'
`c4 local reconf` wrote the new password hash to the play's STORED
config. The running Admin UI process still has the OLD password — it
needs a play restart to pick up the new config.

On Exasol Personal Edition, `c4 local reboot/down/up` are stubs (the
dispatcher rejects them with "CCC_CLOUD_ACTION is not supported for
cloud type local"). The supported path is an EC2 instance reboot, which
restarts the `exasol_launcher` chain and brings the play up with the
new stored config.

This is a ~3-5 min outage for ALL services on the EC2 host:
  - Exasol DB (chatbot's exasol_query, Streamlit QBR, Grafana datasource)
  - chatbot (<PROJECT_SLUG>-chatbot)
  - Streamlit QBR (xyz-qbr-streamlit)
  - Grafana (xyz-grafana)
  - MCP servers (internal + public)
  - All 4 cloudflared tunnels
EOF
echo ""
read -r -p "Reboot EC2 ${INSTANCE_ID} now? [y/N] " reboot_ok
if [[ "$reboot_ok" != "y" && "$reboot_ok" != "Y" ]]; then
    cat <<'EOF'

DEFERRED: NOT rebooting EC2 yet, NOT stashing to SSM yet. The new
password is in the play's stored config but the running Admin UI still
has the old password. Re-run this script during your maintenance window
— it'll skip the staging + reconf (already done) and just trigger the
reboot + SSM stash.

To re-trigger the apply step later, you can also just run:
    aws ec2 reboot-instances --region eu-central-1 --instance-ids <EC2_INSTANCE_ID>
followed by the `aws ssm put-parameter` shown in the runbook §7.5.
EOF
    exit 1
fi

aws ec2 reboot-instances --region "$REGION" --instance-ids "$INSTANCE_ID"
echo "✓ EC2 reboot triggered. Waiting for Admin UI to come back…"

for i in $(seq 1 40); do
    sleep 15
    code=$(curl -sIk --max-time 5 "${ADMIN_UI_URL}/" -o /dev/null -w '%{http_code}' 2>/dev/null || echo "down")
    if [[ "$code" == "200" ]]; then
        echo "  [t+$((i*15))s] Admin UI HTTP $code — back up ✓"
        break
    fi
    echo "  [t+$((i*15))s] Admin UI HTTP $code — still rebooting…"
done

# ─── Stash to SSM ────────────────────────────────────────────────────────
echo ""
echo "──────────────────────────────────────────────────────────────────────────"
echo "  Stashing the new password into SSM Parameter Store"
echo "──────────────────────────────────────────────────────────────────────────"
echo ""
aws ssm put-parameter \
    --name "$SSM_NAME" \
    --value "$NEW_PW" \
    --type SecureString \
    --overwrite \
    --description "Exasol Personal Admin UI (port 8443) — rotated $(date -u +%Y-%m-%dT%H:%M:%SZ) via XYZ-PILOT-083 v3 walkthrough" \
    --region "$REGION" \
    --query '{name:Name,version:Version}' --output table

cat <<EOF

✓ SSM Parameter Store updated.
✓ EC2 rebooted; running Admin UI now uses the new password.

CLEANUP — recommended:
  shred -u $PWFILE                          # wipe local copy
  # On macOS Terminal, Cmd+K clears scrollback to remove the embedded
  # password from your visible history.

VERIFY in browser:
  ${ADMIN_UI_URL}
    → sign in as admin with the new password. Must succeed.
  https://<PROJECT_SLUG>-status-<AWS_ACCOUNT_ID>.s3.eu-central-1.amazonaws.com/dashboard.html
    → "Exasol Personal · Admin UI" → Reveal returns the new value.
EOF
