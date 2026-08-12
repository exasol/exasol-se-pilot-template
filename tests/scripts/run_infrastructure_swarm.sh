#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
run_id="${1:-$(date -u +%Y-%m-%dT%H%M%SZ)}"

xyz_PILOT_INCLUDE_INFRASTRUCTURE=1 "$script_dir/run_e2e_swarm.sh" "$run_id"
