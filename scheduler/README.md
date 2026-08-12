# <PILOT_NAME> Scheduler — AWS Lambda + EventBridge

> **Purpose:** Stop the <PILOT_NAME> EC2 instance every night at 24:00 CEST and restart it every business day at 12:00 CEST so US colleagues can still work with it while the EU team is off. Save ~50 % on the EC2-hourly cost (instance is stopped ~12 h/day on weekdays + the whole weekend).
> **Status:** Live. EventBridge rules `xyz-pilot-start-12cest` + `xyz-pilot-stop-24cest` are enabled.

## Architecture

```
                EventBridge cron rules
                ┌───────────────────────────────┐
                │ xyz-pilot-start-12cest         │
                │ cron(0 10 ? * MON-FRI *) UTC   │  -> 12:00 CEST Mon-Fri
                │                                │
                │ xyz-pilot-stop-24cest          │
                │ cron(0 22 * * ? *) UTC         │  -> 24:00 CEST every day
                │                                │
                │ xyz-pilot-candidate-drain-     │
                │ hourly                         │
                │ cron(0 11-23 ? * MON-FRI *)    │  -> 13:00-01:00 CEST
                │                                │
                │ xyz-pilot-sns-watchdog-         │
                │ bizdays-13cest                  │
                │ cron(0 11 ? * MON-FRI *) UTC    │  -> 13:00 CEST
                │                                │
                │ xyz-pilot-sns-watchdog-         │
                │ weekly-pending-15cest           │
                │ cron(0 13 ? * MON *) UTC        │  -> Mon 15:00 CEST
                └───────────────┬───────────────┘
                                │ invoke
                                ▼
                ┌───────────────────────────────┐
                │ Lambdas: xyz-pilot-{start,stop}│
                │ Python 3.13, 256 MB            │
                │ start 900 s, stop 600 s        │
                │ ACTION env var dispatches      │
                │ which handler runs.            │
                └───────────────┬───────────────┘
                                │
        ┌───────────────────────┼──────────────────────────────┐
        │                       │                              │
        ▼                       ▼                              ▼
  ec2:StartInstances /     (named-tunnel URLs            ses:SendEmail
  StopInstances on the      are constants now —          to verified
  <PILOT_NAME> instance         no scraping needed)            recipients
  <EC2_INSTANCE_ID>      since XYZ-PILOT-025 Resolved   (<OPERATOR_NAME>, <OPS_USER_3>,
                            2026-05-20                      Denise, ...)
                                │
                                ▼
                        ┌────────────────────┐
                        │ s3:PutObject       │
                        │ index.html in      │
                        │ xyz-pilot-status-  │
                        │ <account>          │
                        └────────────────────┘
                                │
                                ▼
            http://xyz-pilot-status-<SOURCE_AWS_ACCOUNT_ID>.s3-website.<AWS_REGION>.amazonaws.com
                (publicly readable status dashboard — every state change rewrites it)
```

## Schedule

| Action | EventBridge cron (UTC) | Local time | Days |
|---|---|---|---|
| Start | `cron(0 10 ? * MON-FRI *)` | **12:00 CEST** | Mon–Fri |
| Stop | `cron(0 22 * * ? *)` | **24:00 CEST** | every day |
| Candidate-drain digest (XYZ-PILOT-208) | `cron(0 11-23 ? * MON-FRI *)` | **13:00–01:00 CEST** | Mon–Fri UTC window |
| Onboarding confirmation watchdog (XYZ-PILOT-209) | `cron(0 11 ? * MON-FRI *)` | **13:00 CEST** | Mon–Fri |
| Weekly pending-user explainer (XYZ-PILOT-209) | `cron(0 13 ? * MON *)` | **15:00 CEST** | Monday |
| One-time pending-user explainer (XYZ-PILOT-209) | `cron(0 11 3 6 ? 2026)` | **13:00 CEST, 2026-06-03** | one-time |
| **Ad-hoc start** (XYZ-PILOT-031) | Dashboard button → Lambda Function URL | any time | any day |

## Notification policy (XYZ-PILOT-034)

The SES lifecycle email is sent on these invocation paths:

1. EventBridge cron at **12:00 CEST Mon–Fri** (scheduled start)
2. EventBridge cron at **24:00 CEST every day** (scheduled stop)
3. Cognito-authenticated **ad-hoc START** through the Operator Dashboard
4. Cognito-authenticated **ad-hoc STOP** through the Operator Dashboard

