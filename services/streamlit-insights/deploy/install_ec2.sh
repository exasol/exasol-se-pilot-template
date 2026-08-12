#!/usr/bin/env bash
# Idempotent installer for Docker engine + compose plugin on Ubuntu 22.04.
# Cloudflared comes from the official Cloudflare apt repo; we use the docker
# image at runtime, so we don't strictly need the host binary, but installing
# it makes one-off `cloudflared tunnel` commands available for ops.
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
    echo "[install] adding Docker apt repo + installing engine + compose plugin"
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo usermod -aG docker "$USER" || true
else
    echo "[install] docker already present: $(docker --version)"
fi

if ! command -v cloudflared >/dev/null 2>&1; then
    echo "[install] adding Cloudflare apt repo + installing cloudflared"
    sudo mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(. /etc/os-release && echo "$VERSION_CODENAME") main" \
        | sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y cloudflared
else
    echo "[install] cloudflared already present: $(cloudflared --version | head -1)"
fi

echo "[install] done"
echo "  docker: $(docker --version)"
echo "  compose plugin: $(docker compose version)"
echo "  cloudflared: $(cloudflared --version | head -1)"
