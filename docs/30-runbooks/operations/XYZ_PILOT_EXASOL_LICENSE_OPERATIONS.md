# <PILOT_NAME> Exasol License Operations

**Owner:** Pilot operators
**Scope:** Grafana Exasol Monitor license panel, `exasol_proxy` ConfD license export, and guarded Exasol Personal license rotation support

## Operating Boundary

Grafana is read-only for license operations. It can show current ConfD metadata, export the current metadata JSON, and show the rotation plan. It must not upload `.exasol_license` files or mutate ConfD directly. License rotation remains an explicit operator script path.

## Components

- `services/grafana/exasol_proxy/main.py` exposes:
  - `GET /license` for table rows consumed by Grafana
  - `GET /license/export?refresh=true` for non-secret ConfD metadata export
  - `GET /license/import-plan` for the guarded rotation plan
  - `POST /license/import` as an intentionally disabled boundary
- `scripts/exasol-personal-license-export.sh` preserves the current raw license file plus ConfD metadata without printing the license body.
- `scripts/exasol-personal-license-rotate.sh` exports first, stages a replacement, and only calls `confd_client license_upload` when both `--apply` and `--confirm-apply` are provided.

## Standard Rotation Sequence

1. Export current ConfD metadata from Grafana: `/api/datasources/proxy/1/license/export?refresh=true`.
2. Export and preserve the current raw license on the Exasol node:

```bash
scripts/exasol-personal-license-export.sh \
  --node <EXASOL_NODE> \
  --ssh-key <SSH_KEY> \
  --preserve-dir licenses
```

3. Stage the replacement license without applying it:

```bash
scripts/exasol-personal-license-rotate.sh \
  --node <EXASOL_NODE> \
  --ssh-key <SSH_KEY> \
  --license-file licenses/<new-license>.exasol_license
```

4. Apply only after explicit operator approval:

```bash
scripts/exasol-personal-license-rotate.sh \
  --node <EXASOL_NODE> \
  --ssh-key <SSH_KEY> \
  --license-file licenses/<new-license>.exasol_license \
  --apply --confirm-apply
```

5. Validate in Grafana and from script output: ConfD Contract/Limits reflect the intended license, `license_run_check=OK`, and `db_state=running`.

## Evidence Rules

Keep signed `.exasol_license` files out of logs and generated QA artifacts. Commit only neutral scripts, runbooks, and non-secret metadata examples. Hashes, byte counts, basenames, ConfD summary fields, and command IDs are acceptable evidence; license bodies are not.
