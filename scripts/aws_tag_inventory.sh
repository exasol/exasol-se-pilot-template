#!/usr/bin/env bash
#
# aws_tag_inventory.sh — Exasol AWS Resource Tagging Policy inventory & compliance reporter
# ---------------------------------------------------------------------------------------
# Identifies the taggable AWS resources in a given AWS profile / account, classifies each
# against the Exasol `exa:*` tagging policy, distinguishes a target project (default the
# <PILOT_NAME> deployment, `exa:Project=XYZPILOT`) from everything else, prints a table to the
# prompt, and exports timestamped CSV, JSON, and Markdown reports.
#
# Policy reference: Exasol IT "AWS Resource Tagging Policy" (FinOps; exa:* tag schema).
# Mandatory exa tags: Department, Environment, Workload, CostCenter, Owner, Project
# (DataClassification is mandatory for sensitive-data resources).
#
# Usage:
#   scripts/aws_tag_inventory.sh --profile default [--region <AWS_REGION>|all]
#       [--project XYZPILOT] [--project-label XYZ-Pilot]
#       [--department SE] [--environment staging] [--owner <OPERATOR_USER>]
#       [--customer XYZ] [--cost-center 20060] [--data-classification confidential]
#       [--workload personal-edition] [--managed-by opentofu]
#       [--include-iam-role ROLE_NAME] [--missing-mandatory]
#       [--format table|csv|json|md|all] [--out-dir DIR]
#
# Filter flags (--department, --environment, …) are AND-combined and restrict the report to
# resources whose corresponding exa:* tag matches the given value. With no filter flags the
# full account inventory is reported. --missing-mandatory restricts to resources that are
# missing one or more mandatory exa tags (compliance gap view).
#
# Requires: awscli v2, python3 (stdlib only). Read-only — never mutates tags or resources.
#
set -euo pipefail

# Requires bash >= 4 (associative arrays, mapfile). macOS ships 3.2 at /bin/bash;
# `/usr/bin/env bash` picks up a newer Homebrew bash when present.
if [ -z "${BASH_VERSINFO:-}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
  echo "ERROR: bash >= 4 required (found ${BASH_VERSION:-unknown}). On macOS: 'brew install bash'." >&2
  exit 2
fi

PROFILE=""
REGION="<AWS_REGION>"
TARGET_PROJECT="XYZPILOT"
PROJECT_LABEL=""
OUT_DIR="docs/60-evidence-inventory/aws-tagging/inventory"
FORMAT="all"
MISSING_ONLY="0"
declare -A FILTERS   # exa-tag-key (without prefix) -> required value
INCLUDE_IAM_ROLES=()

die() { echo "ERROR: $*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --profile)              PROFILE="$2"; shift 2;;
    --region)               REGION="$2"; shift 2;;
    --project)              TARGET_PROJECT="$2"; FILTERS[Project]="$2"; shift 2;;
    --project-label)        PROJECT_LABEL="$2"; shift 2;;
    --department)           FILTERS[Department]="$2"; shift 2;;
    --environment)          FILTERS[Environment]="$2"; shift 2;;
    --workload)             FILTERS[Workload]="$2"; shift 2;;
    --cost-center)          FILTERS[CostCenter]="$2"; shift 2;;
    --owner)                FILTERS[Owner]="$2"; shift 2;;
    --customer)             FILTERS[customer]="$2"; shift 2;;
    --data-classification)  FILTERS[DataClassification]="$2"; shift 2;;
    --managed-by)           FILTERS[ManagedBy]="$2"; shift 2;;
    --include-iam-role)     INCLUDE_IAM_ROLES+=("$2"); shift 2;;
    --missing-mandatory)    MISSING_ONLY="1"; shift;;
    --format)               FORMAT="$2"; shift 2;;
    --out-dir)              OUT_DIR="$2"; shift 2;;
    -h|--help)              sed -n '2,40p' "$0"; exit 0;;
    *) die "unknown flag: $1 (try --help)";;
  esac
done

case "$FORMAT" in
  table|csv|json|md|markdown|all) ;;
  *) die "--format must be one of: table, csv, json, md, all";;
esac

[ -n "$PROFILE" ] || die "--profile is required (e.g. --profile default)"
command -v aws  >/dev/null 2>&1 || die "awscli not found on PATH"
command -v python3 >/dev/null 2>&1 || die "python3 not found on PATH"

AWS=(aws --profile "$PROFILE")

# Confirm identity / capture account id.
ACCOUNT_ID="$("${AWS[@]}" sts get-caller-identity --query Account --output text)" \
  || die "could not resolve account for profile '$PROFILE' (check AWS_PROFILE / credentials)"

