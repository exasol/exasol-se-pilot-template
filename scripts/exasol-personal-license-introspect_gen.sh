#!/usr/bin/env bash
# =============================================================================
#  exasol-personal-license-introspect.sh
#  Generic license & database introspection for an Exasol Personal deployment.
#
#  Tested on:       macOS 14+ (bash 3.2), Linux (bash 4+/5+)
#  Maintained by:   Exasol Solution Engineering
#
#  WHAT IT DOES
#  ------------
#    1. Asks the operator which deployment to inspect, accepting either:
#         (a) a local "exasol" deployment directory (the dir you created with
#             `exasol init` / `exasol install`), OR
#         (b) explicit SSH coordinates (host + private key + ssh-user) for any
#             reachable Exasol Personal node, regardless of cloud provider.
#    2. Pulls the AUTHORITATIVE license details from ConfD over SSH:
#             c4 connect -s cos -i <play_id> -- confd_client license_info
#         (Exasol 2025.x exposes no license fields via SQL; ConfD is the only
#          source of truth.)
#    3. Optionally, if you also provide the SQL host/port/password, runs a
#       handful of read-only SQL queries that complement the license file
#       (engine version, storage usage, active sessions).
#    4. Writes every artifact to ./exasol-introspect-<UTC-timestamp>/.
#
#  STDIN-SAFETY NOTE
#  -----------------
#    Every `c4 connect ... -- confd_client …` call is invoked with `</dev/null`
#    and via `ssh -n`. `c4 connect` is the deployment-internal ssh-equivalent
#    and will otherwise consume the OpenSSH channel's stdin, silently
#    truncating the remainder of the remote script. Do not remove these
#    guards.
#
#  PREREQUISITES
#  -------------
#    macOS:   brew install jq openssh
#             # (optional, for SQL section) brew install python && pip3 install pyexasol
#    Linux:   apt/yum install jq openssh-client
#             # (optional, for SQL section) python3 -m pip install --user pyexasol
#
#  USAGE
#  -----
#    Interactive (recommended on first run):
#        ./exasol-personal-license-introspect.sh
#
#    Non-interactive (e.g. CI / repeated runs):
#        ./exasol-personal-license-introspect.sh \
#            --node ubuntu@<dns-or-ip> \
#            --ssh-key ~/.ssh/exasol_node.pem \
#            [--state-dir /path/to/exasol-deployment-dir] \
#            [--sql-host <dns-or-ip> --sql-port 8563 --sql-user sys --sql-pass-env SYS_PW]
#
#    Pull credentials from an `exasol` CLI deployment directory:
#        ./exasol-personal-license-introspect.sh --state-dir ~/exasol-deployments/prod
# =============================================================================

set -euo pipefail

# ---------------------------- Helpers ---------------------------------------
ts()    { date -u +%Y%m%dT%H%M%SZ ; }
log()   { printf '\033[1;36m[%s] %s\033[0m\n' "$(date -u +%H:%M:%SZ)" "$*" ; }
warn()  { printf '\033[1;33m[%s] WARN: %s\033[0m\n' "$(date -u +%H:%M:%SZ)" "$*" ; }
err()   { printf '\033[1;31m[%s] ERR : %s\033[0m\n' "$(date -u +%H:%M:%SZ)" "$*" >&2 ; }

ask()   {
    # ask "Prompt" [default]   -> echoes the answer
    local prompt="$1" default="${2:-}" reply
    if [[ -n "$default" ]]; then
        read -r -p "$prompt [$default]: " reply </dev/tty
        echo "${reply:-$default}"
    else
        read -r -p "$prompt: " reply </dev/tty
        echo "$reply"
    fi
}

ask_secret() {
    local prompt="$1" reply
    read -r -s -p "$prompt: " reply </dev/tty
    echo >/dev/tty
    echo "$reply"
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || { err "Missing required command: $1"; exit 2; }
}

