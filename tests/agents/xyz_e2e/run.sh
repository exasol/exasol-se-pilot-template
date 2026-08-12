#!/usr/bin/env bash
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$script_dir/../../../../scripts/common.sh" ]; then
  source "$script_dir/../../../../scripts/common.sh"
else
  source "$script_dir/../../scripts/common.sh"
fi

pilot_agent_begin "End-to-end observable journeys across QBR, Grafana, Chatbot, MCP, and Exasol."

if pilot_http_probe "qbr_app_shell" "$xyz_PILOT_QBR_URL/" "200"; then
  pilot_result "QBR app shell" "HTTP 200 Streamlit shell" "$(pilot_status_code "qbr_app_shell") $(pilot_body_preview "qbr_app_shell")" "PASS" ""
else
  pilot_result "QBR app shell" "HTTP 200 Streamlit shell" "$(pilot_status_code "qbr_app_shell") $(pilot_body_preview "qbr_app_shell")" "FAIL" ""
fi

if pilot_http_probe "grafana_root" "$xyz_PILOT_GRAFANA_URL/" "200,302"; then
  pilot_result "Grafana root" "login shell or redirect" "$(pilot_status_code "grafana_root")" "PASS" ""
else
  pilot_result "Grafana root" "login shell or redirect" "$(pilot_status_code "grafana_root")" "FAIL" ""
fi

if pilot_http_probe "grafana_api_user_unauth" "$xyz_PILOT_GRAFANA_URL/api/user" "401,403"; then
  pilot_result "Grafana API auth gate" "401/403" "$(pilot_status_code "grafana_api_user_unauth")" "PASS" ""
else
  pilot_result "Grafana API auth gate" "401/403" "$(pilot_status_code "grafana_api_user_unauth")" "FAIL" ""
fi

if pilot_http_probe "chatbot_shell" "$xyz_PILOT_CHAT_URL/" "200"; then
  pilot_result "Chatbot app shell" "HTTP 200 Streamlit shell" "$(pilot_status_code "chatbot_shell") $(pilot_body_preview "chatbot_shell")" "PASS" ""
else
  pilot_result "Chatbot app shell" "HTTP 200 Streamlit shell" "$(pilot_status_code "chatbot_shell") $(pilot_body_preview "chatbot_shell")" "FAIL" ""
fi

if pilot_http_probe "mcp_initialize_unauth" "$xyz_mcp_url" "401,403" "POST" "Content-Type: application/json"; then
  pilot_result "MCP initialize without bearer" "401/403" "$(pilot_status_code "mcp_initialize_unauth")" "PASS" "Rejected before tool execution"
else
  pilot_result "MCP initialize without bearer" "401/403" "$(pilot_status_code "mcp_initialize_unauth")" "FAIL" ""
fi

if grep -q "invalid_token" artifacts/mcp_initialize_unauth.body 2>/dev/null; then
  pilot_result "MCP unauth error semantics" "invalid_token returned before tool execution" "invalid_token" "PASS" ""
else
  pilot_result "MCP unauth error semantics" "invalid_token returned before tool execution" "$(pilot_body_preview "mcp_initialize_unauth")" "WARN" "OAuth boundary still passed by HTTP code"
fi

status="$(pilot_cmd_sh "exasol_mv_monthly_count" "exapump sql --profile '$xyz_PILOT_EXASOL_PROFILE' 'SELECT COUNT(*) FROM <PILOT_SCHEMA>.MV_MONTHLY_METRICS'")"
actual="$(pilot_artifact_text "exasol_mv_monthly_count")"
if [ "$status" = "0" ]; then
  pilot_result "Exasol MV read path" "read succeeds" "$actual" "PASS" "Validates dashboard-backed read surface"
else
  pilot_result "Exasol MV read path" "read succeeds" "$actual" "FAIL" ""
fi

status="$(pilot_cmd_sh "streamlit_auth_static_gate" "rg -n 'APP_PASSWORD|_check_password|st.stop|_require_auth' services/streamlit-insights/app.py services/chatbot/app.py")"
if [ "$status" = "0" ]; then
  pilot_result "Streamlit auth static gate" "auth gate markers present in QBR/chatbot source" "$(pilot_artifact_text "streamlit_auth_static_gate")" "PASS" ""
else
  pilot_result "Streamlit auth static gate" "auth gate markers present in QBR/chatbot source" "$(pilot_artifact_text "streamlit_auth_static_gate")" "WARN" ""
fi

pilot_agent_finish
