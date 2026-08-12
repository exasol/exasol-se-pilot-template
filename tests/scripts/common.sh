#!/usr/bin/env bash

# Shared helpers for the <PILOT_NAME> runtime test harness.
# The scripts are intentionally read-only by default and redact common secret
# tokens from captured command output.

set -uo pipefail

xyz_PILOT_FAILS=0
xyz_PILOT_WARNS=0

pilot_find_repo_root() {
  local d
  d="${xyz_PILOT_REPO_ROOT:-$(pwd)}"
  while [ "$d" != "/" ]; do
    if [ -f "$d/CLAUDE.md" ] && [ -d "$d/tests" ]; then
      printf '%s\n' "$d"
      return 0
    fi
    d="$(dirname "$d")"
  done
  git rev-parse --show-toplevel 2>/dev/null
}

pilot_safe_name() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9_.-' '_'
}

pilot_escape_md() {
  printf '%s' "$1" | tr '\n' ' ' | sed 's/|/\\|/g'
}

pilot_redact_file() {
  local f="$1"
  [ -f "$f" ] || return 0
  perl -0pi -e '
    s/(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+\/=-]+/${1}<REDACTED>/gi;
    s/((password|passwd|token|secret|api[_-]?key|access[_-]?key|session[_-]?token)\s*[:=]\s*)[^\s,\n"]+/${1}<REDACTED>/gi;
    s/(AWS_ACCESS_KEY_ID=)[A-Z0-9]+/${1}<REDACTED>/g;
    s/(AWS_SECRET_ACCESS_KEY=)[A-Za-z0-9\/+=]+/${1}<REDACTED>/g;
    s/(AWS_SESSION_TOKEN=)[A-Za-z0-9\/+=]+/${1}<REDACTED>/g;
  ' "$f"
}

pilot_agent_begin() {
  local title="$1"
  xyz_PILOT_AGENT="${xyz_PILOT_AGENT:-$(basename "$(pwd)")}"
  xyz_PILOT_RUN_ID="${xyz_PILOT_RUN_ID:-$(basename "$(dirname "$(pwd)")")}"
  xyz_PILOT_REPO_ROOT="${xyz_PILOT_REPO_ROOT:-$(pilot_find_repo_root)}"
  xyz_PILOT_RUN_DIR="${xyz_PILOT_RUN_DIR:-$(dirname "$(pwd)")}"
  xyz_PILOT_REGION="${xyz_PILOT_REGION:-eu-central-1}"
  xyz_PILOT_EC2_INSTANCE_ID="${xyz_PILOT_EC2_INSTANCE_ID:-<EC2_INSTANCE_ID>}"
  xyz_PILOT_QBR_URL="${xyz_PILOT_QBR_URL:-https://<PROJECT_SLUG>-qbr.<PILOT_DOMAIN>}"
  xyz_PILOT_GRAFANA_URL="${xyz_PILOT_GRAFANA_URL:-https://<PROJECT_SLUG>-grafana.<PILOT_DOMAIN>}"
  xyz_PILOT_CHAT_URL="${xyz_PILOT_CHAT_URL:-https://<PROJECT_SLUG>-chat.<PILOT_DOMAIN>}"
  xyz_mcp_url="${xyz_mcp_url:-https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp}"
  xyz_PILOT_EXASOL_PROFILE="${xyz_PILOT_EXASOL_PROFILE:-xyz-pilot-ro}"

  mkdir -p artifacts
  {
    printf '# %s Commands\n\n' "$xyz_PILOT_AGENT"
    printf -- '- Run: `%s`\n' "$xyz_PILOT_RUN_ID"
    printf -- '- Run log directory: `%s`\n' "$xyz_PILOT_RUN_DIR"
    printf -- '- Agent directory: `%s`\n' "$PWD"
    printf -- '- Artifact directory: `%s/artifacts`\n' "$PWD"
    printf -- '- Started UTC: `%s`\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > commands.md
  {
    printf '# %s Results\n\n' "$xyz_PILOT_AGENT"
    printf -- '- Run: `%s`\n' "$xyz_PILOT_RUN_ID"
    printf -- '- Run log directory: `%s`\n' "$xyz_PILOT_RUN_DIR"
    printf -- '- Agent directory: `%s`\n' "$PWD"
    printf -- '- Candidate file: `%s/issue-candidates.md`\n\n' "$PWD"
    printf '%s\n' "$title"
    if [ -f skills.md ]; then
      printf '\nSkill profile: `skills.md`\n'
    fi
    printf '\n| Probe | Expected | Actual | Result | Notes |\n'
    printf '|---|---|---|---|---|\n'
  } > results.md
  {
    printf '# %s Issue Candidates\n\n' "$xyz_PILOT_AGENT"
    printf -- '- Run: `%s`\n' "$xyz_PILOT_RUN_ID"
    printf -- '- Run log directory: `%s`\n' "$xyz_PILOT_RUN_DIR"
    printf -- '- Agent directory: `%s`\n\n' "$PWD"
    printf 'This file is evidence for `xyz_master`; it is not a tracker edit.\n\n'
  } > issue-candidates.md
}

pilot_result() {
  local probe="$1"
  local expected="$2"
  local actual="$3"
  local result="$4"
  local notes="${5:-}"
  printf '| %s | %s | %s | %s | %s |\n' \
    "$(pilot_escape_md "$probe")" \
    "$(pilot_escape_md "$expected")" \
    "$(pilot_escape_md "$actual")" \
    "$(pilot_escape_md "$result")" \
    "$(pilot_escape_md "$notes")" >> results.md
  case "$result" in
    FAIL*) xyz_PILOT_FAILS=$((xyz_PILOT_FAILS + 1)) ;;
    WARN*|CANDIDATE*) xyz_PILOT_WARNS=$((xyz_PILOT_WARNS + 1)) ;;
  esac
}

