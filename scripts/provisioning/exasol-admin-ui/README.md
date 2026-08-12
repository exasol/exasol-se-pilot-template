# Exasol Admin UI resilience provisioning

Durable hardening for the Exasol Personal Admin UI (`exasol-admin-ui.service` →
`c4 admin-ui serve`, port 8443) on the Pilot node, so its Health surfaces
(`/databases` Health widgets + the overall `/health` System Health view) stay up
across STOP/START, crashes, and wedges. Background: [XYZ-PILOT-277](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md).

## Files (canonical reference copies)

| File | Installed to (on the node, as the `ubuntu` c4 user) | Purpose |
|---|---|---|
| `restart.conf` | `~/.config/systemd/user/exasol-admin-ui.service.d/restart.conf` | `Restart=on-failure` — auto-recover the c4-managed unit from a crash |
| `exasol-admin-ui-healthprobe.sh` | `~/.local/bin/exasol-admin-ui-healthprobe.sh` | Probe `:8443/health`; restart the service if down |
| `exasol-admin-ui-healthprobe.service` | `~/.config/systemd/user/` | Oneshot that runs the probe |
| `exasol-admin-ui-healthprobe.timer` | `~/.config/systemd/user/` | 120 s after every boot/START, then every 5 min |

## Apply / re-apply (idempotent)

Run **[`scripts/harden_exasol_admin_ui.sh`](../../harden_exasol_admin_ui.sh)** — it
installs all four files above, `daemon-reload`s, and enables the timer. Run it
after any instance rebuild / `exasol` launcher upgrade / c4 re-play (those
regenerate the c4 unit and wipe these `--user` overlays):

```bash
# via SSM (root shell) on <EC2_INSTANCE_ID>, or as the ubuntu c4 user:
sudo bash scripts/harden_exasol_admin_ui.sh
```

The script embeds equivalents of these files (so it works standalone over SSM
without a repo checkout). Keep the two in sync.
