#!/usr/bin/env bash
# XYZ-PILOT-168 — install the commit-msg attribution hook locally (opt-in).
#
# Run once per workstation / per checkout:
#   ./scripts/install_attribution_hook.sh
#
# Re-run is idempotent (overwrites the prior install with the current
# tracked hook from scripts/hooks/).
#
# What it does: copies scripts/hooks/commit-msg-attribution to
# .git/hooks/commit-msg, marks it +x. The hook is non-blocking — warns
# when a Claude Code session is detected (env CLAUDECODE=1) and the
# commit message lacks a Co-Authored-By: trailer. Never fails commits.
#
# To uninstall: rm .git/hooks/commit-msg

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

SRC="scripts/hooks/commit-msg-attribution"
DST=".git/hooks/commit-msg"

if [[ ! -f "$SRC" ]]; then
    echo "ERROR: source hook ${SRC} not found in this checkout." >&2
    echo "       Are you in the <PROJECT_SLUG> repo root?" >&2
    exit 2
fi

if [[ ! -d .git ]]; then
    echo "ERROR: .git directory not found — this script must run inside a git checkout." >&2
    exit 3
fi

if [[ -f "$DST" ]] && ! cmp -s "$SRC" "$DST"; then
    echo "Note: existing ${DST} differs from ${SRC}. Overwriting (the tracked source wins)."
fi

mkdir -p .git/hooks
cp -f "$SRC" "$DST"
chmod +x "$DST"

echo "Installed ${DST}"
echo "  Source: ${SRC}"
echo "  Mode:   non-blocking (warns only, never fails a commit)"
echo "  Scope:  warns when CLAUDECODE=1 is set and trailer is missing"
echo "  Per:    XYZ-PILOT-168 / CLAUDE.md Rule #11"
echo ""
echo "Test it: try a commit inside a Claude Code session without the trailer — you'll see the heads-up on stderr."
echo "Uninstall: rm ${DST}"
