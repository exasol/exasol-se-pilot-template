#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
run_id="${1:-$(date -u +%Y-%m-%dT%H%M%SZ)}"
run_rel="tests/logs/runs/$run_id"

# Standard E2E swarm execution targets the current <PILOT_NAME> AWS target
# account. Local operator shells default to the `default` profile for
# account <AWS_ACCOUNT_ID>; GitHub Actions OIDC runs set
# XYZ_E2E_AWS_PROFILE_MODE=oidc so the temporary env credentials are used.
if [ "${XYZ_E2E_AWS_PROFILE_MODE:-profile}" = "oidc" ]; then
  unset AWS_PROFILE
else
  export AWS_PROFILE="${XYZ_E2E_AWS_PROFILE:-default}"
fi

# Per-profile (XYZ_BIZ / external semantic layer) stress dimension for XYZ_STRESS.
# xyz_stress_probe.py DEFAULTS its --biz-profile to `xyz-pilot-biz`, so the BIZ
# pack (which includes a BLOCKING core target against
# <PILOT_SCHEMA>.V_BUSINESS_METRICS) would run by default. XYZ_SWARM_BIZ_PROFILE
# (default `xyz-pilot-biz`) is the single knob that the XYZ_STRESS agent run.sh
# reads; setting it to the empty string disables the BIZ dimension entirely.
# It is exported here so every agent subshell inherits the same value.
#
# The XYZ_STRESS agent run.sh runs a credential-aware PREFLIGHT
# (`exapump sql --profile "$XYZ_SWARM_BIZ_PROFILE" "SELECT 1"`) and auto-disables
# the BIZ leg if the credential is absent / cannot connect, so the demo-critical
# XYZ_DASH path is never regressed where `xyz-pilot-biz` is not wired (e.g. CI /
# nightly today). To ENABLE BIZ in CI/nightly the `xyz-pilot-biz` exapump
# credential must be wired the same way the XYZ_DASH (`xyz-pilot-ro`) credential
# is — do NOT enable it in the nightly workflow YAML until that secret is wired.
export XYZ_SWARM_BIZ_PROFILE="${XYZ_SWARM_BIZ_PROFILE-xyz-pilot-biz}"

run_dir="$("$script_dir/init_swarm_run.sh" "$run_id")"

printf 'E2E swarm run initialized\n'
printf 'Run ID: %s\n' "$run_id"
printf 'Run log directory: %s\n' "$run_dir"
printf 'Manifest: %s/manifest.md\n' "$run_dir"
printf 'Agent logs: %s/<AGENT>/{agent.stdout,agent.stderr,commands.md,results.md,issue-candidates.md,artifacts/}\n' "$run_dir"

# Base tester set is overridable (XYZ-PILOT-250): an operator-scheduled run may
# de-select any of the 9 agents. Default = the canonical 6; opt-ins below still append.
if [ -n "${XYZ_TESTER_AGENTS:-}" ]; then
  read -r -a tester_agents <<< "${XYZ_TESTER_AGENTS}"
else
  tester_agents=( XYZ_EXPERT XYZ_SMOKE XYZ_INTEGRATION XYZ_E2E XYZ_UAT XYZ_CHAOS )
fi

if [ "${XYZ_INCLUDE_STRESS:-0}" = "1" ]; then
  tester_agents+=(XYZ_STRESS)
fi

if [ "${XYZ_INCLUDE_REGRESSION:-0}" = "1" ]; then
  tester_agents+=(XYZ_REGRESSION)
fi

if [ "${XYZ_INCLUDE_INFRASTRUCTURE:-0}" = "1" ]; then
  tester_agents+=(XYZ_INFRASTRUCTURE)
fi

if [ "${XYZ_SWARM_PRINT_AGENTS_AND_EXIT:-0}" = "1" ]; then
  printf '%s\n' "${tester_agents[@]}"
  exit 0
fi

{
  printf '\n## Execution\n\n'
  printf -- '- Started UTC: `%s`\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf -- '- Run log directory: `%s`\n' "$run_dir"
  printf -- '- Agent log pattern: `%s/<AGENT>/{agent.stdout,agent.stderr,commands.md,results.md,issue-candidates.md,artifacts/}`\n' "$run_dir"
  printf -- '- Parallel testers: `%s`\n' "${tester_agents[*]}"
  printf -- '- Master consolidation: `XYZ_MASTER after tester completion`\n\n'
} >> "$run_dir/manifest.md"

: > "$run_dir/.pids"

