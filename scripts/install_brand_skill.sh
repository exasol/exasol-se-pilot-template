#!/usr/bin/env bash
#
# install_brand_skill.sh — fetch the Exasol branded-document skill into this
# repository's ignored skill path.
# ----------------------------------------------------------------------------
# The skill carries the official Exasol letterhead, the logo extracts and the
# brand slide template. Those are production originals with no distribution
# licence attached, so they live in an INTERNAL repository that only members of
# the Exasol GitHub organization can read, and they are never committed here.
#
# This script therefore installs into .claude/skills/, which .gitignore already
# excludes. It deliberately does NOT install into .agents/skills/, which is
# tracked: a brand asset committed there would be published with the repository.
#
#   ./scripts/install_brand_skill.sh            # install or update
#   ./scripts/install_brand_skill.sh --check    # report state, change nothing
#
# A non-member gets a clean "not accessible" message and exit code 1. That is
# the expected outcome, not a failure of this repository: the pilot generator,
# the deploy path and every test run work without the skill. Only document
# rendering in Exasol house style needs it.
set -euo pipefail

REPO="${BRAND_SKILL_REPO:-exasol/exasol-branded-document}"
DEST="${BRAND_SKILL_DEST:-.claude/skills/exasol-branded-document}"
MODE="${1:-}"

cd "$(dirname "$0")/.."

if [[ "$MODE" == "--check" ]]; then
  if [[ -f "$DEST/scripts/exasol_render.py" ]]; then
    echo "installed: $DEST ($(git -C "$DEST" log -1 --format=%h 2>/dev/null || echo 'no git metadata'))"
    exit 0
  fi
  echo "not installed: $DEST"
  exit 1
fi

# .gitignore must cover the destination, or an install would stage brand assets
# into a public repository on the next broad 'git add'.
if ! git check-ignore -q "$DEST"; then
  echo "ERROR: $DEST is not ignored by .gitignore - refusing to install." >&2
  echo "       Add it before retrying; brand assets must never be tracked here." >&2
  exit 2
fi

command -v gh >/dev/null 2>&1 || {
  echo "ERROR: the GitHub CLI (gh) is required. See https://cli.github.com/" >&2
  exit 2; }

if ! gh repo view "$REPO" >/dev/null 2>&1; then
  cat >&2 <<MSG
ERROR: $REPO is not accessible with your current GitHub login.

  The branded-document skill is internal to the Exasol organization. If you are
  an Exasol member, check which account is active and that SSO is authorised:

      gh auth status
      gh auth switch

  If you are not a member, continue without it. Nothing else in this repository
  depends on the skill; only Exasol house-style PDF/DOCX rendering does.
MSG
  exit 1
fi

if [[ -d "$DEST/.git" ]]; then
  echo "updating $DEST"
  git -C "$DEST" pull --ff-only
else
  echo "cloning $REPO -> $DEST"
  mkdir -p "$(dirname "$DEST")"
  gh repo clone "$REPO" "$DEST"
fi

echo
echo "installed. Verify with:"
echo "    python3 $DEST/scripts/test_identity.py"
