#!/usr/bin/env bash
# XYZ-PILOT-184 Phase 1 — Chatbot RP-series regression agent.
#
# Fires a small canonical reference-prompt suite against the chatbot's
# BACKEND tool-loop (same Bedrock Converse + pyexasol path the live
# chatbot uses) and evaluates machine-readable pass/fail assertions per
# prompt. Mirrors the XYZ_STRESS opt-in pattern: only runs when
# XYZ_INCLUDE_REGRESSION=1 OR the operator dispatches with that env set.
#
# Outputs (per agent convention):
#   artifacts/regression_results.jsonl
#   artifacts/regression_summary.json
#   artifacts/regression_summary.md
#
# On any RP failure, emits one issue-candidates.md row per failed RP via
# xyz_add_candidate (routed through XYZ_MASTER per the single-writer rule).

set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$script_dir/../../../../scripts/common.sh" ]; then
  source "$script_dir/../../../../scripts/common.sh"
else
  source "$script_dir/../../scripts/common.sh"
fi

xyz_agent_begin "Chatbot RP-series regression probe — asserts pass/fail per reference prompt against the live Bedrock+Exasol backend."

# Skip cleanly if the operator hasn't opted in. The skip is itself a PASS so
# the agent doesn't fail the swarm when intentionally disabled.
if [ "${XYZ_INCLUDE_REGRESSION:-0}" != "1" ]; then
  xyz_result "XYZ_REGRESSION enabled" "XYZ_INCLUDE_REGRESSION=1 to activate" "${XYZ_INCLUDE_REGRESSION:-0}" "PASS" "Agent skipped — opt in via XYZ_INCLUDE_REGRESSION=1"
  xyz_agent_finish
  exit 0
fi

mkdir -p artifacts

# Choose python invocation. GitHub Actions installs boto3 + pyexasol in the
# workflow, while local operator shells may prefer uv to provision them.
status=0
if command -v uv >/dev/null 2>&1; then
  uv run --with boto3 --with pyexasol python3 \
    "$XYZ_REPO_ROOT/tests/scripts/xyz_regression_probe.py" \
    --artifact-dir "$PWD/artifacts" \
    > artifacts/probe.stdout 2> artifacts/probe.stderr || status=$?
else
  python3 "$XYZ_REPO_ROOT/tests/scripts/xyz_regression_probe.py" \
    --artifact-dir "$PWD/artifacts" \
    > artifacts/probe.stdout 2> artifacts/probe.stderr || status=$?
fi

actual_summary="$(cat artifacts/regression_summary.md 2>/dev/null | head -40 || echo "(no summary file produced)")"

if [ "$status" = "0" ]; then
  xyz_result "Chatbot RP-series regression suite" \
    "all RP prompts pass their assertion blocks (required substrings, banned regexes, max tool calls, max latency)" \
    "$actual_summary" "PASS" "See artifacts/regression_summary.md"
else
  xyz_result "Chatbot RP-series regression suite" \
    "all RP prompts pass their assertion blocks (required substrings, banned regexes, max tool calls, max latency)" \
    "$actual_summary" "FAIL" \
    "Probe exit=$status — see artifacts/regression_summary.md + artifacts/regression_results.jsonl for per-RP detail"

  # Emit one XYZ_MASTER candidate aggregating the failures (single-writer
  # discipline — XYZ_MASTER triages + files as appropriate).
  failing_rps="$(python3 -c "
import json, sys
try:
    with open('artifacts/regression_summary.json') as f:
        s = json.load(f)
    failed = s.get('first_failures', [])
    if not failed:
        print('(no structured failures captured)')
    else:
        for f in failed:
            print(f\"  - {f['id']}: \" + '; '.join(f.get('failures', [])))
except Exception as exc:
    print(f'(could not parse summary: {exc})')
")"

  xyz_add_candidate "Chatbot RP-series regression failure — see XYZ_REGRESSION artifacts" \
"- **Type:** defect
- **Severity:** minor pending operator triage
- **Area:** Chatbot · regression test harness · RP-series prompt suite · CI integration · XYZ-PILOT-184 follow-up
- **Observed:** \`XYZ_REGRESSION\` agent reports at least one RP failure. Per-RP detail:
${failing_rps}
- **Expected:** All RP prompts in \`tests/scripts/xyz_regression_probe.py::RP_SUITE\` pass their assertion blocks (required substrings present, banned regexes absent, tool-call count within budget, latency within budget).
- **Evidence:** \`\$XYZ_RUN_DIR/XYZ_REGRESSION/artifacts/regression_summary.md\` and \`regression_results.jsonl\`.
- **Duplicate hints:** Check open XYZ-PILOT tickets in the Chatbot area before filing. A repeat of a known RP failure should append to the existing ticket via XYZ_MASTER status-transition, not file a new ticket."
fi

xyz_agent_finish