Any other invocation — `aws lambda invoke` from an operator's terminal, the AWS Console "Test" button, a maintenance repaint — still updates the S3 status dashboard so state stays accurate, but does **not** fire an email. The dispatcher in `lambda_handler` distinguishes by inspecting `event["source"] == "aws.events"` (EventBridge) and `event["requestContext"]["http"]` (Function URL); everything else is treated as operator-driven and emails are suppressed.

Caveat: EventBridge schedules are in UTC; we hard-code the +2 offset for CEST. **Around the DST boundaries** (last Sunday of March; last Sunday of October) the window will shift by 1 h for ~1 week until someone manually re-aligns the cron. Acceptable for the Pilot; production should use the AWS Instance Scheduler solution which understands timezones.

## What each invocation does

### `xyz-pilot-start`
1. Reads current EC2 state. If not `running`, calls `ec2:StartInstances` and waits for `running`.
2. Sleeps 60 s for the `docker compose` stack to settle (cloudflared takes ~30 s to re-establish its named-tunnel connections).
3. For cold starts, runs the [XYZ-PILOT-156](../docs/XYZ_PILOT_ISSUE_TRACKER.md) warm-up gate through SSM: wait for SSM Online, verify whether `xyz-exasol-warmup.service` already completed for the current boot, and otherwise trigger/wait for `sudo systemctl start xyz-exasol-warmup.service`. This prevents start emails and ad-hoc button responses from advertising a ready QBR dashboard while Exasol is still warming.
4. Returns the stable named-tunnel URLs as constants (XYZ-PILOT-025 / -020 Resolved 2026-05-20 — `xyz-pilot-qbr.<PILOT_DOMAIN>` + `xyz-pilot-grafana.<PILOT_DOMAIN>`). No SSM scrape needed.
5. Renders an HTML status dashboard and uploads it to S3 (`s3://xyz-pilot-status-<account>/index.html`, public-read).
6. Sends a SES email to verified recipients with the URLs, dashboard link, and warm-up status.

### Ad-hoc START button (XYZ-PILOT-031)

When the EC2 is `stopped`, the status dashboard renders a green **"Start Pilot Environment now"** button. Clicking it POSTs to a Lambda Function URL on `xyz-pilot-start`, which (after validating an internal token) runs the exact same `handle_start` flow as the EventBridge cron — there is no separate code path.

- **Endpoint:** `https://<LAMBDA_URL_ID>.lambda-url.<AWS_REGION>.on.aws/?token=<TRIGGER_TOKEN>`
- **Auth:** Function URL `AuthType=NONE` + internal token check inside the Lambda. The token is embedded in the public dashboard HTML; effective auth boundary is "anyone who knows the dashboard URL". See XYZ-PILOT-031 for the trade-off rationale.
- **Idempotent:** if the instance is already running, `handle_start` does not re-run the warm-up gate. After a cold start, it returns only after the warm-up gate has succeeded or reported a warning in the Lambda result.
- **CORS:** `AllowOrigins=*`, `AllowMethods=POST`, `AllowHeaders=content-type`, `MaxAge=86400`.

### `xyz-pilot-stop`
1. Tries to scrape the still-live URLs *before* stopping (so the "now stopping" email has the last-known values).
2. For the regular scheduled 24:00 CEST STOP only, runs the daily user
   activity monitor before `ec2:StopInstances`.
3. Calls `ec2:StopInstances` and waits for `stopped`.
4. Re-renders the dashboard in "stopped" state.
5. Sends the normal STOP SES email.

### Scheduled STOP daily user activity monitor (XYZ-PILOT-034 extension)

The daily user activity monitor runs **once per reviewed day** during the
regular 24:00 CEST EventBridge STOP cycle, before EC2 shutdown. It never runs
for Operator Dashboard ad-hoc STOP calls. If the monitor fails, the Lambda sends
a failure notice to `OPS_ONLY` and still continues the shutdown sequence for
cost discipline.

Archive target:

```text
docs/60-evidence-inventory/daily-user-monitor/XYZ_PILOT_DAILY_USER_MONITOR_YYYY-MM-DD.md
```

Email target:

```text
OPS_ONLY only
```

The email subject starts with the reviewed activity day, for example:

```text
2026.06.26 : Daily <PILOT_NAME> user activity monitor
```

Captured data is metadata only:

