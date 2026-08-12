#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ./generate --project <slug> --pilot-name <name> [options]
  ./generate -project <slug> -awsprofile <profile> -awsaz <az> [options]

Required:
  --project, -project          Target repository/directory slug.

Recommended:
  --pilot-name, -pilot-name    Human-readable pilot name.

Options:
  --customer-name <name>       Customer/stakeholder display name.
  --customer-domain <domain>   Customer email domain used to count confirmed
                               subscribers in status reporting.
  --pilot-schema <name>        Exasol analytical schema name. Domain-neutral by
                               default so any vertical can adopt it.
                               Default: PILOT_DW.
  --github-owner <owner>       GitHub org/user hosting the generated repository.
  --cognito-domain-prefix <p>  Cognito Hosted-UI domain prefix. Default: project slug.
  --operator-email <email>     Primary operator email; also used for commit
                               trailers and the exa:Owner tag.
  --project-id <id>            Exactly three lowercase letters replacing xyz.
                               Runtime identifiers stay lowercase; tracker IDs and documentation filenames use uppercase.
                               Default: first three letters from the sanitized project slug.
  --tracker-prefix <prefix>    Deprecated alias for --project-id; must also be exactly three letters.
  --aws-profile, -awsprofile   AWS CLI profile. Default: default.
  --aws-region, -awsregion     AWS region. Default: derived from AZ or eu-central-1.
  --aws-az, -awsaz             AWS availability zone.
  --domain <domain>            Optional public base domain.
  --target-root <path>         Parent directory. Default: ~/GitHub.
  --dry-run                    Print actions without writing target repo.
  --deploy-aws                 Allow generated deploy plan to be marked deployable.
                               This generator still does not create AWS resources.
  --create-github-repo         Reserved; not executed in this baseline.
  --help                       Show help.

Examples:
  ./generate -project gcme-analytics-pilot --project-id gcm -awsprofile default -awsregion eu-central-1 -awsaz eu-central-1a
  ./generate --project defence-readiness-pilot --project-id drp --pilot-schema MISSION_DW
  ./generate --project gcme-analytics-pilot --project-id gcm --pilot-name "GCME Analytics Pilot" \
            --customer-name "GCME - German Companies Make Everything" --customer-domain gcme.example \
            --aws-profile default --aws-region eu-central-1 --aws-az eu-central-1a

  GCME - German Companies Make Everything is a fictional stand-in company used
  in these examples. Replace it with the real target project and company name.
USAGE
}

project=""
pilot_name=""
customer_name=""
customer_domain=""
pilot_schema="PILOT_DW"
github_owner=""
cognito_domain_prefix=""
operator_email="${OPERATOR_EMAIL:-}"
project_id=""
aws_profile="default"
aws_region=""
aws_az=""
domain=""
target_root="${HOME}/GitHub"
dry_run=0
deploy_aws=0
create_github_repo=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project=*|-project=*)
      project="${1#*=}"; shift ;;
    --project|-project)
      project="${2:-}"; shift 2 ;;
    --pilot-name=*|-pilot-name=*)
      pilot_name="${1#*=}"; shift ;;
    --pilot-name|-pilot-name)
      pilot_name="${2:-}"; shift 2 ;;
    --customer-name=*|-customer-name=*)
      customer_name="${1#*=}"; shift ;;
    --customer-name|-customer-name)
      customer_name="${2:-}"; shift 2 ;;
    --customer-domain=*|-customer-domain=*)
      customer_domain="${1#*=}"; shift ;;
    --customer-domain|-customer-domain)
      customer_domain="${2:-}"; shift 2 ;;
    --pilot-schema=*|-pilot-schema=*)
      pilot_schema="${1#*=}"; shift ;;
    --pilot-schema|-pilot-schema)
      pilot_schema="${2:-}"; shift 2 ;;
    --cognito-domain-prefix=*|-cognito-domain-prefix=*)
      cognito_domain_prefix="${1#*=}"; shift ;;
    --cognito-domain-prefix|-cognito-domain-prefix)
      cognito_domain_prefix="${2:-}"; shift 2 ;;
    --github-owner=*|-github-owner=*)
      github_owner="${1#*=}"; shift ;;
    --github-owner|-github-owner)
      github_owner="${2:-}"; shift 2 ;;
    --operator-email=*|-operator-email=*)
      operator_email="${1#*=}"; shift ;;
    --operator-email|-operator-email)
      operator_email="${2:-}"; shift 2 ;;
    --project-id=*|--projectid=*|-projectid=*|--tracker-prefix=*|-tracker-prefix=*)
      project_id="${1#*=}"; shift ;;
    --project-id|--projectid|-projectid|--tracker-prefix|-tracker-prefix)
      project_id="${2:-}"; shift 2 ;;
    --aws-profile=*|--awsprofile=*|-awsprofile=*)
      aws_profile="${1#*=}"; shift ;;
    --aws-profile|--awsprofile|-awsprofile)
      aws_profile="${2:-}"; shift 2 ;;
    --aws-region=*|--awsregion=*|-awsregion=*)
      aws_region="${1#*=}"; shift ;;
    --aws-region|--awsregion|-awsregion)
      aws_region="${2:-}"; shift 2 ;;
    --aws-az=*|--awsaz=*|-awsaz=*)
      aws_az="${1#*=}"; shift ;;
    --aws-az|--awsaz|-awsaz)
      aws_az="${2:-}"; shift 2 ;;
    --domain=*)
      domain="${1#*=}"; shift ;;
    --domain)
      domain="${2:-}"; shift 2 ;;
    --target-root=*)
      target_root="${1#*=}"; shift ;;
    --target-root)
      target_root="${2:-}"; shift 2 ;;
    --dry-run)
      dry_run=1; shift ;;
    --deploy-aws)
      deploy_aws=1; shift ;;
    --create-github-repo)
      create_github_repo=1; shift ;;
    --help|-h)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

