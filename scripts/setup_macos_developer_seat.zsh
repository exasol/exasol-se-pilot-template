#!/bin/zsh
# Bootstrap a macOS developer seat for the <PILOT_NAME> repository.
# XYZ-PILOT-179: macOS developer-seat onboarding automation.
#
# Scope:
# - installs missing local utilities through Homebrew;
# - activates/clones the <PILOT_NAME> checkout under ~/GitHub by default;
# - fast-forwards an existing clean main checkout from origin/main;
# - configures the VS Code folder-open fast-forward task;
# - configures repo-local commit hooks and AI commit templates;
# - installs recommended VS Code extensions;
# - opens the repo-local XYZ_PILOT.code-workspace.

set -euo pipefail

readonly DEFAULT_REPO_DIR="$HOME/GitHub/<PROJECT_SLUG>"
readonly DEFAULT_GITHUB_REPO="<GITHUB_OWNER>/<PROJECT_SLUG>"
readonly COPILOT_TRAILER="Co-Authored-By: GitHub Copilot <<OPERATOR_EMAIL>>"
readonly CODEX_TRAILER="Co-Authored-By: OpenAI Codex <<OPERATOR_EMAIL>>"

REPO_DIR="${XYZ_REPO_DIR:-$DEFAULT_REPO_DIR}"
GITHUB_REPO="${XYZ_GITHUB_REPO:-$DEFAULT_GITHUB_REPO}"
WORKSPACE_FILE=""
DRY_RUN=0
OPEN_CODE=1
SKIP_BREW=0
SKIP_GH_AUTH=0
SKIP_UPDATE=0
ASSUME_YES=0

log() {
  print -r -- "[xyz-macos-seat] $*"
}

warn() {
  print -r -- "[xyz-macos-seat][WARN] $*" >&2
}

die() {
  print -r -- "[xyz-macos-seat][ERROR] $*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  zsh scripts/setup_macos_developer_seat.zsh [options]

Options:
  --repo-dir PATH      Target checkout path. Default: ~/GitHub/<PROJECT_SLUG>
  --github-repo OWNER/REPO
                       Repository to clone when --repo-dir is absent.
                       Default: <GITHUB_OWNER>/<PROJECT_SLUG>
  --workspace PATH     VS Code workspace file to open.
                       Default: <repo-dir>/XYZ_PILOT.code-workspace
  --no-open            Configure only; do not start VS Code.
  --skip-brew          Do not install Homebrew packages or VS Code.
  --skip-gh-auth       Do not start gh auth login when GitHub auth is missing.
  --skip-update        Do not fetch/pull an existing checkout from origin/main.
  --yes                Accept non-destructive installation prompts.
  --dry-run            Print intended actions without changing the workstation.
  --help               Show this help.

The script is idempotent. Existing clean checkouts on main are updated with
git pull --ff-only origin main. Dirty checkouts are left untouched.
USAGE
}

expand_path() {
  local raw="$1"
  if [[ "$raw" == "~" ]]; then
    print -r -- "$HOME"
  elif [[ "$raw" == "~/"* ]]; then
    print -r -- "$HOME/${raw#~/}"
  else
    print -r -- "$raw"
  fi
}

run_cmd() {
  log "+ $*"
  if (( DRY_RUN )); then
    return 0
  fi
  "$@"
}

prompt_yes_no() {
  local question="$1"
  if (( ASSUME_YES )); then
    return 0
  fi
  print -n -- "$question [y/N] "
  local reply=""
  read -r reply
  [[ "$reply" == "y" || "$reply" == "Y" || "$reply" == "yes" || "$reply" == "YES" ]]
}

parse_args() {
  while (( $# > 0 )); do
    case "$1" in
      --repo-dir)
        (( $# >= 2 )) || die "--repo-dir requires a path"
        REPO_DIR="$(expand_path "$2")"
        shift 2
        ;;
      --github-repo)
        (( $# >= 2 )) || die "--github-repo requires OWNER/REPO"
        GITHUB_REPO="$2"
        shift 2
        ;;
      --workspace)
        (( $# >= 2 )) || die "--workspace requires a path"
        WORKSPACE_FILE="$(expand_path "$2")"
        shift 2
        ;;
      --no-open)
        OPEN_CODE=0
        shift
        ;;
      --skip-brew)
        SKIP_BREW=1
        shift
        ;;
      --skip-gh-auth)
        SKIP_GH_AUTH=1
        shift
        ;;
      --skip-update)
        SKIP_UPDATE=1
        shift
        ;;
      --yes)
        ASSUME_YES=1
        shift
        ;;
      --dry-run)
        DRY_RUN=1
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        die "unknown option: $1"
        ;;
    esac
  done

  if [[ -z "$WORKSPACE_FILE" ]]; then
    WORKSPACE_FILE="$REPO_DIR/XYZ_PILOT.code-workspace"
  fi
}

