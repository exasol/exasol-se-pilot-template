---
name: workshop-runner
description: Use when asked to run, validate, or debug the Exasol workshop in this repository end to end; read README.md and linked setup docs, execute the safest practical local/devcontainer path, capture blockers, avoid exposing secrets, and prefer reproducible fixes.
---

# Workshop Runner

Use this skill for end-to-end validation of the Exasol workshop starter repo.

## Goals

- Read `README.md` and any linked setup docs before running the workshop.
- Execute the workshop end to end in the safest practical local/devcontainer path.
- Record every failing command, missing dependency, broken path, invalid config, or runtime blocker.
- Never expose, rotate, or regenerate real secrets automatically.
- Prefer reproducible fixes with tests, scripts, or documented commands.
- Summarize blockers by severity: setup blocker, runtime blocker, UX blocker, security blocker.

## Safety Rules

- Treat `deployment/` as sensitive. It can contain `secrets.json`, private keys, launcher state, connection instructions, and Terraform/OpenTofu state.
- Do not print real passwords, tokens, private keys, TLS material, AWS credentials, or full secret-bearing JSON. Redact values when summarizing.
- Do not run cost-bearing or cloud-mutating commands unless the user has explicitly asked for that action in the current task. This includes `exasol deploy`, `exasol start`, `exasol stop`, `exasol destroy`, and AWS commands that create, modify, or delete resources.
- Do not rotate GitHub Codespaces secrets, AWS credentials, Exasol passwords, or Kestra credentials automatically.
- Before changing workshop files, check `git status --short` and preserve unrelated user changes.
- If a command needs network access, Docker, AWS, or a local service, state that dependency and capture failures exactly enough to reproduce without exposing secrets.

## Repo Map

- `README.md`: primary current workshop flow.
- `setup/codespaces.md`: Codespaces setup and credential bootstrap secrets.
- `setup/local.md`: local AWS CLI and Exasol CLI setup.
- `setup.sh`: manual fallback for injected AWS container credentials.
- `reference/`: known-good Python scripts, Kestra flows, `pyproject.toml`, and `uv.lock`.
- `code/`: learner workspace created during the workshop; may be absent or partially complete.
- `deployment/`: generated Exasol Personal deployment; sensitive and normally ignored.
- `docs/GRAFANA_INTEGRATION_GUIDE.md`: optional Grafana extension, not part of the core path unless requested.

## Execution Order

1. Recon:
   - Read `README.md`, `setup/codespaces.md`, `setup/local.md`, and relevant `.devcontainer` files.
   - Inspect `git status --short`, `.gitignore`, existing `code/`, existing `deployment/`, and tool availability.
   - Prefer `rg`/`rg --files` for searches.

2. Local static checks:
   - Confirm required tools as applicable: `uv --version`, `python --version`, `docker --version`, `docker compose version`, `aws --version`, `exasol --version` or `which exasol`.
   - Validate checked-in Python without touching cloud first, for example from `reference/`: `uv run python -m py_compile find_urls.py load_addr.py load_chem.py load_pdpi.py check.py dashboard.py utils/*.py`.
   - If dependencies are not synced, use the repo's `uv.lock` when possible.

3. Python workshop path:
   - If `code/` exists and contains the learner scripts, run from `code/`.
   - If `code/` is absent and the task is validation rather than learner simulation, run from `reference/`.
   - Generate URL metadata with `uv run python find_urls.py`.
   - Load the first month in order: `uv run python load_addr.py --period 201008`, `uv run python load_chem.py --period 201008`, `uv run python load_pdpi.py --period 201008`.
   - Verify with `uv run python check.py`.
   - Database-loading commands require a working `deployment/` and Exasol connectivity.

4. Exasol deployment path:
   - For Codespaces, verify `aws sts get-caller-identity` before deployment.
   - For local runs, verify AWS CLI credentials and `AWS_DEFAULT_REGION`, usually `eu-central-1`.
   - Run Exasol launcher commands from `deployment/`.
   - Prefer the current README path: `exasol init aws --s3-archive-enabled=false`, review `allowed_cidr`, then `exasol deploy`.
   - If deployment fails or is interrupted, report possible running AWS resources and recommend checking the AWS console.

5. Kestra path:
   - Kestra assets live under `code/kestra` during the workshop and under `reference/kestra` as source material.
   - Ensure `kestra/flows/` exists before `docker compose up -d`.
   - Use `docker compose ps` and service logs to verify startup.
   - If the file watcher fails, use `uv run python scripts/sync_flows.py` from the `kestra` directory.
   - Execute `load_month` for `201008` before attempting `load_all`.

## Fixing Policy

- Prefer small, reproducible fixes close to the failing surface: scripts for repeatable setup, tests for parsing/logic, and README patches for broken commands or paths.
- Keep reference files and learner workspace behavior aligned. If changing `reference/`, consider whether `README.md` instructions need the same update.
- Do not overwrite learner files in `code/` without checking their contents first.
- For fragile external dependencies, add diagnostics that fail with actionable messages instead of silently continuing.

## Reporting Format

Report with these sections when validation completes or blocks:

- `Setup blockers`: missing tools, credentials, permissions, ports, dependency installation, AWS identity, Docker availability.
- `Runtime blockers`: Python exceptions, Exasol connection failures, loader failures, SQL errors, Kestra execution failures.
- `UX blockers`: incorrect README commands, confusing paths, missing prerequisites, unclear expected output, commands that work only in one environment.
- `Security blockers`: secret exposure, tracked sensitive files, unsafe `.gitignore`, overbroad secret handling, credential rotation risk.

For each blocker, include the command, working directory, observed failure, likely cause, and proposed fix. Redact secret values.