- Cognito user/group posture for `OPS_ONLY`, `XYZ_OPS`, `XYZ_PILOT`, and
  `XYZ_BIZ`
- confirmed <CUSTOMER_NAME> user count
- chatbot per-user history file metadata such as modified time, file size, and
  entry count
- container log counters for QBR Streamlit, Chatbot, Grafana, and MCP

The monitor must not archive prompt text, response text, tokens, cookies,
passwords, or secret values. QBR Streamlit and Grafana currently provide
service activity markers, not Cognito-user attribution.

Operator preview / dry-run:

```bash
AWS_PROFILE=default aws lambda invoke \
  --region <AWS_REGION> \
  --function-name xyz-pilot-stop \
  --cli-binary-format raw-in-base64-out \
  --payload '{"daily_user_monitor_dry_run":true,"recipient":"<OPERATOR_EMAIL>"}' \
  /tmp/xyz-daily-user-monitor-dry-run.json
```

The dry run does not start or stop EC2, does not archive to GitHub, and does
not purge or drain logs. It reads the same metadata sources and sends a preview
email with the full Markdown report body to the requested recipient only.

### `xyz-pilot-candidate-drain` ([XYZ-PILOT-208](../docs/XYZ_PILOT_ISSUE_TRACKER.md) / [XYZ-PILOT-215](../docs/XYZ_PILOT_ISSUE_TRACKER.md))

This Lambda is the hourly XYZ_MASTER candidate-drain service.

1. Lists committed `tests/logs/runs/*/*/issue-candidates.md` files from the GitHub `main` tree through the GitHub API.
2. Compares each candidate file's path + blob SHA with an S3 state file at `s3://xyz-pilot-status-<SOURCE_AWS_ACCOUNT_ID>/candidate-drain/state.json`.
3. Suppresses newly seen files whose sibling `candidate-outcomes/candidate-NNN.*.md` markers already decide every candidate in the file. These files are recorded as seen but do not generate digest noise.
4. Dispatches the deterministic background filing workflow for eligible `minor` / `feature` new-ticket candidates when low-severity auto-filing is enabled.
5. Leaves existing-issue `status-transition` close-out candidates in the Operator Dashboard approval queue; hourly automation must not resolve or close existing tracker issues without elevated operator action.
6. Sends exactly one SES digest to <OPERATOR_NAME>, <OPS_USER_2>, and <OPS_USER_3> for candidates that still need human review.
7. Records those files as seen in S3 after dispatch/digest handling completes.

First deployment bootstraps existing historical candidate files into the state store so operators are not spammed with old handoffs. A one-off operator test can pass `{"include_existing": true, "dry_run": true}` to preview the discovered set without sending email or changing state.

The Operator Dashboard `xyz_master_candidates` picker is intentionally stricter
than a raw candidate-file scan. It lists only actionable candidates expecting
approval or denial, suppressing explicit outcome markers, run folders older than
`XYZ_MASTER_BACKLOG_MIN_RUN_ID`, and old pre-marker candidates whose detail
title, lightweight candidate heading, or terminal status is already present in
the central tracker. It also suppresses daily-status top-table prune housekeeping handoffs from
`XYZ_PILOT_DAILY_STATUS` so tracker cleanup does not create operator backlog or
digest noise. Burst discipline is enforced in both automation surfaces:
candidate-drain will not auto-dispatch more than `MAX_AUTO_DISPATCH_PER_FILE`
items from one file, and the Operator Dashboard hides files above
`XYZ_MASTER_CANDIDATE_FILE_VISIBLE_LIMIT` actionable candidates. Burst authors
must submit one aggregate candidate rather than dozens or hundreds of repeated
candidate blocks. Status-transition and severity-transition candidates remain
visible until approved or denied, because they are the token-saving
post-implementation close-out and priority-elevation paths for existing
`XYZ-PILOT-NNN` issues.

The dashboard authorisation label is intentionally decoupled from the heavier
backlog scan. It calls `xyz_master_operator_context` first so elevated operators
see a confirmed authorisation state even if `xyz_master_candidates` is still
loading or fails. The candidate scan is bounded by
`XYZ_MASTER_CANDIDATE_FILE_SCAN_LIMIT` (default `40`) so stale swarm artifacts
cannot make the Lambda hit its 30 s runtime limit before returning the current
backlog.

