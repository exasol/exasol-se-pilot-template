# Regression Checks

Standing chatbot RP-series regression checks for the <PILOT_NAME> CI cadence (`XYZ-PILOT-184`).

Default `xyz_regression` scope:

- fire a curated set of reference prompts (`RP-1` … `RP-N`) against the chatbot's BACKEND tool-loop (Bedrock Converse + pyexasol, the same path the live chatbot uses) without going through the Streamlit UI or Cognito OAuth;
- evaluate **machine-readable pass/fail assertions per prompt** — required substrings, banned regexes, max tool-call count, max latency;
- carry an embedded `SYSTEM_PROMPT` replicating the live chatbot's hallucination-guard (`XYZ-PILOT-183`), security-property refusal (`XYZ-PILOT-230`), and Exasol-SQL-dialect discipline (`XYZ-PILOT-230`) so the harness can assert against policy-as-written, independent of any pending container redeploy;
- emit one xyz_master candidate aggregating all RP failures per the single-writer discipline (`XYZ-PILOT-108` / `XYZ-PILOT-157`);
- run inside the nightly 22:30 CEST swarm via `xyz_PILOT_INCLUDE_REGRESSION=1` injected by `.github/workflows/e2e-swarm-nightly.yml`.

Current RP-suite (5 prompts):

| RP | Purpose | Guards |
|---|---|---|
| `RP-1` | Hallucination guard — OPS-ONLY recipient enumeration | `XYZ-PILOT-182` / `XYZ-PILOT-183` |
| `RP-2` | Security refusal — Exasol TLS fingerprint | `XYZ-PILOT-230` |
| `RP-3` | Security refusal — credential read | `XYZ-PILOT-230` |
| `RP-4` | Exasol SQL dialect — `FACT_PRIMARY` row count | `XYZ-PILOT-230` |
| `RP-5` | Chatbot self-describe — no foreign-model leak | none yet — preventative |

Current limitations:

- The probe carries its OWN compact `SYSTEM_PROMPT` replicating the live chatbot's discipline; it does NOT reach into the live chatbot's Streamlit/Cognito path. Drift between the embedded prompt and the deployed `services/chatbot/app.py::SYSTEM_PROMPT` is a known follow-up (track via `XYZ-PILOT-184` Phase 3).
- The probe registers ONLY the `exasol_query` tool. The live chatbot has additional tools (`read_repo_file`, `start/stop ec2`, etc.). RPs that exercise non-Exasol tools are out of Phase-1/2 scope.
- Bedrock model availability is a single point of failure; no retry logic. A transient throttle = a noisy false-positive failure.
- The probe currently depends on `~/.exapump/config.toml[xyz-pilot-ro]` existing on the executing host. CI/OIDC paths must seed an equivalent config (see [`xyz_regression_e2e_tests.md` T6](xyz_regression_e2e_tests.md#t6-ci-environment-exapump-profile-compatibility)).

Detailed standing E2E test cases — including user + operator execution instructions — live in [`xyz_regression_e2e_tests.md`](xyz_regression_e2e_tests.md).
