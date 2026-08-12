#!/bin/bash
# Installed to ~/.local/bin/exasol-admin-ui-healthprobe.sh by scripts/harden_exasol_admin_ui.sh.
# Probe the Exasol Admin UI /health endpoint; restart the service if it is down.
# Derives the node's primary private IP dynamically (survives a re-IP).
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
ip="$(hostname -I | awk '{print $1}')"
code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 "https://${ip}:8443/health" 2>/dev/null || echo 000)"
if [ "$code" != "200" ]; then
  systemctl --user restart exasol-admin-ui.service
  logger -t exasol-admin-ui-healthprobe "/health on ${ip}:8443 returned ${code} -> restarted exasol-admin-ui.service"
else
  logger -t exasol-admin-ui-healthprobe "/health on ${ip}:8443 OK (${code})"
fi
