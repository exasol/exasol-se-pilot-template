# Exasol Personal cache warm-up (XYZ-PILOT-059)

Pre-loads the Streamlit QBR + Grafana <PILOT_SCHEMA> dashboards' main queries
into Exasol Personal's in-memory column cache at every EC2 boot, so the
first subscriber to hit the dashboards after a start cycle doesn't pay
the cold-cache penalty.

## Why

Exasol Personal is in-memory columnar. The first query that touches a column
reads the relevant blocks from disk into RAM; subsequent queries against
the same column are sub-second. After an EC2 stop (overnight cron) the
cache is cold. The first dashboard hit each morning pays ~5–15 s of cache
warm-up across the 4 QBR tabs; this script does that proactively at boot
so subscribers see the warm latency immediately.

## How

1. **`warmup.py`** — pyexasol script that issues the representative dashboard
   queries (filter_options, the 4 QBR tabs' main aggregations, Data-tab
   catalog walks, exact Grafana panel queries, Exasol monitor queries, §8
   performance-demo rollups, and common chatbot chart patterns). Per-query
   latency is logged to stdout; failures surface as a non-zero exit.
2. **`run_warmup.sh`** — wrapper that sources DB credentials from
   `/opt/xyz-chatbot/mcp-internal.env`, activates the venv, runs `warmup.py`,
   and tees output to `/var/log/xyz-warmup.log`.
3. **`xyz-exasol-warmup.service`** — systemd unit fired at boot after
   `exasol_launcher.target` is reached. This covers both scheduled starts
   (12:00 CEST Mon-Fri) and ad-hoc starts from the operator surfaces because
   both paths cold-start the same EC2 host. Type=oneshot, TimeoutStartSec=600.
4. **`install.sh`** — one-time setup: apt-installs `python3-pip` + `python3-venv`
   if missing, creates `/opt/xyz-warmup/venv` with pyexasol, copies the
   script + unit into place, enables the service.

## Deploy

From the dev mac:

```bash
SSHK=exasol-deployment/node_access.pem
HOST=ubuntu@<EXASOL_HOSTNAME>

# Copy the whole directory to EC2 + run install.sh
scp -i $SSHK -r scripts/exasol_warmup $HOST:/tmp/
ssh -i $SSHK $HOST 'bash /tmp/exasol_warmup/install.sh'
```

## Operate

```bash
# Trigger immediately (without waiting for next boot)
sudo systemctl start xyz-exasol-warmup.service

# Status
sudo systemctl status xyz-exasol-warmup.service

# Live journal
sudo journalctl -u xyz-exasol-warmup.service -f

# Latest log (rolls one-deep to .1 on each fire)
sudo less /var/log/xyz-warmup.log
```

## Validate end-to-end (after a reboot)

```bash
# Stop EC2, wait, start, wait for systemd to settle
aws ec2 stop-instances --instance-ids <EC2_INSTANCE_ID> --region <AWS_REGION>
aws ec2 wait instance-stopped --instance-ids <EC2_INSTANCE_ID> --region <AWS_REGION>
aws ec2 start-instances --instance-ids <EC2_INSTANCE_ID> --region <AWS_REGION>
aws ec2 wait instance-running --instance-ids <EC2_INSTANCE_ID> --region <AWS_REGION>

# Wait a couple of minutes for Exasol to be ready + warmup to run
sleep 180

# Confirm the warmup fired
ssh -i $SSHK $HOST 'sudo cat /var/log/xyz-warmup.log | tail -30'

# Expected output: "==== xyz-warmup starting ====" + "Exasol responding after N
# attempt(s)" + per-query lines + "==== xyz-warmup finished ===="
```

## Query coverage

The warm-up intentionally covers more than the first dashboard render:

- Streamlit QBR filters, tab aggregations, and Data-tab catalog metadata.
- Exact Grafana Infinity proxy SQL for the <PILOT_SCHEMA> load dashboard and
  Exasol Personal monitor dashboard.
- High-value performance demos from the runbook: multi-dimensional
  `GROUPING SETS`, rolling 12-month sales windows, account penetration, and
  latest inventory analytics.
- Common chatbot chart prompts over the pre-materialised `MV_*` tables.