# ---------------------------- Argument parsing ------------------------------
NODE=""
SSH_KEY=""
STATE_DIR=""
SQL_HOST=""
SQL_PORT="8563"
SQL_USER=""
SQL_PASS_ENV=""
INTERACTIVE=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --node)         NODE="$2";        shift 2 ;;
        --ssh-key)      SSH_KEY="$2";     shift 2 ;;
        --state-dir)    STATE_DIR="$2";   shift 2 ;;
        --sql-host)     SQL_HOST="$2";    shift 2 ;;
        --sql-port)     SQL_PORT="$2";    shift 2 ;;
        --sql-user)     SQL_USER="$2";    shift 2 ;;
        --sql-pass-env) SQL_PASS_ENV="$2"; shift 2 ;;
        --non-interactive) INTERACTIVE=0; shift ;;
        -h|--help)
            sed -n '2,55p' "$0"
            exit 0 ;;
        *) err "Unknown flag: $1"; exit 2 ;;
    esac
done

# ---------------------------- Prereq checks ---------------------------------
log "Checking prerequisites…"
need_cmd ssh
need_cmd jq

HAVE_PYEXASOL=0
if command -v python3 >/dev/null 2>&1 && python3 -c "import pyexasol" 2>/dev/null; then
    HAVE_PYEXASOL=1
fi

# ---------------------------- Determine inputs ------------------------------
# Mode A: caller passed --state-dir  -> autoload SSH + SQL creds from there
# Mode B: caller passed --node + --ssh-key explicitly
# Mode C: interactive — ask for whichever is missing

if [[ -n "$STATE_DIR" ]]; then
    [[ -d "$STATE_DIR" ]] || { err "--state-dir not a directory: $STATE_DIR"; exit 2; }
    log "Loading deployment context from $STATE_DIR"

    # The `exasol` CLI bundles each deployment with secrets.json + node_access.pem.
    # We accept either the live deployment dir OR an extracted state bundle dir.
    if [[ -z "$SSH_KEY" && -f "$STATE_DIR/node_access.pem" ]]; then
        SSH_KEY="$STATE_DIR/node_access.pem"
        chmod 600 "$SSH_KEY" 2>/dev/null || true
    fi
    if [[ -z "$SQL_USER" && -f "$STATE_DIR/secrets.json" ]]; then
        SQL_USER="$(jq -r '.dbUsername // .sysUsername // "sys"' "$STATE_DIR/secrets.json")"
        SQL_PASS_FROM_FILE="$(jq -r '.dbPassword // .sysPassword // .databasePassword // empty' "$STATE_DIR/secrets.json")"
        if [[ -n "$SQL_PASS_FROM_FILE" ]]; then
            export __SQL_PASS_FROM_FILE="$SQL_PASS_FROM_FILE"
            SQL_PASS_ENV="__SQL_PASS_FROM_FILE"
        fi
    fi
    if [[ -z "$NODE" ]]; then
        # connection-instructions.txt (shipped by the exasol CLI) carries the host.
        if [[ -f "$STATE_DIR/connection-instructions.txt" ]]; then
            HOST_GUESS="$(grep -Eo '[a-zA-Z0-9.-]+\.(amazonaws\.com|cloudapp\.azure\.com|exoscale\.com|com|net|io)' \
                          "$STATE_DIR/connection-instructions.txt" | head -1 || true)"
            SSH_USER_GUESS="$(grep -Eo 'ssh[[:space:]]+(-i[[:space:]]+[^[:space:]]+[[:space:]]+)?[a-z][a-z0-9_-]*@' \
                              "$STATE_DIR/connection-instructions.txt" | sed -E 's/.*[[:space:]]([a-z][a-z0-9_-]*)@/\1/' | head -1 || true)"
            [[ -n "$HOST_GUESS" && -n "$SSH_USER_GUESS" ]] && NODE="${SSH_USER_GUESS}@${HOST_GUESS}"
        fi
        if [[ -z "$NODE" && -f "$STATE_DIR/deployment.json" ]]; then
            HOST_GUESS="$(jq -r '.publicDns // .publicIp // .host // empty' "$STATE_DIR/deployment.json")"
            [[ -n "$HOST_GUESS" ]] && NODE="ubuntu@${HOST_GUESS}"
        fi
    fi
