---
name: docker-compose-ec2
description: Use when deploying small multi-service Docker Compose stacks to a single AWS EC2 instance — especially when co-locating apps with a database on the same host, exposing services via Cloudflare Tunnel without inbound port changes, and avoiding the wget/healthcheck and host-gateway gotchas that come up with slim Python and Alpine images.
license: locally authored — authored for this pilot template
---

# Compose deploys to a single EC2 host

Use this skill when:
- You're deploying a small multi-service stack (web app + reverse proxy / tunnel + sidecar) to a **single Ubuntu EC2 instance** that already runs another workload (e.g. an Exasol database) and you don't want to disturb it.
- You want **HTTPS exposure without opening Security Group ports** — i.e. via Cloudflare Tunnel quick-tunnels (`*.trycloudflare.com`) or named tunnels.
- You're hitting one of the classic compose-on-EC2 traps below.

Do NOT use this skill for: production-grade k8s, multi-host orchestration, or anything requiring an LB / autoscaling. Use ECS / EKS / Nomad for that.

---

## Workflow

### 1. Reconnaissance before touching the host

Always probe first — assume nothing about the EC2 image or what's already installed.

```bash
ssh ubuntu@HOST '
  echo "=== uname ==="; uname -a
  echo "=== free -h ==="; free -h
  echo "=== docker ==="; docker --version 2>&1 || echo "no docker"
  echo "=== compose plugin ==="; docker compose version 2>&1 || echo "no compose"
  echo "=== cloudflared ==="; cloudflared --version 2>&1 | head -1 || echo "no cloudflared"
  echo "=== listening ==="; sudo ss -tlnp | head -20
  echo "=== disk ==="; df -h / | tail -1
  echo "=== firewall ==="; ufw status; sudo iptables -L INPUT -n | head -5
'
```

Things to confirm before installing anything:
- **Free RAM** ≥ 2 GiB headroom over the existing workload.
- **Free disk** ≥ 5 GiB for image layers.
- **Ports you plan to bind locally are free** (`ss -tlnp`).
- **No host firewall conflicts** (ufw is usually inactive on AWS Ubuntu but check).
- **What's already listening on `0.0.0.0`** vs loopback — these are processes the SG already exposes.

### 2. Idempotent installer

Write a `deploy/install_ec2.sh` you can re-run safely. Skeleton:

```bash
#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null; then
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
      | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
      docker-buildx-plugin docker-compose-plugin
    sudo usermod -aG docker "$USER" || true
fi

if ! command -v cloudflared >/dev/null; then
    sudo mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
      | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
      https://pkg.cloudflare.com/cloudflared $(. /etc/os-release && echo "$VERSION_CODENAME") main" \
      | sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
    sudo apt-get update -qq
    sudo apt-get install -y cloudflared
fi
```

Re-run after every Ubuntu apt update — it's a no-op if both are already current.

### 3. Compose anti-patterns and what to do instead

#### Trap 1: `wget` is not in `python:*-slim` base images

Healthchecks like:
```yaml
test: ["CMD", "wget", "-q", "-O", "-", "http://localhost:8501/_stcore/health"]
```
fail with `OCI runtime exec failed: ... wget: executable file not found`. The container reports unhealthy and any service with `depends_on: { condition: service_healthy }` blocks forever.

Use Python's stdlib instead — every `python:*` image has it:
```yaml
test:
  - "CMD"
  - "python"
  - "-c"
  - "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health',timeout=3).read()==b'ok' else 1)"
```

For non-Python images (e.g. Grafana), `curl` is usually present:
```yaml
test: ["CMD-SHELL", "curl -fs http://localhost:3000/api/health || exit 1"]
```

#### Trap 2: `EXASOL_HOST=localhost` from inside a container points to the container

A container's `localhost` is its own loopback, NOT the host. Three ways to fix:

| Approach | When to use |
|---|---|
| `network_mode: host` | App needs many host services, no port-mapping conflicts. Conflicts with bridge containers in the same project. |
| `extra_hosts: ["host.docker.internal:host-gateway"]` + `EXASOL_HOST=host.docker.internal` | Cleanest for bridge networks. Requires Linux ≥ kernel 5.5 and Docker ≥ 20.10. |
| Hardcode the EC2 private IP (e.g. `172.30.1.11`) | Last resort. Brittle if the instance is replaced. |

#### Trap 3: Cloudflared starts before the upstream is healthy

If the tunnel container races ahead, the first edge requests get 502. Use the compose dependency gate:
```yaml
cloudflared:
  image: cloudflare/cloudflared:latest
  command: tunnel --no-autoupdate --url http://streamlit:8501
  depends_on:
    streamlit:
      condition: service_healthy
```

