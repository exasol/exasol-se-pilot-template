#!/usr/bin/env bash
# XYZ-PILOT-071 — snapshot the live tracker into docs/archive/ BEFORE editing.
#
# Rationale: XYZ_PILOT_ISSUE_TRACKER.md is concurrently written by three
# different actors:
#   - humans (via PRs / direct commits)
#   - the chatbot (`_log_to_tracker` writes new XYZ-PILOT-NNN tickets via the
#     GitHub Contents API)
#   - the daily-status Lambda (`_trim_tracker_top_table` prunes expired
#     Resolved rows + refreshes the "Today is …" visibility-rule sentence)
#
# GitHub's SHA-based optimistic concurrency protects against blind overwrites
# (the PUT 409s if the file changed between fetch and PUT), but a sloppy
# `git pull --rebase` conflict resolution on a human's local branch can
# silently drop entries from one side of the merge. The archive snapshot
# gives us a one-click recovery copy: if anything is lost after a rebase
# we can `git diff archive/...md docs/XYZ_PILOT_ISSUE_TRACKER.md` to spot it.
#
# Usage:
#   ./scripts/archive_tracker.sh                # archive current main + your local
#   ./scripts/archive_tracker.sh "my-edit-reason"  # custom suffix
#
# Output: docs/archive/XYZ_PILOT_ISSUE_TRACKER_<UTC-timestamp>_<suffix>.md

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

SUFFIX="${1:-pre-edit}"
TS=$(date -u +"%Y-%m-%dT%H%M")
ARCHIVE="docs/archive/XYZ_PILOT_ISSUE_TRACKER_${TS}_${SUFFIX}.md"

mkdir -p docs/archive

# Snapshot what's on origin/main, not the local file. The local file may
# already contain pending edits; we want the "before" state as visible to
# the rest of the world.
git fetch origin main --quiet 2>/dev/null || true

if git show origin/main:docs/XYZ_PILOT_ISSUE_TRACKER.md > "$ARCHIVE" 2>/dev/null; then
    LINES=$(wc -l < "$ARCHIVE")
    BYTES=$(wc -c < "$ARCHIVE" | tr -d ' ')
    echo "Archived origin/main tracker → ${ARCHIVE} (${LINES} lines, ${BYTES} bytes)"
else
    # Fallback: archive the working-tree copy if origin/main isn't reachable
    cp -f docs/XYZ_PILOT_ISSUE_TRACKER.md "$ARCHIVE"
    echo "Archived working-tree tracker (origin/main unreachable) → ${ARCHIVE}"
fi
