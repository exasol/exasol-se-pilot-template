#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_FILE="${SCRIPT_DIR}/create_esl_base_layer_minimal_view.sql"

profile=""
dry_run=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/exasol_views/run_esl_base_layer_minimal_view.sh --profile <exapump-profile> [--dry-run]

Purpose:
  Create or replace <SEMANTIC_LAYER_SCHEMA>.BASE_LAYER_MINIMAL and grant SELECT
  for XYZ_BIZ/XYZ_DASH.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      profile="${2:-}"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$profile" ]]; then
  echo "Missing required --profile" >&2
  usage >&2
  exit 2
fi

if [[ "$dry_run" -eq 1 ]]; then
  sed -n '1,80p' "$SQL_FILE"
  printf '\n[DRY RUN] SQL file: %s\n' "$SQL_FILE"
  printf '[DRY RUN] Would run: exapump sql --profile %s - < %s\n' "$profile" "$SQL_FILE"
  exit 0
fi

exapump sql --profile "$profile" - < "$SQL_FILE"
