---
name: runtime-debugger
description: Use when asked to reproduce, diagnose, or fix runtime failures in this Exasol workshop repository, especially Docker, devcontainer, AWS auth bootstrap, Exasol launcher, Python loaders, Kestra, or Streamlit startup issues.
---

# Runtime Debugger

Use this skill to reproduce and fix runtime failures in the workshop flow.

## Goals

- Reproduce failures locally or in a devcontainer/Codespaces-compatible flow.
- Prioritize Docker, devcontainer, AWS auth bootstrap, Exasol launcher, Python, Kestra, and Streamlit startup issues.
- For each bug, identify trigger, root cause, fix, and validation command.
- Avoid destructive cleanup unless explicitly approved.

## Safety Rules

- Do not delete containers, volumes, generated deployments, Terraform/OpenTofu state, credentials, or user data without explicit approval.
- Do not run cost-bearing or cloud-mutating commands unless the user explicitly requested them in the current task.
- Treat `deployment/`, `secrets.json`, `*.pem`, Terraform state, AWS credentials, Exasol passwords, and GitHub Codespaces secrets as sensitive.
- Redact secrets in logs and summaries. Never paste full secret-bearing files.
- Before editing files, inspect `git status --short` and preserve unrelated changes.

## Debugging Workflow

1. Capture the failing command exactly:
   - Command and working directory.
   - Relevant environment: local, devcontainer, Codespaces, shell, OS, architecture.
   - Exit code and the smallest useful log excerpt.
   - Whether network, Docker daemon, AWS credentials, Exasol deployment, or local ports are required.

2. Reproduce with the narrowest command:
   - Prefer one service or script at a time before running the full workshop.
   - Use checked-in `reference/` files to isolate repo/runtime issues from learner edits in `code/`.
   - Avoid cleanup as a first response; inspect state before changing it.

3. Diagnose by layer:
   - Tooling: `uv`, Python, Docker, Docker Compose, AWS CLI, Exasol launcher.
   - Environment: devcontainer settings, Codespaces secrets, `AWS_DEFAULT_REGION`, port conflicts, permissions.
   - Runtime: Python exceptions, SQL errors, Exasol connectivity, Kestra task logs, Streamlit import/startup errors.
   - Configuration: compose files, paths mounted into containers, `deployment/` lookup paths, `.gitignore`, generated flow files.

4. Fix close to the cause:
   - Prefer small code/config changes over broad rewrites.
   - Add validation commands, smoke tests, or diagnostics when possible.
   - Keep `README.md`, `reference/`, and `code/` behavior aligned when the bug affects workshop instructions.

## Common Checks

- Repo state: `git status --short`
- Files: `rg --files`
- Python syntax from `reference/`: `uv run python -m py_compile find_urls.py load_addr.py load_chem.py load_pdpi.py check.py dashboard.py utils/*.py`
- Dependency sync from `reference/`: `uv sync`
- AWS identity: `aws sts get-caller-identity`
- Exasol launcher location: run launcher commands from `deployment/`
- Exasol connection info: `uv run python -m utils.connection_info` from the active Python workspace
- Kestra startup from `code/kestra` or `reference/kestra`: `docker compose up -d`, then `docker compose ps`
- Kestra logs: `docker compose logs kestra` and `docker compose logs kestra_postgres`
- Kestra flow sync when file watching fails: `uv run python scripts/sync_flows.py`
- Streamlit startup from the active Python workspace: `uv run streamlit run dashboard.py`

## Report Format

For every confirmed bug, report:

- `Trigger`: exact command, path, and prerequisite state.
- `Root cause`: the specific broken code, config, path, dependency, permission, or service state.
- `Fix`: files changed or exact manual action required.
- `Validation`: command that proves the fix works.
- `Residual risk`: anything not validated, especially cloud, cost, or secret-sensitive paths.
