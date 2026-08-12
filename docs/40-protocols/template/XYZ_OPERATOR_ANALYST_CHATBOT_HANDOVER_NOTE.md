# Handover note — Operator / Analyst / Chatbot integration status

**To:** operators adopting `exasol-se-pilot-template`
**From:** operator of the reference implementation these surfaces were extracted from
**Date:** 2026-08-07
**Status:** all three surfaces are **already templatized in this repo**. This note is the integration snapshot — what each surface is, how it authenticates, what it reads, how it deploys, how the three wire into the shared backbone (Cognito + read-only Exasol + tunnels + the `xyz_master` flow), the adoption checklist, and the current-state notes worth carrying.

> Provenance: the practices below come from a live pilot where these ran in anger. Template equivalents use the neutral placeholders (`services/*`, `scheduler/xyz_pilot_user_mgmt.py`, `XYZ-PILOT-NNN`, `<PILOT_NAME>`, `<OPERATOR_USER>`, ops-group names like `OPS_ONLY`, …) that `generate.sh` renders.

---

## 1. The triad + the shared backbone

Three human-facing surfaces sit on **one shared backbone**:

- **One Cognito user pool**, with a **separate app client per surface** and **group-scoped** access (ops groups for the operator surface; subscriber/business groups for the analyst + chatbot).
- **One read-only Exasol role** (the analyst DB user) for all live query paths — the analyst and the chatbot's SQL tool both read through it; nothing customer-facing gets a write role.
- **Named Cloudflare tunnels** front every browser surface (stable hostnames, valid edge certs); optionally **Cloudflare Access** in front of a surface for edge SSO (see `XYZ_MASTER`/Grafana handover for the Access pattern).
- **Build-stamp discipline** on every container rebuild and **DEV→verify→UAT** promotion apply to all three (CLAUDE.md Rules #15 / #17).

| Surface | Template home | Auth | Reads | Deploy |
|---|---|---|---|---|
| **Operator** dashboard | `scheduler/status_dashboard/` (prod static), `scheduler/dev_operator_dashboard/` (dev Flask), backend `scheduler/xyz_pilot_user_mgmt.py` | Cognito **Hosted-UI SSO** → **ops groups** (`OPS_ONLY` / the ops group) | via the user-mgmt Lambda (service probes, secrets metadata, Cognito, S3 evidence) | CloudFront over **private S3** (prod) + Lambda; Flask container (dev) |
| **Analyst** (QBR) | `services/streamlit-insights/` (`app.py`, `db.py`) | **shared APP_PASSWORD** gate — fail-closed when `<REQUIRE_AUTH>=1`, bypass+warn in local dev | read-only Exasol role; **`MV_*` snapshot tables** for sub-second tabs | Docker on EC2 behind a named tunnel |
| **Chatbot** | `services/chatbot/` (`app.py`, `iam/`) | Cognito **Hosted-UI SSO** (confidential client, refresh-token grant) → subscriber/business groups | read-only Exasol role via a guarded SQL tool; **`mcp-internal` sidecar** for metadata; **Bedrock Claude** for reasoning | Docker on EC2 behind a named tunnel (+ `services/exasol-mcp` for the public MCP endpoint) |

---

## 2. Per-surface integration detail

### Operator dashboard
- Static prod page (`status_dashboard/dashboard.html`) is served from CloudFront over a **private** S3 origin; it holds no secrets and calls the **`xyz_pilot_user_mgmt` Lambda** for everything.
- Lambda action surface (all Cognito-token-gated, elevated-operator checks): **`service_probe`** (live UP/DOWN of every ServiceURI), secret **reveal** (metadata + one-value-at-a-time), **onboarding** (Cognito invite / status / pool history), **`xyz_master` candidate dispatch** (the tracker-filing button → GitHub `workflow_dispatch`), **`warmup_progress`** stream, **license/evidence** streams.
- Dev parity is the Flask container (`dev_operator_dashboard/`), reading node-mounted secrets read-only.
- Deploy the Lambda with `scheduler/deploy_user_mgmt.sh`.

### Analyst (Streamlit QBR / `streamlit-insights`)
- `app.py` = the executive/analyst tabs (sales, target variance, YoY, inventory, a raw-data tab); `db.py` = a cached `pyexasol` connection with **TLS enforced + self-signed skip** (`websocket_sslopt={'cert_reqs': 0}`).
- Reads the **`MV_*` snapshot tables** (not raw facts) so tabs stay sub-second at scale; raw catalog only for the data tab.
- Gate is a **shared password** (`APP_PASSWORD`); it **fail-closes** when the require-auth flag is set, so a missing password refuses to start in prod rather than serving open.

### Chatbot
- Cognito **Hosted-UI OAuth code flow** (confidential client → the client secret + refresh-token grant give durable SSO shared with the operator surface).
- **Bedrock Claude** tool-use loop; tools include repo access, a **live read-only SQL tool** (row/char caps, read-only-verb checks, a process lock, and a **forbidden-table guard** that blocks internal role-play tables from analytical answers), and an **`mcp-internal` sidecar** (`MCP_INTERNAL_URL`) for metadata/describe.
- **Emits tracker candidates** (chatbot-filed feature/defect requests → `issue-candidates.md` → the `xyz_master` flow) and archives **per-user chat history** to GitHub.

---

## 3. Integration points (how they wire together)

```
        Cognito user pool  ── app client per surface, group-scoped ──┐
                                                                     │
  Operator ──(user-mgmt Lambda)── service_probe / secrets / onboarding / warmup
        │                        └── xyz_master candidate DISPATCH ──► xyz_master flow ──► tracker
        │
  Analyst  ──(read-only Exasol role, MV_* tables)──► Exasol
        │
  Chatbot  ──(read-only SQL tool + mcp-internal + Bedrock)──► Exasol
        │                        └── EMITS issue-candidates ─────────► xyz_master flow ──► tracker
        │
  all three ──► named Cloudflare tunnels (stable hostnames, edge certs; optional Cloudflare Access)
```

- **Chatbot → `xyz_master`:** chatbot submissions become committed candidates the `xyz_master` flow drains (see `XYZ_MASTER_FLOW_HANDOVER_NOTE.md`).
- **Operator → `xyz_master`:** the dashboard's dispatch button fires the same workflow; it also reads the swarm's run artifacts + warm-up progress.
- **Operator ↔ Swarm:** `service_probe` mirrors the swarm's endpoint/auth-boundary checks for a live operator view (see `XYZ_SWARM_TESTER_HANDOVER_NOTE.md`).
- **Shared read-only role:** analyst + chatbot both query through the one read-only DB user — the single lever for what customer-facing surfaces can see.

---

## 4. Taking it over — adoption checklist

1. **Render placeholders** (`generate.sh`) across `services/{streamlit-insights,chatbot,exasol-mcp,grafana}` + `scheduler/`; run `validate_template_neutrality.sh`.
2. **Cognito:** create the pool + **one app client per surface**; wire the Hosted-UI domain + callback URLs; define the group model (ops groups vs subscriber/business groups) and the group-scoped gates.
3. **Read-only DB role:** provision the analyst read-only Exasol user + the `MV_*` snapshots; point `db.py` / the chatbot SQL tool at it (TLS + skip-self-signed).
4. **Operator Lambda:** deploy `scheduler/xyz_pilot_user_mgmt.py` (`deploy_user_mgmt.sh`); grant it the minimum for its actions (Cognito admin, Secrets read of the project prefix, `workflow_dispatch`, S3 evidence read). Publish `status_dashboard/dashboard.html` to the **private** S3 + CloudFront.
5. **Analyst + Chatbot containers:** set `APP_PASSWORD` (+ require-auth flag) for the analyst; set the Cognito client + `MCP_INTERNAL_URL` + Bedrock region/model + the read-only DB creds for the chatbot. Build **with the build-stamp args** (Rule #15) and promote **DEV→verify→UAT** (Rule #17).
6. **Tunnels:** create the named Cloudflare tunnels per surface (see the Cloudflare handover); add Cloudflare Access where a surface should be edge-SSO gated.
7. **Keep the guards:** chatbot read-only-verb + row/char caps + forbidden-table guard; analyst fail-closed auth; operator elevated-operator checks; candidate-emit → `xyz_master` (never let a surface write the tracker directly).

---

## 5. Current-state notes worth carrying

- **Pin every dependency (MCP SDK included).** In the reference pilot an unpinned MCP SDK bump renamed a transport symbol and broke the chatbot + MCP import at deploy. Pin exact `==` versions taken from a live-healthy container, and validate each rebuild with an **in-container import smoke** before declaring the deploy done.
- **`service_probe` must treat auth-gate responses as healthy.** An edge-gated surface returns `401/403` (MCP) or `302 → Access` (a Cloudflare-Access'd surface) to an unauthenticated probe — that is *healthy*, not DOWN. If you Access-gate a surface, either exempt its `/…/health` path so the probe sees a real `200`, or teach the probe to accept the gate's redirect. (This exact false-DOWN was hit when the Grafana surface was Access-gated.)
- **Chatbot telemetry kwargs.** When adding an observability helper, watch for a keyword collision between the event name and a business field (e.g. both named `kind`) at the emit call sites — it throws a visible traceback *after* the candidate is already committed. Grep all emit call sites when you add one.
- **The read-only role is the containment boundary.** Suppressing a sensitive table only in a view layer does **not** remove it from the raw schema the chatbot's SQL tool can still reach — enforce it at the grant/guard level, and keep the chatbot's forbidden-table guard in sync with the regression (`xyz_regression`) prompts.

---

## 6. Ready-to-review

**Prepared, not committed** — cross-project artifact for this template's operators to review and land (suggested home: `docs/40-protocols/template/`, here). No source files were modified; all three surfaces are already present under the `services/` + `scheduler/` placeholders.
