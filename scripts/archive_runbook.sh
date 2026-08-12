#!/usr/bin/env bash
# XYZ-PILOT-167 — snapshot a runbook-class doc into docs/archive/ BEFORE a
# structural rewrite, phase delete, header change, or any change that
# would render later git-history lookup awkward.
#
# Rationale: docs/archive/ is the durable point-in-time snapshot home for
# runbook-class documents (developer runbooks, deployment runbooks, MCP
# client guides, PILOT runbooks, GitHub runbooks, credentials doc). git
# history captures every line change, but reviewers wanting "the runbook
# as it stood on 2026-05-30 before the Section 0 restructure" want a
# single discoverable file under docs/archive/, not a SHA they have to
# look up in git log.
#
# Per CLAUDE.md Rule #10, run this BEFORE:
#   - structural rewrites (section reorders, multi-section deletes,
#     TOC reshapes that change anchor slugs)
#   - phase / appendix / dedicated subsection deletions
#   - Audience: / Outcome: / Total wall time: / Cost: header changes
#   - any change that would invalidate cross-doc links from other files
#
# Incremental edits (typo, single-paragraph clarification, link update,
# new code block) do NOT require an archive — git history suffices.
#
# Tracker archives are NOT this script's responsibility. The tracker
# (docs/XYZ_PILOT_ISSUE_TRACKER.md) has its own dedicated archive script
# (./scripts/archive_tracker.sh) per CLAUDE.md Rule #5(a). This script
# refuses the tracker path with a pointer to Rule #5(a).
#
# Usage:
#   ./scripts/archive_runbook.sh <relative-doc-path> "<reason>"
#
# Examples:
#   ./scripts/archive_runbook.sh docs/30-runbooks/operations/XYZ_PILOT_DEVELOPER_RUNBOOK.md "pre-section-0-restructure"
#   ./scripts/archive_runbook.sh docs/30-runbooks/deployment/XYZ_PILOT_EXASOL_PILOT_RUNBOOK.md "phase-7-rewrite"
#   ./scripts/archive_runbook.sh docs/30-runbooks/mcp/XYZ_MCP_CLIENT_GUIDE.md "pre-windows-variations-section"
#
# Output: docs/archive/<basename-without-md>_<UTC-timestamp>_<slugified-reason>.md

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <relative-doc-path> \"<reason>\"" >&2
    echo "Example: $0 docs/30-runbooks/operations/XYZ_PILOT_DEVELOPER_RUNBOOK.md \"pre-section-0-restructure\"" >&2
    exit 2
fi

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

DOC_PATH="$1"
REASON="$2"

# Refuse the tracker — it has its own dedicated archive flow.
if [[ "$DOC_PATH" == "docs/XYZ_PILOT_ISSUE_TRACKER.md" ]]; then
    echo "ERROR: ${DOC_PATH} is the tracker; use ./scripts/archive_tracker.sh per CLAUDE.md Rule #5(a)." >&2
    exit 3
fi

# Refuse non-Markdown.
if [[ "$DOC_PATH" != *.md ]]; then
    echo "ERROR: ${DOC_PATH} must end in .md (this script archives Markdown runbooks only)." >&2
    exit 4
fi

# Refuse paths outside docs/.
if [[ "$DOC_PATH" != docs/* ]]; then
    echo "ERROR: ${DOC_PATH} must be under docs/ (this script archives docs/-rooted runbooks only)." >&2
    exit 5
fi

BASENAME=$(basename "$DOC_PATH" .md)

# Slugify reason: lowercase, replace non-alphanumeric with hyphen,
# collapse repeats, trim leading/trailing hyphens.
REASON_SLUG=$(printf '%s' "$REASON" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9]+/-/g; s/-+/-/g; s/^-+//; s/-+$//')

if [[ -z "$REASON_SLUG" ]]; then
    echo "ERROR: reason must contain at least one alphanumeric character." >&2
    exit 6
fi

TS=$(date -u +"%Y-%m-%dT%H%M")
ARCHIVE="docs/archive/${BASENAME}_${TS}_${REASON_SLUG}.md"

mkdir -p docs/archive

# Snapshot from origin/main, not the local working tree (which may have
# pending edits — we want the "before" state as visible to the rest of
# the world). Mirrors archive_tracker.sh's behaviour.
git fetch origin main --quiet 2>/dev/null || true

if git show "origin/main:${DOC_PATH}" > "$ARCHIVE" 2>/dev/null; then
    LINES=$(wc -l < "$ARCHIVE" | tr -d ' ')
    BYTES=$(wc -c < "$ARCHIVE" | tr -d ' ')
    echo "Archived origin/main:${DOC_PATH} → ${ARCHIVE} (${LINES} lines, ${BYTES} bytes)"
elif [[ -f "$DOC_PATH" ]]; then
    cp -f "$DOC_PATH" "$ARCHIVE"
    LINES=$(wc -l < "$ARCHIVE" | tr -d ' ')
    BYTES=$(wc -c < "$ARCHIVE" | tr -d ' ')
    echo "Archived working-tree ${DOC_PATH} (origin/main unreachable) → ${ARCHIVE} (${LINES} lines, ${BYTES} bytes)"
else
    rm -f "$ARCHIVE"
    echo "ERROR: ${DOC_PATH} not found on origin/main and not in working tree." >&2
    exit 7
fi
