$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location $RepoRoot

$Triple = if ($env:LEMMA_SIDECAR_TRIPLE) { $env:LEMMA_SIDECAR_TRIPLE } else { "x86_64-pc-windows-msvc" }
$OutDir = Join-Path $RepoRoot "desktop/binaries"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# One invocation, not three: they share a target directory now, and asking for
# them separately resolves features over three package sets and rebuilds the
# common dependency tree each time.
# $ErrorActionPreference governs cmdlets, not native executables, and
# $PSNativeCommandUseErrorActionPreference is off by default even on
# PowerShell 7 -- so a failed cargo build does not stop this script. It used to
# fall through to Copy-Item and surface as "cannot find path
# ...\lemma-locald.exe", which sends whoever hit it looking for a missing file
# instead of reading the compiler error just above it. Every native call below
# is checked for the same reason; desktop.ps1 has done this all along.
cargo build --manifest-path desktop/Cargo.toml --release --target $Triple `
  -p lemma-locald -p lemma-agent-host -p lemma-runtime
if ($LASTEXITCODE -ne 0) { throw "cargo build failed (exit $LASTEXITCODE)" }
$Built = "desktop/target/$Triple/release"
Copy-Item "$Built/lemma-locald.exe" "$OutDir/lemma-locald-$Triple.exe"
Copy-Item "$Built/lemma-agent-host.exe" "$OutDir/lemma-agent-host-$Triple.exe"
Copy-Item "$Built/lemma-runtime.exe" "$OutDir/lemma-runtime-$Triple.exe"

# The smoke tests are the point of this block: a sidecar that cannot start is
# one Tauri will happily bundle. Discarding their exit codes made them decorative.
foreach ($helper in @("lemma-locald", "lemma-agent-host", "lemma-runtime")) {
    & "$OutDir/$helper-$Triple.exe" --version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "$helper smoke test failed (exit $LASTEXITCODE)" }
}
Write-Host "Windows runtime helpers: smoke ok"
