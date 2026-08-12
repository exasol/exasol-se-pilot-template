#!/usr/bin/env bash
# tests/scripts/test_swarm_agent_selection.sh — verify XYZ_TESTER_AGENTS overrides the base set.
set -uo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
runner="$script_dir/run_e2e_swarm.sh"
fail=0
out="$(XYZ_TESTER_AGENTS='XYZ_SMOKE XYZ_E2E' XYZ_INCLUDE_STRESS=1 \
       XYZ_SWARM_PRINT_AGENTS_AND_EXIT=1 bash "$runner" 2>/dev/null)"
echo "$out" | grep -q 'XYZ_SMOKE' || { echo "FAIL: XYZ_SMOKE missing"; fail=1; }
echo "$out" | grep -q 'XYZ_E2E'  || { echo "FAIL: XYZ_E2E missing"; fail=1; }
echo "$out" | grep -q 'XYZ_STRESS' || { echo "FAIL: opt-in XYZ_STRESS not appended"; fail=1; }
echo "$out" | grep -q 'XYZ_EXPERT' && { echo "FAIL: XYZ_EXPERT should be excluded by override"; fail=1; }
def_out="$(XYZ_SWARM_PRINT_AGENTS_AND_EXIT=1 bash "$runner" 2>/dev/null)"
for a in XYZ_EXPERT XYZ_SMOKE XYZ_INTEGRATION XYZ_E2E XYZ_UAT XYZ_CHAOS; do
  echo "$def_out" | grep -q "$a" || { echo "FAIL: default base missing $a"; fail=1; }
done
[ "$fail" = 0 ] && echo "PASS" || exit 1
