#!/usr/bin/env bash
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$script_dir/../../../../scripts/common.sh" ]; then
  source "$script_dir/../../../../scripts/common.sh"
else
  source "$script_dir/../../scripts/common.sh"
fi

pilot_agent_begin "Fast public runtime health, endpoint, and auth-boundary checks."

status="$(pilot_cmd_sh "ec2_state" "aws ec2 describe-instances --region '$xyz_PILOT_REGION' --instance-ids '$xyz_PILOT_EC2_INSTANCE_ID' --query 'Reservations[].Instances[].State.Name' --output text --no-cli-pager")"
actual="$(pilot_artifact_text "ec2_state")"
if [ "$status" = "0" ] && grep -q "running" artifacts/ec2_state.out; then
  pilot_result "EC2 state" "running" "$actual" "PASS" ""
else
  pilot_result "EC2 state" "running" "$actual" "FAIL" "Live runtime may be stopped"
fi

if pilot_http_probe "qbr_health" "$xyz_PILOT_QBR_URL/_stcore/health" "200"; then
  pilot_result "Streamlit QBR health" "HTTP 200 ok" "$(pilot_status_code "qbr_health") $(pilot_body_preview "qbr_health")" "PASS" ""
else
  pilot_result "Streamlit QBR health" "HTTP 200 ok" "$(pilot_status_code "qbr_health") $(pilot_body_preview "qbr_health")" "FAIL" ""
fi

if pilot_http_probe "grafana_health" "$xyz_PILOT_GRAFANA_URL/api/health" "200"; then
  pilot_result "Grafana health" "HTTP 200 database ok" "$(pilot_status_code "grafana_health") $(pilot_body_preview "grafana_health")" "PASS" ""
else
  pilot_result "Grafana health" "HTTP 200 database ok" "$(pilot_status_code "grafana_health") $(pilot_body_preview "grafana_health")" "FAIL" ""
fi

if grep -q '"version"' artifacts/grafana_health.body 2>/dev/null; then
  pilot_result "Grafana version disclosure" "health body includes version for drift checks" "$(pilot_body_preview "grafana_health")" "PASS" ""
else
  pilot_result "Grafana version disclosure" "health body includes version for drift checks" "$(pilot_body_preview "grafana_health")" "WARN" "UAT docs drift checks may lose live comparison data"
fi

if pilot_http_probe "chat_health" "$xyz_PILOT_CHAT_URL/_stcore/health" "200"; then
  pilot_result "Chatbot health" "HTTP 200 ok" "$(pilot_status_code "chat_health") $(pilot_body_preview "chat_health")" "PASS" ""
else
  pilot_result "Chatbot health" "HTTP 200 ok" "$(pilot_status_code "chat_health") $(pilot_body_preview "chat_health")" "FAIL" ""
fi

if pilot_http_probe "mcp_metadata" "https://<PROJECT_SLUG>.<PILOT_DOMAIN>/.well-known/oauth-protected-resource/mcp" "200"; then
  pilot_result "MCP protected-resource metadata" "HTTP 200 JSON" "$(pilot_status_code "mcp_metadata") $(pilot_body_preview "mcp_metadata")" "PASS" ""
else
  pilot_result "MCP protected-resource metadata" "HTTP 200 JSON" "$(pilot_status_code "mcp_metadata") $(pilot_body_preview "mcp_metadata")" "FAIL" ""
fi

if grep -q "authorization_servers" artifacts/mcp_metadata.body 2>/dev/null; then
  pilot_result "MCP OAuth metadata shape" "authorization_servers present" "present" "PASS" ""
else
  pilot_result "MCP OAuth metadata shape" "authorization_servers present" "$(pilot_body_preview "mcp_metadata")" "FAIL" ""
fi

if pilot_http_probe "mcp_anonymous_get" "$xyz_mcp_url" "401,403"; then
  pilot_result "MCP anonymous gate" "HTTP 401/403 OAuth challenge" "$(pilot_status_code "mcp_anonymous_get") $(pilot_body_preview "mcp_anonymous_get")" "PASS" ""
else
  pilot_result "MCP anonymous gate" "HTTP 401/403 OAuth challenge" "$(pilot_status_code "mcp_anonymous_get") $(pilot_body_preview "mcp_anonymous_get")" "FAIL" ""
fi

pilot_agent_finish
