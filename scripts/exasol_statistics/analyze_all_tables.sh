#!/usr/bin/env bash
# Refresh Exasol optimizer statistics for physical user tables.
#
# Exasol Personal accepted syntax in the <PILOT_NAME> target:
#   ANALYZE TABLE "SCHEMA"."TABLE"
#
# The rejected forms during XYZ-PILOT-217 triage were:
#   UPDATE STATISTICS <table>
#   UPDATE STATISTICS SET <table>
#   ALTER TABLE <table> UPDATE STATISTICS
#
# Views do not own table statistics. Analyze the physical tables below them.

set -euo pipefail

profile=""
dry_run=0
schemas=()
exclude_schemas=("ESL_POC_SEMANTIC_LAYER_VIRTUAL_SCHEMA")

usage() {
    cat <<'USAGE'
Usage:
  scripts/exasol_statistics/analyze_all_tables.sh --profile <exapump-profile> [options]

Options:
  --profile <name>          Required exapump profile with ANALYZE privilege.
  --schema <schema>         Restrict to one schema. Repeat for multiple schemas.
  --exclude-schema <schema> Exclude a schema. Repeat for multiple schemas.
                            Default exclusion: ESL_POC_SEMANTIC_LAYER_VIRTUAL_SCHEMA.
  --dry-run                 Print ANALYZE TABLE statements without executing.
  -h, --help                Show this help.

Examples:
  scripts/exasol_statistics/analyze_all_tables.sh --profile xyz-pilot-target-sys
  scripts/exasol_statistics/analyze_all_tables.sh --profile xyz-pilot-target-sys --schema <PILOT_SCHEMA>
  scripts/exasol_statistics/analyze_all_tables.sh --profile xyz-pilot-target-sys --schema <PILOT_SCHEMA> --dry-run
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)
            profile="${2:-}"
            shift 2
            ;;
        --schema)
            schemas+=("${2:-}")
            shift 2
            ;;
        --exclude-schema)
            exclude_schemas+=("${2:-}")
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
            echo "ERROR: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "$profile" ]]; then
    echo "ERROR: --profile is required" >&2
    usage >&2
    exit 2
fi

quote_sql_literal() {
    local value="$1"
    value="${value//\'/\'\'}"
    printf "'%s'" "$value"
}

quote_ident() {
    local value="$1"
    value="${value//\"/\"\"}"
    printf '"%s"' "$value"
}

csv_to_sql_list() {
    local first=1
    local value
    for value in "$@"; do
        [[ -n "$value" ]] || continue
        if [[ "$first" -eq 0 ]]; then
            printf ", "
        fi
        quote_sql_literal "$value"
        first=0
    done
}

where_sql="TABLE_SCHEMA NOT LIKE 'SYS%'"
if [[ "${#schemas[@]}" -gt 0 ]]; then
    where_sql+=" AND TABLE_SCHEMA IN ($(csv_to_sql_list "${schemas[@]}"))"
fi
if [[ "${#exclude_schemas[@]}" -gt 0 ]]; then
    where_sql+=" AND TABLE_SCHEMA NOT IN ($(csv_to_sql_list "${exclude_schemas[@]}"))"
fi

catalog_sql="SELECT TABLE_SCHEMA || '|' || TABLE_NAME AS TARGET FROM EXA_ALL_TABLES WHERE ${where_sql} ORDER BY TABLE_SCHEMA, TABLE_NAME"
catalog_output="$(exapump sql --profile "$profile" "$catalog_sql")"

targets=()
while IFS= read -r line; do
    [[ "$line" == *"|"* ]] || continue
    [[ "$line" != TARGET ]] || continue
    [[ "$line" != \[* ]] || continue
    [[ "$line" != *"statement executed"* ]] || continue
    targets+=("$line")
done <<< "$catalog_output"

if [[ "${#targets[@]}" -eq 0 ]]; then
    echo "No physical user tables discovered for profile '$profile'." >&2
    exit 1
fi

echo "ANALYZE sweep target count: ${#targets[@]}"
echo "Profile: $profile"
echo "Started UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

failures=0
index=0
for target in "${targets[@]}"; do
    schema="${target%%|*}"
    table="${target#*|}"
    index=$((index + 1))
    statement="ANALYZE TABLE $(quote_ident "$schema").$(quote_ident "$table")"

    if [[ "$dry_run" -eq 1 ]]; then
        printf '[%d] %s\n' "$index" "$statement"
        continue
    fi

    started=$(date +%s)
    printf '[%d] %s\n' "$index" "$statement"
    if exapump sql --profile "$profile" "$statement"; then
        elapsed=$(( $(date +%s) - started ))
        printf '[%d] OK %s.%s %ss\n' "$index" "$schema" "$table" "$elapsed"
    else
        elapsed=$(( $(date +%s) - started ))
        failures=$((failures + 1))
        printf '[%d] FAIL %s.%s %ss\n' "$index" "$schema" "$table" "$elapsed" >&2
    fi
done

echo "Finished UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Tables: ${#targets[@]}"
echo "Failures: $failures"
exit "$failures"
