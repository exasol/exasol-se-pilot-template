#!/usr/bin/env bash
# XYZ-PILOT-059 — one-time setup of the Exasol Personal warmup on EC2.
#
# Idempotent: re-running picks up newer warmup.py / unit file without
# disturbing the schedule. Safe to run by hand whenever the script or
# the systemd unit changes.
#
# Run as ubuntu user with sudo; the script self-elevates where needed.

set -euo pipefail

REPO_DIR=$(cd "$(dirname "$0")" && pwd)
TARGET_DIR=/opt/xyz-warmup
UNIT_NAME=xyz-exasol-warmup.service

echo "==> Installing apt prerequisites (python3-pip, python3-venv) if missing"
if ! dpkg -s python3-venv >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3-pip python3-venv
fi

echo "==> Creating ${TARGET_DIR}/ and copying scripts"
sudo mkdir -p "${TARGET_DIR}"
sudo cp "${REPO_DIR}/warmup.py"    "${TARGET_DIR}/warmup.py"
sudo cp "${REPO_DIR}/run_warmup.sh" "${TARGET_DIR}/run_warmup.sh"
sudo chmod 755 "${TARGET_DIR}/run_warmup.sh"
sudo chmod 644 "${TARGET_DIR}/warmup.py"

echo "==> Creating venv + installing pyexasol (if not present)"
if [[ ! -x "${TARGET_DIR}/venv/bin/python" ]]; then
    sudo python3 -m venv "${TARGET_DIR}/venv"
fi
sudo "${TARGET_DIR}/venv/bin/pip" install --quiet --upgrade pip
sudo "${TARGET_DIR}/venv/bin/pip" install --quiet "pyexasol>=0.27"

echo "==> Installing systemd unit ${UNIT_NAME}"
sudo cp "${REPO_DIR}/${UNIT_NAME}" "/etc/systemd/system/${UNIT_NAME}"
sudo chmod 644 "/etc/systemd/system/${UNIT_NAME}"
sudo systemctl daemon-reload
sudo systemctl enable "${UNIT_NAME}"

echo "==> Done. Service enabled; will fire on next EC2 boot."
echo "    Trigger immediately:  sudo systemctl start ${UNIT_NAME}"
echo "    Inspect:              sudo systemctl status ${UNIT_NAME}"
echo "    Journal:              sudo journalctl -u ${UNIT_NAME} --since today"
echo "    Log file:             sudo less /var/log/xyz-warmup.log"