if [[ -z "$project" ]]; then
  echo "Missing required --project / -project" >&2
  usage >&2
  exit 2
fi

if [[ -z "$pilot_name" ]]; then
  pilot_name="$project"
fi

if [[ -z "$customer_name" ]]; then
  customer_name="$pilot_name"
fi

if [[ -z "$project_id" ]]; then
  project_id="$(printf '%s' "$project" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z]+//g' | cut -c1-3)"
else
  project_id="$(printf '%s' "$project_id" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z]+//g')"
fi

if [[ ! "$project_id" =~ ^[a-z]{3}$ ]]; then
  echo "Invalid --project-id: $project_id" >&2
  echo "Project ID must be exactly three lowercase ASCII letters, e.g. abc or gcm." >&2
  exit 2
fi

pilot_schema="$(printf '%s' "${pilot_schema:-PILOT_DW}" | tr '[:lower:]' '[:upper:]')"
if [[ ! "$pilot_schema" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
  echo "Invalid --pilot-schema: $pilot_schema" >&2
  echo "Schema must be a valid Exasol identifier, e.g. PILOT_DW or MISSION_DW." >&2
  exit 2
fi

if [[ -z "$aws_region" ]]; then
  if [[ -n "$aws_az" ]]; then
    aws_region="${aws_az::-1}"
  else
    aws_region="eu-central-1"
  fi
fi

template_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target_dir="${target_root%/}/${project}"

echo "Template root : $template_root"
echo "Target repo   : $target_dir"
echo "Project slug  : $project"
echo "Pilot name    : $pilot_name"
echo "Customer name : $customer_name"
echo "Customer domn : ${customer_domain:-<unset>}"
echo "Pilot schema  : $pilot_schema"
echo "GitHub owner  : ${github_owner:-<unset>}"
echo "Operator email: ${operator_email:-<unset>}"
echo "Project ID    : $project_id"
echo "AWS profile   : $aws_profile"
echo "AWS region    : $aws_region"
echo "AWS AZ        : ${aws_az:-<unset>}"
echo "Domain        : ${domain:-<unset>}"
echo "Deploy AWS    : $deploy_aws"
echo "Create GitHub : $create_github_repo"

if [[ "$dry_run" -eq 1 ]]; then
  echo "Dry-run requested; no files written."
  exit 0
fi

if [[ -e "$target_dir" ]]; then
  echo "Target already exists: $target_dir" >&2
  exit 1
fi

mkdir -p "$target_dir"

rsync -a \
  --exclude='.git/' \
  --exclude='.DS_Store' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='generate' \
  --exclude='generate.sh' \
  --exclude='scripts/render_template_placeholders.py' \
  --exclude='.env' \
  --exclude='.venv/' \
  --exclude='tests/logs/' \
  "$template_root/" "$target_dir/"

python3 "$template_root/scripts/render_template_placeholders.py" \
  --root "$target_dir" \
  --project "$project" \
  --pilot-name "$pilot_name" \
  --customer-name "$customer_name" \
  --project-id "$project_id" \
  --aws-profile "$aws_profile" \
  --aws-region "$aws_region" \
  --aws-az "$aws_az" \
  --domain "$domain" \
  --pilot-schema "$pilot_schema" \
  --github-owner "$github_owner" \
  --cognito-domain-prefix "$cognito_domain_prefix" \
  --customer-domain "$customer_domain" \
  --operator-email "$operator_email" \
  --deploy-aws "$deploy_aws"

(
  cd "$target_dir"
  cp config/template.defaults.env config/project.env.example
  if [[ ! -f config/project.env ]]; then
    cp config/project.env.example config/project.env
  fi
  git init -b main >/dev/null 2>&1 || {
    git init >/dev/null
    git branch -m main
  }
  git add .
  git commit -m "chore: initialize pilot repository from template" \
    -m "Co-Authored-By: Exasol SE Pilot Template <${OPERATOR_EMAIL:-template@example.invalid}>" >/dev/null || true
)

cat <<EOF

Generated project repository:
  $target_dir

Next:
  cd "$target_dir"
  edit config/project.env
  ./scripts/validate_template_neutrality.sh
  ./scripts/verify_tracker_completeness.sh

AWS deployment is not executed by generate. Use the generated deployment
runbook and explicit deploy commands after reviewing account, region, AZ, and
cost-bearing resources.
EOF