The Operator Dashboard and `xyz-pilot-user-mgmt` Lambda also enforce the
`direct-main` safety boundary before GitHub Actions dispatch: new-ticket
`direct-main` approvals are accepted only for `minor` and `feature`
classifications. `major` and `critical` candidates must use PR mode unless the
candidate is an approved existing-issue status or severity transition. A
severity transition updates the active-issues table row and full-register detail
severity in one archived/verifier-clean XYZ_MASTER mutation, then the
major-defect heads-up workflow treats minor-to-major/critical changes as
operator-only notification candidates.

### `xyz-pilot-sns-confirmation-watchdog` (XYZ-PILOT-209)

This Lambda is now the business-day onboarding confirmation watchdog. It fires at 13:00 CEST on Pilot operational weekdays and checks three surfaces:

1. Cognito first-login / temporary-password state for active Pilot users.
2. SES email identity verification status.
3. The `xyz-pilot-major-resolution-heads-up` SNS subscription state for the approved operator trio: <OPERATOR_NAME>, <OPS_USER_2>, and <OPS_USER_3>.

Fresh AWS confirmation messages are re-issued only when the previous request is missing or its TTL has expired, unless an operator invokes with `{"force": true}`. The Lambda writes durable state to `s3://xyz-pilot-status-<SOURCE_AWS_ACCOUNT_ID>/sns-watchdog/state.json`; the Operator Dashboard's **New User Onboarding Facility** reads that state and shows reminder timestamps, TTL expiry, likely SNS unsubscribe signals, and deactivated-user trails in the individual-status view.

The TTL-reissue reminder email is sent to the affected user and CCs <OPERATOR_NAME>, <OPS_USER_2>, and <OPS_USER_3>. It explicitly thanks the user for testing the <PILOT_NAME> Chatbot with Exasol MCP Server and the <PILOT_NAME> QBR Dashboard, and states the DEV E2E operating window: 12:00-24:00 CEST on operational business days.

In addition, EventBridge rule `xyz-pilot-sns-watchdog-weekly-pending-15cest` invokes the same Lambda every Monday at 15:00 CEST with `{"mode":"weekly_pending_reminder"}`. This sends an extraordinary explainer email only to users who are still pending at that time, again CCing <OPERATOR_NAME>, <OPS_USER_2>, and <OPS_USER_3>. It does not reissue AWS confirmation requests; it explains why the confirmations matter, how to complete them, the current TTLs known from the state file, and when the Pilot E2E environment is normally available.

One extraordinary pre-weekly rollout email for 2026-06-03 is scheduled through date-bound EventBridge rule `xyz-pilot-sns-watchdog-onetime-20260603-13cest` (`cron(0 11 3 6 ? 2026)`). It invokes the same `weekly_pending_reminder` mode at 13:00 CEST today for still-pending users only, with <OPERATOR_NAME>, <OPS_USER_2>, and <OPS_USER_3> on CC.

If a user clearly declines participation, the operator records the opt-out in `DEACTIVATED_USERS_JSON` (or the lower-detail `DEACTIVATED_EMAILS`) on the watchdog Lambda and redeploys. On the next watchdog run, the Lambda:

- skips future reminders for that user,
- disables the Cognito user when still enabled,
- persists reason/source/recorded timestamp/denied scopes in the S3 trail,
- sends one rejection heads-up to <OPERATOR_NAME>, <OPS_USER_2>, and <OPS_USER_3> through SNS where possible plus direct SES fan-out, and
- exposes the trail in the Operator Dashboard individual-status panel.

Manual dry-run preview:

```bash
aws lambda invoke --function-name xyz-pilot-sns-confirmation-watchdog \
    --region <AWS_REGION> \
    --cli-binary-format raw-in-base64-out \
    --payload '{"dry_run":true,"force":true}' \
    /tmp/xyz-sns-watchdog.json
cat /tmp/xyz-sns-watchdog.json
```

## Email format

Subject (UTC + 2 CEST timestamp):
```
YYYY.MM.DD.HH.SS : Status <PILOT_NAME> Exasol environment on AWS
```

Body lists the current state (`RUNNING` / `STOPPED`), the dashboard URL, and every ServiceURI: Exasol DB, Exasol Admin UI, SSH, Streamlit QBR, Grafana.

Recipients are discovered from Cognito groups at send time, then filtered by
SES identity verification so one unverified address cannot fail the whole send.
The notification policy is:

- START/STOP/STATUS: `OPS_ONLY` ∪ `XYZ_PILOT` ∪ `XYZ_OPS`, explicitly
  excluding any user also present in `XYZ_BIZ`.
