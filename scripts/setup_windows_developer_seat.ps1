# Bootstrap a Windows 10/11 developer seat for the <PILOT_NAME> repository.
# XYZ-PILOT-179: Windows developer-seat onboarding automation.
#
# Scope:
# - installs missing local utilities through winget when available;
# - activates/clones the <PILOT_NAME> checkout under %USERPROFILE%\GitHub by default;
# - fast-forwards an existing clean main checkout from origin/main;
# - configures VS Code workspace startup update logging;
# - configures repo-local commit hooks and AI commit templates;
# - installs recommended VS Code extensions;
# - opens the repo-local XYZ_PILOT.code-workspace.

[CmdletBinding()]
param(
    [string]$RepoDir = (Join-Path $HOME "GitHub\<PROJECT_SLUG>"),
    [string]$GitHubRepo = "<GITHUB_OWNER>/<PROJECT_SLUG>",
    [string]$Workspace = "",
    [switch]$NoOpen,
    [switch]$SkipWinget,
    [switch]$SkipGhAuth,
    [switch]$SkipUpdate,
    [switch]$Yes,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CopilotTrailer = "Co-Authored-By: GitHub Copilot <<OPERATOR_EMAIL>>"
$CodexTrailer = "Co-Authored-By: OpenAI Codex <<OPERATOR_EMAIL>>"

if (-not $Workspace) {
    $Workspace = Join-Path $RepoDir "XYZ_PILOT.code-workspace"
}

function Write-SeatLog {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host "[xyz-windows-seat] $Message"
}

function Write-SeatWarn {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Warning "[xyz-windows-seat] $Message"
}

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Display,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    Write-SeatLog "+ $Display"
    if ($DryRun) {
        return
    }
    & $Action
}

function Invoke-GitChecked {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    Invoke-Step "git $($Arguments -join ' ')" {
        & git @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
        }
    }
}

function Test-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Add-CommonPathEntries {
    $candidates = @(
        "$env:ProgramFiles\Git\cmd",
        "$env:ProgramFiles\Git\bin",
        "$env:ProgramFiles\GitHub CLI",
        "$env:ProgramFiles\Amazon\AWSCLIV2",
        "$env:LOCALAPPDATA\Programs\Microsoft VS Code\bin",
        "$env:ProgramFiles\Microsoft VS Code\bin",
        "$env:LOCALAPPDATA\Microsoft\WindowsApps"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate) -and ($env:Path -notlike "*$candidate*")) {
            $env:Path = "$candidate;$env:Path"
        }
    }
}

function Assert-Windows {
    if ($PSVersionTable.PSVersion.Major -lt 5) {
        throw "PowerShell 5.1 or newer is required."
    }
    if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
        throw "This bootstrap is Windows-only."
    }
    Write-SeatLog "Windows: $([System.Environment]::OSVersion.VersionString)"
    Write-SeatLog "PowerShell: $($PSVersionTable.PSVersion)"
}

function Ensure-Winget {
    if ($SkipWinget) {
        return
    }
    if (Test-Command winget) {
        Write-SeatLog "winget: present"
        return
    }
    throw "winget is missing. Install App Installer from Microsoft Store, then rerun, or use -SkipWinget after manual tool installation."
}

function Install-WingetPackageIfMissing {
    param(
        [Parameter(Mandatory = $true)][string]$CommandName,
        [Parameter(Mandatory = $true)][string[]]$PackageIds
    )
    Add-CommonPathEntries
    if (Test-Command $CommandName) {
        Write-SeatLog "$CommandName`: present"
        return
    }
    if ($SkipWinget) {
        throw "$CommandName is missing and -SkipWinget was set."
    }

    foreach ($packageId in $PackageIds) {
        try {
            Invoke-Step "winget install --id $packageId --exact" {
                winget install --id $packageId --exact --source winget --accept-package-agreements --accept-source-agreements
            }
            Add-CommonPathEntries
            if (Test-Command $CommandName) {
                Write-SeatLog "$CommandName`: installed via $packageId"
                return
            }
        } catch {
            Write-SeatWarn "winget install failed for $packageId`: $($_.Exception.Message)"
        }
    }

    Write-SeatWarn "$CommandName was not detected after winget install. A new PowerShell session may be required."
}

