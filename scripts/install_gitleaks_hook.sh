#!/usr/bin/env bash
# XYZ-PILOT-072 — install the pre-commit gitleaks hook locally (opt-in).
#
# Run once per workstation / per checkout:
#   ./scripts/install_gitleaks_hook.sh
#
# Re-run is idempotent (overwrites the prior install with the current
# tracked hook from scripts/hooks/).
#
# What it does: copies scripts/hooks/pre-commit-gitleaks to
# .git/hooks/pre-commit, marks it +x. The hook BLOCKS commits when
# gitleaks finds a credential-shaped string in the staged diff
# (.gitleaks.toml provides the allowlist for known false-positives).
#
# Coexistence: if you've also installed the XYZ-PILOT-168 commit-msg
# attribution hook (./scripts/install_attribution_hook.sh), the two
# hooks run on different git events (pre-commit vs commit-msg) and
# don't conflict.
#
# To uninstall: rm .git/hooks/pre-commit
#
# To temporarily skip the hook on an emergency commit:
#   git commit --no-verify    (CLAUDE.md forbids unless explicitly approved)

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

SRC="scripts/hooks/pre-commit-gitleaks"
DST=".git/hooks/pre-commit"

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
echo "  Mode:   BLOCKING (fails commit if gitleaks finds a credential)"
echo "  Config: .gitleaks.toml (repo root)"
echo "  Per:    XYZ-PILOT-072 / CI workflow at .github/workflows/secret-scan.yml"
echo ""

if ! command -v gitleaks >/dev/null 2>&1; then
    cat <<EOF
WARNING: gitleaks not on PATH yet. The hook will print an install hint
on each commit and exit 0 (non-blocking) until gitleaks is installed:

  macOS:    brew install gitleaks
  Windows:  scoop install gitleaks   (or winget install gitleaks.gitleaks)
  Linux:    https://github.com/gitleaks/gitleaks/releases/latest

Install gitleaks now, then any future commit gets full local secret-scan
coverage. CI gating (.github/workflows/secret-scan.yml) remains the
guaranteed gate independent of local hook state.

EOF
else
    GL_VERSION=$(gitleaks version 2>/dev/null || echo "unknown")
    echo "gitleaks detected on PATH: ${GL_VERSION}"
    echo "Test it: stage a fake credential, then 'git commit -m test' — the hook should block."
fi

echo ""
echo "Uninstall: rm ${DST}"
