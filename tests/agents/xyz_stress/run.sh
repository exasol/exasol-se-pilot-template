#!/usr/bin/env bash
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$script_dir/../../../../scripts/common.sh" ]; then
  source "$script_dir/../../../../scripts/common.sh"
else
  source "$script_dir/../../scripts/common.sh"
fi

xyz_agent_begin "Bounded concurrent-user stress probes for QBR, Chatbot, and Exasol read paths."

users="${XYZ_STRESS_USERS:-5}"
iterations="${XYZ_STRESS_ITERATIONS:-3}"
timeout="${XYZ_STRESS_TIMEOUT_SECONDS:-15}"
db_timeout="${XYZ_STRESS_DB_TIMEOUT_SECONDS:-120}"
db_iterations="${XYZ_STRESS_DB_ITERATIONS:-1}"
db_query_limit="${XYZ_STRESS_DB_QUERY_LIMIT:-0}"
session_hold_seconds="${XYZ_STRESS_SESSION_HOLD_SECONDS:-15}"
think_ms="${XYZ_STRESS_THINK_MS:-250}"
max_p95_ms="${XYZ_STRESS_MAX_P95_MS:-5000}"
max_db_p95_ms="${XYZ_STRESS_MAX_DB_P95_MS:-45000}"
max_error_rate="${XYZ_STRESS_MAX_ERROR_RATE:-0.05}"
heavy_analytics_mode="${XYZ_STRESS_HEAVY_ANALYTICS_MODE:-advisory}"
db_args=()

if ! [[ "$users" =~ ^[0-9]+$ ]] || [ "$users" -lt 1 ]; then
  xyz_result "Stress user count" "integer 1..10 scheduled, 1..50 with approved override" "$users" "FAIL" "Set XYZ_STRESS_USERS"
  xyz_agent_finish
fi

if [ "$users" -gt 10 ] && [ "${XYZ_STRESS_ALLOW_HIGHER:-0}" != "1" ]; then
  xyz_result "Stress user count" "scheduled max 10; approved extraordinary max 50" "$users" "FAIL" "Set XYZ_STRESS_ALLOW_HIGHER=1 only for approved load tests"
  xyz_agent_finish
fi

if [ "$users" -gt 50 ]; then
  xyz_result "Stress user count" "hard max 50 for approved extraordinary runs" "$users" "FAIL" "Reduce XYZ_STRESS_USERS to 50 or less"
  xyz_agent_finish
fi

case "$heavy_analytics_mode" in
  advisory|blocking|skip) ;;
  *)
    xyz_result "Heavy analytics mode" "advisory, blocking, or skip" "$heavy_analytics_mode" "FAIL" "Set XYZ_STRESS_HEAVY_ANALYTICS_MODE"
    xyz_agent_finish
    ;;
esac

# Per-profile (XYZ_BIZ / external semantic layer) stress dimension. xyz_stress_probe.py
# DEFAULTS --biz-profile to `xyz-pilot-biz`, so the probe runs the BIZ pack
# (which includes a BLOCKING core target against <PILOT_SCHEMA>.V_BUSINESS_METRICS)
# unless we say otherwise. We make the BIZ leg credential-aware so it is ENABLED
# where the `xyz-pilot-biz` exapump credential is configured (local operator shells)
# but SAFELY OFF where it is absent (CI/nightly until the secret is wired) — the
# probe documents `--biz-profile ""` as the opt-out.
#
# To enable BIZ in CI/nightly you MUST wire the `xyz-pilot-biz` exapump credential
# the same way the XYZ_DASH (`xyz-pilot-ro`) credential is wired for the runner;
# until then the preflight below auto-disables the BIZ leg so the XYZ_DASH path
# (the demo-critical path) is never regressed.
biz_profile="${XYZ_SWARM_BIZ_PROFILE-xyz-pilot-biz}"
if [ -n "$biz_profile" ]; then
  if ! exapump sql --profile "$biz_profile" "SELECT 1" >/dev/null 2>&1; then
    printf 'XYZ_STRESS: BIZ preflight failed for exapump profile "%s" (creds absent / cannot connect); disabling BIZ stress dimension and continuing on the XYZ_DASH path.\n' "$biz_profile" >&2
    biz_profile=""
  fi
fi

if [ "${XYZ_STRESS_SKIP_DB:-0}" = "1" ]; then
  db_args+=(--skip-db)