fi

if [[ -z "$NODE" && "$INTERACTIVE" -eq 1 ]]; then
    cat >/dev/tty <<EOF

────────────────────────────────────────────────────────────────────────────
  Exasol Personal — License Introspection
────────────────────────────────────────────────────────────────────────────
  Provide SSH access to ANY node of your Exasol Personal deployment.
  Works equally for aws / azure / exoscale presets.

  Examples of the NODE field:
    ubuntu@<EXASOL_HOSTNAME>
    azureuser@40.118.74.12
    ubuntu@my-exasol-host.example.com
EOF
    NODE="$(ask 'SSH target (user@host-or-ip)')"
fi

if [[ -z "$SSH_KEY" && "$INTERACTIVE" -eq 1 ]]; then
    SSH_KEY="$(ask 'Path to private SSH key' "$HOME/.ssh/id_rsa")"
fi

[[ -n "$NODE"    ]] || { err "SSH target (--node) is required"; exit 2; }
[[ -n "$SSH_KEY" ]] || { err "SSH key (--ssh-key) is required"; exit 2; }
[[ -f "$SSH_KEY" ]] || { err "SSH key file not found: $SSH_KEY"; exit 2; }
chmod 600 "$SSH_KEY" 2>/dev/null || true

OUT_DIR="./exasol-introspect-$(ts)"
EVIDENCE_DIR="$OUT_DIR/evidence"
mkdir -p "$EVIDENCE_DIR"

log "Output directory: $OUT_DIR"
log "SSH target:       $NODE"
log "SSH key:          $SSH_KEY"

# ---------------------------- 1. SSH reachability ---------------------------
log "Phase 1 — SSH reachability probe"
ssh -n -i "$SSH_KEY" \
    -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=15 \
    "$NODE" 'hostnamectl 2>/dev/null || uname -a ; uptime' \
    > "$EVIDENCE_DIR/ssh-probe.txt" 2>&1 \
    || { err "SSH connection failed. See $EVIDENCE_DIR/ssh-probe.txt"; cat "$EVIDENCE_DIR/ssh-probe.txt" >&2; exit 3; }
log "SSH OK"

# ---------------------------- 2. ConfD license details ---------------------
log "Phase 2 — Querying ConfD (authoritative license source)"

# NOTE: every `c4 connect ... -- confd_client …` MUST close stdin with
# `</dev/null`. See header note.
#
# CRITICAL: do NOT pipe this script into `ssh -n host "bash -s"`. ssh -n
# redirects ssh's stdin from /dev/null, and `bash -s` reads its program from
# stdin -> immediate EOF -> nothing runs. We instead pass the remote script
# as ssh's last positional argument (the well-trodden `ssh host '<script>'`
# pattern), which sends it via the SSH exec channel and is decoupled from
# stdin entirely. Empirically verified against a live Exasol Personal node.
REMOTE_SCRIPT='
set -u
PLAY_ID=$(c4 ps 2>/dev/null | awk "NR==2 {print \$2}")
if [ -z "$PLAY_ID" ] ; then
    echo "ERROR: could not discover PLAY_ID via c4 ps" >&2
    c4 ps 2>&1 || true
    exit 2
fi
confd_client() {
    if command -v timeout >/dev/null 2>&1 ; then
        timeout --kill-after=5 "${CONFD_TIMEOUT_SECONDS:-20}" c4 connect -s cos -i "$PLAY_ID" -- confd_client "$@" </dev/null
    else
        c4 connect -s cos -i "$PLAY_ID" -- confd_client "$@" </dev/null
    fi
}
DB_NAME=$(confd_client db_list 2>/dev/null \
          | awk "NR==1 {gsub(/^- /,\"\"); print}")
