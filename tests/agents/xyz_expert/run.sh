#!/usr/bin/env bash
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$script_dir/../../../../scripts/common.sh" ]; then
  # Local copy under tests/logs/runs/<run_id>/XYZ_EXPERT.
  source "$script_dir/../../../../scripts/common.sh"
else
  source "$script_dir/../../scripts/common.sh"
fi

xyz_agent_begin "Expert static and local operating-discipline checks."

status="$(xyz_cmd_sh "tracker_verifier" "./scripts/verify_tracker_completeness.sh -v")"
actual="$(xyz_artifact_text "tracker_verifier")"
if [ "$status" = "0" ]; then
  xyz_result "Tracker verifier" "clean tracker structure" "$actual" "PASS" ""
else
  xyz_result "Tracker verifier" "clean tracker structure" "$actual" "FAIL" "Fix before any tracker filing"
fi

status="$(xyz_cmd "git_status" git status --short --branch)"
xyz_result "Git status" "status captured without mutation" "$(xyz_artifact_text "git_status")" "PASS" "Informational"

missing=""
for path in CLAUDE.md README.md docs/XYZ_PILOT_ISSUE_TRACKER.md docs/50-testing-validation/XYZ_PILOT_TEST_OVERVIEW.md docs/00-live-status/XYZ_PILOT_LIVE_RUNBOOK.md docs/50-testing-validation/XYZ_PILOT_TEST_RUNBOOK.md; do
  if [ ! -f "$XYZ_REPO_ROOT/$path" ]; then
    missing="$missing $path"
  fi
done
if [ -z "$missing" ]; then
  xyz_result "Required docs" "all core docs present" "present" "PASS" ""
else
  xyz_result "Required docs" "all core docs present" "missing:$missing" "FAIL" ""
fi

missing_tools=""
for tool in python3 aws curl exapump git rg; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    missing_tools="$missing_tools $tool"
  fi
done
if [ -z "$missing_tools" ]; then
  xyz_result "Required CLI tools" "python3/aws/curl/exapump/git/rg available" "available" "PASS" ""
else
  xyz_result "Required CLI tools" "python3/aws/curl/exapump/git/rg available" "missing:$missing_tools" "FAIL" ""
fi

status="$(xyz_cmd_sh "python_syntax_core_apps" "PYTHONPYCACHEPREFIX=/tmp/xyz-swarm-pycache-$XYZ_RUN_ID python3 -W default -m py_compile services/streamlit-insights/app.py services/chatbot/app.py scheduler/daily_status.py scheduler/weekend_recap.py scripts/exasol_warmup/warmup.py")"
actual="$(xyz_artifact_text "python_syntax_core_apps")"
if [ "$status" = "0" ]; then
  if [ -s artifacts/python_syntax_core_apps.err ]; then
    xyz_result "Core Python syntax" "compile without warnings" "$(xyz_artifact_text "python_syntax_core_apps")" "WARN" "Candidate: Python warning emitted during compile"
    xyz_add_candidate "Python SyntaxWarning in chatbot markdown renderer docstring" \