require_macos() {
  [[ "$(uname -s)" == "Darwin" ]] || die "this bootstrap is macOS-only"
}

ensure_xcode_tools() {
  if xcode-select -p >/dev/null 2>&1; then
    log "Xcode command line tools: present"
    return 0
  fi

  warn "Xcode command line tools are missing."
  if prompt_yes_no "Start Apple's Xcode command line tools installer now?"; then
    run_cmd xcode-select --install
    warn "Re-run this script after the installer finishes."
    exit 2
  fi
  die "Xcode command line tools are required for git and compiler-backed packages"
}

load_brew_shellenv() {
  if command -v brew >/dev/null 2>&1; then
    return 0
  fi

  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
}

ensure_homebrew() {
  (( SKIP_BREW )) && return 0
  load_brew_shellenv
  if command -v brew >/dev/null 2>&1; then
    log "Homebrew: $(brew --version | head -n 1)"
    return 0
  fi

  warn "Homebrew is missing."
  if prompt_yes_no "Install Homebrew from the official installer now?"; then
    if (( DRY_RUN )); then
      log "+ /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    else
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      load_brew_shellenv
    fi
  else
    die "Homebrew is required unless --skip-brew is used"
  fi
}

brew_install_if_missing() {
  local command_name="$1"
  local package_name="$2"

  if command -v "$command_name" >/dev/null 2>&1; then
    log "$command_name: present"
    return 0
  fi

  (( SKIP_BREW )) && die "$command_name is missing and --skip-brew was set"
  run_cmd brew install "$package_name"
}

install_cli_tools() {
  (( SKIP_BREW )) && return 0
  brew_install_if_missing git git
  brew_install_if_missing gh gh
  brew_install_if_missing aws awscli
  brew_install_if_missing jq jq
  brew_install_if_missing uv uv
  brew_install_if_missing gitleaks gitleaks
  brew_install_if_missing node node
  brew_install_if_missing python3 python@3.13
}

check_optional_agent_clis() {
  if command -v claude >/dev/null 2>&1; then
    log "Claude Code CLI: present"
  else
    warn "Claude Code CLI is not installed; see Developer Runbook section 0.4 if this seat will use Claude Code."
  fi

  if command -v codex >/dev/null 2>&1; then
    log "OpenAI Codex CLI: present"
  else
    warn "OpenAI Codex CLI is not installed; see Developer Runbook section 0.5 if this seat will use Codex."
  fi
}

ensure_vscode() {
  if command -v code >/dev/null 2>&1; then
    log "VS Code CLI: $(command -v code)"
    return 0
  fi

  if [[ ! -d "/Applications/Visual Studio Code.app" ]]; then
    (( SKIP_BREW )) && die "VS Code is missing and --skip-brew was set"
    run_cmd brew install --cask visual-studio-code
  fi

  local app_code="/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"
  if [[ -x "$app_code" && ! -e /usr/local/bin/code ]]; then
    if [[ -w /usr/local/bin ]]; then
      run_cmd ln -s "$app_code" /usr/local/bin/code
    else
      run_cmd mkdir -p "$HOME/bin"
      if [[ ! -e "$HOME/bin/code" ]]; then
        run_cmd ln -s "$app_code" "$HOME/bin/code"
      fi
      warn "Add $HOME/bin to PATH if the code command is still unavailable."
    fi
  fi
}

ensure_github_auth() {
  if ! command -v gh >/dev/null 2>&1; then
    warn "gh is unavailable; skipping GitHub auth check"
    return 0
  fi

  if gh auth status -h github.com >/dev/null 2>&1; then
    log "GitHub CLI auth: present"
    return 0
  fi

  (( SKIP_GH_AUTH )) && {
    warn "GitHub CLI auth missing; skipped by --skip-gh-auth"
    return 0
  }

  warn "GitHub CLI auth is missing."
  if prompt_yes_no "Start gh auth login for github.com now?"; then
    run_cmd gh auth login -h github.com -p https -s repo
  else
    warn "Private repository clone may fail until gh auth login succeeds."
  fi
}