- START warm-up warning follow-ups (`[WARM-UP WARNING]`): `OPS_ONLY` only;
  these are operational remediation signals, not broad XYZ_OPS/UAT notices.
- Swarm Tester result notifications: `OPS_ONLY` only.
- Ops-only START/STOP invocations from the dashboard checkbox: restricted to
  `OPS_ONLY` ∪ `XYZ_OPS`, explicitly excluding any user also present in
  `XYZ_BIZ`, without adding recipient-scope labels to the subject. The
  dashboard checkbox is checked by default; unchecking it uses the normal
  START/STOP/STATUS subscriber union above.

`SES_TO` remains configured only as a deployment fallback/source-of-truth for
legacy operators and sender verification checks; it must not be treated as the
authoritative recipient roster.

Side-effect-free daily-status recipient validation:

```bash
AWS_PROFILE=default aws lambda invoke \
  --region <AWS_REGION> \
  --function-name xyz-pilot-daily-status \
  --cli-binary-format raw-in-base64-out \
  --payload '{"recipient_probe_only":true,"include_emails":true}' \
  /tmp/xyz-daily-status-recipient-probe.json

jq '.include_groups,.exclude_groups,.group_counts,.deliverable_recipients' \
  /tmp/xyz-daily-status-recipient-probe.json
```

The email's **Documentation freshness** table is maintained in `daily_status.py` via `TRACKED_DOCS` ([XYZ-PILOT-152](../docs/XYZ_PILOT_ISSUE_TRACKER.md)). It should include every top-level `docs/*.md` file plus the curated operational READMEs that can otherwise stall unnoticed (`scheduler/`, `services/exasol-mcp/`, `services/chatbot/`, `services/streamlit-insights/`, `scripts/exasol_warmup/`, `tests/`). Generated archives, QA snapshots, screenshots, and architecture capture exports are intentionally excluded from the morning status.

The email's **Repository retention cleanup candidates** section is report-only.
It scans tracked Git files through the GitHub Git tree API, reports file counts,
directory sizes, and cleanup candidate classes, but never deletes repository
content. Policy and local scanner commands live in
[`docs/30-runbooks/operations/XYZ_PILOT_REPOSITORY_RETENTION_RUNBOOK.md`](../docs/30-runbooks/operations/XYZ_PILOT_REPOSITORY_RETENTION_RUNBOOK.md).

The daily-status Lambda's GitHub-backed sections read the PAT from AWS Secrets
Manager secret `GITHUB_TOKEN_SECRET_ID` (default:
`xyz-pilot/github/service-token`). The legacy SSM SecureString named by
`GITHUB_TOKEN_PARAM` (default: `/xyz-pilot/chatbot/github-token`) remains a
temporary rollback fallback. The Lambda environment must not carry the PAT value
itself as `GITHUB_TOKEN`. Since
[XYZ-PILOT-210](../docs/XYZ_PILOT_ISSUE_TRACKER.md), the token must
cover more than Contents:

- Fine-grained PAT on `<GITHUB_OWNER>/<PROJECT_SLUG>`: `Contents: Read and write`, `Actions: Read`, `Pull requests: Read`.
- Classic PAT equivalent: `repo` + `workflow`.

For [XYZ-PILOT-215](../docs/XYZ_PILOT_ISSUE_TRACKER.md), the
same stored PAT is used by `xyz-pilot-user-mgmt` and
`xyz-pilot-candidate-drain`. It must go one step further: fine-grained
`Actions: Read and write` is required to dispatch
`.github/workflows/xyz-master-background-filing.yml`. A token with
`Actions: Read` can list workflow runs but fails approval dispatch with GitHub
`403 Resource not accessible by personal access token`.

[XYZ-PILOT-259](../docs/XYZ_PILOT_ISSUE_TRACKER.md) hardening:

- `xyz-pilot-daily-status`, `xyz-pilot-candidate-drain`,
  `xyz-pilot-user-mgmt`, `xyz-pilot-weekend-recap-oneshot`, and the
  scheduled STOP daily user monitor inside `xyz-pilot-stop` carry only
  `GITHUB_TOKEN_SECRET_ID` plus rollback-only `GITHUB_TOKEN_PARAM`.
