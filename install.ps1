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

# LEMMA_STACK_SOURCE lets developers bootstrap from a local checkout:
#   $env:LEMMA_STACK_SOURCE = "$PWD\lemma-stack"; .\install.ps1 -y
$lemmaStackSpec = if ($env:LEMMA_STACK_SOURCE) {
    $env:LEMMA_STACK_SOURCE
} else {
    "git+https://github.com/lemma-work/lemma-platform.git#subdirectory=lemma-stack"
}

Say "Installing lemma-stack..."
uv tool install --force $lemmaStackSpec | Out-Null

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