function Install-Tooling {
    Ensure-Winget
    Install-WingetPackageIfMissing git @("Git.Git")
    Install-WingetPackageIfMissing gh @("GitHub.cli")
    Install-WingetPackageIfMissing aws @("Amazon.AWSCLI")
    Install-WingetPackageIfMissing jq @("jqlang.jq", "stedolan.jq")
    Install-WingetPackageIfMissing uv @("astral-sh.uv")
    Install-WingetPackageIfMissing gitleaks @("Gitleaks.Gitleaks", "gitleaks.gitleaks")
    Install-WingetPackageIfMissing node @("OpenJS.NodeJS.LTS")
    Install-WingetPackageIfMissing python @("Python.Python.3.13", "Python.Python.3.12")
    Install-WingetPackageIfMissing code @("Microsoft.VisualStudioCode")
}

function Check-OptionalAgentClis {
    if (Test-Command claude) {
        Write-SeatLog "Claude Code CLI: present"
    } else {
        Write-SeatWarn "Claude Code CLI is not installed; see Developer Runbook section 0.4 if this seat will use Claude Code."
    }

    if (Test-Command codex) {
        Write-SeatLog "OpenAI Codex CLI: present"
    } else {
        Write-SeatWarn "OpenAI Codex CLI is not installed; see Developer Runbook section 0.5 if this seat will use Codex."
    }
}

function Ensure-GitHubAuth {
    if (-not (Test-Command gh)) {
        Write-SeatWarn "gh is unavailable; skipping GitHub auth check."
        return
    }
    & gh auth status -h github.com *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-SeatLog "GitHub CLI auth: present"
        return
    }
    if ($SkipGhAuth) {
        Write-SeatWarn "GitHub CLI auth missing; skipped by -SkipGhAuth."
        return
    }

    Invoke-Step "gh auth login -h github.com -p https -s repo" {
        gh auth login -h github.com -p https -s repo
    }
}

function Update-ExistingRepo {
    if ($SkipUpdate) {
        Write-SeatLog "Repository update skipped by -SkipUpdate."
        return
    }
    & git -C $RepoDir remote get-url origin *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-SeatWarn "Repository has no origin remote; skipping origin/main update."
        return
    }
    $localStatus = (& git -C $RepoDir status --porcelain) -join "`n"
    if ($localStatus) {
        Write-SeatWarn "Repository has local changes; skipping origin/main update."
        Invoke-Step "git -C `"$RepoDir`" status --short" {
            git -C $RepoDir status --short
        }
        return
    }
    $branch = ((& git -C $RepoDir rev-parse --abbrev-ref HEAD) -join "").Trim()
    if ($branch -ne "main") {
        Write-SeatWarn "Repository is on branch $branch, not main; skipping origin/main update."
        return
    }
    Invoke-GitChecked @("-C", $RepoDir, "fetch", "origin", "main")
    Invoke-GitChecked @("-C", $RepoDir, "pull", "--ff-only", "origin", "main")
}

function Ensure-Repo {
    if (Test-Path (Join-Path $RepoDir ".git")) {
        Write-SeatLog "Repository checkout: $RepoDir"
        Invoke-Step "git -C `"$RepoDir`" status --short --branch" {
            git -C $RepoDir status --short --branch
        }
        Update-ExistingRepo
        return
    }

    if (Test-Path $RepoDir) {
        $children = @(Get-ChildItem -Force -Path $RepoDir -ErrorAction SilentlyContinue)
        if ($children.Count -gt 0) {
            throw "$RepoDir exists but is not a git checkout and is not empty."
        }
    }

    $parent = Split-Path -Parent $RepoDir
    Invoke-Step "New-Item -ItemType Directory -Force `"$parent`"" {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }

    if ((Test-Command gh)) {
        & gh auth status -h github.com *> $null
        if ($LASTEXITCODE -eq 0) {
            Invoke-Step "gh repo clone $GitHubRepo `"$RepoDir`"" {
                gh repo clone $GitHubRepo $RepoDir
                if ($LASTEXITCODE -ne 0) {
                    throw "gh repo clone failed with exit code $LASTEXITCODE"
                }
            }
            return
        }
    }

    Invoke-GitChecked @("clone", "https://github.com/$GitHubRepo.git", $RepoDir)
}