ensure_repo() {
  if [[ -d "$REPO_DIR/.git" ]]; then
    log "Repository checkout: $REPO_DIR"
    run_cmd git -C "$REPO_DIR" status --short --branch
    update_existing_repo
    return 0
  fi

  [[ -e "$REPO_DIR" ]] && die "$REPO_DIR exists but is not a git checkout"

  run_cmd mkdir -p "${REPO_DIR:h}"
  if command -v gh >/dev/null 2>&1 && gh auth status -h github.com >/dev/null 2>&1; then
    run_cmd gh repo clone "$GITHUB_REPO" "$REPO_DIR"
  else
    run_cmd git clone "https://github.com/$GITHUB_REPO.git" "$REPO_DIR"
  fi
}

update_existing_repo() {
  (( SKIP_UPDATE )) && {
    log "Repository update skipped by --skip-update"
    return 0
  }

  if ! git -C "$REPO_DIR" remote get-url origin >/dev/null 2>&1; then
    warn "Repository has no origin remote; skipping origin/main update."
    return 0
  fi

  local local_status=""
  local_status="$(git -C "$REPO_DIR" status --porcelain)"
  if [[ -n "$local_status" ]]; then
    warn "Repository has local changes; skipping origin/main update."
    run_cmd git -C "$REPO_DIR" status --short
    return 0
  fi

  local current_branch=""
  current_branch="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)"
  if [[ "$current_branch" != "main" ]]; then
    warn "Repository is on branch $current_branch, not main; skipping origin/main update."
    return 0
  fi

  run_cmd git -C "$REPO_DIR" fetch origin main
  run_cmd git -C "$REPO_DIR" pull --ff-only origin main
}

write_file_if_missing() {
  local target_path="$1"
  local body="$2"

  if [[ -f "$target_path" ]]; then
    log "$target_path: present"
    return 0
  fi

  run_cmd mkdir -p "${target_path:h}"
  log "writing $target_path"
  if (( ! DRY_RUN )); then
    print -r -- "$body" > "$target_path"
  fi
}

configure_commit_templates() {
  local copilot_template="$HOME/.copilot-commit-template"
  local codex_template="$HOME/.codex-commit-template"

  write_file_if_missing "$copilot_template" $'\n\n'"$COPILOT_TRAILER"
  write_file_if_missing "$codex_template" $'\n\n'"$CODEX_TRAILER"

  local current_template=""
  current_template="$(git -C "$REPO_DIR" config --get commit.template || true)"
  if [[ -z "$current_template" ]]; then
    run_cmd git -C "$REPO_DIR" config commit.template "$copilot_template"
    if (( DRY_RUN )); then
      log "git commit.template would be set for VS Code Copilot commits"
    else
      log "git commit.template set for VS Code Copilot commits"
    fi
  else
    log "git commit.template already set to $current_template; leaving it unchanged"
  fi
}

install_repo_hooks() {
  local attribution="$REPO_DIR/scripts/install_attribution_hook.sh"
  local gitleaks="$REPO_DIR/scripts/install_gitleaks_hook.sh"

  if [[ -x "$attribution" ]]; then
    log "+ (cd $REPO_DIR && scripts/install_attribution_hook.sh)"
    if (( ! DRY_RUN )); then
      (cd "$REPO_DIR" && scripts/install_attribution_hook.sh)
    fi
  fi

  if [[ -x "$gitleaks" ]]; then
    log "+ (cd $REPO_DIR && scripts/install_gitleaks_hook.sh)"
    if (( ! DRY_RUN )); then
      (cd "$REPO_DIR" && scripts/install_gitleaks_hook.sh)
    fi
  fi
}

ensure_workspace_file() {
  if [[ -f "$WORKSPACE_FILE" ]]; then
    log "VS Code workspace: $WORKSPACE_FILE"
    return 0
  fi

  local body='{
  "folders": [
    {
      "name": "<PROJECT_SLUG>",
      "path": "."
    }
  ],
  "settings": {
    "editor.formatOnSave": true,
    "files.autoSave": "afterDelay",
    "git.autofetch": true,
    "terminal.integrated.defaultProfile.osx": "zsh",
    "workbench.editor.enablePreview": false
  },
  "extensions": {
    "recommendations": [
      "GitHub.copilot",
      "GitHub.copilot-chat",
      "eamodio.gitlens",
      "usernamehw.errorlens",
      "EditorConfig.EditorConfig",
      "yzhang.markdown-all-in-one",
      "redhat.vscode-yaml",
      "ms-python.python",
      "ms-python.vscode-pylance",
      "ms-python.black-formatter",
      "ms-azuretools.vscode-docker",
      "ms-vscode-remote.remote-containers",
      "mtxr.sqltools",
      "mtxr.sqltools-driver-exasol"
    ]
  }
}'
  write_file_if_missing "$WORKSPACE_FILE" "$body"
}

