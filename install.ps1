# Lemma local installer bootstrap for Windows.
#
#   iwr https://raw.githubusercontent.com/lemma-work/lemma-platform/v0.5.4/install.ps1 | iex
#
# Installs uv (if missing), installs lemma-stack as a uv tool, and hands off
# to `lemma-stack install`, which detects Docker Desktop, pulls the released
# images, and starts the stack at ~/.lemma/local. Pass arguments through:
#
#   .\install.ps1 --runtime docker -y
#
# Requires: PowerShell 5.1+ or PowerShell 7+, Docker Desktop running.
#
# SECURITY (BP-003): the `uv tool install` step below is pinned to a tagged
# release, not `main`. A mutable branch would mean every fresh host running
# this one-liner executes the latest commit on `main` with the installing
# user's privileges — a compromised upstream becomes arbitrary code
# execution. Override the pin only with explicit intent — see $env:LEMMA_STACK_REF.

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$StackArgs
)

$ErrorActionPreference = "Stop"

function Say { param([string]$msg) Write-Host $msg }
function Fail { param([string]$msg) Write-Error "error: $msg"; exit 1 }

# Ensure $HOME\.local\bin is on PATH (where uv places tools on Windows)
$uvBin = Join-Path $env:USERPROFILE ".local\bin"
if ($env:PATH -notlike "*$uvBin*") {
    $env:PATH = "$uvBin;$env:PATH"
}

# Install uv if missing
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say "Installing uv (https://astral.sh/uv)..."
    $uvInstaller = Join-Path $env:TEMP "uv-installer.ps1"
    Invoke-RestMethod "https://astral.sh/uv/install.ps1" -OutFile $uvInstaller
    & powershell -ExecutionPolicy Bypass -File $uvInstaller
    Remove-Item $uvInstaller -ErrorAction SilentlyContinue

    # Re-source PATH after uv install
    $env:PATH = "$uvBin;$env:PATH"

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Fail "uv installed but not on PATH. Open a new PowerShell window and re-run."
    }
}

# LEMMA_STACK_PIN is the upstream release tag we install from. Hard-pinning
# (rather than a moving ref like `main` or `releases/latest`) means every
# fresh host gets an immutable, named artifact — easy to audit, easy to
# reproduce. To ship a new release: bump this tag, cut the GitHub release.
$lemmaStackPin = if ($env:LEMMA_STACK_PIN) { $env:LEMMA_STACK_PIN } else { "v0.5.4" }

# Overrides — opt-in only, never the default for first-time users:
#   $env:LEMMA_STACK_SOURCE = "C:\path\to\lemma-stack"; .\install.ps1 -y
#     → install from a local checkout (uv source / editable install).
#   $env:LEMMA_STACK_REF = "v0.5.3";                        .\install.ps1 -y
#     → pin to a specific tag (downgrade / reproducibility).
#   $env:LEMMA_STACK_REF = "main";                          .\install.ps1 -y
#     → EXPLICIT canary opt-in against `main`. You're opting into the same
#       mutable-ref risk this script otherwise defends against; don't do
#       this on a shared / production host.
$lemmaStackSpec = if ($env:LEMMA_STACK_SOURCE) {
    $env:LEMMA_STACK_SOURCE
} else {
    $ref = if ($env:LEMMA_STACK_REF) { $env:LEMMA_STACK_REF } else { $lemmaStackPin }
    "git+https://github.com/lemma-work/lemma-platform.git@${ref}#subdirectory=lemma-stack"
}

Say "Installing lemma-stack..."
Say "  source: $lemmaStackSpec"
uv tool install --force $lemmaStackSpec | Out-Null

if (-not (Get-Command lemma-stack -ErrorAction SilentlyContinue)) {
    $uvToolBin = uv tool dir --bin 2>$null
    if ($uvToolBin) { $env:PATH = "$uvToolBin;$env:PATH" }
}

if (-not (Get-Command lemma-stack -ErrorAction SilentlyContinue)) {
    Fail "lemma-stack installed but not on PATH. Run: uv tool update-shell"
}

& lemma-stack install @StackArgs
