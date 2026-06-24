# Lemma local installer bootstrap for Windows.
#
#   iwr https://raw.githubusercontent.com/lemma-work/lemma-platform/main/install.ps1 | iex
#
# Installs uv (if missing), installs lemma-stack as a uv tool, and hands off
# to `lemma-stack install`, which detects Docker Desktop, pulls the released
# images, and starts the stack at ~/.lemma/local. Pass arguments through:
#
#   .\install.ps1 --runtime docker -y
#
# Requires: PowerShell 5.1+ or PowerShell 7+, Docker Desktop running.

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

& lemma-stack install @StackArgs
