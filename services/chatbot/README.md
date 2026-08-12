# <PILOT_NAME> Chatbot — `services/chatbot/`

> **XYZ-PILOT-043 Phase 1 + XYZ-PILOT-049 Phase 2 + XYZ-PILOT-053 Phase 3.** Browser
> chatbot at `https://<PROJECT_SLUG>-chat.<PILOT_DOMAIN>` for Pilot subscribers
> to ask implementation-status + schema + live-data questions. Answers ground
> in a 12-file repo snapshot, an agentic whole-repo `read_repo_file` tool,
> ~20 metadata tools from the internal MCP sidecar, and a `exasol_query`
> tool for live SELECTs. The archive button writes generated Q&A back to the
> repo, and the feature/defect buttons emit `XYZ_MASTER` candidates instead of
> mutating the tracker directly.

## Architecture

```
Subscriber browser
   │ HTTPS
   ▼
Cloudflare edge (xyz-pilot-chat.<PILOT_DOMAIN>)
   │ Argo Tunnel
   ▼
cloudflared sidecar (named tunnel)
   │ http://chatbot:8501 (docker network)
   ▼
Streamlit chatbot container
   │
   ├──► AWS Cognito Hosted UI (SSO — same user pool as MCP server)
   │
   ├──► Bedrock Claude Sonnet 4.6 — Converse API + tool-use (us.anthropic.… inference profile)
   │
   ├──► SSM Parameter Store → /xyz-pilot/chatbot/github-token
   │      └──► GitHub Contents API (authenticated reads of the private repo,
   │           write Q&A archives + XYZ_MASTER candidates via dup-check)
   │
   ├──► raw repo doc snapshot (12 curated docs, cached 5 min)
   │
   ├──► mcp-internal sidecar (exadockerci4/exasol-mcp-server:1.7.0,
   │    --no-auth, internal-only docker network) — ~20 metadata tools
   │    (list_exasol_schemas, describe_exasol_table_or_view, find_*, etc.)
   │
   └──► Exasol Personal :8563 via pyexasol — read-only XYZ_DASH user
        for live SELECTs via the chatbot's `exasol_query` tool
```

## What this chatbot does

**Phase 1 (XYZ-PILOT-043, 2026-05-20)**
- Cognito Hosted-UI OAuth code flow for sign-in. Subscribers use the same credentials as the MCP server (XYZ-PILOT-035).
- Bedrock Claude Sonnet 4.6 answers grounded in a 12-file curated repo snapshot (README + CLAUDE.md + tracker + daily status + progress log + 4 runbooks + schema mapping + credentials + BIM migration report). Cached 5 min.

**Phase 2 (XYZ-PILOT-049, 2026-05-21)**
- Agentic whole-repo access via `read_repo_file(path, max_chars)` + `list_repo_directory(path)` Bedrock tools — when the static digest doesn't cover the question.
- Guardrails: forbidden-pattern check (`.env`, `*.pem`, `*.key`, `secrets.json`, `node_access*`, `config/exasol-personal`, `config/mcp-servers`, `.claude/settings`), path-traversal block, configurable tool-loop ceiling, audit-log line per call.
- The tool-loop ceiling defaults to 14 tool calls per user turn via `XYZ_CHATBOT_MAX_TOOL_ITERATIONS`, with an operator-tunable checkpoint via `XYZ_CHATBOT_TOOL_LOOP_CHECKPOINT_AFTER` that prompts the model to stop exploring and return partial progress before the ceiling is reached ([XYZ-PILOT-270](../../docs/XYZ_PILOT_ISSUE_TRACKER.md)).
- For complex visualization testcase lookup/execution, the chatbot should shortcut directly to TC-5 / TC-5A in `docs/30-runbooks/ui/XYZ_PILOT_STREAMLIT_VISUALS.md` instead of directory-walking.

**Phase 3 (XYZ-PILOT-053, 2026-05-21) — live SQL hybrid**
- `mcp-internal` sidecar (same exasol-mcp-server image, `--no-auth`, internal docker network only) exposes ~20 metadata tools to Bedrock dynamically.
- Chatbot-side `exasol_query(sql)` tool — pyexasol against the same DB, same XYZ_DASH read-only credential. SELECT / WITH / DESCRIBE / EXPLAIN / SHOW only. Results capped at 200 rows / 12 K chars.
- "Powered by Exasol MCP Server" caption on the main page is now functionally literal.