else
  db_args+=(
    --exasol-profile "$XYZ_EXASOL_PROFILE"
    --biz-profile "$biz_profile"
    --db-iterations "$db_iterations"
    --db-timeout "$db_timeout"
    --db-query-limit "$db_query_limit"
    --exasol-dsn "${XYZ_EXASOL_DSN:-}"
    --exasol-user "${XYZ_EXASOL_USER:-XYZ_DASH}"
    --exasol-schema "${XYZ_EXASOL_SCHEMA:-<PILOT_SCHEMA>}"
    --exasol-password-env "${XYZ_EXASOL_PASSWORD_ENV:-XYZ_EXASOL_PASSWORD}"
    --session-hold-seconds "$session_hold_seconds"
  )
fi

if [ "${XYZ_STRESS_SKIP_DYNAMIC_VIEWS:-0}" = "1" ]; then
  db_args+=(--skip-dynamic-views)
fi
if [ "${XYZ_STRESS_INCLUDE_VIEW_COUNT_UNION_ALL:-0}" = "1" ]; then
  db_args+=(--include-view-count-union-all)
fi
if [ "${XYZ_STRESS_SKIP_SESSION_HOLD:-0}" = "1" ]; then
  db_args+=(--skip-session-hold)
fi
if [ "${XYZ_STRESS_REQUIRE_SESSION_HOLD:-0}" = "1" ]; then
  db_args+=(--require-session-hold)
fi

status="$(xyz_cmd "stress_probe" \
  python3 tests/scripts/xyz_stress_probe.py \
    --qbr-url "$XYZ_QBR_URL" \
    --chat-url "$XYZ_CHAT_URL" \
    --users "$users" \
    --iterations "$iterations" \
    --timeout "$timeout" \
    --think-ms "$think_ms" \
    --max-p95-ms "$max_p95_ms" \
    --max-db-p95-ms "$max_db_p95_ms" \
    --max-error-rate "$max_error_rate" \
    --heavy-analytics-mode "$heavy_analytics_mode" \
    "${db_args[@]}" \
    --artifact-dir "$PWD/artifacts")"

actual="$(xyz_artifact_text "stress_probe")"
expected="core UAT error rate <= $max_error_rate, HTTP p95 <= ${max_p95_ms}ms, DB p95 <= ${max_db_p95_ms}ms; individual schema-inventory view counts advisory; combined view_count_union_all opt-in; heavy analytics mode=$heavy_analytics_mode"

if [ "$status" = "0" ]; then
  xyz_result "QBR + Chatbot + Exasol concurrent stress" "$expected" "$actual" "PASS" "See artifacts/stress_summary.md"
else
  xyz_result "QBR + Chatbot + Exasol concurrent stress" "$expected" "$actual" "FAIL" "See artifacts/stress_summary.md and artifacts/stress_results.jsonl"
  xyz_add_candidate "Concurrent QBR/chatbot/Exasol stress threshold exceeded" \
"- **Type:** defect
- **Severity:** minor pending operator approval to promote to major
- **Area:** Streamlit QBR · Chatbot · Exasol Personal · concurrent UAT readiness
- **Observed:** Bounded stress probe with \`$users\` concurrent virtual users exceeded core UAT error-rate, HTTP p95, or DB p95 latency thresholds. Individual schema-inventory \`view_count_*\` probes are advisory; combined \`view_count_union_all\` is opt-in only; heavy analytical SQL mode was \`$heavy_analytics_mode\`.
- **Expected:** QBR, Chatbot, and Exasol core read-only query paths should tolerate the approved \`$users\` concurrent UAT-style users with <= \`$max_error_rate\` blocking error rate, HTTP p95 <= \`${max_p95_ms}ms\`, and DB p95 <= \`${max_db_p95_ms}ms\`. Individual schema-inventory live-view counts remain reported but advisory. Known heavy analytical SQL probes remain reported but are advisory unless \`XYZ_STRESS_HEAVY_ANALYTICS_MODE=blocking\`; \`view_count_union_all\` is included only when \`XYZ_STRESS_INCLUDE_VIEW_COUNT_UNION_ALL=1\`.
- **Evidence:** \`$XYZ_RUN_DIR/XYZ_STRESS/artifacts/stress_summary.md\` and \`stress_results.jsonl\`.
- **Duplicate hints:** Check recent UI/runtime performance tickets before filing. If this blocks UAT, ask <OPERATOR_NAME> before filing as major per severity discipline."
fi

xyz_agent_finish
