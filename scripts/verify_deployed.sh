#!/usr/bin/env bash
# verify_deployed.sh — confirm every xyz-pilot-* Lambda's deployed source
# matches the local git working copy.
#
# Encodes the daily-documentation-discipline check from CLAUDE.md rule #6.
# Exit code: 0 if every Lambda's deployed source matches local; 1 if any
# diverge. Designed to be safe to run anytime — read-only against AWS.
#
# Usage:  ./scripts/verify_deployed.sh
#
# Requirements: aws CLI authenticated as a principal that can call
#   lambda:GetFunction on xyz-pilot-* in <AWS_REGION>, plus `unzip` + `diff`.
set -euo pipefail

REGION="${AWS_REGION:-<AWS_REGION>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d -t xyz-verify-deployed.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

# Map of Lambda name → expected local source path.
declare -a SPECS=(
    "xyz-pilot-start|scheduler/lambda_function.py"
    "xyz-pilot-stop|scheduler/lambda_function.py"
    "xyz-pilot-daily-status|scheduler/daily_status.py"
    "xyz-pilot-cognito-invite-blast|scheduler/cognito_invite_blast.py"
    "xyz-pilot-cognito-sync-subscribers|scheduler/cognito_sync_subscribers.py"
    "xyz-pilot-candidate-drain|scheduler/xyz_master_candidate_drain.py"
    "xyz-pilot-get-sys-password|scheduler/get_sys_password.py"
    "xyz-pilot-get-secret|scheduler/get_secret.py"
    "xyz-pilot-weekend-recap-oneshot|scheduler/weekend_recap.py"
)

drift=0
echo "verify_deployed.sh · region=$REGION · root=$REPO_ROOT"
for spec in "${SPECS[@]}"; do
    fn="${spec%%|*}"
    src="${spec##*|}"
    local_path="$REPO_ROOT/$src"
    if [ ! -f "$local_path" ]; then
        echo "  ✗ $fn — local source missing ($src)"
        drift=$((drift+1)); continue
    fi
    url=$(aws lambda get-function --function-name "$fn" --region "$REGION" \
          --query Code.Location --output text 2>/dev/null || echo "")
    if [ -z "$url" ]; then
        echo "  ? $fn — not deployed (skipping)"; continue
    fi
    if ! curl -sf "$url" -o "$WORK/$fn.zip"; then
        echo "  ✗ $fn — unable to download deployed zip from Lambda presigned URL"
        drift=$((drift+1)); continue
    fi
    rm -rf "$WORK/$fn"; mkdir "$WORK/$fn"
    if ! unzip -q "$WORK/$fn.zip" -d "$WORK/$fn"; then
        echo "  ✗ $fn — unable to unzip deployed package"
        drift=$((drift+1)); continue
    fi
    remote_path="$WORK/$fn/$(basename "$src")"
    if [ ! -f "$remote_path" ]; then
        echo "  ✗ $fn — deployed zip missing $(basename "$src")"
        drift=$((drift+1)); continue
    fi
    if diff -q "$local_path" "$remote_path" >/dev/null 2>&1; then
        echo "  ✓ $fn — deployed matches local ($src)"
    else
        nl=$( (diff "$local_path" "$remote_path" 2>/dev/null || true) | wc -l)
        echo "  ✗ $fn — DRIFT ($nl diff lines) — local=$src vs deployed"
        drift=$((drift+1))
    fi
done

echo ""
if [ "$drift" -gt 0 ]; then
    echo "DRIFT detected — $drift Lambda(s) deployed code does not match local."
    echo "Either redeploy from local, or rebase local to match deployed."
    exit 1
fi
echo "All Lambdas deployed-vs-local: match."
