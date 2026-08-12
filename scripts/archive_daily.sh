#!/usr/bin/env bash
# Archive the current docs/00-live-status/XYZ_PILOT_DAILY_STATUS.md to docs/archive/ keyed by
# the date in its "Report date" header, before the file is overwritten with
# the new day's content. Idempotent: re-running on the same date is a no-op.
#
# Usage:   scripts/archive_daily.sh
# Output:  copies XYZ_PILOT_DAILY_STATUS.md → docs/archive/xyz_PILOT_DAILY_STATUS_YYYY-MM-DD.md
#          stages the new archive file with `git add`
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
DAILY="$REPO_ROOT/docs/00-live-status/XYZ_PILOT_DAILY_STATUS.md"
ARCHIVE_DIR="$REPO_ROOT/docs/archive"

[ -f "$DAILY" ]       || { echo "ERROR: $DAILY not found"       >&2; exit 1; }
[ -d "$ARCHIVE_DIR" ] || { echo "ERROR: $ARCHIVE_DIR not found" >&2; exit 1; }

DATE=$(grep -m1 "Report date" "$DAILY" | grep -oE '20[0-9]{2}-[0-9]{2}-[0-9]{2}') || true
if [ -z "${DATE:-}" ]; then
    echo "ERROR: could not parse 'Report date: YYYY-MM-DD' from $DAILY" >&2
    exit 1
fi

TARGET="$ARCHIVE_DIR/xyz_PILOT_DAILY_STATUS_${DATE}.md"
REL_TARGET="docs/archive/xyz_PILOT_DAILY_STATUS_${DATE}.md"

if [ -e "$TARGET" ]; then
    echo "Already archived: $REL_TARGET (existing copy is preserved as-is)"
    exit 0
fi

cp "$DAILY" "$TARGET"

# Verify the archive landed (catches silent FS errors / partial writes).
SRC_SIZE=$(wc -c < "$DAILY")
DST_SIZE=$(wc -c < "$TARGET")
if [ "$SRC_SIZE" != "$DST_SIZE" ]; then
    echo "ERROR: archive copy size mismatch (src=$SRC_SIZE dst=$DST_SIZE)" >&2
    exit 1
fi

# Make the archive copy read-only at the FS layer so it can't be
# overwritten or wiped by accident. To edit an archived daily later,
# `chmod 644 docs/archive/<file>` first, edit, then chmod back to 444.
chmod 444 "$TARGET"

# Re-apply read-only to any existing archives in case someone earlier
# committed them with default mode. Belt + braces; cheap.
chmod 444 "$ARCHIVE_DIR"/xyz_PILOT_DAILY_STATUS_*.md 2>/dev/null || true

git -C "$REPO_ROOT" add "$TARGET"

echo "Archived day $DATE -> $REL_TARGET (staged for commit, read-only on disk)"
echo "Next: overwrite docs/00-live-status/XYZ_PILOT_DAILY_STATUS.md with today's content, then commit both."