- The execution roles `xyz-pilot-scheduler` and
  `xyz-pilot-user-mgmt-role` need `secretsmanager:GetSecretValue` on exactly
  `<AWS_ARN><AWS_REGION>:<AWS_ACCOUNT_ID>:secret:xyz-pilot/github/service-token-*`.
  While the rollback fallback exists, they also need `ssm:GetParameter` on the
  exact SSM parameter ARN plus `kms:Decrypt` for the SSM KMS key.
- `scripts/migrate_github_token_to_secrets_manager.sh` can copy the existing
  SSM SecureString into Secrets Manager or accept a freshly scoped PAT through
  stdin / `gh-cli`. Moving the token to Secrets Manager does not add GitHub
  scopes; the PAT itself still needs the Actions write capability above.
- Use `scripts/phase06_full/harden_lambda_github_token_env.sh` after any
  manual Lambda restore or migration replay to strip a stale `GITHUB_TOKEN`
  environment variable and redeploy the local source.

Metadata-only validation:

```bash
AWS_PROFILE=default AWS_REGION=<AWS_REGION> \
  aws secretsmanager describe-secret \
    --secret-id xyz-pilot/github/service-token \
    --query '{Name:Name,ARN:ARN,LastChangedDate:LastChangedDate}' \
    --output table

AWS_PROFILE=default AWS_REGION=<AWS_REGION> \
  aws ssm describe-parameters \
    --parameter-filters Key=Name,Option=Equals,Values=/xyz-pilot/chatbot/github-token \
    --query 'Parameters[].{Name:Name,Type:Type,KeyId:KeyId}' \
    --output table

AWS_PROFILE=default AWS_REGION=<AWS_REGION> \
  ./scripts/phase06_full/harden_lambda_github_token_env.sh
```

Secret-safe validation after a token rotation or Lambda redeploy:

```bash
aws lambda invoke --function-name xyz-pilot-daily-status --region <AWS_REGION> \
  --payload '{"github_probe_only":true}' \
  --cli-binary-format raw-in-base64-out /tmp/xyz-daily-github-probe.json

jq '.workflow_health.ok, .dependabot_prs.ok, .repo_retention.ok' /tmp/xyz-daily-github-probe.json
```

This probe exercises only the GitHub Actions workflow-runs call, the Pull Requests call, and the report-only Git tree retention scan. It does **not** refresh tracker/docs, probe services, delete files, or send email.

## AWS resources created by this scheduler