# Resolve the region list to sweep.
if [ "$REGION" = "all" ]; then
  mapfile -t REGIONS < <("${AWS[@]}" ec2 describe-regions --query 'Regions[].RegionName' --output text | tr '\t' '\n' | sort)
else
  IFS=',' read -r -a REGIONS <<< "$REGION"
fi

if [ -z "$PROJECT_LABEL" ]; then
  case "$TARGET_PROJECT" in
    XYZPILOT) PROJECT_LABEL="XYZ-Pilot";;
    EXASOLE2EWORKSHOP) PROJECT_LABEL="E2E-Workshop";;
    EXASOLAWSMONSAASCALC) PROJECT_LABEL="AWS-Cost-Mon-SaaS-Calc";;
    *) PROJECT_LABEL="$TARGET_PROJECT";;
  esac
fi

echo ">> Account     : $ACCOUNT_ID  (profile: $PROFILE)" >&2
echo ">> Regions     : ${REGIONS[*]}" >&2
echo ">> Project tag : exa:Project=$TARGET_PROJECT" >&2
echo ">> Project name: $PROJECT_LABEL" >&2
[ "${FILTERS[*]+x}" = x ] && echo ">> Filters     : $(for k in "${!FILTERS[@]}"; do printf 'exa:%s=%s ' "$k" "${FILTERS[$k]}"; done)" >&2
[ "$MISSING_ONLY" = "1" ] && echo ">> Mode        : missing-mandatory only" >&2

# Gather raw resource+tag mappings (paginated) across all requested regions into one JSON array.
RAW_JSON="$(mktemp)"; REGION_JSON="$(mktemp)"; trap 'rm -f "$RAW_JSON" "$REGION_JSON"' EXIT
echo "[]" > "$RAW_JSON"
for r in "${REGIONS[@]}"; do
  echo ">> scanning $r ..." >&2
  "${AWS[@]}" resourcegroupstaggingapi get-resources --region "$r" \
      --query 'ResourceTagMappingList[].{ARN:ResourceARN,Tags:Tags}' \
      --output json > "$REGION_JSON" || { echo "WARN: scan failed for $r, skipping" >&2; continue; }
  python3 - "$RAW_JSON" "$r" "$REGION_JSON" <<'MERGE'
import json, sys
acc_path, region, region_path = sys.argv[1], sys.argv[2], sys.argv[3]
data = open(region_path).read().strip()
new = json.loads(data) if data else []
for item in new:
    item["Region"] = region
acc = json.load(open(acc_path))
acc.extend(new)
json.dump(acc, open(acc_path, "w"))
MERGE
done

for role_name in "${INCLUDE_IAM_ROLES[@]}"; do
  echo ">> including IAM role $role_name ..." >&2
  ROLE_ARN="$("${AWS[@]}" iam get-role --role-name "$role_name" --query Role.Arn --output text)" \
    || { echo "WARN: IAM role lookup failed for $role_name, skipping" >&2; continue; }
  "${AWS[@]}" iam list-role-tags --role-name "$role_name" --query Tags --output json > "$REGION_JSON" \
    || { echo "WARN: IAM role tag lookup failed for $role_name, skipping" >&2; continue; }
  python3 - "$RAW_JSON" "$ROLE_ARN" "$REGION_JSON" <<'MERGE_IAM'
import json, sys
acc_path, arn, tags_path = sys.argv[1], sys.argv[2], sys.argv[3]
tags = json.load(open(tags_path))
acc = json.load(open(acc_path))
if not any(item.get("ARN") == arn for item in acc):
    acc.append({"ARN": arn, "Region": "global", "Tags": tags})
json.dump(acc, open(acc_path, "w"))
MERGE_IAM
done

mkdir -p "$OUT_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BASENAME="aws_tag_inventory_${ACCOUNT_ID}_${TS}"

# Classify / filter / render via python3 (stdlib only).
# Pass active filters to python as newline-delimited key=value pairs (exa-key without prefix).
FILTER_PAIRS=""
if [ "${FILTERS[*]+x}" = x ]; then
  for k in "${!FILTERS[@]}"; do FILTER_PAIRS+="${k}=${FILTERS[$k]}"$'\n'; done
fi

ACCOUNT_ID="$ACCOUNT_ID" TARGET_PROJECT="$TARGET_PROJECT" PROJECT_LABEL="$PROJECT_LABEL" MISSING_ONLY="$MISSING_ONLY" \
FORMAT="$FORMAT" OUT_DIR="$OUT_DIR" BASENAME="$BASENAME" TS="$TS" FILTER_PAIRS="$FILTER_PAIRS" \
python3 - "$RAW_JSON" <<'PY'
import json, os, sys, csv, re