**Three filing buttons under every answer** (XYZ-PILOT-052 + XYZ-PILOT-054)
- "📁 **Archive this Q&A to repo**" → commits `docs/70-generated-qa/qa/YYYY-MM-DD_<slug>.md` to `main`, attributed to `xyz-pilot-chatbot`.
- "✨ **File as feature request**" → emits an `XYZ_MASTER` candidate under `tests/logs/.../issue-candidates.md`; no direct tracker write. Bedrock dup-check first; existing tickets surfaced instead of creating duplicates.
- "🐛 **Log as new defect**" → same candidate path as feature-request; severity defaults to minor unless an operator later escalates through `XYZ_MASTER`.

**Per-user history sidebar** (XYZ-PILOT-051) — last 10 prompts persisted across logout/login via bind-mounted JSON files at `/opt/xyz-chatbot/history/<email>.json`. Click any past query to re-ask now.

**Visual-first rendering contract** ([XYZ-PILOT-149](../../docs/XYZ_PILOT_ISSUE_TRACKER.md))
- Diagram/chart specs in assistant answers are renderer inputs, not the main subscriber experience.
- Mermaid, DOT, `diagrams` JSON, and `chart` JSON render as live visuals first.
- Mermaid extraction tolerates standard, escaped, and longer Markdown fences;
  experimental C4 macros are converted to a compatible `flowchart LR` visual
  fallback when the embedded Mermaid runtime cannot reliably render them
  ([XYZ-PILOT-185](../../docs/XYZ_PILOT_ISSUE_TRACKER.md) /
  [XYZ-PILOT-186](../../docs/XYZ_PILOT_ISSUE_TRACKER.md)).
- The chatbot should use the richest appropriate shipped Streamlit surface:
  architecture diagrams, analytical charts, AgGrid/dataframes, chart-container
  controls, and metric/card summaries where they improve the answer.
- Analytical Altair and Plotly charts share the XYZ palette plus BI-style
  axes, legends, gridlines, hover labels, and compact title treatment.
- Source/spec/data panes stay collapsed after successful rendering and open only for failures or explicit debugging.
- Source-only output is a regression when a subscriber asks to draw, visualize, chart, graph, plot, map, or diagram.
- Prompt-level Bedrock failures, live Exasol/MCP tool failures, and
  visual/widget/render failures first run a deterministic duplicate precheck
  against existing tracker/candidate-feed fingerprints, then queue an
  `XYZ_MASTER` defect candidate with redacted prompt, renderer/tool,
  deployment stamp, failure message, SQL, source/spec, and data-preview
  excerpts where available ([XYZ-PILOT-149](../../docs/XYZ_PILOT_ISSUE_TRACKER.md)).

## Still on the backlog (Phase 4)

- **Operator-approval gate** before commits. At 6 subscribers we trust them; before wider rollout the filing + archive buttons should become a per-commit confirmation flow.
- **Streaming responses.** Converse supports it; not wired here yet.
- **Per-user DB identity** at the MCP layer — OAuth-passthrough tokens so the mcp-internal sidecar can do per-user audit instead of looking like one shared identity.

## Pre-requisites (target baseline after the 2026-06 <AWS_REGION> migration)

| Pre-req | Resolution | Where |
|---|---|---|
| AWS Bedrock Sonnet 4.6 model access | ✅ XYZ-PILOT-257 | `us.anthropic.claude-sonnet-4-6` inference profile, ACTIVE in `<AWS_ACCOUNT_ID>/<AWS_REGION>` |
| GitHub PAT for repo writes | ✅ SSM SecureString | `/xyz-pilot/chatbot/github-token` (uploaded via `scripts/upload_chatbot_pat.sh`) |
| Cognito user pool with migrated subscribers | ✅ XYZ-PILOT-255 | `<COGNITO_USER_POOL_ID>` |
| Cognito app client for chatbot | ✅ Recreated in target | `xyz-pilot-chatbot-client` |
| Cloudflare zone authorization | ✅ XYZ-PILOT-035 | `cert.pem` on EC2 for `<PILOT_DOMAIN>` |
| Cloudflare named tunnel | ⏳ Created during deploy | `cloudflared tunnel create xyz-pilot-chat` |
| IAM role for the EC2 with Bedrock+SSM+KMS grants | ⏳ Granted during deploy | inline policy on the existing EC2 instance-profile role |

## Deploy

On the EC2 host:

```bash
# 1. Drop the stack files (scp from dev mac):
#      services/chatbot/Dockerfile  → /opt/xyz-chatbot/Dockerfile
#      services/chatbot/app.py      → /opt/xyz-chatbot/app.py
#      services/chatbot/docker-compose.yml → /opt/xyz-chatbot/docker-compose.yml

# 2. Create the named tunnel + DNS:
cloudflared tunnel create xyz-pilot-chat
cloudflared tunnel route dns xyz-pilot-chat xyz-pilot-chat.<PILOT_DOMAIN>
sudo mkdir -p /opt/xyz-chatbot/cloudflared
sudo cp /home/ubuntu/.cloudflared/cert.pem /opt/xyz-chatbot/cloudflared/
sudo cp /home/ubuntu/.cloudflared/<UUID>.json /opt/xyz-chatbot/cloudflared/
sudo tee /opt/xyz-chatbot/cloudflared/config.yml > /dev/null <<YAML
tunnel: <UUID>
credentials-file: /etc/cloudflared/<UUID>.json
ingress:
  - hostname: xyz-pilot-chat.<PILOT_DOMAIN>
    service: http://chatbot:8501
  - service: http_status:404
YAML

# 3. Write /opt/xyz-chatbot/.env (mode 600) from .env.example, fill in
#    COGNITO_CLIENT_SECRET. The PAT comes from SSM at runtime, not the env file.

# 4. Bring up:
cd /opt/xyz-chatbot
sudo env \
  XYZ_LAST_DEPLOYMENT_ISSUE=XYZ-PILOT-150 \
  XYZ_BUILD_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  XYZ_DEPLOYED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  docker compose up -d --build xyz-chatbot
sudo docker logs xyz-pilot-chatbot --tail 30
```

Important deployment guardrail: `/opt/xyz-chatbot` may run as host-local source
(no `.git` checkout). In that mode, pushing to GitHub does not update runtime
files automatically. Verify first:

```bash
test -d /opt/xyz-chatbot/.git && echo git-backed || echo host-local
```

If `host-local`, copy/patch `/opt/xyz-chatbot/app.py` (and related files)
before rebuilding the container.

The top of the Chatbot UI shows a provenance line added by
[XYZ-PILOT-150](../../docs/XYZ_PILOT_ISSUE_TRACKER.md): build timestamp,
deployment timestamp, current Streamlit process start time, and exactly one
linked `XYZ-PILOT-NNN`: the last issue that caused this UI rebuild/redeployment.
The build/deploy values come from the Docker build args above and default to
`not-stamped` if a rebuild omits them.
The shared Operator Dashboard / Chatbot / QBR UI provenance contract and
validation guard are documented in
[`XYZ_PILOT_UI_RUNBOOK.md`](../../docs/30-runbooks/ui/XYZ_PILOT_UI_RUNBOOK.md)
([XYZ-PILOT-202](../../docs/XYZ_PILOT_ISSUE_TRACKER.md)).

## Operate

| Action | Command |
|---|---|
| Restart after code edit | `cd /opt/xyz-chatbot && sudo env XYZ_LAST_DEPLOYMENT_ISSUE=XYZ-PILOT-150 XYZ_BUILD_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)" XYZ_DEPLOYED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)" docker compose up -d --build xyz-chatbot` |
| Tail chatbot logs | `sudo docker logs xyz-pilot-chatbot -f` |
| Tail tunnel logs | `sudo docker logs xyz-chatbot-cloudflared -f` |
| Refresh GitHub PAT after rotation | re-run `scripts/upload_chatbot_pat.sh`; chatbot picks it up on next `_get_pat` cache miss (TTL 1 h) |
| Add a new subscriber | `AWS_PROFILE=default aws cognito-idp admin-create-user --region <AWS_REGION> --user-pool-id <COGNITO_USER_POOL_ID> --username <email> --user-attributes Name=email,Value=<email> Name=email_verified,Value=true --desired-delivery-mediums EMAIL` (uses the same HTML invite template as the MCP server) |

## Cost

| Item | Monthly |
|---|---|
| EC2 container runtime | inside existing r6i.xlarge spend |
| Bedrock Sonnet 4.6 (6 subscribers × ~20 queries × ~5 K tokens) | ~$1–$3 |
| SSM Parameter Store (1 Standard SecureString) | $0 |
| Cloudflare named tunnel | $0 |
| Cognito MAU (≤ 50 000 free) | $0 |
| **Total** | **~$1–$3 / month** incremental on top of Pilot baseline |
