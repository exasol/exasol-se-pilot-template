# <CUSTOMER_NAME> MCP Stack — remote Exasol MCP Server on AWS

> **Purpose:** deploy the official `exadockerci4/exasol-mcp-server` as the
> public remote MCP endpoint for ChatGPT Web, Claude Desktop/Code, VS Code
> Copilot, and SDK clients. It is exposed through a named Cloudflare tunnel and
> protected by Cognito OAuth, so subscribers can connect without installing a
> local MCP server or receiving an Exasol password. Complements the local-install
> path in [`docs/30-runbooks/mcp/XYZ_MCP_CLIENT_GUIDE.md`](../../docs/30-runbooks/mcp/XYZ_MCP_CLIENT_GUIDE.md).

## Architecture

```
Subscriber clients                       AWS eu-central-1
+---------------------+                  +-----------------------------+
| ChatGPT Web /       |                  | EC2 <EC2_INSTANCE_ID>     |
| Claude / VS Code /  |                  |                             |
| SDK clients         |                  |                             |
|                     |   HTTP/MCP       |  +---------------------+    |
|   (no local MCP)    ──Cognito OAuth───▶|  | xyz-mcp-cloudflared |    |
|                     |                  |  | named tunnel        |    |
+---------------------+                  |  +──────────┬──────────+    |
                                         |             │ proxy_pass    |
                                         |             ▼               |
                                         |  +─────────────────────+    |
                                         |  | xyz-mcp-server      |    |
                                         |  | exadockerci4/exasol-|    |
                                         |  |   mcp-server:1.7.0  |    |
                                         |  | OAuthProxy :8000    |    |
                                         |  +──────────┬──────────+    |
                                         |             │ pyexasol      |
                                         |             ▼ TLS:8563      |
                                         |  +─────────────────────+    |
                                         |  | Exasol Personal     |    |
                                         |  | <PILOT_SCHEMA>          |    |
                                         |  | user: xyz_dash (RO) |    |
                                         |  +─────────────────────+    |
                                         +-----------------------------+
```

The remote MCP server connects to the live <PILOT_NAME> DB with the shared
least-privilege `xyz_dash` credential from the EC2-side env file. Public clients
authenticate with Cognito and never receive the Exasol password.

Important tool-surface note: the Pilot deployment wraps the official image with
`pilot_public_mcp_server.py` and exposes guarded read-only SQL aliases
`exasol_query`, `execute_read_only_sql`, and `run_exasol_query` for ChatGPT Web,
Claude Desktop, and VS Code. This shipped under
`XYZ-PILOT-145` and resolves
`XYZ-PILOT-144`.

## Trust model (Pilot)

| Layer | Boundary |
|---|---|
| Cloudflared named tunnel | Stable public hostname `https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp`; not a secret. |
| MCP server auth | FastMCP OAuthProxy validates Cognito-issued JWTs. Anonymous calls return HTTP 401 with protected-resource metadata. |
| Database | `xyz_dash` user — `SELECT` only on `<PILOT_SCHEMA>`. Cannot mutate. |

The public URL is safe to share with Pilot subscribers because OAuth is the
access boundary. Keep the subscriber list controlled; the backing database user
is still shared until per-user Exasol identity is implemented.

## Production hardening status

The first hardening step shipped under
`XYZ-PILOT-035`: named tunnel,
stable hostname, and Cognito OAuth on the MCP server. Remaining hardening when
the Pilot widens out:

1. **Public guarded SQL tool** — shipped in
   `XYZ-PILOT-145` with
   `xyz_dash`, verb allow-listing, row/char caps, process serialization, and
   DDL/DML rejection.
2. **Per-user database identity** — switch from a single `xyz_dash` to OAuth
   passthrough or mapped Exasol users. Each subscriber gets attributable audit
   logs.
3. **Action-level controls** — when new MCP tools are added, refresh/review them
   in ChatGPT workspace app settings and VS Code/Claude client trust prompts.

## File layout

```
pilot_mcp_stack/
├── README.md            <- this file
├── docker-compose.yml   <- mcp + cloudflared services (official image)
└── .env.example         <- EXA_DSN / EXA_USER / EXA_PASSWORD + OAuth template
```