for agent in "${tester_agents[@]}"; do
  printf 'Starting %s; logs: %s/%s\n' "$agent" "$run_dir" "$agent"
  (
    cd "$run_dir/$agent"
    set +e
    XYZ_REPO_ROOT="$repo_root" \
    XYZ_RUN_ID="$run_id" \
    XYZ_RUN_DIR="$run_dir" \
    XYZ_AGENT="$agent" \
    ./run.sh > agent.stdout 2> agent.stderr
    status=$?
    printf '%s\n' "$status" > agent.exit
    exit "$status"
  ) &
  printf '%s %s\n' "$agent" "$!" >> "$run_dir/.pids"
done

overall=0
while read -r agent pid; do
  if wait "$pid"; then
    printf '%s\n' 0 > "$run_dir/$agent/agent.exit"
  else
    status=$?
    printf '%s\n' "$status" > "$run_dir/$agent/agent.exit"
    overall=1
  fi
done < "$run_dir/.pids"

(
  printf 'Starting XYZ_MASTER; logs: %s/XYZ_MASTER\n' "$run_dir"
  cd "$run_dir/XYZ_MASTER"
  set +e
  XYZ_REPO_ROOT="$repo_root" \
  XYZ_RUN_ID="$run_id" \
  XYZ_RUN_DIR="$run_dir" \
  XYZ_AGENT="XYZ_MASTER" \
  ./run.sh > agent.stdout 2> agent.stderr
  status=$?
  printf '%s\n' "$status" > agent.exit
  exit "$status"
) || overall=1

