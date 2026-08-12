#!/bin/zsh
# VS Code folder-open helper: keep a clean main checkout current.
# XYZ-PILOT-179: macOS developer-seat onboarding automation.
# XYZ-PILOT-179: append startup summaries instead of overwriting latest.md.
#
# This is intentionally conservative. It fast-forwards origin/main only when
# the checkout is clean and on main. Local developer work is never stashed,
# reset, or overwritten.

set -euo pipefail

RUN_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
RUN_RESULT="UNKNOWN"
RUN_MESSAGE="Started."
BEFORE_HEAD="unknown"
BEFORE_SUBJECT="unknown"
AFTER_HEAD="unknown"
AFTER_SUBJECT="unknown"
BRANCH="unknown"
LOG_FILE=""
LATEST_FILE=""

log() {
  print -r -- "[xyz-vscode-open-pull] $*"
}

warn() {
  print -r -- "[xyz-vscode-open-pull][WARN] $*" >&2
}

write_latest_summary() {
  [[ -n "$LATEST_FILE" ]] || return 0

  AFTER_HEAD="$(git rev-parse --short HEAD 2>/dev/null || print -r -- unknown)"
  AFTER_SUBJECT="$(git log -1 --format=%s 2>/dev/null || print -r -- unknown)"
  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || print -r -- unknown)"

  if [[ ! -f "$LATEST_FILE" ]]; then
    cat > "$LATEST_FILE" <<EOF
# XYZ VS Code Workspace Startup Git Update Log

This local, gitignored file appends one run entry per VS Code workspace-open
git update.

EOF
  fi

  cat >> "$LATEST_FILE" <<EOF
---

## Run $RUN_UTC

- Last run UTC: \`$RUN_UTC\`
- Result: \`$RUN_RESULT\`
- Message: $RUN_MESSAGE
- Repository: \`$repo_root\`
- Branch: \`$BRANCH\`
- Before: \`$BEFORE_HEAD\` — $BEFORE_SUBJECT
- After: \`$AFTER_HEAD\` — $AFTER_SUBJECT
- Full log: \`$LOG_FILE\`

## Current Status

\`\`\`text
$(git status --short --branch 2>/dev/null || true)
\`\`\`
EOF
}

finish() {
  local exit_code="$1"
  RUN_RESULT="$2"
  RUN_MESSAGE="$3"
  write_latest_summary
  log "Result: $RUN_RESULT — $RUN_MESSAGE"
  [[ -n "$LATEST_FILE" ]] && log "Latest startup summary: $LATEST_FILE"
  [[ -n "$LOG_FILE" ]] && log "Full startup log: $LOG_FILE"
  log "=== VS Code workspace startup git update end: $RUN_UTC ==="
  exit "$exit_code"
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
  warn "Not inside a git checkout; skipping origin/main update."
  exit 0
fi

cd "$repo_root"

log_dir="$repo_root/.vscode/startup-logs"
LOG_FILE="$log_dir/vscode-workspace-open-pull.log"
LATEST_FILE="$log_dir/latest.md"
mkdir -p "$log_dir"

exec > >(tee -a "$LOG_FILE") 2>&1

log "=== VS Code workspace startup git update start: $RUN_UTC ==="
log "Repository: $repo_root"
log "User: ${USER:-unknown}"
log "Host: $(hostname 2>/dev/null || print -r -- unknown)"

BEFORE_HEAD="$(git rev-parse --short HEAD 2>/dev/null || print -r -- unknown)"
BEFORE_SUBJECT="$(git log -1 --format=%s 2>/dev/null || print -r -- unknown)"
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || print -r -- unknown)"
log "Before: $BEFORE_HEAD — $BEFORE_SUBJECT"
log "Branch: $BRANCH"
log "Status before update:"
git status --short --branch || true

if ! git remote get-url origin >/dev/null 2>&1; then
  RUN_MESSAGE="No origin remote configured; skipping origin/main update."
  warn "$RUN_MESSAGE"
  finish 0 "SKIPPED" "$RUN_MESSAGE"
fi

if [[ "$BRANCH" != "main" ]]; then
  RUN_MESSAGE="Current branch is $BRANCH, not main; skipping origin/main update."
  warn "$RUN_MESSAGE"
  finish 0 "SKIPPED" "$RUN_MESSAGE"
fi

local_status="$(git status --porcelain)"
if [[ -n "$local_status" ]]; then
  RUN_MESSAGE="Local changes exist; skipping origin/main update."
  warn "$RUN_MESSAGE"
  git status --short
  finish 0 "SKIPPED" "$RUN_MESSAGE"
fi

log "Fetching origin/main."
if ! git fetch origin main; then
  RUN_MESSAGE="git fetch origin main failed; inspect the full startup log."
  warn "$RUN_MESSAGE"
  finish 1 "FAILED" "$RUN_MESSAGE"
fi

log "Fast-forwarding clean main checkout."
if git pull --ff-only origin main; then
  AFTER_HEAD="$(git rev-parse --short HEAD 2>/dev/null || print -r -- unknown)"
  if [[ "$AFTER_HEAD" == "$BEFORE_HEAD" ]]; then
    RUN_MESSAGE="Fetched origin/main; checkout was already current at $AFTER_HEAD."
    log "Workspace checkout was already current."
    log "Status after update:"
    git status --short --branch || true
    finish 0 "CURRENT" "$RUN_MESSAGE"
  fi

  RUN_MESSAGE="Fast-forwarded clean main checkout from $BEFORE_HEAD to $AFTER_HEAD."
  log "Workspace checkout is current."
  log "Status after update:"
  git status --short --branch || true
  finish 0 "UPDATED" "$RUN_MESSAGE"
else
  RUN_MESSAGE="git pull --ff-only origin main failed; inspect the full startup log."
  warn "$RUN_MESSAGE"
  finish 1 "FAILED" "$RUN_MESSAGE"
fi