pilot_cmd() {
  local label="$1"
  shift
  local safe out err status
  safe="$(pilot_safe_name "$label")"
  out="artifacts/${safe}.out"
  err="artifacts/${safe}.err"
  {
    printf '## %s\n\n' "$label"
    printf -- '- UTC: `%s`\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf -- '- Command: `%s`\n\n' "$*"
  } >> commands.md
  (cd "$xyz_PILOT_REPO_ROOT" && "$@") >"$out" 2>"$err"
  status=$?
  pilot_redact_file "$out"
  pilot_redact_file "$err"
  printf '%s' "$status"
  return 0
}

pilot_cmd_sh() {
  local label="$1"
  local cmd="$2"
  local safe out err status
  safe="$(pilot_safe_name "$label")"
  out="artifacts/${safe}.out"
  err="artifacts/${safe}.err"
  {
    printf '## %s\n\n' "$label"
    printf -- '- UTC: `%s`\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf -- '- Command: `%s`\n\n' "$cmd"
  } >> commands.md
  (cd "$xyz_PILOT_REPO_ROOT" && bash -lc "$cmd") >"$out" 2>"$err"
  status=$?
  pilot_redact_file "$out"
  pilot_redact_file "$err"
  printf '%s' "$status"
  return 0
}

pilot_artifact_text() {
  local label="$1"
  local safe
  safe="$(pilot_safe_name "$label")"
  if [ -s "artifacts/${safe}.out" ]; then
    head -20 "artifacts/${safe}.out" | tr '\n' ' ' | cut -c 1-240
  elif [ -s "artifacts/${safe}.err" ]; then
    head -10 "artifacts/${safe}.err" | tr '\n' ' ' | cut -c 1-240
  else
    printf '<no output>'
  fi
}

pilot_http_probe() {
  local label="$1"
  local url="$2"
  local expected="$3"
  local method="${4:-GET}"
  local header="${5:-}"
  local safe body code status attempt max_attempts
  safe="$(pilot_safe_name "$label")"
  body="artifacts/${safe}.body"
  max_attempts="${xyz_PILOT_HTTP_RETRIES:-3}"
  {
    printf '## %s\n\n' "$label"
    printf -- '- UTC: `%s`\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf -- '- Command: `curl -k -sS --connect-timeout 5 --max-time 20 -o %s -w %%{http_code} -X %s %s`\n' \
      "$body" "$method" "$url"
    printf -- '- Attempts: `%s`\n\n' "$max_attempts"
  } >> commands.md
  status=1
  code="000"
  : >"artifacts/${safe}.err"
  attempt=1
  while [ "$attempt" -le "$max_attempts" ]; do
    if [ -n "$header" ]; then
      code="$(curl -k -sS --connect-timeout 5 --max-time 20 -o "$body" -w '%{http_code}' -X "$method" -H "$header" "$url" 2>"artifacts/${safe}.err")"
    else
      code="$(curl -k -sS --connect-timeout 5 --max-time 20 -o "$body" -w '%{http_code}' -X "$method" "$url" 2>"artifacts/${safe}.err")"
    fi
    status=$?
    case ",$expected," in
      *,"$code",*) break ;;
    esac
    if [ "$status" = "0" ] && [ "$code" != "000" ]; then
      break
    fi
    sleep 1
    attempt=$((attempt + 1))
  done
  pilot_redact_file "$body"
  pilot_redact_file "artifacts/${safe}.err"
  printf '%s:%s' "$status" "$code" >"artifacts/${safe}.status"
  case ",$expected," in
    *,"$code",*) return 0 ;;
    *) return 1 ;;
  esac
}

pilot_body_preview() {
  local label="$1"
  local safe
  safe="$(pilot_safe_name "$label")"
  if [ -f "artifacts/${safe}.body" ]; then
    tr '\n' ' ' <"artifacts/${safe}.body" | cut -c 1-160
  else
    printf '<no body>'
  fi
}

pilot_status_code() {
  local label="$1"
  local safe
  safe="$(pilot_safe_name "$label")"
  cut -d: -f2 "artifacts/${safe}.status" 2>/dev/null || printf 'n/a'
}

pilot_add_candidate() {
  local title="$1"
  shift
  {
    printf '## Candidate: %s\n\n' "$title"
    printf -- '- **Agent:** `%s`\n' "$xyz_PILOT_AGENT"
    printf -- '- **Run:** `%s`\n' "$xyz_PILOT_RUN_ID"
    printf '%s\n\n' "$*"
  } >> issue-candidates.md
}

pilot_agent_finish() {
  {
    printf '\n## Summary\n\n'
    printf -- '- Finished UTC: `%s`\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf -- '- Failures: `%s`\n' "$xyz_PILOT_FAILS"
    printf -- '- Warnings/candidates: `%s`\n' "$xyz_PILOT_WARNS"
  } >> results.md
  cp results.md report.md
  if [ "$xyz_PILOT_FAILS" -gt 0 ]; then
    exit 1
  fi
  exit 0
}