raw = json.load(open(sys.argv[1]))
account      = os.environ["ACCOUNT_ID"]
target       = os.environ["TARGET_PROJECT"]
project_label_env = os.environ.get("PROJECT_LABEL", "")
missing_only = os.environ["MISSING_ONLY"] == "1"
fmt          = os.environ["FORMAT"]
out_dir      = os.environ["OUT_DIR"]
base         = os.environ["BASENAME"]
ts           = os.environ["TS"]
filters      = {}
for line in os.environ.get("FILTER_PAIRS","").splitlines():
    if "=" in line:
        k,v = line.split("=",1); filters[k]=v

MANDATORY = ["Department","Environment","Workload","CostCenter","Owner","Project"]

PROJECT_LABELS = {
    "XYZPILOT": "XYZ-Pilot",
    "EXASOLE2EWORKSHOP": "E2E-Workshop",
    "EXASOLAWSMONSAASCALC": "AWS-Cost-Mon-SaaS-Calc",
}

def label_for_project(project):
    if not project:
        return ""
    if project == target and project_label_env:
        return project_label_env
    return PROJECT_LABELS.get(project, project)

target_label = label_for_project(target)
not_target_label = f"Not-{target_label}"

DEPLOYMENT_TAG_KEYS = (
    "Deployment",
    "DeploymentId",
    "deployment",
    "deployment_id",
    "exa:Deployment",
    "exa:DeploymentId",
)

def deployment_descriptor(tags, arn, resource_name):
    for key in DEPLOYMENT_TAG_KEYS:
        value = tags.get(key)
        if value:
            return value

    candidates = [tags.get("Name", ""), resource_name, arn]
    candidates.extend(tags.values())
    for value in candidates:
        match = re.search(r"\bexasol-[0-9a-fA-F]{8}\b", str(value))
        if match:
            return match.group(0)
    return ""

def parse_arn(arn, region):
    # arn:partition:service:region:account:resourcetype/id  (or :id, or rt:id)
    parts = arn.split(":", 5)
    service = parts[2] if len(parts) > 2 else ""
    region = parts[3] or region
    tail = parts[5] if len(parts) > 5 else (parts[-1] if parts else arn)
    if "/" in tail:
        rtype, _, rid = tail.partition("/")
    elif ":" in tail:
        rtype, _, rid = tail.partition(":")
    else:
        rtype, rid = "", tail
    return service, region, rtype.rstrip(":"), (rid or rtype)

rows = []
for item in raw:
    arn = item["ARN"]
    region = item.get("Region","")
    tags = { t["Key"]: t["Value"] for t in (item.get("Tags") or []) }
    exa  = { k[4:]: v for k, v in tags.items() if k.startswith("exa:") }
    service, region, rtype, rid = parse_arn(arn, region)

    # filter (AND); filter keys are exa-tag keys without the prefix
    if filters and any(exa.get(k) != v for k, v in filters.items()):
        continue

    missing = [m for m in MANDATORY if m not in exa]
    if missing_only and not missing:
        continue

    project_label = label_for_project(exa.get("Project", ""))
    if exa.get("Project") == target:
        classification = target_label
    elif project_label:
        classification = project_label
    elif exa:
        classification = f"{not_target_label} (other-exa-tagged)"
    else:
        classification = f"{not_target_label} (untagged)"

    rows.append({
        "Classification": classification,
        "Service": service,
        "Region": region,
        "ResourceType": rtype,
        "ResourceName": rid,
        "DeploymentDescriptor": deployment_descriptor(tags, arn, rid),
        "exa:Project": exa.get("Project",""),
        "exa:Environment": exa.get("Environment",""),
        "exa:Department": exa.get("Department",""),
        "exa:Workload": exa.get("Workload",""),
        "exa:CostCenter": exa.get("CostCenter",""),
        "exa:Owner": exa.get("Owner",""),
        "exa:customer": exa.get("customer",""),
        "exa:DataClassification": exa.get("DataClassification",""),
        "MandatoryComplete": "yes" if not missing else "no",
        "MissingMandatory": ",".join(missing),
        "AllTagsJson": json.dumps(tags, sort_keys=True, separators=(",", ":")),
        "AllTags": tags,
        "ARN": arn,
    })

rows.sort(key=lambda r: (r["Classification"], r["Service"], r["ResourceName"]))

cols = ["Classification","Service","Region","ResourceType","ResourceName",
        "DeploymentDescriptor",
        "exa:Project","exa:Environment","exa:Department","exa:Workload","exa:CostCenter",
        "exa:Owner","exa:customer","exa:DataClassification","MandatoryComplete",
        "MissingMandatory","AllTagsJson","ARN"]

