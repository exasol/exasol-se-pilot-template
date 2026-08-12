# Template Backport Assessment — 2026-07-01

**Source:** local source pilot repository snapshot
**Target:** neutral template repository at `~/GitHub/exasol-se-pilot-template`
**Author:** OpenAI Codex

## Selection Rule

Only reusable mechanics were backported. Live customer evidence, account IDs, region migration logs, signed license files, user activity reports, candidate outcomes, tracker mutations, and project-specific service URIs were intentionally not copied.

## Backported

| Area | Template paths | Rationale |
|---|---|---|
| Stress harness durability | `tests/scripts/xyz_stress_probe.py`, `tests/agents/xyz_stress/run.sh`, `tests/scripts/test_xyz_stress_probe.py` | Keeps core UAT paths blocking while classifying heavy analytics and schema-inventory probes as advisory. Makes `view_count_union_all` opt-in and supports approved 50-user extraordinary runs. |
| Optional BIZ read-only stress lane | `tests/scripts/xyz_stress_probe.py`, `scripts/exasol_warmup/warmup.py` | Provides a neutral second-profile hook for generated pilots that need to compare dashboard-user and business-user read paths. The default template query pack is empty so no generated pilot inherits customer-specific semantic SQL. |
| Grafana Exasol license export/import-plan controls | `services/grafana/exasol_proxy/main.py`, `services/grafana/grafana/dashboards/xyz_exasol_monitor.json`, `services/grafana/grafana/provisioning/datasources/datasources.yml` | Adds read-only ConfD license metadata export and a non-mutating rotation plan while keeping direct license upload disabled. |
| Guarded Exasol Personal license scripts | `scripts/exasol-personal-license-export.sh`, `scripts/exasol-personal-license-rotate.sh` | Gives generated pilots a safe operator path: export current license first, stage replacement, apply only with `--apply --confirm-apply`, and publish non-secret evidence only. |
| License operations runbook and tests | `docs/30-runbooks/operations/XYZ_PILOT_EXASOL_LICENSE_OPERATIONS.md`, `tests/scripts/test_grafana_license_proxy.py` | Documents the operator procedure and locks the proxy/script contract with focused tests. |

## Not Backported

| Source change family | Reason |
|---|---|
| Live source-pilot tracker mutations and candidate outcomes | The template tracker must stay neutral and generated projects create their own IDs. |
| AWS account/region migration runbooks, lingering-resource inventories, and daily user activity evidence | These are source-pilot operational records, not reusable template state. |
| Signed `.exasol_license` files and backup license artifacts | License bodies are controlled assets and must not be embedded into a generic template. |
| Customer/user-specific Cognito membership changes and notification incidents | Generated pilots define their own groups, recipients, and legal access model. |
| Source-pilot DEV migration scripts with concrete account, region, bucket, and EC2 assumptions | Too project-specific for a neutral template; only generic stress/license mechanics were extracted. |

## Validation

Focused local validation after the backport:

```bash
PYTEST_ADDOPTS='-p no:cacheprovider' python3 -m pytest \
  tests/scripts/test_xyz_stress_probe.py \
  tests/scripts/test_grafana_license_proxy.py
```

Result: 24 tests passed.
