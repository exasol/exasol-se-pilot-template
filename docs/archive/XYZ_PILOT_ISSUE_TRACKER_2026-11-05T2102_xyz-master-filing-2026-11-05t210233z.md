# Pilot Issue Tracker

**Last refresh:** <TRACKER_TODAY_DATE> (template initialized).

This tracker is the single source of truth for project scope, defects, feature
requests, operational status items, and documentation discipline.

## Active issues

> **Visibility rule:** Resolved items remain visible briefly, then are pruned
> from this top table while their full register entries stay permanent.
>
> *Today is <TRACKER_TODAY>; cut-off date is <TRACKER_CUTOFF>.*

| ID | Severity | Area | Summary |
|---|---|---|---|
| [XYZ-PILOT-001](#xyz-pilot-001) | feature | Template · project bootstrap | Initialize the generated pilot repository and replace placeholders with project-specific configuration. |

---

## Full Register (newest first)

<a id="xyz-pilot-001"></a>

### XYZ-PILOT-001 - Generated pilot repository bootstrap

- **Status:** Open
- **Severity:** feature
- **Area:** Template · project bootstrap
- **Surfaced:** <YYYY-MM-DD> by template generator

#### Summary

The generated pilot repository has been created from the Exasol SE pilot
template. The operator must provide customer/project-specific values, confirm
the AWS account/region/AZ binding, and decide whether to run the protected
deployment steps.

#### Acceptance

- Placeholder values reviewed.
- `config/project.env` populated from `config/project.env.example`.
- `./scripts/validate_template_neutrality.sh` passes.
- `./scripts/verify_tracker_completeness.sh` passes.
- Deployment remains dry-run until explicitly approved.