echo "### play_id = $PLAY_ID"
echo "### db_name = $DB_NAME"
echo "### timestamp_utc = $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

echo "===== confd_client license_info ====="
confd_client license_info
echo

echo "===== confd_client license_run_check ====="
confd_client license_run_check
echo

echo "===== confd_client db_state (db_name=$DB_NAME) ====="
if [ -n "$DB_NAME" ] ; then
    confd_client db_state db_name: "$DB_NAME"
else
    echo "(skipped: db_name could not be auto-discovered)"
fi
'

SSH_COMMAND=(ssh -n -i "$SSH_KEY" \
    -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=20 \
    "$NODE" "$REMOTE_SCRIPT")

if command -v timeout >/dev/null 2>&1 ; then
    timeout --kill-after=5 "${CONFD_SSH_TIMEOUT_SECONDS:-90}" "${SSH_COMMAND[@]}" \
        | tee "$EVIDENCE_DIR/confd_license_info.txt"
else
    "${SSH_COMMAND[@]}" | tee "$EVIDENCE_DIR/confd_license_info.txt"
fi

if [[ ! -s "$EVIDENCE_DIR/confd_license_info.txt" ]] \
   || ! grep -q '^Contract:' "$EVIDENCE_DIR/confd_license_info.txt" ; then
    warn "confd_license_info.txt looks empty or missing the 'Contract:' block."
    warn "Inspect $EVIDENCE_DIR/confd_license_info.txt — common causes:"
    warn "  · The node has no PLAY_ID (deployment not started yet)"
    warn "  · The c4 binary is not on PATH for the SSH user (try --node root@…)"
    warn "  · A c4 connect call ate stdin — verify </dev/null is intact in this script"
fi

# ---------------------------- 3. Optional SQL context ----------------------
log "Phase 3 — Optional SQL context queries"

if [[ "$HAVE_PYEXASOL" -ne 1 ]]; then
    warn "Skipping SQL queries: python3 + pyexasol not installed."
    warn "Install with:  pip3 install --user pyexasol"
    log  "License details above are still complete and authoritative."
else
    if [[ -z "$SQL_HOST" && "$INTERACTIVE" -eq 1 ]]; then
        SQL_HOST_DEFAULT="$(echo "$NODE" | sed 's/^[^@]*@//')"
        SQL_HOST="$(ask 'Exasol SQL host (DNS or IP — usually same as SSH host)' "$SQL_HOST_DEFAULT")"
    fi
    if [[ -z "$SQL_USER" && "$INTERACTIVE" -eq 1 ]]; then
        SQL_USER="$(ask 'Exasol SQL user' 'sys')"
    fi
    if [[ -z "$SQL_PASS_ENV" ]]; then
        if [[ "$INTERACTIVE" -eq 1 ]]; then
            SQL_PASS="$(ask_secret "Exasol SQL password for $SQL_USER")"
            export __SQL_PASS_ENTERED="$SQL_PASS"
            SQL_PASS_ENV="__SQL_PASS_ENTERED"
        else
            warn "Non-interactive mode: --sql-pass-env not set; skipping SQL section."
            SQL_HOST=""
        fi
    fi

    if [[ -n "$SQL_HOST" ]]; then
        EXASOL_HOST="$SQL_HOST" EXASOL_PORT="$SQL_PORT" \
        DB_USER="$SQL_USER" DB_PASS="${!SQL_PASS_ENV}" \
        EVIDENCE_DIR="$EVIDENCE_DIR" \
        python3 - <<'PY'
import os, sys, pyexasol

HOST   = os.environ["EXASOL_HOST"]
PORT   = int(os.environ["EXASOL_PORT"])
USER   = os.environ["DB_USER"]
PASS   = os.environ["DB_PASS"]
EVID   = os.environ["EVIDENCE_DIR"]

try:
    C = pyexasol.connect(
        dsn=f"{HOST}:{PORT}", user=USER, password=PASS,
        encryption=True, websocket_sslopt={"cert_reqs": 0},
        connection_timeout=15, query_timeout=30,
    )
