# XYZ_REGRESSION — E2E Standing Test Cases

**Audience:** <PILOT_NAME> subscribers (User) and <PILOT_NAME> operators (Operator). Each test below states **who** can run it and the exact command.

**Scope:** The 8 standing E2E tests that exercise the chatbot RP-series regression harness shipped under [XYZ-PILOT-184](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md) — covering the probe itself, the agent wrapper, the swarm-integration path, the candidate-emission failure branch, the nightly GitHub Actions workflow, the CI environment, latency under concurrent load, and Bedrock availability.

**Role:** `XYZ_REGRESSION` (one of the Tester Swarm agents — see [<PILOT_NAME> Test Runbook](../../../docs/50-testing-validation/XYZ_PILOT_TEST_RUNBOOK.md) § 1).

**Run cadence:** All 8 tests should pass after any change touching `tests/scripts/xyz_regression_probe.py`, `tests/agents/XYZ_REGRESSION/run.sh`, `tests/scripts/run_e2e_swarm.sh`, `.github/workflows/e2e-swarm-nightly.yml`, `services/chatbot/app.py::SYSTEM_PROMPT` (live chatbot drift detection), or any AWS Bedrock / Exasol credential rotation.

**Evidence convention:** Local runs land under `/tmp/xyz_regression_<TX>/`. Swarm runs land under `tests/logs/runs/<UTC-run-id>/XYZ_REGRESSION/artifacts/`. Nightly runs land under the GitHub Actions workflow's job artifacts plus the same `tests/logs/runs/...` path archived back to `main` by the swarm's archive step.

---

## Index