{
  printf '\n## Exit Codes\n\n'
  printf '| Agent | Exit |\n'
  printf '|---|---:|\n'
  for agent in XYZ_MASTER "${tester_agents[@]}"; do
    printf '| `%s` | `%s` |\n' "$agent" "$(cat "$run_dir/$agent/agent.exit" 2>/dev/null || printf '?')"
  done
  printf '\n- Finished UTC: `%s`\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "$run_dir/manifest.md"

archive_ref="$(git -C "$repo_root" rev-parse HEAD)"
archive_failed=0

# XYZ-PILOT-265 follow-up: archive pushes can race a concurrent push to main (the
# nightly, a parallel session, or operator commits), failing non-fast-forward and
# reding an otherwise-green run. Rebase-retry the push — the run archive only ADDS
# files under tests/logs/runs/<run_id>/, so a rebase onto an advanced main is
# always conflict-free. Defined top-level so both archive passes (in their
# subshells) can call it.
git_push_with_rebase_retry() {
  local attempt
  for attempt in 1 2 3; do
    if git push origin main; then return 0; fi
    printf 'archive push rejected (attempt %s); fetch + rebase onto origin/main…\n' "$attempt" >&2
    git fetch origin main --quiet || true
    if ! git rebase origin/main; then
      git rebase --abort 2>/dev/null || true
      return 1
    fi
  done
  git push origin main
}

# XYZ-PILOT-207: the email links must resolve when the operator opens them. Archive
# the completed run folder first, push it, then send exactly one notification
# using the resulting commit SHA as the GitHub link ref.
if [ "${XYZ_E2E_ARCHIVE:-1}" = "1" ]; then
  {
    printf '\n## GitHub Archive\n\n'
    printf -- '- Triggered UTC: `%s`\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf -- '- Path: `%s`\n' "$run_rel"
    printf -- '- AWS credential mode used by agents: `%s`\n' "${AWS_PROFILE:-env-credentials}"
  } >> "$run_dir/manifest.md"

  set +e
  (
    cd "$repo_root"
    # Generated curl/Cloudflare response bodies may contain CRLF or trailing
    # whitespace. Normalize the run archive so `git diff --check` remains a
    # useful guard without making raw HTTP formatting a blocker.
    find "$run_rel" -type f -exec perl -0pi -e 's/[ \t\r]+\n/\n/g; s/\n+\z/\n/' {} +
    git add "$run_rel"
    git diff --cached --check -- "$run_rel"
    if git diff --cached --quiet -- "$run_rel"; then
      exit 0
    fi
    commit_args=(
      -m "XYZ-PILOT-207: archive E2E swarm run $run_id"
      -m "Archive completed Tester Swarm run artefacts before the standard post-run notification is sent, so email links resolve to committed GitHub paths."
    )
    if [ -n "${XYZ_E2E_ARCHIVE_COAUTHOR:-}" ]; then
      commit_args+=(-m "$XYZ_E2E_ARCHIVE_COAUTHOR")
    fi
    git commit -o "$run_rel" "${commit_args[@]}"
    git_push_with_rebase_retry
  )
  archive_rc=$?
  set -e
  if [ "$archive_rc" -eq 0 ]; then
    archive_ref="$(git -C "$repo_root" rev-parse HEAD)"
    printf -- '- Archive commit: `%s`\n' "$archive_ref" >> "$run_dir/manifest.md"
  else
    archive_failed=1
    overall=1
    {
      printf -- '- Archive commit: `FAILED`\n'
      printf -- '- Notification: `archive-failed operational email will be attempted without artefact links`\n'
    } >> "$run_dir/manifest.md"
    printf 'Run archive failed; sending E2E archive-failed notification without artefact links.\n' >&2
  fi
else
  printf '\n## GitHub Archive\n\n- Archive: `skipped by XYZ_E2E_ARCHIVE=0`\n' >> "$run_dir/manifest.md"
fi

# XYZ-PILOT-207: standard post-completion SES notification. Per operator
# directive 2026-06-02 ("Persist for every future Tester Swarm E2E this
# notification as standard execution textbook"), every standard E2E swarm run
# sends one health-coloured SES email to the configured recipients after the
# run artefacts have been committed and pushed. If archive push fails, send an
# operational failure email without artefact links so operators still learn that
# the E2E run and archive path failed.
#
# Disable the notification with `XYZ_E2E_NOTIFY=0`. Recipient discovery is
# strict OPS_ONLY (Cognito group) with SES verification filtering; any
# `XYZ_E2E_NOTIFY_RECIPIENTS` override is ignored by design. Override the Python
# interpreter via `XYZ_E2E_NOTIFY_PYTHON` (defaults to the services/streamlit-insights venv
# which already has boto3).
if [ "${XYZ_E2E_NOTIFY:-1}" = "1" ]; then
  notify_py="${XYZ_E2E_NOTIFY_PYTHON:-$repo_root/services/streamlit-insights/.venv/bin/python}"
  if [ ! -x "$notify_py" ]; then
    notify_py="python3"
  fi
  notify_args=(
    "$script_dir/send_e2e_swarm_notification.py"
    --run-id "$run_id"
    --github-ref "$archive_ref"
    --sent-marker "$run_dir/notification.sent.json"
  )
  if [ "$archive_failed" -ne 0 ]; then
    notify_args+=(--archive-failed)
    if [ -n "${GITHUB_SERVER_URL:-}" ] && [ -n "${GITHUB_REPOSITORY:-}" ] && [ -n "${GITHUB_RUN_ID:-}" ]; then
      notify_args+=(--actions-url "${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}")
    fi
  fi
  {
    printf '\n## Notification\n\n'
    printf -- '- Triggered UTC: `%s`\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf -- '- Interpreter: `%s`\n' "$notify_py"
    printf -- '- AWS credential mode: `%s`\n' "${AWS_PROFILE:-env-credentials}"
    printf -- '- GitHub archive ref: `%s`\n' "$archive_ref"
    printf -- '- Archive failed mode: `%s`\n' "$archive_failed"
    printf -- '- Recipients: `OPS_ONLY` Cognito group only (SES-verified subset at send time)\n'
    printf -- '- Idempotency marker: `%s`\n' "$run_dir/notification.sent.json"
  } >> "$run_dir/manifest.md"
  # Notification step never fails the swarm exit code — the swarm's own
  # pass/fail status is the source of truth; notification is a courtesy.
  "$notify_py" "${notify_args[@]}" \
      > "$run_dir/notification.stdout" 2> "$run_dir/notification.stderr" \
      || printf 'Notification step exited non-zero — see notification.std{out,err} (swarm exit code preserved).\n' >&2

  # Persist the local idempotency marker and notification stdout/stderr after
  # the send. This is intentionally a second, non-notifying archive pass: the
  # email links already resolve to the pre-send run archive, and this commit
  # only records that the notification was attempted/sent.
  if [ "${XYZ_E2E_ARCHIVE_NOTIFICATION_MARKER:-1}" = "1" ] && [ "$archive_failed" -eq 0 ]; then
    set +e
    (
      cd "$repo_root"
      find "$run_rel" -type f -exec perl -0pi -e 's/[ \t\r]+\n/\n/g; s/\n+\z/\n/' {} +
      git add "$run_rel"
      git diff --cached --check -- "$run_rel"
      if git diff --cached --quiet -- "$run_rel"; then
        exit 0
      fi
      marker_commit_args=(
        -m "XYZ-PILOT-207: archive E2E swarm notification marker $run_id"
        -m "Archive notification stdout/stderr and sent marker after the single standard E2E swarm email was processed."
      )
      if [ -n "${XYZ_E2E_ARCHIVE_COAUTHOR:-}" ]; then
        marker_commit_args+=(-m "$XYZ_E2E_ARCHIVE_COAUTHOR")
      fi
      git commit -o "$run_rel" "${marker_commit_args[@]}"
      git_push_with_rebase_retry
    )
    marker_archive_rc=$?
    set -e
    if [ "$marker_archive_rc" -ne 0 ]; then
      printf 'Notification marker archive failed; notification result remains local in %s.\n' "$run_dir" >&2
    fi
  fi
fi

printf 'Run directory: %s\n' "$run_dir"
exit "$overall"
