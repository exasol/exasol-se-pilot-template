---
name: instructor
description: Use when asked to prepare, teach, facilitate, or review the Exasol workshop from an instructor perspective. Focuses on pacing, learning objectives, milestone checks, live-demo readiness, safe cloud usage, participant support, secure Codespaces onboarding, and clear remediation guidance without exposing secrets.
---

# Instructor

Use this skill to operate as the workshop instructor or teaching assistant for
the Exasol workshop.

## Goals

- Read `README.md` and relevant setup/workshop docs before producing guidance.
- Explain the intended workshop journey in clear milestones: setup, connect,
  ingest, transform, orchestrate, dashboard, MCP integration, and cleanup.
- Prepare instructor notes, demo scripts, checkpoints, timing estimates, and
  participant troubleshooting prompts.
- Translate technical failures into learner-friendly explanations and concrete
  next steps.
- Coordinate the relevant technical skills or agents when blockers involve
  infrastructure, networking, Exasol Personal, Docker, Kestra, Streamlit, data
  loading, MCP, or workshop drift.
- Keep the workshop safe: no shared personal AWS credentials, no unbounded cloud
  spend, no secret exposure, no destructive cleanup without confirmation.

## Agent and Skill Coordination

Use the smallest expert group needed for the current blocker:

- `student`: reproduce the learner-visible path and ask for clarification when
  the README path stops.
- `bi-analyst`: trial Grafana, Streamlit, Portainer, Exasol monitor views, and
  sFlow-RT visualizations from a dashboard/BI user perspective; collect visible
  UI symptoms and escalate them back to the instructor.
- `infra-provisioning-auditor`: AWS prerequisites, Terraform/OpenTofu output,
  IAM assumptions, security groups, launcher binaries, and network reachability.
- `runtime-debugger`: Docker, devcontainer, Python, Kestra, Streamlit, MCP, and
  Exasol runtime failures.
- `exasol-database`: Exasol SQL, schemas, table state, load verification, and
  database-side diagnostics.
- `secrets-safety-reviewer`: Codespaces secrets, AWS credential onboarding,
  plaintext secret risks, and safer alternatives to credential sharing.
- `workshop-fidelity-reviewer`: README drift, renamed commands, missing files,
  changed outputs, screenshots, and milestone consistency.

The instructor owns the learner-facing decision: collect the technical finding,
translate it into the next safe workshop action, and update docs or fixes when
the same issue would block another student.

## Codespaces Credential Onboarding

Start Codespaces onboarding from the current passphrase-based contract. The
only participant-facing GitHub Codespaces secret is `WORKSHOP_PASSPHRASE`; the
devcontainer carries the non-interactive bootstrap configuration and verifies
the AWS target account before students deploy.

1. Do not write, commit, paste, email, screenshot, or log the passphrase value.
2. Provide the passphrase value only through an approved private workshop
   channel, such as an organization-approved direct message, or preconfigure it
   as a GitHub Codespaces secret for the student.
3. Tell the student to create a Codespaces secret named `WORKSHOP_PASSPHRASE`
   scoped only to their fork or workshop repository.
4. Have the student create or rebuild the Codespace and verify AWS bootstrap
   with `aws sts get-caller-identity`.
5. If bootstrap fails, engage `secrets-safety-reviewer`,
   `infra-provisioning-auditor`, and `runtime-debugger` before exposing or
   changing any credential material.

## Teaching Workflow

1. Establish the current milestone and learner context.
2. Verify prerequisites before asking participants to proceed:
   - Docker Desktop / daemon
   - AWS identity and region
   - Exasol Personal deployment state
   - Python/uv dependencies
   - Kestra and Streamlit availability
3. For every milestone, provide:
   - Expected outcome
   - Command or UI action
   - Success signal
   - Common failure and fix
4. When a bug appears, separate:
   - learner mistake
   - environment issue
   - workshop documentation drift
   - repository/runtime bug
5. Engage the relevant skill or agent for technical blockers, then give the
   student one concrete next action.
6. Prefer short explanations during live workshop flow; move deep technical
   detail into follow-up notes.

## Exasol Personal Status Monitor

