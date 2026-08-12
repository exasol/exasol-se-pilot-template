# VS Code folder-open helper: keep a clean main checkout current on Windows.
# XYZ-PILOT-179: Windows developer-seat onboarding automation.
# XYZ-PILOT-179: append startup summaries instead of overwriting latest.md.
#
# This is intentionally conservative. It fast-forwards origin/main only when
# the checkout is clean and on main. Local developer work is never stashed,
# reset, or overwritten.

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RunUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$RunResult = "UNKNOWN"
$RunMessage = "Started."
$BeforeHead = "unknown"
$BeforeSubject = "unknown"
$AfterHead = "unknown"
$AfterSubject = "unknown"
$Branch = "unknown"
$LogFile = ""
$LatestFile = ""

function Write-Log {
    param([Parameter(Mandatory = $true)][string]$Message)
    $line = "[xyz-vscode-open-pull] $Message"
    Write-Host $line
    if ($script:LogFile) {
        Add-Content -Path $script:LogFile -Value $line -Encoding UTF8
    }
}

function Write-Warn {
    param([Parameter(Mandatory = $true)][string]$Message)
    $line = "[xyz-vscode-open-pull][WARN] $Message"
    Write-Warning $Message
    if ($script:LogFile) {
        Add-Content -Path $script:LogFile -Value $line -Encoding UTF8
    }
}

function Invoke-GitLogged {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & git @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) {
        Write-Host $line
        if ($script:LogFile) {
            Add-Content -Path $script:LogFile -Value $line -Encoding UTF8
        }
    }
    return $exitCode
}

function Get-GitOutput {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & git @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) {
        return ""
    }
    return ($output -join "`n").Trim()
}

function Write-LatestSummary {
    if (-not $script:LatestFile) {
        return
    }

    $script:AfterHead = Get-GitOutput @("rev-parse", "--short", "HEAD")
    if (-not $script:AfterHead) { $script:AfterHead = "unknown" }

    $script:AfterSubject = Get-GitOutput @("log", "-1", "--format=%s")
    if (-not $script:AfterSubject) { $script:AfterSubject = "unknown" }

    $script:Branch = Get-GitOutput @("rev-parse", "--abbrev-ref", "HEAD")
    if (-not $script:Branch) { $script:Branch = "unknown" }

    $status = Get-GitOutput @("status", "--short", "--branch")

    if (-not (Test-Path $script:LatestFile)) {
        $header = @"
# XYZ VS Code Workspace Startup Git Update Log

This local, gitignored file appends one run entry per VS Code workspace-open
git update.

"@
        Set-Content -Path $script:LatestFile -Value $header -Encoding UTF8
    }

    $summary = @"
---

## Run $script:RunUtc

- Last run UTC: ``$script:RunUtc``
- Result: ``$script:RunResult``
- Message: $script:RunMessage
- Repository: ``$repoRoot``
- Branch: ``$script:Branch``
- Before: ``$script:BeforeHead`` - $script:BeforeSubject
- After: ``$script:AfterHead`` - $script:AfterSubject
- Full log: ``$script:LogFile``

## Current Status

````text
$status
````
"@

    Add-Content -Path $script:LatestFile -Value $summary -Encoding UTF8
}

function Finish {
    param(
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [Parameter(Mandatory = $true)][string]$Result,
        [Parameter(Mandatory = $true)][string]$Message
    )
    $script:RunResult = $Result
    $script:RunMessage = $Message
    Write-LatestSummary
    Write-Log "Result: $Result - $Message"
    if ($script:LatestFile) { Write-Log "Latest startup summary: $script:LatestFile" }
    if ($script:LogFile) { Write-Log "Full startup log: $script:LogFile" }
    Write-Log "=== VS Code workspace startup git update end: $script:RunUtc ==="
    exit $ExitCode
}

$repoRoot = Get-GitOutput @("rev-parse", "--show-toplevel")
if (-not $repoRoot) {
    Write-Warning "Not inside a git checkout; skipping origin/main update."
    exit 0
}

Set-Location $repoRoot

$logDir = Join-Path $repoRoot ".vscode\startup-logs"
$script:LogFile = Join-Path $logDir "vscode-workspace-open-pull.log"
$script:LatestFile = Join-Path $logDir "latest.md"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Write-Log "=== VS Code workspace startup git update start: $RunUtc ==="
Write-Log "Repository: $repoRoot"
Write-Log "User: $env:USERNAME"
Write-Log "Host: $env:COMPUTERNAME"

$script:BeforeHead = Get-GitOutput @("rev-parse", "--short", "HEAD")
if (-not $script:BeforeHead) { $script:BeforeHead = "unknown" }

$script:BeforeSubject = Get-GitOutput @("log", "-1", "--format=%s")
if (-not $script:BeforeSubject) { $script:BeforeSubject = "unknown" }

$script:Branch = Get-GitOutput @("rev-parse", "--abbrev-ref", "HEAD")
if (-not $script:Branch) { $script:Branch = "unknown" }

Write-Log "Before: $script:BeforeHead - $script:BeforeSubject"
Write-Log "Branch: $script:Branch"
Write-Log "Status before update:"
[void](Invoke-GitLogged @("status", "--short", "--branch"))

$originUrl = Get-GitOutput @("remote", "get-url", "origin")
if (-not $originUrl) {
    $message = "No origin remote configured; skipping origin/main update."
    Write-Warn $message
    Finish 0 "SKIPPED" $message
}

if ($script:Branch -ne "main") {
    $message = "Current branch is $script:Branch, not main; skipping origin/main update."
    Write-Warn $message
    Finish 0 "SKIPPED" $message
}

$localStatus = Get-GitOutput @("status", "--porcelain")
if ($localStatus) {
    $message = "Local changes exist; skipping origin/main update."
    Write-Warn $message
    [void](Invoke-GitLogged @("status", "--short"))
    Finish 0 "SKIPPED" $message
}

Write-Log "Fetching origin/main."
$fetchExit = Invoke-GitLogged @("fetch", "origin", "main")
if ($fetchExit -ne 0) {
    $message = "git fetch origin main failed; inspect the full startup log."
    Write-Warn $message
    Finish 1 "FAILED" $message
}

Write-Log "Fast-forwarding clean main checkout."
$pullExit = Invoke-GitLogged @("pull", "--ff-only", "origin", "main")
if ($pullExit -ne 0) {
    $message = "git pull --ff-only origin main failed; inspect the full startup log."
    Write-Warn $message
    Finish 1 "FAILED" $message
}

$script:AfterHead = Get-GitOutput @("rev-parse", "--short", "HEAD")
if ($script:AfterHead -eq $script:BeforeHead) {
    $message = "Fetched origin/main; checkout was already current at $script:AfterHead."
    Write-Log "Workspace checkout was already current."
    Write-Log "Status after update:"
    [void](Invoke-GitLogged @("status", "--short", "--branch"))
    Finish 0 "CURRENT" $message
}

$message = "Fast-forwarded clean main checkout from $script:BeforeHead to $script:AfterHead."
Write-Log "Workspace checkout is current."
Write-Log "Status after update:"
[void](Invoke-GitLogged @("status", "--short", "--branch"))
Finish 0 "UPDATED" $message