Dynamic exact `COUNT(*)` probes against live `V_*` views are enabled by default
for normal <PILOT_NAME> starts. This trades a longer warm-up for avoiding the
first-user penalty on operator diagnostics and schema-inventory probes. Set
`XYZ_WARMUP_DYNAMIC_VIEWS=0` only for exceptional fast-starts where Exasol
Personal should come online before the live-view diagnostic paths are primed.
When dynamic view warm-up is enabled, any individual `view_count_*` failure or
the combined `view_count_union_all` failure is treated as a warm-up failure so
the start gate cannot silently report success while live-view diagnostics are
cold or broken.

XYZ-PILOT-217 also primes the external diagnostic SQL variants for the expensive
semantic `V_*` views. Exasol's result cache can distinguish semantically
equivalent SQL text, so those views now cover:

- `SELECT COUNT(*) FROM <PILOT_SCHEMA>.VIEW_NAME`
- `SELECT COUNT(*) AS ROW_COUNT FROM <PILOT_SCHEMA>.VIEW_NAME`
- `SELECT COUNT(*) FROM "<PILOT_SCHEMA>"."VIEW_NAME"`
- `SELECT COUNT(*) AS "ROW_COUNT" FROM "<PILOT_SCHEMA>"."VIEW_NAME"`

Every view still receives the base `SELECT COUNT(*)` warm-up, and the
Streamlit-style `UNION ALL` count remains primed for the Data tab. Limiting the
quoted/aliased expansion to `V_*` keeps local `exapump` checks, Streamlit's
explicit Data-tab `COUNT(*)` action, and Swarm schema-inventory probes aligned
without multiplying cold-start cost for raw cube compatibility views.

XYZ-PILOT-292 hardened the cold-start `V_*` phase after a colder node stalled the
unit past its `TimeoutStartSec`:

- **Cold start primes only the base `SELECT COUNT(*)`** per `V_*` view. The
  three quoted/aliased variants above are gated behind
  `XYZ_WARMUP_VVIEW_ALL_VARIANTS=1` (default off) — set it only when you
  explicitly want the result-cache variant coverage on a cold run.
- **The `V_*` phase is best-effort** — an individual `V_*` count miss is logged
  but does not fail the unit (the subscriber-facing `MV_*` paths stay strict and
  are primed first). A between-views budget `XYZ_WARMUP_VVIEW_BUDGET_S` (default
  300s) skips the remaining views + union once exceeded.
- **Each warm-up query is bounded by `XYZ_WARMUP_QUERY_TIMEOUT_S`** (default
  90s), applied as the session `QUERY_TIMEOUT`. A single contended/hung primer
  aborts server-side and the best-effort handler continues, so no one query can
  run the unit past its systemd timeout. `XYZ_WARMUP_CONNECT_TIMEOUT_S` (default
  20s) bounds the initial connect. With these in place a quiet-node cold warm-up
  completes in ~90s with at most a logged best-effort `V_*` miss.

Heavy performance-demo queries (`perf_*`) remain separately controlled by
`XYZ_WARMUP_HEAVY_ANALYTICS`. Set `XYZ_WARMUP_HEAVY_ANALYTICS=1` only for
explicit demo-performance priming.

Keep this list aligned with user-facing query shapes. If a new dashboard panel,
chatbot prompt pattern, or runbook benchmark is added, add a matching
`WARMUP_QUERIES` entry in `warmup.py` before relying on first-user latency.

## Cache eviction

Exasol's column-block cache is LRU. The warm-up sets up the working set; if
the DB serves other queries that exceed RAM, blocks evict. For the Pilot's
workload (~512 K fact rows × 4 dashboards) the working set fits in RAM
indefinitely, so warm-up effectively persists for the session.

## Iterating on the query set

Edit `warmup.py`'s `WARMUP_QUERIES` list, re-run `install.sh` to push the
updated copy into `/opt/xyz-warmup/`. Re-running `install.sh` is idempotent
and doesn't re-create the venv; it just overwrites the scripts and reloads
systemd.

## Stale Target Detection

The <AWS_REGION> migration triage on 2026-06-21 found a target EC2 warm-up copy
older than the repository version. The SSM warm-up output contained:

```text
[warmup]   - dynamic V_view counts skipped (set XYZ_WARMUP_DYNAMIC_VIEWS=1 to enable)
```

That message means the deployed `/opt/xyz-warmup/warmup.py` is stale or the host
explicitly disabled dynamic view warm-up. A current normal-start run should say
`dynamic V_view step enabled` and then print `V_views: priming ... views in
<PILOT_SCHEMA>`. Remediation is to copy `scripts/exasol_warmup/` to the target EC2,
run `install.sh`, then start `xyz-exasol-warmup.service` and confirm the log
contains the dynamic `view_count_*` lines.