| # | Test | Owner | Required role | Risk if it fails |
|---|---|---|---|---|
| [T1](#t1-standalone-probe-smoke-test) | Standalone probe smoke test | User or Operator | Bedrock + Exasol read | False sense of green on probe internals |
| [T2](#t2-agent-wrapper-runsh-through-commonsh) | Agent wrapper `run.sh` through `common.sh` | User or Operator | Same as T1 plus `bash` | Nightly fails at agent-startup, not at RP-assertion |
| [T3](#t3-full-local-swarm-with-xyz_include_regression1) | Full local swarm with `XYZ_INCLUDE_REGRESSION=1` | Operator | Same as T2 plus all other tester-agent permissions | `init_swarm_run.sh` may not auto-discover the new agent directory |
| [T4](#t4-failure-xyz_master-candidate-emission) | Failure path → XYZ_MASTER candidate emission | User or Operator | Same as T2 | Real failures in nightly produce no candidate; regression is invisible |
| [T5](#t5-nightly-gh-actions-workflow-first-fire) | Nightly GH Actions workflow first-fire | Operator (read GH Actions job logs) | GH Actions UI access | Env-var injection silently skips the agent |
| [T6](#t6-ci-environment-exapump-profile-compatibility) | CI environment `exapump` profile compatibility | Operator | OIDC role can read SSM | Probe fails at `_load_exapump_profile()` in OIDC runner with `FileNotFoundError`; agent dies before any RP runs |
| [T7](#t7-latency-budget-under-concurrent-load) | Latency budget under load concurrent with `XYZ_STRESS` | Operator | Same as T3 | RP-4 30 s budget exceeded under load → false-positive failure |
| [T8](#t8-bedrock-availability-throttling-resilience) | Bedrock availability / throttling resilience | Operator | AWS Bedrock console / CloudTrail | Single transient throttle → false-positive failure |

---

## T1 — Standalone probe smoke test

**Purpose:** Validate the `xyz_regression_probe.py` harness end-to-end against the live Bedrock + Pilot Exasol. The fastest signal that the RP suite + assertion logic + JSONL/JSON/Markdown output writers are all working.

**Risk covered:** Regression in the probe's tool-loop, Bedrock Converse contract, pyexasol fetch path, or assertion evaluator.

**Prerequisites:**
- `~/.exapump/config.toml` has a `[xyz-pilot-ro]` profile pointing at the live Pilot Exasol.
- `AWS_PROFILE=default` resolves to Pilot account `<AWS_ACCOUNT_ID>`.
- EC2 `<EC2_INSTANCE_ID>` is `running` (Mon-Fri 12:00–24:00 CEST window).
- `uv` is on PATH.

**Execution (User):**
```bash
AWS_PROFILE=default uv run --with boto3 --with pyexasol python3 \
  tests/scripts/xyz_regression_probe.py \
  --artifact-dir /tmp/xyz_regression_t1
```

**Execution (Operator):** Same as User. Operator-level credentials are not required — the probe runs as `XYZ_DASH` (read-only).

**Optional: single-RP fast probe** (for tighter iteration during development):
```bash
AWS_PROFILE=default uv run --with boto3 --with pyexasol python3 \
  tests/scripts/xyz_regression_probe.py \
  --artifact-dir /tmp/xyz_regression_t1 \
  --only RP-2
```

**Expected result:**
- Exit code `0`.
- `/tmp/xyz_regression_t1/regression_summary.md` shows `Overall: **PASS**` and a 5-row table with all `✅`.
- `regression_summary.json` has `"passed": true`, `"failed_rps": 0`.
- Total wall-clock under ~60 s (typically 22–30 s).

**Evidence location:** `/tmp/xyz_regression_t1/{regression_results.jsonl, regression_summary.json, regression_summary.md}`.

**If it fails:** Inspect the JSONL for which RP regressed. A REGRESSION (e.g. a security RP now leaks a secret string) is the expected signal; a runtime error (Bedrock throttle, Exasol connection refusal) is an environmental issue — re-run after a short wait and surface to the operator if persistent. Never paste full output to chat — sanitize per [<PILOT_NAME> Test Runbook § 8 Evidence Standards](../../../docs/50-testing-validation/XYZ_PILOT_TEST_RUNBOOK.md#8-evidence-standards).

**Tracker linkage:** [XYZ-PILOT-184](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md), [XYZ-PILOT-241](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md).

---

## T2 — Agent wrapper `run.sh` through `common.sh`

**Purpose:** Validate the swarm-agent wrapper script (`tests/agents/XYZ_REGRESSION/run.sh`) loads `scripts/common.sh` correctly, calls `xyz_agent_begin` / `xyz_result` / `xyz_agent_finish` against a properly-initialised run directory, and routes the probe exit code through to the agent's `agent.exit` file.

**Risk covered:** Path resolution bugs in `run.sh` (relative `source` of `common.sh`), missing executable bit, helper-function signature drift.

**Prerequisites:** Same as T1 plus a writable `tests/logs/runs/<run-id>/` tree.

**Execution (User or Operator):**
```bash
RUN_ID="$(date -u +%Y-%m-%dT%H%M%SZ)-t2"
./tests/scripts/init_swarm_run.sh "$RUN_ID"
cd "tests/logs/runs/$RUN_ID/XYZ_REGRESSION"
XYZ_REPO_ROOT="$(git -C ../../../../.. rev-parse --show-toplevel)" \
XYZ_RUN_ID="$RUN_ID" \
XYZ_RUN_DIR="../.." \
XYZ_AGENT="XYZ_REGRESSION" \
XYZ_INCLUDE_REGRESSION=1 \
AWS_PROFILE=default \
./run.sh
cat agent.exit
```

**Expected result:**
- `agent.exit` contains `0`.
- `agent.stdout` and `agent.stderr` exist (the stdout file is empty when the probe runs cleanly; stderr carries the probe's progress lines).
- `artifacts/regression_summary.md` exists and shows `Overall: **PASS**`.

**If it fails:**
- `agent.exit` != `0` AND `artifacts/regression_summary.md` missing → wrapper or common.sh source path broken. Inspect `run.sh` line 21 (the `source` path).
- `agent.exit` != `0` AND summary shows real RP failures → genuine regression, treat as T1's failure path.
- `permission denied` running `./run.sh` → executable bit lost; `chmod +x tests/agents/XYZ_REGRESSION/run.sh`.

**Evidence location:** `tests/logs/runs/<run-id>/XYZ_REGRESSION/{agent.stdout, agent.stderr, agent.exit, artifacts/}`.

**Cleanup:** Remove `tests/logs/runs/<run-id>-t2/` if the run was a one-off smoke; if it was a real swarm pass, the swarm's archive step preserves it.

**Tracker linkage:** [XYZ-PILOT-184](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md).

---

## T3 — Full local swarm with `XYZ_INCLUDE_REGRESSION=1`

**Purpose:** Exercise the full Tester Swarm with `XYZ_REGRESSION` activated, validating that `tests/scripts/run_e2e_swarm.sh` adds `XYZ_REGRESSION` to the `tester_agents` array, that `tests/scripts/init_swarm_run.sh` creates the per-agent run directory, and that the agent runs in parallel with `XYZ_EXPERT` / `XYZ_SMOKE` / `XYZ_INTEGRATION` / `XYZ_E2E` / `XYZ_UAT` / `XYZ_CHAOS` (plus optionally `XYZ_STRESS`).

**Risk covered:** Swarm-integration path that the nightly relies on. T2 only proves the wrapper standalone; T3 proves it in real swarm cohort context.

**Prerequisites:** Same as T2 plus the full swarm's prerequisites (every agent's read prerequisites — listed in [<PILOT_NAME> Test Runbook § 7 Execution Discipline](../../../docs/50-testing-validation/XYZ_PILOT_TEST_RUNBOOK.md#7-execution-discipline)).

**Execution (Operator only — full swarm is operator-cadence):**
```bash
RUN_ID="$(date -u +%Y-%m-%dT%H%M%SZ)-t3"
XYZ_INCLUDE_REGRESSION=1 \
XYZ_INCLUDE_STRESS=0 \
AWS_PROFILE=default \
./tests/scripts/run_e2e_swarm.sh "$RUN_ID"
ls "tests/logs/runs/$RUN_ID/XYZ_REGRESSION/"
cat "tests/logs/runs/$RUN_ID/XYZ_REGRESSION/agent.exit"
grep -A2 'XYZ_REGRESSION' "tests/logs/runs/$RUN_ID/manifest.md"
```

**Expected result:**
- `manifest.md` lists `XYZ_REGRESSION` in the `Parallel testers` line.
- `tests/logs/runs/<run-id>/XYZ_REGRESSION/agent.exit` contains `0`.
- All other 6 tester agents complete normally (their own pass criteria — checked by the swarm's own evidence summary).
- XYZ_MASTER consolidation phase finishes without picking up any candidate from `XYZ_REGRESSION` (because the agent passed).

**If it fails:**
- `tests/logs/runs/<run-id>/XYZ_REGRESSION/` directory is missing → `init_swarm_run.sh` doesn't auto-discover the new agent. Inspect `tests/scripts/init_swarm_run.sh` for hardcoded agent list.
- `tester_agents` array missing `XYZ_REGRESSION` in `manifest.md` → `tests/scripts/run_e2e_swarm.sh` env-gate isn't firing. Re-export `XYZ_INCLUDE_REGRESSION=1` and try again.
- Other tester agents fail in unexpected ways → likely an environmental issue (EC2 stopped, exapump credentials stale), not an `XYZ_REGRESSION` issue.

**Evidence location:** `tests/logs/runs/<run-id>-t3/`. Run the swarm's archive step (`tests/scripts/archive_swarm_run.sh <run-id>`) afterward to push evidence to `main` if it should be persisted.

**Tracker linkage:** [XYZ-PILOT-184](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md), [XYZ-PILOT-198](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md) (advisory-continue pattern).

---

## T4 — Failure → XYZ_MASTER candidate emission

**Purpose:** Validate the agent's failure branch — when the probe exits non-zero, `xyz_add_candidate` produces a valid `issue-candidates.md` handoff under `tests/logs/runs/<run-id>/XYZ_REGRESSION/` that XYZ_MASTER can ingest.

**Risk covered:** Silent regression-invisibility. T1/T2/T3 only cover the green path. If a future probe failure produces a malformed candidate (or no candidate at all), regressions in production cadence are invisible.

**Prerequisites:** Same as T2.

**Execution (User or Operator):** Force a deliberate failure by tightening an RP assertion temporarily.

```bash
# Make a temp clone of the probe with one assertion forced to fail
cp tests/scripts/xyz_regression_probe.py /tmp/xyz_regression_probe_t4.py
# Inject an impossible required_substring for RP-5 ("zzzz-impossible-marker"):
sed -i.bak '/"id": "RP-5"/,/}/ s/"required_substrings": \[\("<PILOT_NAME> Chatbot", "Exasol"\)\]/"required_substrings": ["zzzz-impossible-marker", \1]/' /tmp/xyz_regression_probe_t4.py
# Smoke-test it
AWS_PROFILE=default uv run --with boto3 --with pyexasol python3 \
  /tmp/xyz_regression_probe_t4.py \
  --artifact-dir /tmp/xyz_regression_t4 \
  --only RP-5
echo "Exit code: $?"
cat /tmp/xyz_regression_t4/regression_summary.md
cat /tmp/xyz_regression_t4/regression_summary.json
```

Then validate the agent wrapper's candidate-emission path by pointing `run.sh` at the modified probe:

```bash
RUN_ID="$(date -u +%Y-%m-%dT%H%M%SZ)-t4"
./tests/scripts/init_swarm_run.sh "$RUN_ID"
cd "tests/logs/runs/$RUN_ID/XYZ_REGRESSION"
XYZ_REPO_ROOT="$(git -C ../../../../.. rev-parse --show-toplevel)" \
XYZ_RUN_ID="$RUN_ID" \
XYZ_RUN_DIR="../.." \
XYZ_AGENT="XYZ_REGRESSION" \
XYZ_INCLUDE_REGRESSION=1 \
XYZ_REGRESSION_PROBE_OVERRIDE=/tmp/xyz_regression_probe_t4.py \
AWS_PROFILE=default \
./run.sh || true   # we EXPECT failure
cat agent.exit
ls ../candidates/
cat ../candidates/issue-candidates.md
```

(Note: the `XYZ_REGRESSION_PROBE_OVERRIDE` env-var override is not yet wired in Phase 1/2; deferred to Phase 3. For now the operator path is to temporarily replace `tests/scripts/xyz_regression_probe.py` in-place, run the agent, then `git restore` the file.)

**Expected result:**
- `agent.exit` contains `1`.
- `../candidates/issue-candidates.md` exists and is non-empty.
- The candidate file has the per-RP failure detail and points at `regression_summary.md` and `regression_results.jsonl`.
- The candidate is parseable by `scripts/xyz_master_background_filing.py --mode dry-run --candidate-file <path>` (which should accept it as a `new-ticket` candidate awaiting XYZ_MASTER triage).

**If it fails:**
- `agent.exit` = `0` despite probe failure → wrapper is masking exit codes. Inspect `tests/agents/XYZ_REGRESSION/run.sh::status=$?` line.
- `issue-candidates.md` missing → `xyz_add_candidate` helper is broken or run.sh's failure branch is skipped.
- Filer rejects the candidate → format drift between probe failure output and the canonical candidate template. Reconcile against [`XYZ_MASTER.md`](../../skills/xyz_master.md) skill profile.

**Cleanup:** `git restore tests/scripts/xyz_regression_probe.py` after the test. Remove `/tmp/xyz_regression_probe_t4.py*` and `/tmp/xyz_regression_t4/`.

**Tracker linkage:** [XYZ-PILOT-184](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md), [XYZ-PILOT-108](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md) (single-writer discipline).

---

## T5 — Nightly GH Actions workflow first-fire

**Purpose:** Confirm the nightly 22:30 CEST cron picks up the `XYZ_INCLUDE_REGRESSION=1` env-var, runs the agent, and archives `tests/logs/runs/<run-id>/XYZ_REGRESSION/` back to `main` via the workflow's archive step.

**Risk covered:** YAML parse / whitespace / env-block ordering bugs that would silently skip the agent. T3 proves the local path; T5 proves the GH Actions production path.

**Prerequisites:** Operator GitHub Actions read access on `<GITHUB_OWNER>/<PROJECT_SLUG>`.

**Execution (Operator only):** Wait for the next 22:30 CEST scheduled run, OR force a manual dispatch:

```bash
# Manual dispatch (operator) — runs the full nightly swarm now
gh workflow run e2e-swarm-nightly.yml \
  --ref main \
  -f stress_users=10 \
  -f force_run=false \
  -f dry_run=false
# Watch the run
gh run list --workflow=e2e-swarm-nightly.yml --limit 1
gh run watch "$(gh run list --workflow=e2e-swarm-nightly.yml --limit 1 --json databaseId -q '.[0].databaseId')"
```

After completion:
```bash
# Pull the latest archived swarm run from main
git pull --ff-only
RUN_ID="$(ls -t tests/logs/runs/ | head -1)"
ls "tests/logs/runs/$RUN_ID/XYZ_REGRESSION/"
cat "tests/logs/runs/$RUN_ID/XYZ_REGRESSION/agent.exit"
grep -A2 'XYZ_REGRESSION' "tests/logs/runs/$RUN_ID/manifest.md"
```

**Expected result:**
- The workflow job summary shows the `Run full E2E swarm with XYZ_STRESS + XYZ_REGRESSION` step succeeded.
- The archived `manifest.md` lists `XYZ_REGRESSION` in `Parallel testers`.
- `XYZ_REGRESSION/agent.exit` = `0`.
- The notification SES email to OPS-ONLY recipients (<OPS_USER_2> + <OPS_USER_3> + <OPERATOR_NAME>) lands with `🟢 ... · clean` subject.

**If it fails:**
- Job summary shows `XYZ_REGRESSION` skipped → env-var injection not firing. Diff `.github/workflows/e2e-swarm-nightly.yml` against the Phase-2 ship (commit `50dabbb`).
- Agent runs but fails with credential-loading error → tripped by T6 (CI environment exapump-profile compatibility); fix T6 first.
- SES email missing → see [XYZ-PILOT-238](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md) playbook (IAM out-of-sync with recipient list).

**Evidence location:** GitHub Actions job artifacts (kept ~90 days by repo policy) plus the archived `tests/logs/runs/<run-id>/XYZ_REGRESSION/` on `main`.

**Tracker linkage:** [XYZ-PILOT-184](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md), [XYZ-PILOT-221](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md) (OPS-ONLY recipients), [XYZ-PILOT-238](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md) (recent IAM-sync incident).

---

## T6 — CI environment `exapump` profile compatibility

**Purpose:** The probe loads Exasol credentials from `~/.exapump/config.toml[xyz-pilot-ro]`. The GitHub Actions OIDC runner is ephemeral and has NO `~/.exapump/config.toml`. Confirm that either the workflow seeds an equivalent config OR the probe has a fallback that reads from SSM Parameter Store / environment under OIDC.

**Risk covered:** **HIGHEST RISK** — almost certain to fail T5 if no compat path exists. The agent will die at `_load_exapump_profile()` before any RP runs.

**Prerequisites:** Operator read access to OIDC role policy + SSM Parameter Store `/xyz-pilot/exasol/*`.

**Execution (Operator):**
```bash
# Inspect what the workflow currently provides for the probe
grep -A30 'Run full E2E swarm' .github/workflows/e2e-swarm-nightly.yml \
  | grep -E '(XYZ_REGRESSION|exapump|EXAPUMP|EXASOL)'
# Verify the OIDC role can read the xyz-pilot-ro SSM secret
AWS_PROFILE=default aws iam get-role-policy \
  --role-name xyz-pilot-github-e2e-swarm \
  --policy-name <policy-name> \
  --output json 2>&1 \
  | grep -E '(secrets|exasol)' \
  || echo "No exasol/secrets read in OIDC policy"
# Check if the nightly workflow seeds ~/.exapump/config.toml before invoking run_e2e_swarm.sh
grep -B2 -A5 'exapump\|EXAPUMP' .github/workflows/e2e-swarm-nightly.yml
```

**Expected result:**
- ONE of the following must be true:
  - (a) The workflow has a `Seed exapump profile` step that reads SSM `/xyz-pilot/secrets/exasol-xyz-dash` and writes `~/.exapump/config.toml[xyz-pilot-ro]` before the swarm runs; OR
  - (b) The probe has a fallback in `_load_exapump_profile()` that reads from env vars `EXASOL_HOST / EXASOL_PORT / EXASOL_USER / EXASOL_PASSWORD` when the config file is missing; OR
  - (c) The OIDC role can read the SSM secret AND the probe knows to fetch it.

**If it fails (any of (a)/(b)/(c) all absent):** This is a follow-up ticket and a **known Phase 2 gap**. File an XYZ_MASTER candidate as `new-ticket` severity `minor` with proposed fix path (b) (env-var fallback is the smallest change and least permission surface). Until the fix lands, expect tonight's nightly to surface as `XYZ_REGRESSION agent.exit=1` with `_load_exapump_profile` traceback — that's expected, not a regression.

**Evidence location:** Output of the diagnostic commands plus the OIDC role policy JSON.

**Tracker linkage:** [XYZ-PILOT-184](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md) (Phase 3), [XYZ-PILOT-131](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md) (`xyz-pilot-ro` profile origin).

---

## T7 — Latency budget under concurrent load

**Purpose:** RP-4's 30-second latency budget was set under solo-run conditions. Under nightly load (10 concurrent XYZ_STRESS users + 6 other parallel tester agents), Bedrock latency tends to lengthen. Confirm RP-4 still fits the budget OR raise the budget.

**Risk covered:** False-positive failures from a latency-only regression with no actual chatbot defect.

**Prerequisites:** Same as T3 plus `XYZ_INCLUDE_STRESS=1`.

**Execution (Operator only — 10 stress users + full swarm):**
```bash
RUN_ID="$(date -u +%Y-%m-%dT%H%M%SZ)-t7"
XYZ_INCLUDE_REGRESSION=1 \
XYZ_INCLUDE_STRESS=1 \
XYZ_STRESS_USERS=10 \
XYZ_STRESS_REQUIRE_SESSION_HOLD=1 \
XYZ_STRESS_SESSION_HOLD_SECONDS=20 \
AWS_PROFILE=default \
./tests/scripts/run_e2e_swarm.sh "$RUN_ID"
# Inspect RP latency under load
python3 -c "
import json
with open('tests/logs/runs/$RUN_ID/XYZ_REGRESSION/artifacts/regression_results.jsonl') as f:
    for line in f:
        r = json.loads(line)
        print(f\"{r['id']}: {r['elapsed_ms']:.0f} ms (budget {r.get('purpose','')})\")"
```

**Expected result:**
- All RPs PASS even under concurrent load.
- RP-4 elapsed ≤ 30,000 ms (current budget).
- p95 across RP-1..RP-5 ≤ 15,000 ms each.

**If it fails (latency exceeded budget despite no actual regression):**
- Bump the budget in `tests/scripts/xyz_regression_probe.py::RP_SUITE` for the affected RP — typical headroom factor is 1.5×.
- File the budget bump as an XYZ_MASTER candidate severity `minor`, classification `feature`, cross-linked to [XYZ-PILOT-184](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md).

**Evidence location:** `tests/logs/runs/<run-id>-t7/XYZ_REGRESSION/artifacts/regression_results.jsonl`.

**Tracker linkage:** [XYZ-PILOT-184](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md), [XYZ-PILOT-109](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md) (performance regression discipline).

---

## T8 — Bedrock availability / throttling resilience

**Purpose:** The probe has no retry logic. A transient Bedrock throttle or model-unavailable error → entire agent fails. Confirm that the swarm's advisory-continue pattern ([XYZ-PILOT-198](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md)) catches this AND that the resulting candidate is correctly tagged so XYZ_MASTER doesn't file it as a real regression.

**Risk covered:** Noisy false-positive failures from infrastructure (not chatbot) issues that mask real regressions.

**Prerequisites:** Operator read access to CloudWatch + Bedrock console.

**Execution (Operator):** This test is passively observed across the running nightly cadence — there's no clean way to inject Bedrock throttles. Instead:

```bash
# After each nightly, check if the agent reported a Bedrock error (vs RP assertion failure)
RUN_ID="$(ls -t tests/logs/runs/ | head -1)"
grep -E '(ThrottlingException|ServiceUnavailable|ModelNotReadyException|botocore.errorfactory)' \
  "tests/logs/runs/$RUN_ID/XYZ_REGRESSION/artifacts/probe.stderr" \
  "tests/logs/runs/$RUN_ID/XYZ_REGRESSION/artifacts/regression_results.jsonl" 2>/dev/null \
  || echo "No Bedrock infrastructure errors in $RUN_ID"

# Cross-check Bedrock CloudWatch metrics for the run window
AWS_PROFILE=default aws cloudwatch get-metric-statistics \
  --namespace AWS/Bedrock \
  --metric-name ThrottlingExceptions \
  --start-time "$(date -u -v-1d +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --period 3600 \
  --statistics Sum \
  --region <AWS_REGION>
```

**Expected result:**
- Across 7 consecutive nightlies, 0 Bedrock-infrastructure-caused failures in `XYZ_REGRESSION`.
- If a Bedrock throttle does fire, the swarm's archive marker tags it as `🟡 ... · advisory` (not `🔴`) per [XYZ-PILOT-198](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md).

**If it fails (≥ 1 throttle per 7 nightlies):**
- Add retry logic with exponential backoff to `_run_one_rp` in `xyz_regression_probe.py` — 3 retries, 1s/2s/4s delays. File as XYZ-PILOT-184 Phase 3 follow-up.
- Until the fix lands, treat any single nightly with a Bedrock infrastructure error as a non-regression event; do NOT file an XYZ_MASTER candidate, do NOT page the operator.

**Evidence location:** `tests/logs/runs/<run-id>/XYZ_REGRESSION/artifacts/probe.stderr` + CloudWatch metric history.

**Tracker linkage:** [XYZ-PILOT-184](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md), [XYZ-PILOT-198](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md) (advisory-continue), [XYZ-PILOT-204](../../../docs/XYZ_PILOT_ISSUE_TRACKER.md) (sibling Bedrock probe pattern).

---

## Standing-cadence summary

| When | Who | Tests | Goal |
|---|---|---|---|
| After every PR touching `tests/scripts/xyz_regression_probe.py` or `tests/agents/XYZ_REGRESSION/run.sh` | PR author (User OR Operator) | T1, T2 | Catch regressions before merge |
| After every PR touching `tests/scripts/run_e2e_swarm.sh` or `.github/workflows/e2e-swarm-nightly.yml` | PR author (Operator) | T3, T5 | Catch swarm-integration drift |
| After every PR touching `services/chatbot/app.py::SYSTEM_PROMPT` | PR author (Operator) | T1 only | Detect chatbot-policy drift |
| Per nightly 22:30 CEST | Workflow automation | T5 (T6/T7/T8 piggyback) | Standing production cadence |
| Quarterly | Operator | T4 | Validate failure-emission path stays functional |
| On Bedrock throttle event | Operator | T8 | Confirm advisory-continue still suppresses noise |

## Quick-reference: user vs operator boundary

| Test | User can run? | Operator-only? |
|---|---|---|
| T1 — Standalone probe | ✅ | — |
| T2 — Agent wrapper | ✅ | — |
| T3 — Full local swarm | — | ✅ (multi-service AWS perms) |
| T4 — Failure-branch validation | ✅ | — |
| T5 — Nightly first-fire | — | ✅ (GH Actions dispatch) |
| T6 — CI environment compat | — | ✅ (IAM/SSM inspection) |
| T7 — Latency under load | — | ✅ (full swarm with XYZ_STRESS) |
| T8 — Bedrock resilience | — | ✅ (CloudWatch + Bedrock console) |

Where a User encounters a test that requires operator privileges, **STOP** — do not attempt to escalate; surface the test to the <PILOT_NAME> Operator Admin team via the daily-status email recipient list (see CLAUDE.md OPS-ONLY recipients).
