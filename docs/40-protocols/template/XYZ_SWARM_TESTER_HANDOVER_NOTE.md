# Handover note — the E2E Swarm Tester (`run_e2e_swarm`)

**To:** operators adopting `exasol-se-pilot-template`
**From:** operator of the reference implementation this fleet was extracted from
**Date:** 2026-08-07
**Status:** the swarm is **already largely templatized in this repo** under the `xyz_*` agent names. This note is the take-over guide — architecture, the agent fleet, orchestration, notification, how it feeds the `xyz_master` flow, the adoption checklist, and one **known gap** (the SECRETS lane).

> Provenance: the practices below come from a live pilot where this fleet ran in anger. Template equivalents use the neutral placeholders (`xyz_*`, `XYZ-PILOT-NNN`, `XYZ_RUN_ID`, `<PILOT_NAME>`, …) that `generate.sh` renders.

---

## 1. What it is

A fleet of independent, **read-only / non-destructive** tester agents that probe a live Pilot end-to-end every night (and on demand): public endpoints, auth boundaries, AWS + Exasol + GitHub wiring, chatbot regression prompts, bounded concurrent load, secret-surface DLP, and docs/UAT drift. Each agent writes a self-contained run log; a coordinator consolidates; findings become **issue-candidates** that feed the `xyz_master` single-writer tracker flow (see the companion `XYZ_MASTER_FLOW_HANDOVER_NOTE.md`); a summary email is sent to the ops list.

Design invariants: agents **never restart services or mutate state**; secret values are **never printed** (`pilot_redact_file`); every assertion is captured as an artifact for audit.

---

## 2. Component inventory (present in this repo unless flagged)

| Concern | Template file | Role |
|---|---|---|
| Orchestrator | `tests/scripts/run_e2e_swarm.sh` | Runs the tester fleet in **parallel**, then the coordinator; archives the run; sends the notification |
| Run-dir init | `tests/scripts/init_swarm_run.sh` | Creates `tests/logs/runs/<run-id>/` + per-agent dirs |
| Shared helper lib | `tests/scripts/common.sh` | The agent contract (see §5) |
| Agent fleet | `tests/agents/xyz_*/run.sh` (10) | One `run.sh` per agent (see §4) |
| Regression probe | `tests/scripts/xyz_regression_probe.py` | Chatbot RP-series pass/fail per reference prompt |
| Stress probe | `tests/scripts/xyz_stress_probe.py`, `chatbot_backend_stress.py` | Bounded concurrent load; DB + HTTP p50/p95/max vs thresholds |
| Sub-swarms | `run_infrastructure_swarm.sh`, `run_stress_swarm.sh` | Focused single-lane runners |
| EC2 host probe | `tests/scripts/probe_ec2_host_aws_cli.sh` | In-node checks via SSM/CLI |
| Notification | `tests/scripts/send_e2e_swarm_notification.py` | Builds + SES-sends the summary email |
| Nightly CI | `.github/workflows/e2e-swarm-nightly.yml`, `github-actions-e2e.yml` | `schedule` + `workflow_dispatch`; runs the swarm in CI |
| Self-tests | `test_swarm_agent_selection.sh`, `test_send_e2e_swarm_notification.py`, `test_github_e2e_swarm_iam_policy.py`, `test_xyz_stress_probe.py` | Guard the swarm itself |
| **SECRETS lane** | **— MISSING —** | **Gap: add if the project has a secret-retrieval surface to DLP-test** |

---

## 3. Orchestration / data flow

```
run_e2e_swarm.sh [run-id]              run-id defaults to  $(date -u +%Y-%m-%dT%H%M%SZ)
   │  init_swarm_run.sh → tests/logs/runs/<run-id>/{manifest.md, <AGENT>/…}
   │  BIZ-profile preflight: SELECT 1 on the BIZ DB profile → auto-disable DB lane if absent
   ▼
   run every tester agent IN PARALLEL  (background `&` + `wait $pid`), each with env XYZ_RUN_ID + DB/AWS profiles
   │      each agent → <AGENT>/{agent.stdout, agent.stderr, commands.md, results.md,
   │                            issue-candidates.md, skills.md, artifacts/<probe>.{status,body,out,err}}
   ▼
   xyz_master  (sequential consolidation — CANDIDATE-ONLY, no direct tracker mutation)
   ▼
   archive the run as a git commit  ("archive E2E swarm run <run-id>")
   │      rebase-onto-main safe: fetch + rebase to survive nightly/parallel/operator commits (non-fast-forward)
   ▼
   send_e2e_swarm_notification.py  → SES HTML summary to the ops list
```

Committed `issue-candidates.md` files then drain through the **`xyz_master` flow** (deterministic filer → tracker). The swarm produces findings; it never writes the tracker itself.

---

## 4. The agent fleet (roles)

| Agent (template) | Role |
|---|---|
| `xyz_smoke` | Fast public runtime health, endpoint, and auth-boundary checks |
| `xyz_e2e` | End-to-end observable journeys across QBR, Grafana, Chatbot, MCP, Exasol |
| `xyz_integration` | Integration checks: AWS, Exasol, MCP, Lambda, scheduler, service wiring |
| `xyz_chaos` | Non-destructive chaos / negative-path probes (no restarts, no mutations) |
| `xyz_stress` | Bounded concurrent-user stress for QBR, Chatbot, Exasol read paths |
| `xyz_regression` | Chatbot RP-series regression — pass/fail per reference prompt |
| `xyz_infrastructure` | Migration-aware AWS topology checks across source/target accounts + regions |
| `xyz_expert` | Static + local operating-discipline checks (repo/governance) |
| `xyz_uat` | Subscriber-facing acceptance: availability, wording, documented-demo alignment |
| `xyz_master` | Sequential consolidation; candidate-only by default; no tracker mutation |
| *(`xyz_secrets`)* | **MISSING in template** — read-only secret-surface / token-PAT DLP checks (add if needed) |