except Exception as e:
    print(f"[!] SQL connect FAILED: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)  # don't fail the whole script — ConfD output is the authoritative source
print(f"[+] Connected: {HOST}:{PORT} as {USER}")

QUERIES = [
    ("01_engine_metadata", """
        SELECT PARAM_NAME, PARAM_VALUE
        FROM EXA_METADATA
        WHERE PARAM_NAME IN (
            'databaseName','databaseProductName','databaseProductVersion',
            'databaseMajorVersion','databaseMinorVersion','databaseIncrementVersion',
            'productVersion','collationSequence'
        )
        ORDER BY PARAM_NAME
    """),
    ("02_storage_usage_recent", """
        SELECT MEASURE_TIME,
               RAW_OBJECT_SIZE/1024/1024/1024 AS RAW_GB,
               MEM_OBJECT_SIZE/1024/1024/1024 AS MEM_GB,
               STORAGE_SIZE/1024/1024/1024    AS STORAGE_GB,
               RECOMMENDED_DB_RAM_SIZE        AS RECOMMENDED_RAM_GB,
               USE                            AS USAGE_PCT,
               OBJECT_COUNT
        FROM EXA_DB_SIZE_LAST_DAY
        ORDER BY MEASURE_TIME DESC
        LIMIT 6
    """),
    ("03_system_events", """
        SELECT CLUSTER_NAME, MEASURE_TIME, EVENT_TYPE, DBMS_VERSION,
               NODES, DB_RAM_SIZE, VCPU
        FROM EXA_SYSTEM_EVENTS
        ORDER BY MEASURE_TIME DESC
        LIMIT 10
    """),
    ("04_active_sessions", """
        SELECT SESSION_ID, USER_NAME, CLIENT, DRIVER, HOST, LOGIN_TIME, STATUS
        FROM EXA_DBA_SESSIONS
        ORDER BY LOGIN_TIME DESC
    """),
    ("05_dba_users", """
        SELECT USER_NAME, CREATED
        FROM EXA_DBA_USERS
        ORDER BY USER_NAME
    """),
]

for label, sql in QUERIES:
    print(f"\n=== {label} ===")
    try:
        st = C.execute(sql)
        rows = st.fetchall()
        cols = st.col_names
        path = os.path.join(EVID, f"{label}.tsv")
        with open(path, "w") as f:
            f.write("\t".join(cols) + "\n")
            for r in rows:
                f.write("\t".join("" if v is None else str(v) for v in r) + "\n")
        widths = [max(len(c), *(len(str(r[i])) if r[i] is not None else 0
                                for r in rows[:20])) for i,c in enumerate(cols)]
        print("  " + "  ".join(c.ljust(widths[i]) for i,c in enumerate(cols)))
        print("  " + "  ".join("-"*widths[i] for i in range(len(cols))))
        for r in rows[:20]:
            print("  " + "  ".join((str(v) if v is not None else "").ljust(widths[i])
                                   for i,v in enumerate(r)))
        if len(rows) > 20:
            print(f"  ... ({len(rows)-20} more rows in {path})")
    except Exception as e:
        print(f"  [!] {type(e).__name__}: {e}")

C.close()
print("\n[+] SQL context queries complete.")
PY
    fi
fi

# ---------------------------- 4. Final summary -----------------------------
log ""
log "════════════════════════════════════════════════════════════════════════"
log " DONE.  All artifacts:  $OUT_DIR/"
log "════════════════════════════════════════════════════════════════════════"
log " Key file (authoritative license details):"
log "   $EVIDENCE_DIR/confd_license_info.txt"
log ""
log " Fields to read inside the 'Contract:' and 'Limits:' blocks:"
log "   license_id, company_name, expiration_date, distributor,"
log "   max_session_limit, max_db_mem_size_in_gb,"
log "   max_db_raw_data_size_in_gb, max_nodes_per_cluster, max_num_clusters"
log "════════════════════════════════════════════════════════════════════════"