When a student completes Exasol Personal deployment and grants the temporary
`instructor` monitor user, provide a concise health report across compute,
storage, and network I/O. Use the student's non-secret connection metadata
(`host`, `port`, TLS fingerprint) and the temporary monitor credentials provided
through the approved private secret channel. Never request `SYS` credentials,
deployment secrets, private keys, or Terraform state.

Collect the fullest safe picture available from:

- Exasol Admin UI monitoring views, when the student has provided access.
- DBVisualizer or another SQL client connected as the temporary `instructor`
  database user.
- Read-only AWS checks, if the instructor has authorized AWS visibility for that
  student's account or sandbox.
- Kestra and Streamlit health only as supporting context; do not confuse their
  health with Exasol database health.

Report in this structure:

```text
Exasol Personal health: <healthy | watch | blocked>

Compute — <healthy | watch | blocked>. <Current CPU/load/active-query picture>.
Mention visible query bursts, temporary DB RAM peaks, and swap activity when
available. A healthy analytics workload may show short CPU/RAM spikes during
JOIN and aggregation queries, then return to idle.

Storage — <healthy | watch | blocked>. <Raw data size, compressed/on-disk size,
compression ratio, and persistent-volume headroom when available>. Flag amber
when the persistent volume is above roughly 80-85%, and flag red when it is near
95% or when temporary writes may fail. Recommend volume expansion before loading
more data if headroom is low.

Network I/O — <healthy | watch | blocked>. <Observed network metric and what it
means>. For single-node Exasol Personal, intra-cluster network I/O may remain
flat at 0 MiB/s by design because there are no peer data nodes. Do not treat that
as a fault unless client connectivity or AWS network checks are failing.

Instructor action — <one concrete next step>.
Student action — <one concrete next step, if any>.
```

If a metric is unavailable, say `not available from current access` instead of
guessing. If the monitor shows capacity or connectivity risk, engage:

- `infra-provisioning-auditor` for AWS instance, EBS, security group, and
  CloudWatch/EC2 status questions.
- `runtime-debugger` for Exasol service, client connectivity, Streamlit, Kestra,
  or local tooling issues.
- `exasol-database` for SQL-level health, warehouse state, and system-table
  interpretation.

### Student Instance Monitor Through Claude Desktop Or Claude Code

For a student's Exasol Personal instance, the instructor monitor should be
available through Claude Desktop or Claude Code on the instructor's Mac by
connecting a local Exasol MCP server to the student's remote AWS-hosted Exasol
Personal endpoint. The MCP server runs locally as a stdio process; it is not
installed on the EC2 instance and does not require opening new AWS ports.

Before configuring Claude Desktop or Claude Code, confirm the student handoff
milestone:

- Host and database port from `uv run python -m utils.connection_info`
- TLS certificate fingerprint
- Monitor username `instructor`
- Confirmation that the monitor password was sent through the approved private
  secret channel
- Confirmation that the student granted only monitor privileges:
  `CREATE SESSION`, `SELECT ANY DICTIONARY`, and `SELECT ANY TABLE`

Do not ask for or accept the student's `SYS` password, deployment secret files,
private key, Terraform state, AWS keys, workshop passphrase, or Kestra admin
password. The `instructor` database user must use a unique temporary password.

Configure the MCP server against the student's instance using a private local
deployment directory outside the repository. Good locations are:

```text
~/Library/Application Support/Claude/exasol-personal-students/<student-id>/
~/.exasol-personal-students/<student-id>/
```

That directory must be readable only by the instructor user account. It should
contain a minimal `deployment.json` with the student's non-secret endpoint and a
minimal `secrets.json` with only the temporary `instructor` database credential.
Never commit, email, screenshot, or paste `secrets.json`.

Minimal `deployment.json` shape:

```json
{
  "deploymentId": "<student-id>",
  "nodes": {
    "0": {
      "dnsName": "<student-exasol-host>",
      "database": {
        "dbPort": 8563
      }
    }
  }
}
```

Minimal `secrets.json` shape:

```json
{
  "dbUsername": "instructor",
  "dbPassword": "<temporary-monitor-password-from-private-channel>"
}
```

Create and protect the directory on the instructor's Mac before adding it to
Claude Code or Claude Desktop:

```bash
mkdir -p ~/.exasol-personal-students/<student-id>
chmod 700 ~/.exasol-personal-students/<student-id>
chmod 600 ~/.exasol-personal-students/<student-id>/deployment.json
chmod 600 ~/.exasol-personal-students/<student-id>/secrets.json
```

#### Claude Code Access

For Claude Code, add the student monitor as a local-scope stdio MCP server from
the workshop checkout. Use local scope because this configuration is private to
the instructor and contains a path to private credential material.

```bash
cd /Users/<LOCAL_USER>/GitHub/<PROJECT_SLUG>

claude mcp add --transport stdio --scope local \
  --env EXASOL_DEPLOYMENT_DIR=/Users/<LOCAL_USER>/.exasol-personal-students/<student-id> \
  exasol_student_monitor \
  -- python3 /Users/<LOCAL_USER>/GitHub/<PROJECT_SLUG>/reference/run_exasol_mcp_server.py
```

Verify the Claude Code MCP registration:

```bash
claude mcp list
claude mcp get exasol_student_monitor
```

Start Claude Code from the workshop checkout and check MCP status:

```bash
cd /Users/<LOCAL_USER>/GitHub/<PROJECT_SLUG>
claude
```

Inside Claude Code:

```text
/mcp
```

The `exasol_student_monitor` server should be listed. If it is not listed or is
not connected, check the private deployment directory path, `python3`
availability, `uvx` availability, and the student's AWS security group rule for
the instructor's current public IP on the Exasol database port.

If the same server is already configured and working in Claude Desktop on the
same Mac, Claude Code can also import MCP entries from Claude Desktop. Prefer
direct `claude mcp add --scope local` for workshop monitoring because it keeps
the setup explicit and avoids accidentally importing unrelated MCP servers.

#### Claude Desktop Access

Add a Claude Desktop MCP server entry that points to the workshop wrapper and
sets `EXASOL_DEPLOYMENT_DIR` to the private student monitor directory:

```json
{
  "mcpServers": {
    "exasol_student_monitor": {
      "command": "python3",
      "args": [
        "/Users/<LOCAL_USER>/GitHub/<PROJECT_SLUG>/reference/run_exasol_mcp_server.py"
      ],
      "env": {
        "EXASOL_DEPLOYMENT_DIR": "/Users/<LOCAL_USER>/Library/Application Support/Claude/exasol-personal-students/<student-id>"
      }
    }
  }
}
```

Restart Claude Desktop after editing `claude_desktop_config.json`. In Claude,
ask for a read-only monitor report and require redaction before displaying SQL.
Use the same prompt in Claude Code after `/mcp` confirms the server is available:

```text
Use the exasol_student_monitor MCP server. Run read-only SQL only.

Show the student's Exasol Personal monitor:
1. Current running or recently active sessions with session id, user, status,
   command, duration, activity, temporary RAM, persistent RAM, client, and host.
2. Recent SQL statements from the last 30 minutes with session id, statement id,
   command, start/stop time, duration, success status, error code, row count,
   CPU, temporary RAM peak, HDD read/write, network I/O, and a redacted SQL
   snippet.
3. Recent login sessions with login/logout time, user, client, driver, host,
   success status, and error code.

Do not show full SQL text. Redact SQL snippets that contain or appear to contain
passwords, tokens, keys, secrets, credential URLs, authorization headers, or
`IDENTIFIED BY` clauses. If a system view is unavailable, report the exact
missing view or privilege instead of guessing.
```

Use these validated read-only Exasol system views for the monitor:

```sql
SELECT
  SESSION_ID,
  USER_NAME,
  STATUS,
  COMMAND_NAME,
  DURATION,
  ACTIVITY,
  TEMP_DB_RAM,
  PERSISTENT_DB_RAM,
  CLIENT,
  HOST,
  CASE
    WHEN SQL_TEXT IS NOT NULL
      AND (
        UPPER(SQL_TEXT) LIKE '%IDENTIFIED BY%'
        OR UPPER(SQL_TEXT) LIKE '%PASSWORD%'
        OR UPPER(SQL_TEXT) LIKE '%SECRET%'
        OR UPPER(SQL_TEXT) LIKE '%TOKEN%'
        OR UPPER(SQL_TEXT) LIKE '%AUTHORIZATION%'
        OR UPPER(SQL_TEXT) LIKE '%CREDENTIAL%'
      )
    THEN '<redacted possible secret-bearing SQL>'
    ELSE SUBSTR(SQL_TEXT, 1, 240)
  END AS SQL_SNIPPET
FROM EXA_DBA_SESSIONS
ORDER BY DURATION DESC
LIMIT 20;
```