ensure_vscode_tasks_file() {
  local tasks_file="$REPO_DIR/.vscode/tasks.json"
  if [[ -f "$tasks_file" ]]; then
    log "VS Code tasks: $tasks_file"
    return 0
  fi

  local body='{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "XYZ: fast-forward repo on workspace open",
      "type": "shell",
      "command": "zsh",
      "args": [
        "scripts/vscode_workspace_open_pull.zsh"
      ],
      "options": {
        "cwd": "${workspaceFolder}"
      },
      "runOptions": {
        "runOn": "folderOpen"
      },
      "presentation": {
        "reveal": "silent",
        "panel": "dedicated",
        "clear": true
      },
      "problemMatcher": []
    }
  ]
}'
  write_file_if_missing "$tasks_file" "$body"
}

install_vscode_extensions() {
  command -v code >/dev/null 2>&1 || {
    warn "code command unavailable; skipping extension installation"
    return 0
  }

  local extensions=(
    GitHub.copilot
    GitHub.copilot-chat
    eamodio.gitlens
    usernamehw.errorlens
    EditorConfig.EditorConfig
    yzhang.markdown-all-in-one
    redhat.vscode-yaml
    ms-python.python
    ms-python.vscode-pylance
    ms-python.black-formatter
    ms-azuretools.vscode-docker
    ms-vscode-remote.remote-containers
    mtxr.sqltools
    mtxr.sqltools-driver-exasol
  )

  local extension=""
  for extension in "${extensions[@]}"; do
    install_vscode_extension "$extension"
  done
}

vscode_extension_present() {
  local extension_id="$1"
  local extension_lower="${extension_id:l}"
  local installed_extensions=""
  installed_extensions="$(code --list-extensions 2>/dev/null || true)"
  [[ $'\n'"${installed_extensions:l}"$'\n' == *$'\n'"$extension_lower"$'\n'* ]]
}

install_vscode_extension() {
  local extension_id="$1"

  if vscode_extension_present "$extension_id"; then
    log "VS Code extension $extension_id: present"
    return 0
  fi

  if ! run_cmd code --install-extension "$extension_id"; then
    warn "VS Code extension $extension_id could not be installed automatically; continuing."
  fi
}

open_workspace() {
  (( OPEN_CODE )) || {
    log "VS Code launch skipped by --no-open"
    return 0
  }

  if command -v code >/dev/null 2>&1; then
    run_cmd code "$WORKSPACE_FILE"
  elif [[ -d "/Applications/Visual Studio Code.app" ]]; then
    run_cmd open -a "Visual Studio Code" "$WORKSPACE_FILE"
  else
    warn "VS Code is not available; open $WORKSPACE_FILE manually after installing it."
  fi
}

print_summary() {
  cat <<SUMMARY

<PILOT_NAME> macOS developer seat bootstrap complete.

Repo:
  $REPO_DIR

Workspace:
  $WORKSPACE_FILE

Next checks inside VS Code:
  1. Sign in to GitHub Copilot if prompted.
  2. Confirm .vscode/mcp.json exposes xyz_pilot_exasol.
  3. In Copilot Chat, verify the tool picker lists xyz_pilot_exasol.
  4. Run: git status --short --branch
  5. Existing clean main checkouts are fast-forwarded from origin/main by default.
  6. On first VS Code workspace open, approve the automatic folder-open task.
  7. Before live AWS work, use AWS_PROFILE=default for the current target per CLAUDE.md Rule #6.

Commit attribution:
  - GitHub Copilot template: $HOME/.copilot-commit-template
  - OpenAI Codex template:  $HOME/.codex-commit-template
  - This checkout only changes commit.template when it was previously unset.

SUMMARY
}

main() {
  parse_args "$@"
  require_macos
  ensure_xcode_tools
  ensure_homebrew
  install_cli_tools
  check_optional_agent_clis
  ensure_vscode
  ensure_github_auth
  ensure_repo
  configure_commit_templates
  install_repo_hooks
  ensure_workspace_file
  ensure_vscode_tasks_file
  install_vscode_extensions
  open_workspace
  print_summary
}

main "$@"