function Write-TextFileIfMissing {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    if (Test-Path $Path) {
        Write-SeatLog "$Path`: present"
        return
    }
    $parent = Split-Path -Parent $Path
    Invoke-Step "New-Item -ItemType Directory -Force `"$parent`"" {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    Invoke-Step "write $Path" {
        Set-Content -Path $Path -Value $Content -Encoding UTF8
    }
}

function Configure-CommitTemplates {
    $copilotTemplate = Join-Path $HOME ".copilot-commit-template"
    $codexTemplate = Join-Path $HOME ".codex-commit-template"
    Write-TextFileIfMissing $copilotTemplate "`r`n`r`n$CopilotTrailer"
    Write-TextFileIfMissing $codexTemplate "`r`n`r`n$CodexTrailer"

    $currentTemplate = ((& git -C $RepoDir config --get commit.template 2>$null) -join "").Trim()
    if (-not $currentTemplate) {
        Invoke-Step "git -C `"$RepoDir`" config commit.template `"$copilotTemplate`"" {
            git -C $RepoDir config commit.template $copilotTemplate
        }
        Write-SeatLog "git commit.template set for VS Code Copilot commits."
    } else {
        Write-SeatLog "git commit.template already set to $currentTemplate; leaving it unchanged."
    }
}

function Install-RepoHooks {
    $hooksDir = Join-Path $RepoDir ".git\hooks"
    $commitSrc = Join-Path $RepoDir "scripts\hooks\commit-msg-attribution"
    $commitDst = Join-Path $hooksDir "commit-msg"
    $preCommitSrc = Join-Path $RepoDir "scripts\hooks\pre-commit-gitleaks"
    $preCommitDst = Join-Path $hooksDir "pre-commit"

    Invoke-Step "New-Item -ItemType Directory -Force `"$hooksDir`"" {
        New-Item -ItemType Directory -Force -Path $hooksDir | Out-Null
    }
    if (Test-Path $commitSrc) {
        Invoke-Step "Copy attribution hook to .git\hooks\commit-msg" {
            Copy-Item -Force $commitSrc $commitDst
        }
    }
    if (Test-Path $preCommitSrc) {
        Invoke-Step "Copy gitleaks hook to .git\hooks\pre-commit" {
            Copy-Item -Force $preCommitSrc $preCommitDst
        }
    }
}

function Ensure-VsCodeTasksFile {
    $tasksFile = Join-Path $RepoDir ".vscode\tasks.json"
    if (Test-Path $tasksFile) {
        Write-SeatLog "VS Code tasks: $tasksFile"
        return
    }
    Write-SeatWarn "VS Code tasks file is missing. Pull latest origin/main or restore .vscode/tasks.json from the repository."
}

function Install-VsCodeExtensions {
    if (-not (Test-Command code)) {
        Write-SeatWarn "code command unavailable; skipping extension installation."
        return
    }

    $extensions = @(
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
    )

    $installed = @()
    try {
        $installed = & code --list-extensions 2>$null
    } catch {
        $installed = @()
    }
    $installedLower = @($installed | ForEach-Object { $_.ToLowerInvariant() })

    foreach ($extension in $extensions) {
        if ($installedLower -contains $extension.ToLowerInvariant()) {
            Write-SeatLog "VS Code extension $extension`: present"
            continue
        }
        try {
            Invoke-Step "code --install-extension $extension" {
                code --install-extension $extension
            }
        } catch {
            Write-SeatWarn "VS Code extension $extension could not be installed automatically; continuing."
        }
    }
}

function Open-Workspace {
    if ($NoOpen) {
        Write-SeatLog "VS Code launch skipped by -NoOpen."
        return
    }
    if (Test-Command code) {
        Invoke-Step "code `"$Workspace`"" {
            code $Workspace
        }
    } else {
        Write-SeatWarn "VS Code is not available; open $Workspace manually after installing it."
    }
}

Assert-Windows
Add-CommonPathEntries
Install-Tooling
Check-OptionalAgentClis
Ensure-GitHubAuth
Ensure-Repo
Configure-CommitTemplates
Install-RepoHooks
Ensure-VsCodeTasksFile
Install-VsCodeExtensions
Open-Workspace

Write-Host ""
Write-SeatLog "<PILOT_NAME> Windows developer seat bootstrap complete."
Write-Host "Repo:      $RepoDir"
Write-Host "Workspace: $Workspace"
Write-Host ""
Write-Host "Next checks inside VS Code:"
Write-Host "  1. Approve automatic workspace tasks when prompted."
Write-Host "  2. Sign in to GitHub Copilot if prompted."
Write-Host "  3. Confirm .vscode\mcp.json exposes xyz_pilot_exasol."
Write-Host "  4. Inspect .vscode\startup-logs\latest.md after workspace open."
Write-Host "  5. Before live AWS work, use AWS_PROFILE=default for the current target per CLAUDE.md Rule #6."