---

## 5. The `common.sh` agent contract

Every agent sources `common.sh` and uses only these helpers. They are **`pilot_`-prefixed** (a stable literal prefix in `common.sh` — *not* one of the `generate.sh` placeholders, so it stays `pilot_` after rendering):

- `pilot_agent_begin "<one-line role>"` / `pilot_agent_finish` — open/close a run; write `report.md` + `results.md` + the exit code.
- `pilot_http_probe "<id>" "<url>" "<accepted-codes>"` — GET; captures `<id>.status` + `<id>.body` under `artifacts/`; returns 0 iff the status is in the accepted set. **Accepted-codes is a comma list** (e.g. `"200,302"`).
- `pilot_status_code "<id>"` / `pilot_body_preview "<id>"` — read back a probe's captured status/body.
- `pilot_cmd "<id>" <argv…>` / `pilot_cmd_sh "<id>" "<shell>"` — run a command, capture stdout/stderr as artifacts.
- `pilot_result "<check>" "<expected>" "<actual>" "PASS|FAIL|WARN" "<notes>"` — emit one report row (drives the notification table).
- `pilot_add_candidate` — emit an `issue-candidates.md` entry (feeds `xyz_master`).
- `pilot_redact_file` — DLP scrub before any capture is written (never log secret values).

---

## 6. The notification (`send_e2e_swarm_notification.py`)

SES HTML email to the ops list on completion. Sections: overall health (🟢/🟡/🔴 + failure/warning/candidate counts); the **agent result table** (Agent · Result · Probe · Actual · Notes); **defects queued** by severity; the **stress metrics** tables (concurrent users, DB lane vs HTTP probes, per-target p50/p95/max vs p95 thresholds); the **participating-agents** table (Status · Failures · Warnings · Candidates); run-artifact path + archive ref; unsubscribe line. Recipients follow the same ops-list convention as the rest of the pilot's outbound.

---

## 7. Taking it over — instantiation checklist

1. **Render placeholders** (`generate.sh`) → `xyz_*`/`XYZ-PILOT`/`XYZ_RUN_ID` → your prefix; run `validate_template_neutrality.sh`.
2. **Point the agents at your surfaces** — each `tests/agents/*/run.sh` hard-references endpoints, DB profiles, Cognito pools, AWS resource ids, and **expected status codes**. Reconcile every probe target + accepted-code set to the concrete project.
3. **Wire the nightly workflow** — `e2e-swarm-nightly.yml` (`schedule` + `workflow_dispatch`) and the AWS-reading identity. `test_github_e2e_swarm_iam_policy.py` documents the least-privilege policy the AWS-touching agents need.
4. **Configure the notification** — SES sender + ops recipients in `send_e2e_swarm_notification.py`.
5. **Port the SECRETS lane** (§2 gap) if the project exposes a secret-retrieval surface.
6. **Keep the invariants** — read-only / non-destructive, redaction, candidate-emit → `xyz_master` integration.

Validation before first nightly: `bash -n tests/scripts/*.sh`, `python3 -m py_compile tests/scripts/*.py`, run `test_swarm_agent_selection.sh` + `test_send_e2e_swarm_notification.py` + `test_xyz_stress_probe.py`, then a manual `run_e2e_swarm.sh <run-id>` and inspect `tests/logs/runs/<run-id>/`.

---

## 8. Hard-won lessons (carry these)

- **Probes drift when infra changes — update the accepted-code sets, don't just re-run.** When a surface gets fronted by an edge gate, boundary probes that assert the *origin's* refusal code go stale. Concretely, as observed in the reference pilot: Access-gating Grafana made `xyz_chaos`/`xyz_e2e`/`xyz_integration` (which asserted Grafana's own `401/403` on `/api/admin/settings`, `/api/user`, `/api/datasources`) fail on the new `302 → Access` — they had to **accept `302` as PASS** (an edge-enforced refusal is *stronger*). Meanwhile health probes hitting `/api/health` needed that path **exempted** from the gate so they still see the real origin `200`. **The template's boundary agents carry the pre-gate expectations — reconcile them the day you add any edge gate.**
- **Regression prompts must match your own data guards.** The reference `regression` probe's RP-series asserts the chatbot never leaks internal role-play tables (RP-15 / `BETA_SEGMENT`); a "hide-not-delete" fix at one layer left a *distinct reachable surface* leaking (observed as a multi-ticket lineage in the reference pilot). Scope your RP prompts to your project's actual guards, and treat a recurring RP failure as a real reachable-surface defect, not flakiness.
- **The archive commit races other writers.** The run archive lands on `main` alongside nightly/parallel/operator commits; the runner fetches + rebases to survive non-fast-forward. Keep that logic when porting.
- **DB lane is optional-by-preflight.** The stress/regression DB lane preflights `SELECT 1` on the BIZ profile and auto-disables if the role is absent — so the swarm still runs in envs without that DB user. Preserve the preflight.
- **Nightly filing depends on the `xyz_master` flow, which depends on `workflow_dispatch`.** During the 2026-08-06 Actions outage the swarm's candidates couldn't file until Actions recovered — the committed candidate is the durable buffer (see the master-flow note). Don't wire the swarm to write the tracker directly to "work around" a filing outage.

---

## 9. Ready-to-review

**Prepared, not committed** — cross-project artifact for this template's operators to review and land (suggested home: `docs/40-protocols/template/`, here). No source files were modified; the swarm is already present under the `xyz_*` placeholders, minus the SECRETS lane noted in §2.