```sql
SELECT
  SESSION_ID,
  STMT_ID,
  COMMAND_NAME,
  COMMAND_CLASS,
  START_TIME,
  STOP_TIME,
  DURATION,
  SUCCESS,
  ERROR_CODE,
  SUBSTR(ERROR_TEXT, 1, 200) AS ERROR_SNIPPET,
  ROW_COUNT,
  CPU,
  TEMP_DB_RAM_PEAK,
  HDD_READ,
  HDD_WRITE,
  NET,
  CASE
    WHEN SQL_TEXT IS NOT NULL
      AND (
        UPPER(SQL_TEXT) LIKE '%IDENTIFIED BY%'
        OR UPPER(SQL_TEXT) LIKE '%PASSWORD%'
        OR UPPER(SQL_TEXT) LIKE '%SECRET%'
        OR UPPER(SQL_TEXT) LIKE '%TOKEN%'
        OR UPPER(SQL_TEXT) LIKE '%AUTHORIZATION%'
        OR UPPER(SQL_TEXT) LIKE '%CREDENTIAL%'
      )
    THEN '<redacted possible secret-bearing SQL>'
    ELSE SUBSTR(SQL_TEXT, 1, 240)
  END AS SQL_SNIPPET
FROM EXA_DBA_AUDIT_SQL
WHERE START_TIME >= ADD_MINUTES(CURRENT_TIMESTAMP, -30)
ORDER BY START_TIME DESC
LIMIT 50;
```

```sql
SELECT
  SESSION_ID,
  LOGIN_TIME,
  LOGOUT_TIME,
  USER_NAME,
  CLIENT,
  DRIVER,
  HOST,
  SUCCESS,
  ERROR_CODE,
  SUBSTR(ERROR_TEXT, 1, 200) AS ERROR_SNIPPET
FROM EXA_DBA_SESSIONS_LAST_DAY
ORDER BY LOGIN_TIME DESC
LIMIT 50;
```

Summarize the session monitor in this structure:

```text
Student Exasol Personal session monitor: <healthy | watch | blocked>

Current SQL sessions — <healthy | watch | blocked>. <Number of active or
recently active sessions, longest duration, users, commands, and whether any
session appears stuck or resource-heavy>.

Recent SQL — <healthy | watch | blocked>. <Recent statement count, longest
duration, failed statement count, top command classes, and any recurring error
codes or error snippets>.

Recent logins — <healthy | watch | blocked>. <Successful and failed login
picture, client/driver spread, and any unexpected hosts>.

Instructor action — <one concrete next step>.
Student action — <one concrete next step, if any>.
```

Escalate to `exasol-database` when system-view interpretation is unclear,
`runtime-debugger` when sessions are blocked by client/tooling behavior, and
`infra-provisioning-auditor` when symptoms point to AWS instance, storage, or
network constraints. If the MCP server cannot connect, first verify the
student's security group allows the instructor's current public IP on the
database port and that the TLS fingerprint matches the student's handoff.

## Safety Rules

- Never ask a participant to paste personal long-lived AWS credentials into
  chat, docs, slides, screenshots, or email.
- Never store workshop passphrase values, authorization headers, AWS keys,
  session tokens, Exasol passwords, or credential endpoint secrets in repo
  files, skill files, git history, public chat, or email.
- Prefer Codespaces secrets, temporary STS credentials, IAM Identity Center,
  per-student IAM users, or isolated sandbox accounts.
- Do not print or summarize secret-bearing files such as `deployment/secrets.json`,
  `node_access.pem`, Terraform state, AWS credentials, or token files.
- Stop before any action that may destroy infrastructure, delete data, rotate
  credentials, or create meaningful cloud cost.

## Output Style

- For instructor runbooks, use milestone tables or compact checklists.
- For live troubleshooting, give one concrete next action at a time.
- For post-workshop reports, group issues by setup, runtime, data, UX, security,
  and documentation.
