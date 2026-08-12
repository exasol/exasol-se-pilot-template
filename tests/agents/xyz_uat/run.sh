#!/usr/bin/env bash
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$script_dir/../../../../scripts/common.sh" ]; then
  source "$script_dir/../../../../scripts/common.sh"
else
  source "$script_dir/../../scripts/common.sh"
fi

pilot_agent_begin "Subscriber-facing acceptance checks for availability, wording, and documented demo alignment."

if pilot_http_probe "qbr_uat_health" "$xyz_PILOT_QBR_URL/_stcore/health" "200"; then
  pilot_result "QBR subscriber availability" "HTTP 200 ok" "$(pilot_status_code "qbr_uat_health") $(pilot_body_preview "qbr_uat_health")" "PASS" ""
else
  pilot_result "QBR subscriber availability" "HTTP 200 ok" "$(pilot_status_code "qbr_uat_health") $(pilot_body_preview "qbr_uat_health")" "FAIL" ""
fi

if pilot_http_probe "chat_uat_health" "$xyz_PILOT_CHAT_URL/_stcore/health" "200"; then
  pilot_result "Chatbot subscriber availability" "HTTP 200 ok" "$(pilot_status_code "chat_uat_health") $(pilot_body_preview "chat_uat_health")" "PASS" ""
else
  pilot_result "Chatbot subscriber availability" "HTTP 200 ok" "$(pilot_status_code "chat_uat_health") $(pilot_body_preview "chat_uat_health")" "FAIL" ""
fi

if pilot_http_probe "grafana_uat_health" "$xyz_PILOT_GRAFANA_URL/api/health" "200"; then
  pilot_result "Grafana availability" "HTTP 200 db ok" "$(pilot_status_code "grafana_uat_health") $(pilot_body_preview "grafana_uat_health")" "PASS" ""
else
  pilot_result "Grafana availability" "HTTP 200 db ok" "$(pilot_status_code "grafana_uat_health") $(pilot_body_preview "grafana_uat_health")" "FAIL" ""
fi

status="$(pilot_cmd_sh "readme_version_literals" "rg -n 'Grafana-11\\.6\\.11|512k facts|71 logged' README.md docs/00-live-status/XYZ_PILOT_LIVE_RUNBOOK.md docs/50-testing-validation/XYZ_PILOT_TEST_OVERVIEW.md docs/50-testing-validation/XYZ_PILOT_TEST_RUNBOOK.md || true")"
actual="$(pilot_artifact_text "readme_version_literals")"
if grep -q "Grafana-11.6.11\\|512k facts\\|71 logged" artifacts/readme_version_literals.out 2>/dev/null; then
  pilot_result "Documentation drift scan" "no stale deployed-runtime literals" "$actual" "WARN" "Candidate: docs drift"
  pilot_add_candidate "Runtime documentation drift after deployed changes" \
"- **Type:** documentation
- **Severity:** major docs
- **Area:** Docs
- **Observed:** deployed runtime facts differ from README/runbook/test text.
- **Expected:** operator-facing docs should match live Grafana version, current Exasol scale, and current tracker count.
- **Evidence:** see \`artifacts/readme_version_literals.out\`.
- **Duplicate hints:** related to resolved doc-drift sweeps, but new 2026-05-27 facts; hold for \`XYZ-PILOT-140+\` after reserved IDs \`131..139\` are filed."
else
  pilot_result "Documentation drift scan" "no stale deployed-runtime literals" "$actual" "PASS" ""
fi

status="$(pilot_cmd_sh "mcp_client_guide_scale" "rg -n '170 M|MV_\\*|<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp' docs/30-runbooks/mcp/XYZ_MCP_CLIENT_GUIDE.md")"
if [ "$status" = "0" ]; then
  pilot_result "MCP client guide current scale path" "mentions 170M/MV/remote MCP" "$(pilot_artifact_text "mcp_client_guide_scale")" "PASS" ""
else
  pilot_result "MCP client guide current scale path" "mentions 170M/MV/remote MCP" "$(pilot_artifact_text "mcp_client_guide_scale")" "WARN" ""
fi

pilot_agent_finish
