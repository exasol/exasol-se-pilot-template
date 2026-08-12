# xyz_regression Skill Profile

## Primary Skills

- `aws-bedrock-runtime-validator`
- `exasol-database`
- `runtime-debugger`
- `student`

## Supporting Skills

- `aws-cognito-oauth-validator`
- `streamlit-dashboard-builder`
- `python-tool-validator`

## Operating Lens

`xyz_regression` is the Chatbot CI regression harness — it fires a curated
RP-series (reference-prompt) suite against the chatbot's BACKEND
Bedrock+Exasol tool-loop (same path the live chatbot uses, accessed
without going through the Streamlit UI or Cognito OAuth) and evaluates
machine-readable pass/fail assertions per prompt.

The role should look for:

- chatbot system-prompt regressions that re-introduce previously-fixed
  defects (e.g. RP-1 subscriber-list hallucination per XYZ-PILOT-182/183);
- Bedrock dialect-drift where the model emits non-Exasol SQL tokens
  (ILIKE, ::cast, MySQL pagination, PostgreSQL INTERVAL — per
  XYZ-PILOT-230's surfaced ILIKE incident);
- security-property leaks (credentials, fingerprints, IAM material,
  SSM paths/values, user attributes) being exposed by the chatbot —
  the refusal-and-redirect-to-Operator-Admin-team policy from
  XYZ-PILOT-230 must hold;
- latency / tool-call-count regressions that signal a broken or
  inefficient tool loop;
- catastrophic empty / runtime-error responses.

## Pass/Fail Assertion Framework

Each RP in `tests/scripts/pilot_regression_probe.py::RP_SUITE` declares:

- `required_substrings` — list of strings that MUST appear (case-insensitive
  substring match) in the final response
- `banned_regexes` — list of patterns that MUST NOT match the final response
  OR any tool-call SQL emitted during the turn
- `max_tool_calls` — upper bound on chatbot tool-call count for the turn
- `max_latency_ms` — upper bound on end-to-end turn latency
- `must_refuse` — soft flag for ops-readability; the actual refusal is
  enforced via the required_substrings + banned_regexes block above

Add a new RP by appending one dict to `RP_SUITE`. Keep assertions
concrete + machine-checkable — vague guidance like "answer should be
helpful" is not a regression check.

## Opt-in / Operational Status

Phase 1 (this shipment) registers `xyz_regression` in the swarm but
keeps it OPT-IN via `xyz_PILOT_INCLUDE_REGRESSION=1`. The agent's default
skip path returns `PASS` so the nightly swarm stays green by default
during the Phase 1 validation window.

To enable in the nightly run:
- Local operator session: `xyz_PILOT_INCLUDE_REGRESSION=1 ./tests/scripts/run_e2e_swarm.sh <run-id>`
- GitHub Actions nightly: set `xyz_PILOT_INCLUDE_REGRESSION: "1"` in the
  workflow yaml's `env:` block alongside `xyz_stress_users` etc.

Once XYZ-PILOT-184 Phase 1 is operationally validated across a few
green runs, the gate flips to always-on (delete the env-var check
from `tests/agents/xyz_regression/run.sh`).

## Standing E2E Test Cases

For each of the 8 standing E2E test cases — including user vs operator
execution boundary, exact commands, expected results, failure
diagnostics, and standing cadence — see
[`tests/cases/regression/xyz_regression_e2e_tests.md`](../cases/regression/xyz_regression_e2e_tests.md).
The agent overview lives at
[`tests/cases/regression/README.md`](../cases/regression/README.md).

## Sibling References

- `XYZ-PILOT-184` — this skill's parent ticket
- `XYZ-PILOT-182` — RP-1 hallucination defect (now Resolved; RP-1 in this suite is the regression guard)
- `XYZ-PILOT-183` — chatbot hallucination-guard discipline
- `XYZ-PILOT-230` — chatbot SQL-dialect + security-property discipline (now Resolved; RP-2 + RP-3 + RP-4 are the regression guards)
- `XYZ-PILOT-176` — sibling `xyz_PILOT_GITHUB_ACTIONS` agent slot (shares CI scaffolding)
- `XYZ-PILOT-198` — advisory-continue-on-error pattern (xyz_regression emits candidate, swarm still goes green per XYZ-PILOT-198)