Combined with Trap 1, broken healthchecks make this gate hang forever — debug the healthcheck first.

#### Trap 4: Port conflict with an Exasol Personal host

Exasol Personal binds a lot of ports. Confirmed safe-to-use on a default install: **80, 443, 3000 (Grafana), 8501 (Streamlit), 9090 (Prometheus), 9100 (node_exporter)**. Avoid: 22, 2581, 8443, 8563, 200xx, 379xx.

Always bind app containers to **`127.0.0.1:PORT:PORT`**, never `0.0.0.0:PORT`. This keeps the public Security Group from accidentally exposing them — only the tunnel touches the public internet.

### 4. Cloudflare Tunnel — quick vs named

| Mode | Hostname | Stable? | Setup |
|---|---|---|---|
| Quick tunnel | random `*.trycloudflare.com` | **No** — changes every container restart | Just `--url http://upstream:port` |
| Named tunnel | yours, e.g. `qbr.example.com` | Yes | Requires Cloudflare account, `cloudflared tunnel login`, DNS CNAME |

For demos, pilots, and short-lived POCs, quick tunnels are perfect — no Cloudflare account needed, free TLS, no DNS work.

For anything user-facing for >24 h, switch to a named tunnel so the URL doesn't churn.

To capture the quick tunnel hostname programmatically:
```bash
sudo docker logs xyz-qbr-cloudflared 2>&1 \
  | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1
```
The hostname appears within ~3 s of the cloudflared container starting.

### 5. Secrets handling

- **Never commit `.env`.** Add `${PROJECT_DIR}/.env` to `.gitignore`. Ship `.env.example` instead with empty `*_PASSWORD` lines.
- Provision the EC2-side `.env` with `scp -p` from a `/tmp/` file you've `chmod 600`'d. Don't echo passwords into ssh sessions; their history persists in `~/.bash_history`.
- The dashboard / app should read creds from env vars (`os.environ[...]`), never from constants.
- Use a **read-only DB user** for dashboards (e.g. `HPC_DASH` with `SELECT` only — never `sys`).

### 6. Iteration loop on EC2

```bash
# Local: edit code, then sync
rsync -avz --exclude='.venv' --exclude='__pycache__' \
  -e "ssh -i $KEY" $LOCAL_DIR/ ubuntu@$HOST:/opt/$PROJECT/

# Remote: rebuild changed image only, restart
ssh ubuntu@$HOST 'cd /opt/$PROJECT && sudo docker compose up -d --build'
```

Fast cycle: the only image that needs rebuilding is the one whose `COPY` source changed. Compose handles this. For pure config changes (volumes / env / ports), `docker compose up -d` is enough — no rebuild.

### 7. Teardown

```bash
ssh ubuntu@$HOST '
  cd /opt/$PROJECT
  sudo docker compose down --volumes --remove-orphans
  cd /opt && sudo rm -rf $PROJECT
'
```

The cloudflared quick tunnel disappears the moment the container stops — no DNS leftover.

---

## Reference: minimal compose for "app + tunnel"

```yaml
name: example

services:
  app:
    build: .
    image: example-app:latest
    container_name: example-app
    restart: unless-stopped
    env_file: [.env]
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "127.0.0.1:8000:8000"
    healthcheck:
      test:
        - "CMD"
        - "python"
        - "-c"
        - "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=3).status==200 else 1)"
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s

  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: example-cloudflared
    restart: unless-stopped
    command: tunnel --no-autoupdate --url http://app:8000
    depends_on:
      app:
        condition: service_healthy
```

---

## Lessons captured from the <PILOT_NAME>

1. **Always probe the host first.** Saved a wasted reinstall when we found Ubuntu 22.04 / 6.8 kernel / 30 GiB RAM already had everything we needed except Docker + cloudflared.
2. **`wget`-based healthchecks fail silently in slim Python images.** First-deploy hang on `service_healthy` was the symptom; we traced it via `docker exec` + manual curl.
3. **Container → host networking required `host-gateway`** to talk to Exasol on the same EC2 host. `localhost` from inside the bridge network was a non-starter.
4. **Cloudflare quick tunnels are perfect for POC public exposure.** Zero SG changes, free trusted TLS, easy teardown. The ephemeral hostname is the trade-off — flag it so people don't bookmark it.
5. **Read-only DB user with `SELECT ANY DICTIONARY` is the right scope** for both an <PILOT_NAME> business-data dashboard and a DB-monitoring dashboard. Sys creds never need to leave a developer laptop.
