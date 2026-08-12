#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# harden_exasol_admin_ui.sh — durable resilience for the Exasol Personal Admin UI
# ----------------------------------------------------------------------------
# Re-applies the Admin-UI resilience hardening on the Exasol Personal node after
# any instance rebuild / `exasol` launcher upgrade / c4 re-play (which regenerate
# the c4-managed `exasol-admin-ui.service` and wipe these overlays). Idempotent —
# safe to run repeatedly.
#
# Background — XYZ-PILOT-277 (<AWS_REGION> restore): the Admin UI Health surfaces
# (`/databases` Health widgets + the overall `/health` System Health view) broke
# because (a) EXA_PROUST_MONITOR monitor-credential drift, then (b) the
# `exasol-admin-ui.service` (a `--user` unit under `ubuntu`, → `c4 admin-ui serve`,
# port 8443) crashed with a Go panic and — being `Restart=no` — stayed dead.
# This script installs the three-layer fix:
#   1. `Restart=on-failure` drop-in  → systemd auto-recovers a crash.
#   2. (already covered by Linger=yes + unit `enabled`) auto-start on STOP/START.
#   3. a `--user` health-probe timer  → restarts the service if `:8443/health`
#      is unreachable/wedged (the "active but unhealthy" case + post-START window).
#
# Canonical reference copies of every file written below live in
# `scripts/provisioning/exasol-admin-ui/` — keep them in sync with this script.
#
# Run via SSM (as root, acts on the c4 user) or directly as the c4 user:
#   sudo bash scripts/harden_exasol_admin_ui.sh            # from a root SSM shell
#   bash scripts/harden_exasol_admin_ui.sh                 # as the ubuntu c4 user
# Applied + validated live on <EC2_INSTANCE_ID> 2026-06-22 (XYZ-PILOT-277 follow-up).
# ----------------------------------------------------------------------------
set -euo pipefail

C4_USER="${XYZ_C4_USER:-ubuntu}"
C4_HOME="$(getent passwd "$C4_USER" | cut -d: -f6)"
C4_UID="$(id -u "$C4_USER")"
XRD="/run/user/${C4_UID}"
CFG="${C4_HOME}/.config/systemd/user"
BIN="${C4_HOME}/.local/bin"

# Run a command as the c4 user (direct when already that user, else via sudo).
as_c4() {
  if [ "$(id -un)" = "$C4_USER" ]; then
    env XDG_RUNTIME_DIR="$XRD" "$@"
  else
    sudo -u "$C4_USER" XDG_RUNTIME_DIR="$XRD" "$@"
  fi
}
# Write stdin to a c4-user-owned file.
write_c4() { as_c4 tee "$1" >/dev/null; }

as_c4 mkdir -p "${CFG}/exasol-admin-ui.service.d" "$BIN"

# 1. Restart-on-failure drop-in for the c4-managed admin UI unit.
write_c4 "${CFG}/exasol-admin-ui.service.d/restart.conf" <<'EOF'
[Service]
Restart=on-failure
RestartSec=5
EOF

# 2. Health-probe script — derives the node's primary private IP dynamically so
#    it survives a future re-IP (the Admin UI binds to that IP:8443, not 127.0.0.1).
write_c4 "${BIN}/exasol-admin-ui-healthprobe.sh" <<'EOF'
#!/bin/bash
# Probe the Exasol Admin UI /health endpoint; restart the service if it is down.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
ip="$(hostname -I | awk '{print $1}')"
code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 "https://${ip}:8443/health" 2>/dev/null || echo 000)"
if [ "$code" != "200" ]; then
  systemctl --user restart exasol-admin-ui.service
  logger -t exasol-admin-ui-healthprobe "/health on ${ip}:8443 returned ${code} -> restarted exasol-admin-ui.service"
else
  logger -t exasol-admin-ui-healthprobe "/health on ${ip}:8443 OK (${code})"
fi
EOF
as_c4 chmod +x "${BIN}/exasol-admin-ui-healthprobe.sh"

# 3. Oneshot probe service.
write_c4 "${CFG}/exasol-admin-ui-healthprobe.service" <<'EOF'
[Unit]
Description=Probe Exasol Admin UI /health and restart exasol-admin-ui.service if down
After=exasol-admin-ui.service

[Service]
Type=oneshot
ExecStart=%h/.local/bin/exasol-admin-ui-healthprobe.sh
EOF

# 4. Timer: 120s after every boot/START, then every 5 minutes.
write_c4 "${CFG}/exasol-admin-ui-healthprobe.timer" <<'EOF'
[Unit]
Description=Post-START + periodic Exasol Admin UI /health probe

[Timer]
OnBootSec=120
OnUnitActiveSec=300
Persistent=true

[Install]
WantedBy=timers.target
EOF

as_c4 systemctl --user daemon-reload
as_c4 systemctl --user enable --now exasol-admin-ui-healthprobe.timer

echo "== exasol-admin-ui.service restart policy =="
as_c4 systemctl --user show exasol-admin-ui.service -p Restart -p RestartUSec -p UnitFileState
echo "== health-probe timer =="
as_c4 systemctl --user list-timers exasol-admin-ui-healthprobe.timer --no-pager | head -3
echo "harden_exasol_admin_ui: applied (Restart=on-failure + health-probe timer)."
