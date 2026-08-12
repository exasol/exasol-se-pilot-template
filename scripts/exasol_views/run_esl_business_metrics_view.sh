#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_FILE="${SCRIPT_DIR}/create_esl_business_metrics_view.sql"

profile=""
dry_run=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/exasol_views/run_esl_business_metrics_view.sh --profile <exapump-profile> [--dry-run]

Purpose:
  Create or replace <PILOT_SCHEMA>.MV_BUSINESS_METRICS and
  <PILOT_SCHEMA>.V_BUSINESS_METRICS, the XYZ-owned helper measure surface on
  top of <SEMANTIC_LAYER_SCHEMA>.BASE_LAYER.

This script does not modify ESL_* schemas, UDFs, config tables, generated
semantic-layer objects, or physical <PILOT_SCHEMA> tables.
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
  sed -n '1,100p' "$SQL_FILE"
  printf '\n[DRY RUN] SQL file: %s\n' "$SQL_FILE"
  printf '[DRY RUN] Would run: exapump sql --profile %s - < %s\n' "$profile" "$SQL_FILE"
  exit 0
fi

exapump sql --profile "$profile" - < "$SQL_FILE"