| Resource | ARN / Name | Purpose |
|---|---|---|
| IAM role | `xyz-pilot-ec2-ssm` | Attached to EC2; allows SSM agent to register |
| IAM instance profile | `xyz-pilot-ec2-ssm` | Wraps the role above for EC2 attachment |
| IAM role | `xyz-pilot-scheduler` | Lambda execution role; permissions for ec2/ssm/ses/s3/logs plus Cognito group reads (`ListUsersInGroup`, `AdminListGroupsForUser`) scoped to the target user pool for notification fan-out and ad-hoc initiator authorization |
| Lambda function | `xyz-pilot-start` | Start handler |
| Lambda function | `xyz-pilot-stop` | Stop handler; scheduled 24:00 CEST path also archives the daily user activity monitor before EC2 shutdown |
| Lambda function | `xyz-pilot-cognito-invite-blast` | One-shot Cognito invite-resend (`cognito_invite_blast.py`), dry-run by default; EventBridge fires with `{"send":true}` |
| Lambda function | `xyz-pilot-cognito-sync-subscribers` | Hourly SES→Cognito sync (`cognito_sync_subscribers.py`); creates Cognito users for SES-verified candidates that aren't yet in the pool. XYZ-PILOT-039. |
| Lambda function | `xyz-pilot-daily-status` | Daily status email Tue–Sat 06:00 CEST (`daily_status.py`); HTML body with EC2 + SES + Cognito + recent commits. XYZ-PILOT-045. |
| Lambda function | `xyz-pilot-user-mgmt` | Operator Dashboard user-management Lambda (`xyz_pilot_user_mgmt.py`); onboarding cohort, invite/status/login history, GitHub commit retriever, [XYZ-PILOT-215](../docs/XYZ_PILOT_ISSUE_TRACKER.md) candidate approval backlog, and [XYZ-PILOT-078](../docs/XYZ_PILOT_ISSUE_TRACKER.md) server-side ServiceURI probes. |
| Lambda function | `xyz-pilot-candidate-drain` | Hourly candidate drain (`xyz_master_candidate_drain.py`); dispatches low-severity background filing and sends one operator digest for higher-risk pending files. [XYZ-PILOT-208](../docs/XYZ_PILOT_ISSUE_TRACKER.md) / [XYZ-PILOT-215](../docs/XYZ_PILOT_ISSUE_TRACKER.md). |
| Lambda function | `xyz-pilot-sns-confirmation-watchdog` | Business-day onboarding confirmation watchdog (`sns_watchdog.py`); reissues expired Cognito/SES/SNS confirmation requests, records S3 trail, and surfaces deactivated users. XYZ-PILOT-209. |
| EventBridge rule | `xyz-pilot-start-12cest` | Cron 0 10 ? * MON-FRI * |
| EventBridge rule | `xyz-pilot-stop-24cest` | Cron 0 22 * * ? * |
| EventBridge rule | `xyz-pilot-cognito-invite-oneshot` | One-shot cron `0 22 16 5 ? 2026` — fires at 24:00 CEST on 2026-05-16. Year is pinned so the rule self-expires |
| EventBridge rule | `xyz-pilot-cognito-sync-hourly` | `rate(1 hour)` — fires the SES→Cognito sync Lambda. Retire after <OPS_USER_2>×2 are onboarded |
| EventBridge rule | `xyz-pilot-daily-status-06cest` | `cron(0 4 ? * TUE-SAT *)` — fires the daily-status Lambda 06:00 CEST Tue–Sat |
| EventBridge rule | `xyz-pilot-candidate-drain-hourly` | `cron(0 11-23 ? * MON-FRI *)` — candidate drain during operation time +1h, 13:00–01:00 CEST |
| EventBridge rule | `xyz-pilot-sns-watchdog-bizdays-13cest` | `cron(0 11 ? * MON-FRI *)` — onboarding confirmation watchdog at 13:00 CEST |
| EventBridge rule | `xyz-pilot-sns-watchdog-weekly-pending-15cest` | `cron(0 13 ? * MON *)` — weekly pending-user explainer at Monday 15:00 CEST |
| EventBridge rule | `xyz-pilot-sns-watchdog-onetime-20260603-13cest` | `cron(0 11 3 6 ? 2026)` — one-time pending-user explainer on 2026-06-03 13:00 CEST |
| S3 bucket | `xyz-pilot-status-<account-id>` | Public-read static-website hosting |

All tagged `Pilot=XYZ-PILOT`, `Owner=<OPERATOR_EMAIL>`.

**Untouched / unrelated:** the other Exasol Personal instance (`<EC2_INSTANCE_ID>`) and its EIP `3.67.248.65`. The other SNS topics in the account (`exasol-aws-monitor-dashboard-milestones` etc.) — all left alone.

### Operator Dashboard ServiceURI probes

`scheduler/status_dashboard/dashboard.html` calls `xyz-pilot-user-mgmt` with
`{"action":"service_probe"}` on load and then at most once per minute while the
EC2 instance is running. The Lambda performs bounded server-side HTTP probes
for the tunnel-backed services that browser JavaScript cannot reliably check
because of CORS and authentication redirects:

- Streamlit QBR: `/_stcore/health`
- Grafana: `/api/health`
- Public MCP endpoint: `/mcp` via `HEAD`
- <PILOT_NAME> Chatbot: `/` via `HEAD`

DB and SSH rows stay EC2-state gated because they are not HTTP endpoints. The
Lambda returns only status code, latency, timestamp, and a short diagnostic
note; it does not return response bodies or credentials.

## File layout

```
scheduler/
├── README.md                     ← this file
├── lambda_function.py            ← single Lambda module; ACTION env dispatches
├── lambda_function.zip           ← deployment artefact (rebuilt by deploy.sh)
├── deploy.sh                     ← re-package + update both Lambdas
├── xyz_master_candidate_drain.py  ← XYZ-PILOT-208/215 candidate drain Lambda
├── xyz_master_candidate_drain.zip ← deployment artefact (rebuilt by deploy_candidate_drain.sh)
├── deploy_candidate_drain.sh      ← package/update Lambda + EventBridge rule
├── deploy_sns_watchdog.sh          ← package/update onboarding watchdog + 13:00 CEST rule
├── deploy_user_mgmt.sh             ← package/update Operator user-mgmt Lambda
├── sns_watchdog.py                 ← onboarding confirmation watchdog
├── xyz_pilot_user_mgmt.py          ← Operator Dashboard user-management Lambda
└── iam/
    ├── ec2-ssm-trust-policy.json
    ├── lambda-trust-policy.json
    └── lambda-permissions.json
```

