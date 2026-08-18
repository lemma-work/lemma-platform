# Lemma local installer bootstrap for Windows.
#
#   iwr https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.ps1 | iex
#
# Installs uv (if missing) and installs lemma-stack as a uv tool. By default it
# then starts the external Docker/Podman compatibility installer. Managed
# Windows users should install Lemma Desktop and use -CliOnly:
#
#   .\install.ps1 -CliOnly
#   .\install.ps1 --runtime docker -y  # external compatibility path

param(
    [switch]$CliOnly,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$StackArgs
)

$ErrorActionPreference = "Stop"

function Say { param([string]$msg) Write-Host $msg }
# Writes to stderr and exits 1, the way install.sh's `fail` does. Not
# Write-Error: under `Stop` that raises a terminating error, so the `exit 1`
# after it never ran and the caller got a PowerShell exception trace instead of
# the one-line message and the exit code this is supposed to produce.
function Fail { param([string]$msg) [Console]::Error.WriteLine("error: $msg"); exit 1 }

# install.sh runs under `set -Eeuo pipefail`, so any command that fails stops
# the script. PowerShell has no equivalent for native executables --
# $ErrorActionPreference governs cmdlets, and a non-zero exit from uv.exe or
# lemma-stack.exe is not an error it can see -- so every one of them is checked
# by hand below. Without that this script reported a successful install no
# matter what happened, which is the one thing an installer must never do.
function Require-ExitCode { param([string]$what) if ($LASTEXITCODE -ne 0) { Fail "$what (exit code $LASTEXITCODE)" } }

# `--cli-only` is what install.sh accepts and what the docs show. PowerShell
# binds `-CliOnly`, and anything it does not recognise falls into $StackArgs --
# so the documented spelling used to be forwarded to `lemma-stack install` as a
# stray argument and the user got the full runtime installer they asked to skip.
if ($StackArgs -contains "--cli-only") {
    $CliOnly = $true
    $StackArgs = @($StackArgs | Where-Object { $_ -ne "--cli-only" })
}

# Ensure $HOME\.local\bin is on PATH (where uv places tools on Windows)
$uvBin = Join-Path $env:USERPROFILE ".local\bin"
if ($env:PATH -notlike "*$uvBin*") {
    $env:PATH = "$uvBin;$env:PATH"
}

# Install uv if missing
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say "Installing uv (https://astral.sh/uv)..."
    $uvInstaller = Join-Path $env:TEMP "uv-installer.ps1"
    # Matches the curl flags install.sh uses: a bounded connect timeout and
    # retries, so a flaky network fails with a sentence rather than a raw
    # WebException half a minute later.
    try {
        Invoke-RestMethod "https://astral.sh/uv/install.ps1" `
            -OutFile $uvInstaller `
            -TimeoutSec 20 `
            -MaximumRetryCount 5 `
            -RetryIntervalSec 2
    } catch {
        Fail "could not download the uv installer; check your network and re-run"
    }
    & powershell -ExecutionPolicy Bypass -File $uvInstaller
    $uvInstallerExit = $LASTEXITCODE
    Remove-Item $uvInstaller -ErrorAction SilentlyContinue
    if ($uvInstallerExit -ne 0) { Fail "the uv installer failed (exit code $uvInstallerExit)" }

    # Re-source PATH after uv install
    $env:PATH = "$uvBin;$env:PATH"

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Fail "uv installed but not on PATH. Open a new PowerShell window and re-run."
    }
}

# LEMMA_STACK_SOURCE lets developers bootstrap from a local checkout:
#   $env:LEMMA_STACK_SOURCE = "$PWD\lemma-stack"; .\install.ps1 -y
$lemmaStackSpec = if ($env:LEMMA_STACK_SOURCE) {
    $env:LEMMA_STACK_SOURCE
} else {
    "git+https://github.com/lemma-work/lemma-platform.git#subdirectory=lemma-stack"
}

Say "Installing lemma-stack..."
uv tool install --force $lemmaStackSpec | Out-Null
# Checked before the PATH probes below, so a failed install reports itself
# rather than being reported as "installed but not on PATH".
Require-ExitCode "could not install lemma-stack"

if (-not (Get-Command lemma-stack -ErrorAction SilentlyContinue)) {
    $uvToolBin = uv tool dir --bin 2>$null
    if ($uvToolBin) { $env:PATH = "$uvToolBin;$env:PATH" }
}

if (-not (Get-Command lemma-stack -ErrorAction SilentlyContinue)) {
    Fail "lemma-stack installed but not on PATH. Run: uv tool update-shell"
}

if ($CliOnly -or $env:LEMMA_STACK_CLI_ONLY -eq "1") {
    & lemma-stack self register-cli --use
    if ($LASTEXITCODE -ne 0) { Fail "Could not register the managed local server." }
    Say "Installed lemma-stack. It will discover Lemma Desktop after Local setup has run once."
    exit 0
}

& lemma-stack install @StackArgs
# install.sh ends in `exec`, so the runtime installer's exit code *is* the
# script's. PowerShell has no exec, and without this the bootstrap swallowed
# that code and reported success for a failed install -- to a user who piped
# this whole file into `iex` and has nothing else to check.
exit $LASTEXITCODE
