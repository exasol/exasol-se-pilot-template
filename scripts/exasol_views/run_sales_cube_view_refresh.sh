#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_FILE="${SCRIPT_DIR}/recreate_sales_cube_views.sql"

profile=""
dry_run=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/exasol_views/run_sales_cube_view_refresh.sh --profile <exapump-profile> [--dry-run]

Purpose:
  Recreate only native <PILOT_SCHEMA> views with CREATE OR REPLACE VIEW.

This script does not touch ESL_* semantic-layer schemas, virtual schemas, UDFs,
config tables, grants, physical tables, or MV_* snapshot tables.
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

if [[ ! -f "$SQL_FILE" ]]; then
  echo "Missing SQL file: $SQL_FILE" >&2
  exit 1
fi

if [[ "$dry_run" -eq 1 ]]; then
  sed -n '1,80p' "$SQL_FILE"
  printf '\n[DRY RUN] SQL file: %s\n' "$SQL_FILE"
  printf '[DRY RUN] Would run: exapump sql --profile %s - < %s\n' "$profile" "$SQL_FILE"
  exit 0
fi

exapump sql --profile "$profile" - < "$SQL_FILE"
