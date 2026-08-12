#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"

run_id="${1:-$(date -u +%Y-%m-%dT%H%M%SZ)}"
run_dir="$repo_root/tests/logs/runs/$run_id"

agents=(
  xyz_master
  xyz_expert
  xyz_smoke
  xyz_integration
  xyz_e2e
  xyz_uat
  xyz_chaos
)

if [ "${xyz_PILOT_INCLUDE_STRESS:-0}" = "1" ]; then
  agents+=(xyz_stress)
fi

if [ "${xyz_PILOT_INCLUDE_REGRESSION:-0}" = "1" ]; then
  agents+=(xyz_regression)
fi

if [ "${xyz_PILOT_INCLUDE_INFRASTRUCTURE:-0}" = "1" ]; then
  agents+=(xyz_infrastructure)
fi

mkdir -p "$run_dir"

{
  printf '# <PILOT_NAME> E2E Swarm Run\n\n'
  printf -- '- Run ID: `%s`\n' "$run_id"
  printf -- '- Run log directory: `%s`\n' "$run_dir"
  printf -- '- Agent log pattern: `%s/<AGENT>/{agent.stdout,agent.stderr,commands.md,results.md,issue-candidates.md,artifacts/}`\n' "$run_dir"
  printf -- '- Initialized UTC: `%s`\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf -- '- Repository: `%s`\n' "$repo_root"
  printf -- '- Tracker write mode: `candidate-only by default`\n\n'
  printf '## Run Log Layout\n\n'
  printf '| Path | Purpose |\n'
  printf '|---|---|\n'
  printf '| `%s/manifest.md` | Run-level manifest, exit codes, archive and notification status |\n' "$run_dir"
  printf '| `%s/<AGENT>/commands.md` | Commands executed by one agent |\n' "$run_dir"
  printf '| `%s/<AGENT>/results.md` | Probe results produced by one agent |\n' "$run_dir"
  printf '| `%s/<AGENT>/issue-candidates.md` | Candidate-only tracker handoff for `xyz_master` |\n' "$run_dir"
  printf '| `%s/<AGENT>/artifacts/` | Redacted stdout, stderr, HTTP bodies and structured probe artifacts |\n\n' "$run_dir"
  printf '| Agent | Local script |\n'
  printf '|---|---|\n'
} > "$run_dir/manifest.md"

for agent in "${agents[@]}"; do
  agent_dir="$run_dir/$agent"
  template="$repo_root/tests/agents/$agent/run.sh"
  skill_profile="$repo_root/tests/skills/$agent.md"
  if [ ! -f "$template" ]; then
    printf 'Missing agent template: %s\n' "$template" >&2
    exit 2
  fi
  if [ ! -f "$skill_profile" ]; then
    printf 'Missing agent skill profile: %s\n' "$skill_profile" >&2
    exit 2
  fi
  mkdir -p "$agent_dir/artifacts"
  cp "$template" "$agent_dir/run.sh"
  cp "$skill_profile" "$agent_dir/skills.md"
  chmod +x "$agent_dir/run.sh"
  {
    printf '# %s Commands\n\n' "$agent"
    printf -- '- Run log directory: `%s`\n' "$run_dir"
    printf -- '- Agent directory: `%s`\n' "$agent_dir"
    printf -- '- Artifact directory: `%s/artifacts`\n\n' "$agent_dir"
    printf 'Initialized, not yet executed.\n'
  } > "$agent_dir/commands.md"
  {
    printf '# %s Results\n\n' "$agent"
    printf -- '- Run log directory: `%s`\n' "$run_dir"
    printf -- '- Agent directory: `%s`\n' "$agent_dir"
    printf -- '- Candidate file: `%s/issue-candidates.md`\n\n' "$agent_dir"
    printf 'Initialized, not yet executed.\n'
  } > "$agent_dir/results.md"
  {
    printf '# %s Issue Candidates\n\n' "$agent"
    printf -- '- Run log directory: `%s`\n' "$run_dir"
    printf -- '- Agent directory: `%s`\n\n' "$agent_dir"
    printf 'Initialized, not yet executed.\n'
  } > "$agent_dir/issue-candidates.md"
  printf '| `%s` | `%s` |\n' "$agent" "$agent_dir/run.sh" >> "$run_dir/manifest.md"
done

{
  printf '\n## Skill Profiles\n\n'
  printf 'Canonical matrix: `tests/skills/SWARM_SKILL_MATRIX.md`\n\n'
  printf '| Agent | Local copied profile |\n'
  printf '|---|---|\n'
  for agent in "${agents[@]}"; do
    printf '| `%s` | `%s` |\n' "$agent" "$run_dir/$agent/skills.md"
  done
} >> "$run_dir/manifest.md"

printf '%s\n' "$run_dir"
