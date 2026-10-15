#!/usr/bin/env bash
set -euo pipefail

root="${1:-.}"

# Source-project identity, third-party product IP, domain-specific schema names,
# and secret-shaped literals that must never reach a generated pilot repository.
#
# NOTE: patterns are written with bracketed first characters (e.g. [H]PC) so this
# script does not match itself. It is excluded from the scan anyway; the
# bracketing keeps grep-ability of the file honest for humans too.

# Customer / source-project identity.
identity_regex='[H]arvey|[h]arveyperformance|Performance Company|\b[H]PC\b|\b[h]pc\b|exasol-sol-eng|exasol-workshop-starter|ssas-exasol-migration|[r]ealtdegen|thomas\.degen|/Users/thomas'

# Third-party BI product IP (the source pilot's migration scenario, not this stack).
thirdparty_regex='[e]viit|\b[S]SAS\b|[P]ower ?BI|[p]owerbi|[P]owerBI|\b[P]BIX\b|\b[P]BIP\b|\.pbix|\.pbip|\b[D]AX\b|[O]LEDBProvider|[M]ultidimensional|powerbi_rebuild|sample_sales_cube|sample_bim_conversion_assets'

# Domain-specific schema objects — must be neutral placeholders instead.
schema_regex='[S]ALES_CUBE|[I]NVOICED_SALES|[P]RODUCTGROUP|[S]ALES_TARGET|[S]ALES_REGION|[D]ISTACCTKEY|V_MONTHLY_SALES|V_YOY_SALES|V_SALES_VS_TARGET|V_INVENTORY_ANALYTICS'

# Live account-specific resource identifiers and secret-shaped literals.
# The <AWS_REGION>_xxxxxxxxx alternative matters: a partly-anonymized pool ID
# keeps its (secret) pool suffix while losing the literal region prefix, so it
# only becomes visible after rendering. Catch it in the template too.
secret_regex='gho_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|BEGIN (RSA|OPENSSH|PRIVATE) KEY|[a-z]{2}-[a-z]+-[0-9]_[A-Za-z0-9]{9}|<AWS_REGION>_[A-Za-z0-9]{9}'

source_specific_regex="$identity_regex|$thirdparty_regex|$schema_regex|$secret_regex"

# Unrendered placeholders are only a defect in a *generated* repo. The template
# itself is supposed to still contain them.
placeholder_regex='<CUSTOMER_NAME>|<PROJECT_SLUG>|<PILOT_NAME>|<PILOT_SCHEMA>|XYZ_PILOT_ISSUE_TRACKER|xyz-pilot|xyz_pilot|\bxyz\b'

if [[ -f "${root%/}/generate.sh" ]]; then
  blocked_regex="$source_specific_regex"
else
  blocked_regex="$placeholder_regex|$source_specific_regex"
fi

# --hidden is mandatory: .agents/, .github/ and .streamlit/ are dot-directories
# and ripgrep skips them by default. Omitting it silently passed a repository
# carrying customer logos, operator emails and live Cognito pool IDs.
if rg -n --hidden "$blocked_regex" "$root" \
  -g '!.git/' \
  -g '!scripts/validate_template_neutrality.sh'
then
  echo "Template neutrality check failed: blocked source-specific, third-party or secret-looking terms found." >&2
  exit 1
fi

echo "Template neutrality check OK"
