# Stress Checks

Bounded concurrent-user pressure checks for the <PILOT_NAME> UAT window.

Default `xyz_stress` scope:

- simulate 5-10 virtual users against public QBR and Chatbot Streamlit surfaces;
- open one short read-only `pyexasol` `xyz_dash` session per virtual user so
  Grafana / `EXA_DBA_SESSIONS` can prove database-session concurrency;
- run the Exasol warm-up/regression query pack through the read-only
  `xyz_dash` `exapump` profile for each virtual user, including dynamic
  `V_*` view-count probes;
- collect status codes, DB command statuses, p50/p95/max latency, error rate,
  and per-target evidence;
- fail when total error rate exceeds the configured threshold or a target's p95
  exceeds the configured latency budget;
- submit candidate-only findings for `xyz_master` when thresholds fail.

Current limitations:

- The default browser-facing probe covers public shell and health requests, not
  authenticated QBR filter clicks or live chatbot prompts.
- The DB-backed lane does execute the same representative SQL shapes that the
  warm-up service uses after an <PILOT_NAME> start, but it does not authenticate to
  the Streamlit apps as individual Cognito users.
- Authenticated browser-level stress should be added as a follow-up with
  credential-safe Playwright/session handling before the 12-user UAT window.
- Destructive pressure, service restarts, EC2 stop/start, or database mutation
  remain out of scope unless an operator approves a separate chaos experiment.