target_rows = [r for r in rows if r["Classification"] == target_label]
other_rows = [r for r in rows if r["Classification"] != target_label]
project_breakdown = {}
for r in rows:
    label = label_for_project(r["exa:Project"]) or "No exa:Project"
    project_breakdown[label] = project_breakdown.get(label, 0) + 1

# ---- table to stdout ----
if fmt in ("table","all"):
    tcols = ["Classification","Service","ResourceType","ResourceName","DeploymentDescriptor","exa:Project",
             "exa:Environment","exa:Owner","exa:CostCenter","MandatoryComplete"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) if rows else len(c) for c in tcols}
    line = " | ".join(c.ljust(widths[c]) for c in tcols)
    print(line); print("-" * len(line))
    for r in rows:
        print(" | ".join(str(r[c]).ljust(widths[c]) for c in tcols))
    print()
    print(f"Account {account}: {len(rows)} resources  "
          f"({len(target_rows)} {target_label}, {len(other_rows)} {not_target_label})")
    if project_breakdown:
        parts = [f"{label}={count}" for label, count in sorted(project_breakdown.items())]
        print("Project breakdown: " + ", ".join(parts))
    nc = sum(1 for r in rows if r["MandatoryComplete"] == "no")
    print(f"Mandatory-tag compliant: {len(rows)-nc}/{len(rows)}  ({nc} missing one or more mandatory exa tags)")

# ---- CSV export ----
if fmt in ("csv","all"):
    csv_path = os.path.join(out_dir, base + ".csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"\nCSV  written: {csv_path}", file=sys.stderr)

# ---- JSON export ----
if fmt in ("json","all"):
    json_path = os.path.join(out_dir, base + ".json")
    payload = {
        "account": account,
        "generated_utc": ts,
        "project_tag": target,
        "project_label": target_label,
        "filters": filters,
        "summary": {
            "total": len(rows),
            "target_project": len(target_rows),
            "not_target_project": len(other_rows),
            "project_breakdown": project_breakdown,
            "mandatory_compliant": sum(1 for r in rows if r["MandatoryComplete"] == "yes"),
            "mandatory_noncompliant": sum(1 for r in rows if r["MandatoryComplete"] == "no"),
        },
        "resources": rows,
    }
    with open(json_path, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"JSON written: {json_path}", file=sys.stderr)

# ---- Markdown export ----
if fmt in ("md", "markdown", "all"):
    md_path = os.path.join(out_dir, base + ".md")
    md_cols = [
        "Service",
        "Region",
        "ResourceType",
        "ResourceName",
        "DeploymentDescriptor",
        "exa:Owner",
        "exa:CostCenter",
        "exa:Workload",
        "exa:DataClassification",
        "MandatoryComplete",
        "AllTags",
    ]
    def esc(value):
        text = str(value).replace("|", "\\|").replace("\n", " ")
        return text
    with open(md_path, "w") as fh:
        fh.write(f"# AWS Tag Inventory: {target}\n\n")
        fh.write(f"Generated UTC: `{ts}`\n\n")
        fh.write(f"AWS account: `{account}`\n\n")
        fh.write(f"Project tag: `exa:Project={target}`\n\n")
        fh.write(f"Project label: `{target_label}`\n\n")
        fh.write("## Summary\n\n")
        fh.write("| Metric | Value |\n|---|---:|\n")
        fh.write(f"| Total resources | {len(rows)} |\n")
        fh.write(f"| {target_label} resources | {len(target_rows)} |\n")
        fh.write(f"| Mandatory compliant | {sum(1 for r in rows if r['MandatoryComplete'] == 'yes')} |\n")
        fh.write(f"| Mandatory non-compliant | {sum(1 for r in rows if r['MandatoryComplete'] == 'no')} |\n\n")
        fh.write("## Project Breakdown\n\n")
        fh.write("| Project label | Resources |\n|---|---:|\n")
        for label, count in sorted(project_breakdown.items()):
            fh.write(f"| {esc(label)} | {count} |\n")
        fh.write("\n")
        fh.write("## Resources\n\n")
        fh.write("| " + " | ".join(md_cols) + " |\n")
        fh.write("|" + "|".join("---" for _ in md_cols) + "|\n")
        for r in rows:
            tags_text = "; ".join(f"{k}={v}" for k, v in sorted(r["AllTags"].items()))
            values = [
                r["Service"],
                r["Region"],
                r["ResourceType"],
                r["ResourceName"],
                r["DeploymentDescriptor"],
                r["exa:Owner"],
                r["exa:CostCenter"],
                r["exa:Workload"],
                r["exa:DataClassification"],
                r["MandatoryComplete"],
                tags_text,
            ]
            fh.write("| " + " | ".join(esc(v) for v in values) + " |\n")
    print(f"MD   written: {md_path}", file=sys.stderr)
PY