## How to operate

### Re-deploy after a code change

```bash
cd scheduler
zip -q lambda_function.zip lambda_function.py
for ACTION in start stop; do
    aws lambda update-function-code \
        --function-name "xyz-pilot-$ACTION" \
        --zip-file fileb://lambda_function.zip
done
```

### Pause the schedule (e.g. during a long demo week)

```bash
aws events disable-rule --name xyz-pilot-start-12cest
aws events disable-rule --name xyz-pilot-stop-24cest
```

### Resume

```bash
aws events enable-rule --name xyz-pilot-start-12cest
aws events enable-rule --name xyz-pilot-stop-24cest
```

### Manual invocation (testing)

```bash
aws lambda invoke --function-name xyz-pilot-start \
    --cli-binary-format raw-in-base64-out \
    --payload '{"source":"manual"}' \
    /tmp/out.json
```

Candidate-drain dry-run preview:

```bash
aws lambda invoke --function-name xyz-pilot-candidate-drain \
    --cli-binary-format raw-in-base64-out \
    --payload '{"dry_run":true,"include_existing":true}' \
    /tmp/xyz-candidate-drain.json
```

### Tail logs

```bash
LG=/aws/lambda/xyz-pilot-start
aws logs tail "$LG" --since 5m --follow
```

### Update recipients

```bash
# Add/remove START/STOP/STATUS recipients by Cognito group membership, not by
# Lambda environment editing. SES verification must still be Success.
AWS_PROFILE=default aws cognito-idp admin-add-user-to-group \
  --region <AWS_REGION> \
  --user-pool-id <COGNITO_USER_POOL_ID> \
  --username <cognito-username-or-sub> \
  --group-name XYZ_OPS
```

## Cost

Negligible — typical month:

| Item | Cost |
|---|---|
| Lambda invocations (40/month × ~6 min start / ~70 s stop × 256 MB) | < $0.02 |
| EventBridge events | free tier |
| S3 status dashboard (one HTML file, few KB) | < $0.01 |
| SES emails (40/month × ≤ 3 recipients) | < $0.01 |
| Elastic IP attached to stopped instance (12 h × 5 weekdays + 48 h weekend = ~108 h/month) | ~$0.54 |
| **Saved** EC2 r6i.xlarge × 108 h not running | **~$22** |

Net monthly saving: **~$21**. The scheduler pays for itself in the first morning it runs.

## Limitations (open issues)

- ~~**XYZ-PILOT-025**~~ — **Resolved 2026-05-20.** Streamlit + Grafana now use named Cloudflare tunnels on `<PILOT_DOMAIN>`; URLs are constants in `recapture_tunnel_urls()`, bookmarks stable.
- **DST transitions** — see schedule caveat above; ~1 week of misaligned timing twice a year.
- **SES sandbox** — emails to unverified recipients are silently skipped (logged as warnings in CloudWatch). Move SES to production access when going wider.
- **XYZ-PILOT-031 — public Function URL** — the ad-hoc START button relies on an unauthenticated Function URL guarded only by an internal token in public HTML. Acceptable for the Pilot; switch to `AuthType=AWS_IAM` + Cognito identity pool before opening this beyond the current SES subscriber set.
- **XYZ-PILOT-156** — start-path fix deployed 2026-05-29. Ad-hoc and scheduled cold starts now gate readiness on the Exasol warm-up service through SSM before sending the start email / returning the ad-hoc start result. Tracker remains Open until the next real cold start or an approved stop/start test confirms first-filter QBR latency.

## See also

- Issue tracker entries `XYZ-PILOT-024..027` in [`docs/XYZ_PILOT_ISSUE_TRACKER.md`](../docs/XYZ_PILOT_ISSUE_TRACKER.md)
- Milestone F (Elastic IP) and Milestone G (this scheduler) in [`docs/00-live-status/XYZ_PILOT_PROGRESS_LOG.md`](../docs/00-live-status/XYZ_PILOT_PROGRESS_LOG.md)
- Live runbook §4 for daily-operations cheatsheet: [`docs/00-live-status/XYZ_PILOT_LIVE_RUNBOOK.md`](../docs/00-live-status/XYZ_PILOT_LIVE_RUNBOOK.md)