"- **Type:** defect
- **Severity:** minor
- **Area:** Chatbot
- **Observed:** \`python3 -m py_compile services/chatbot/app.py\` emits a SyntaxWarning for an invalid escape sequence near the Mermaid renderer docstring.
- **Expected:** core Python files should compile without warnings.
- **Evidence:** see \`artifacts/python_syntax_core_apps.err\`.
- **Duplicate hints:** likely duplicate of \`XYZ-PILOT-141\`; keep as evidence only unless the central tracker state changes."
  else
    xyz_result "Core Python syntax" "Streamlit/chatbot/scheduler/warmup compile" "compile clean" "PASS" ""
  fi
else
  xyz_result "Core Python syntax" "Streamlit/chatbot/scheduler/warmup compile" "$actual" "FAIL" ""
fi

status="$(xyz_cmd_sh "ui_provenance_contract" "python3 scripts/check_ui_provenance.py")"
actual="$(xyz_artifact_text "ui_provenance_contract")"
if [ "$status" = "0" ]; then
  xyz_result "UI provenance contract" "Operator Dashboard, Chatbot, and QBR carry build/deploy/start discipline" "$actual" "PASS" "XYZ-PILOT-202"
else
  xyz_result "UI provenance contract" "Operator Dashboard, Chatbot, and QBR carry build/deploy/start discipline" "$actual" "FAIL" "Fix before UI deployment"
  xyz_add_candidate "UI provenance contract drift" \
"- **Type:** defect
- **Severity:** minor
- **Area:** UI provenance · deployment discipline
- **Observed:** \`python3 scripts/check_ui_provenance.py\` failed during XYZ_EXPERT static checks.
- **Expected:** Operator Dashboard, Chatbot, and QBR source surfaces must all carry the single-issue build/deploy/start provenance contract.
- **Evidence:** see \`artifacts/ui_provenance_contract.out\` and \`artifacts/ui_provenance_contract.err\`.
- **Duplicate hints:** compare with \`XYZ-PILOT-150\` and \`XYZ-PILOT-202\` before filing."
fi

status="$(xyz_cmd_sh "tracked_sensitive_paths" "git ls-files | rg '(^|/)(\\.env$|secrets\\.json$|.*\\.pem$|.*\\.key$|.*tfstate.*)|^deployment/' | rg -v '(^|/)\\.env\\.example$'")"
actual="$(xyz_artifact_text "tracked_sensitive_paths")"
if [ "$status" = "1" ]; then
  xyz_result "Tracked sensitive paths" "no tracked secret-bearing files" "no matches" "PASS" ""
else
  xyz_result "Tracked sensitive paths" "no tracked secret-bearing files" "$actual" "WARN" "Review matched paths; filenames only"
  xyz_add_candidate "Tracked sensitive path review" \
"- **Type:** security-review
- **Severity:** major
- **Area:** repository hygiene
- **Observed:** sensitive-looking tracked paths were found by filename pattern.
- **Expected:** only examples, sanitized fixtures, or approved documentation references should match. Root-level \`deployment/\` remains watched as a generated-artifact secret store; documentation folders such as \`docs/30-runbooks/deployment/\` are not secret-bearing by path alone.
- **Evidence:** see \`artifacts/tracked_sensitive_paths.out\`."
fi

status="$(xyz_cmd_sh "github_codeowners_route" "test -f .github/CODEOWNERS && rg -n '^\\*\\s+@<GITHUB_OWNER>\\s*$' .github/CODEOWNERS")"
if [ "$status" = "0" ]; then
  xyz_result "GitHub CODEOWNERS routing" "global route to @<GITHUB_OWNER> present" "$(xyz_artifact_text "github_codeowners_route")" "PASS" "XYZ-PILOT-088 Phase 2"
else
  xyz_result "GitHub CODEOWNERS routing" "global route to @<GITHUB_OWNER> present" "$(xyz_artifact_text "github_codeowners_route")" "FAIL" "XYZ-PILOT-088 Phase 2 governance drift"
fi

status="$(xyz_cmd_sh "github_ruleset_active_static" "jq -e '.enforcement == \"active\" and .target == \"branch\" and (.conditions.ref_name.include[] == \"refs/heads/main\") and ([.rules[].type] | index(\"pull_request\") != null) and (.bypass_actors == [{\"actor_id\":5,\"actor_type\":\"RepositoryRole\",\"bypass_mode\":\"always\"}])' .github/rulesets/xyz-pilot-088-main-governance.json")"
if [ "$status" = "0" ]; then
  xyz_result "GitHub ruleset active static" "active main-branch PR ruleset payload valid" "$(xyz_artifact_text "github_ruleset_active_static")" "PASS" "XYZ-PILOT-088 Phase 3 active"
else
  xyz_result "GitHub ruleset active static" "active main-branch PR ruleset payload valid" "$(xyz_artifact_text "github_ruleset_active_static")" "FAIL" "XYZ-PILOT-088 Phase 3 governance drift"
fi

xyz_agent_finish