There is **no Dockerfile** in this folder; the deployed public stack pulls
`exadockerci4/exasol-mcp-server:1.7.0` straight from Docker Hub.

## Deploy / operate

> **Current target state:** named Cloudflare tunnel terminating at
> `https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp`, with FastMCP OAuthProxy validating
> AWS Cognito-issued JWTs. Full implementation log lives in
> [`docs/30-runbooks/mcp/XYZ_MCP_PRODUCTION_HARDENING.md`](../../docs/30-runbooks/mcp/XYZ_MCP_PRODUCTION_HARDENING.md).

### First-time deploy on EC2 (target state)

```bash
ssh ubuntu@<EXASOL_HOSTNAME>

sudo mkdir -p /opt/xyz-mcp/cloudflared
sudo chown -R ubuntu:ubuntu /opt/xyz-mcp
cd /opt/xyz-mcp

# 1. Tunnel auth (one-time). Drops cert.pem in /opt/xyz-mcp/cloudflared/.
sudo docker run --rm -v /opt/xyz-mcp/cloudflared:/root/.cloudflared \
    cloudflare/cloudflared:latest tunnel login

# 2. Create the tunnel + DNS route on the <PILOT_DOMAIN> zone.
sudo docker run --rm -v /opt/xyz-mcp/cloudflared:/root/.cloudflared \
    cloudflare/cloudflared:latest tunnel create <PROJECT_SLUG>-mcp
sudo docker run --rm -v /opt/xyz-mcp/cloudflared:/root/.cloudflared \
    cloudflare/cloudflared:latest tunnel route dns <PROJECT_SLUG>-mcp <PROJECT_SLUG>.<PILOT_DOMAIN>

# 3. Write /opt/xyz-mcp/cloudflared/config.yml:
#      tunnel: <UUID from step 2>
#      credentials-file: /etc/cloudflared/<UUID>.json
#      ingress:
#        - hostname: <PROJECT_SLUG>.<PILOT_DOMAIN>
#          service: http://mcp:8000
#        - service: http_status:404

# 4. /opt/xyz-mcp/.env — copy from .env.example, fill in EXA_PASSWORD
#    from the SSM-backed xyz_dash secret path and the Cognito OAuth settings.

# 5. Pull + bring up:
sudo docker compose pull && sudo docker compose up -d
```

The MCP endpoint is then permanently `https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp` — no rotation on restart.

### Re-deploy after a code / compose change

```bash
cd /opt/xyz-mcp
sudo docker compose pull && sudo docker compose up -d
```

The hostname is now stable, so there's no longer a "recapture the tunnel URL" step.

### Health check

The MCP server is a JSON-RPC service; there's no simple `/healthz` endpoint. The healthiest probe (once the named tunnel is live) is a real `initialize` request:

```bash
URL='https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp'
curl -s -X POST "$URL" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0.1"}}}'
```

Expect HTTP `401` (auth required — proves OAuthProxy is live) **until** you add a Cognito-issued bearer token. With a token, expect a JSON-RPC response containing `serverInfo.name == "exasol-mcp"`.

## Wiring clients to the remote server

See [`docs/30-runbooks/mcp/XYZ_MCP_CLIENT_GUIDE.md`](../../docs/30-runbooks/mcp/XYZ_MCP_CLIENT_GUIDE.md)
for per-client configuration:

- Option C: Claude Desktop / Claude Code / SDK clients.
- Option F: ChatGPT Web custom MCP app.
- Option G: GitHub Copilot in VS Code using workspace `.vscode/mcp.json`.

The endpoint URL is the same for all of them:

```text
https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp
```

## Cost

| Item | Monthly impact |
|---|---|
| Image pull + container runtime | < $0.01 (CPU/RAM negligible on the existing r6i.xlarge) |
| Cloudflared named tunnel | free |
| Cloudflare DNS + zone | free |
| AWS Cognito MAU (≤ 50 000 free) | free |
| Outbound bandwidth (catalog calls + small result sets) | < $0.10 |
| **Total** | **< $0.50 / month additional** on top of the existing <PILOT_NAME> AWS spend |
