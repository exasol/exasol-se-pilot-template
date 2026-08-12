#!/usr/bin/env bash
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$script_dir/../../../../scripts/common.sh" ]; then
  source "$script_dir/../../../../scripts/common.sh"
else
  source "$script_dir/../../scripts/common.sh"
fi

pilot_agent_begin "Non-destructive chaos and negative-path probes. No service restarts or mutations."

if pilot_http_probe "mcp_invalid_bearer" "$xyz_mcp_url" "401,403" "GET" "Authorization: Bearer invalid-token"; then
  pilot_result "MCP invalid bearer" "401/403" "$(pilot_status_code "mcp_invalid_bearer")" "PASS" ""
else
  pilot_result "MCP invalid bearer" "401/403" "$(pilot_status_code "mcp_invalid_bearer")" "FAIL" ""
fi

if pilot_http_probe "mcp_unknown_path" "https://<PROJECT_SLUG>.<PILOT_DOMAIN>/not-a-real-path" "404"; then
  pilot_result "MCP unknown path" "404" "$(pilot_status_code "mcp_unknown_path")" "PASS" ""
else
  pilot_result "MCP unknown path" "404" "$(pilot_status_code "mcp_unknown_path")" "FAIL" ""
fi

if pilot_http_probe "mcp_trace_method" "$xyz_mcp_url" "401,403,405" "TRACE"; then
  pilot_result "MCP TRACE method" "rejected" "$(pilot_status_code "mcp_trace_method")" "PASS" ""
else
  pilot_result "MCP TRACE method" "rejected" "$(pilot_status_code "mcp_trace_method")" "FAIL" ""
fi

if pilot_http_probe "grafana_admin_settings_unauth" "$xyz_PILOT_GRAFANA_URL/api/admin/settings" "401,403,404"; then
  pilot_result "Grafana admin API unauth" "401/403/404" "$(pilot_status_code "grafana_admin_settings_unauth")" "PASS" ""
else
  pilot_result "Grafana admin API unauth" "401/403/404" "$(pilot_status_code "grafana_admin_settings_unauth")" "FAIL" ""
fi

if pilot_http_probe "qbr_health_post" "$xyz_PILOT_QBR_URL/_stcore/health" "401,403,405" "POST"; then
  pilot_result "QBR health POST method mismatch" "rejected" "$(pilot_status_code "qbr_health_post")" "PASS" ""
else
  pilot_result "QBR health POST method mismatch" "rejected" "$(pilot_status_code "qbr_health_post")" "FAIL" ""
fi

if pilot_http_probe "chat_health_post" "$xyz_PILOT_CHAT_URL/_stcore/health" "401,403,405" "POST"; then
  pilot_result "Chat health POST method mismatch" "rejected" "$(pilot_status_code "chat_health_post")" "PASS" ""
else
  pilot_result "Chat health POST method mismatch" "rejected" "$(pilot_status_code "chat_health_post")" "FAIL" ""
fi

status="$(pilot_cmd_sh "qbr_concurrent_health" "for i in {1..10}; do curl -k -sS -o /dev/null -w '%{http_code}\\n' '$xyz_PILOT_QBR_URL/_stcore/health' & done; wait")"
if [ "$status" = "0" ] && [ "$(grep -c '^200$' artifacts/qbr_concurrent_health.out 2>/dev/null || true)" = "10" ]; then
  pilot_result "QBR concurrent health" "10/10 HTTP 200" "10/10 200" "PASS" ""
else
  pilot_result "QBR concurrent health" "10/10 HTTP 200" "$(pilot_artifact_text "qbr_concurrent_health")" "FAIL" ""
fi

status="$(pilot_cmd_sh "chat_concurrent_health" "for i in {1..10}; do curl -k -sS -o /dev/null -w '%{http_code}\\n' '$xyz_PILOT_CHAT_URL/_stcore/health' & done; wait")"
if [ "$status" = "0" ] && [ "$(grep -c '^200$' artifacts/chat_concurrent_health.out 2>/dev/null || true)" = "10" ]; then
  pilot_result "Chat concurrent health" "10/10 HTTP 200" "10/10 200" "PASS" ""
else
  pilot_result "Chat concurrent health" "10/10 HTTP 200" "$(pilot_artifact_text "chat_concurrent_health")" "FAIL" ""
fi

pilot_agent_finish
