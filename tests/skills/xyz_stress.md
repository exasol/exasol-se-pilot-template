# XYZ_STRESS Skill Profile

Purpose: bounded concurrent-user pressure checks for QBR and Chatbot readiness.

Primary skills:

- `runtime-debugger`
- `streamlit-dashboard-builder`
- `exasol-database`
- `aws-cloudwatch-observability-validator`
- `container-network-telemetry-validator`

Supporting skills:

- `workshop-runner`
- `xyz-ui-ux-dashboard-reviewer`
- `aws-ec2-ssm-runtime-validator`

Default posture:

- Non-destructive and bounded.
- 5 concurrent users by default; scheduled workflow uses 10; approved
  extraordinary/manual runs may use up to 50 with `XYZ_STRESS_ALLOW_HIGHER=1`.
- Public QBR/Chatbot Streamlit shell and health flows plus read-only Exasol
  regression SQL by default.
- The DB lane first opens a short `pyexasol` session-hold connection per
  virtual user so Grafana / `EXA_DBA_SESSIONS` can prove the requested
  concurrency, then uses `exapump sql --profile xyz-pilot-ro` / `XYZ_DASH`,
  extracts `scripts/exasol_warmup/warmup.py::WARMUP_QUERIES`, discovers current
  `<PILOT_SCHEMA>` views, and executes those query shapes per virtual user.
- Dynamic `view_count_*` probes are schema-inventory evidence, not core UAT
  gates. They intentionally expose expensive live-view `COUNT(*)` paths such as
  `V_MONTHLY_METRICS`, `V_ACTUAL_VS_TARGET`, `V_YOY_METRICS`, and
  `view_count_union_all`, but those paths are advisory unless an operator is
  explicitly qualifying schema inventory behavior.
- Known heavy analytical SQL probes remain visible in the stress artefacts but
  are advisory by default. The nightly gate should prove the UAT core path
  (web shells, session holds, catalog/read-path queries, and MV-backed
  dashboard/chatbot shapes) without failing solely because exploratory
  grouping/window/account-penetration queries exceed Exasol Personal's
  concurrent analytical envelope.
- No passwords, Cognito tokens, cookies, or chatbot prompts are printed.
- Authenticated browser-level filter/prompt stress requires separate explicit
  operator scope and credential-safe tooling.

Stress controls:

- `XYZ_STRESS_DB_ITERATIONS` defaults to `1`; keep scheduled runs at this unless
  an operator asks for heavier pressure.
- `XYZ_STRESS_DB_QUERY_LIMIT=0` means run the full warm-up query pack; set a
  positive value to cap scheduled or diagnostic runs.
- `XYZ_STRESS_HEAVY_ANALYTICS_MODE=advisory` is the default; set `blocking`
  for explicit performance qualification runs where heavy analytical SQL should
  fail the run, or `skip` when isolating the core UAT read path.
- `XYZ_STRESS_SESSION_HOLD_SECONDS` defaults to `15`; scheduled GitHub runs use
  `20` so the Exasol Personal Monitor can visibly show concurrent sessions.
- `XYZ_STRESS_SKIP_DB=1` reverts to HTTP-only stress for isolating web-surface
  regressions.
- `XYZ_STRESS_SKIP_SESSION_HOLD=1` skips the observable pyexasol hold but keeps
  the `exapump` query pack.
- `XYZ_STRESS_REQUIRE_SESSION_HOLD=1` makes missing hold credentials a hard
  failure; the scheduled GitHub workflow sets this because it has the masked
  `XYZ_DASH` credential available from SSM.
- `XYZ_STRESS_SKIP_DYNAMIC_VIEWS=1` skips the dynamic `V_*` count probes when
  isolating static warm-up-query behavior.

XYZ-PILOT-217 guardrail:

- If HTTP probes pass while `view_count_V_MONTHLY_METRICS`,
  `view_count_V_ACTUAL_VS_TARGET`, `view_count_V_YOY_METRICS`, or
  `view_count_union_all` time out near `XYZ_STRESS_DB_TIMEOUT_SECONDS`, treat it
  as a schema-inventory/live-view performance signal. First verify that the
  target EC2 has `xyz-exasol-warmup.service` installed and enabled; then check
  whether the QBR Data tab or any chatbot prompt path is forcing exact live-view
  counts instead of using MV-backed dashboard tables.
- Do not run `scripts/exasol_statistics/*` from inside `XYZ_STRESS` or any
  normal swarm tester. Statistics refresh is a database-maintenance remediation,
  not a read-only test action. If stale statistics are suspected after restore
  or model reorganization, emit/reuse an issue candidate and recommend
  `scripts/exasol_statistics/analyze_all_tables.sh` or the large-table
  `analyze_table_pyexasol.py` companion for an operator-approved maintenance
  run.
