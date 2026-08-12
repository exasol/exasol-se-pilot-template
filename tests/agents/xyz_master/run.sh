#!/usr/bin/env bash
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$script_dir/../../../../scripts/common.sh" ]; then
  source "$script_dir/../../../../scripts/common.sh"
else
  source "$script_dir/../../scripts/common.sh"
fi

xyz_agent_begin "Sequential consolidation. Candidate-only by default; no tracker mutation."

summary="summary.md"
{
  printf '# XYZ_MASTER Summary\n\n'
  printf -- '- Run: `%s`\n' "$XYZ_RUN_ID"
  printf -- '- Started UTC: `%s`\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf -- '- Tracker mode: `candidate-only`\n\n'
  printf '## Agent Exit Codes\n\n'
  printf '| Agent | Exit |\n'
  printf '|---|---:|\n'
} > "$summary"

tester_agents=(xyz_expert xyz_smoke xyz_integration xyz_e2e xyz_uat xyz_chaos)
if [ "${XYZ_INCLUDE_STRESS:-0}" = "1" ] || [ -d "$XYZ_RUN_DIR/xyz_stress" ]; then
  tester_agents+=(xyz_stress)
fi
if [ "${XYZ_INCLUDE_REGRESSION:-0}" = "1" ] || [ -d "$XYZ_RUN_DIR/xyz_regression" ]; then
  tester_agents+=(xyz_regression)
fi
if [ "${XYZ_INCLUDE_INFRASTRUCTURE:-0}" = "1" ] || [ -d "$XYZ_RUN_DIR/xyz_infrastructure" ]; then
  tester_agents+=(xyz_infrastructure)
fi

for agent in "${tester_agents[@]}"; do
  exit_code="$(cat "$XYZ_RUN_DIR/$agent/agent.exit" 2>/dev/null || printf '?')"
  printf '| `%s` | `%s` |\n' "$agent" "$exit_code" >> "$summary"
  if [ "$exit_code" != "0" ]; then
    XYZ_WARNS=$((XYZ_WARNS + 1))
  fi
done

{
  printf '\n## Consolidated Issue Candidates\n\n'
  printf 'The following are collected from agent-local `issue-candidates.md` files. They are not tracker edits.\n\n'
} >> "$summary"

: > artifacts/consolidated-issue-candidates.md
for agent in "${tester_agents[@]}"; do
  f="$XYZ_RUN_DIR/$agent/issue-candidates.md"
  if [ -f "$f" ] && rg -q '^## Candidate:' "$f"; then
    {
      printf '\n# From %s\n\n' "$agent"
      sed -n '/^## Candidate:/,$p' "$f"
    } >> artifacts/consolidated-issue-candidates.md
  fi
done

if [ -s artifacts/consolidated-issue-candidates.md ]; then
  cat artifacts/consolidated-issue-candidates.md >> "$summary"
  xyz_result "Candidate consolidation" "candidate files merged" "merged" "PASS" "See summary.md"
else
  printf 'No candidates submitted by tester agents.\n' >> "$summary"
  xyz_result "Candidate consolidation" "candidate files merged" "no candidates" "PASS" ""
fi

status="$(xyz_cmd_sh "tracker_verifier_master" "./scripts/verify_tracker_completeness.sh -v")"
actual="$(xyz_artifact_text "tracker_verifier_master")"
if [ "$status" = "0" ]; then
  xyz_result "Tracker verifier" "clean before any future filing" "$actual" "PASS" ""
else
  xyz_result "Tracker verifier" "clean before any future filing" "$actual" "FAIL" ""
fi

status="$(xyz_cmd_sh "tracker_local_diff" "git diff --quiet -- docs/XYZ_PILOT_ISSUE_TRACKER.md")"
if [ "$status" = "0" ]; then
  xyz_result "Tracker local diff" "no local central tracker mutation" "clean" "PASS" ""
else
  xyz_result "Tracker local diff" "no local central tracker mutation" "local diff present" "WARN" "Do not file until XYZ_MASTER owns the post-15:00 CEST tracker sequence"
fi

status="$(xyz_cmd_sh "tracker_max_id" "rg -o '<a id=\"xyz-pilot-[0-9]{3}\"></a>' docs/XYZ_PILOT_ISSUE_TRACKER.md | sed -E 's/.*xyz-pilot-([0-9]{3}).*/\\1/' | sort -n | tail -1")"
max_id="$(tr -d '[:space:]' < artifacts/tracker_max_id.out 2>/dev/null || true)"
if [ "$status" = "0" ] && [ -n "$max_id" ]; then
  xyz_result "Tracker max ID discovery" "current max ID discovered at filing time" "XYZ-PILOT-$max_id" "PASS" "Next ID is assigned only during guarded tracker filing"
else
  xyz_result "Tracker max ID discovery" "current max ID discovered at filing time" "$(xyz_artifact_text "tracker_max_id")" "WARN" "Do not file candidates until max ID is known"
fi

{
  printf '\n## Tracker Filing Hold\n\n'
  printf 'This run remains candidate-only. Runtime candidates are not filed by the harness.\n'
  printf 'If a candidate is later promoted, XYZ_MASTER must fetch latest `origin/main`, archive the tracker, run the verifier, perform duplicate triage, then assign the next sequential `XYZ-PILOT-NNN` at filing time.\n'
  if [ -n "$max_id" ]; then
    printf 'Observed current max ID during this run: `XYZ-PILOT-%s`.\n' "$max_id"
  fi
  printf '\n## Duplicate Triage Note\n\n'
  printf 'The XYZ Pilot Chatbot duplicate checker is currently coupled to the tracker write path. This harness does not call it, because a non-duplicate result can proceed to write. Duplicate triage remains read-only/manual until a check-only wrapper exists.\n'
} >> "$summary"

cp "$summary" report.md
xyz_agent_finish
