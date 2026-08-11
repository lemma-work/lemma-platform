$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location $RepoRoot

$Triple = if ($env:LEMMA_SIDECAR_TRIPLE) { $env:LEMMA_SIDECAR_TRIPLE } else { "x86_64-pc-windows-msvc" }
$OutDir = Join-Path $RepoRoot "desktop/binaries"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# One invocation, not three: they share a target directory now, and asking for
# them separately resolves features over three package sets and rebuilds the
# common dependency tree each time.
cargo build --manifest-path desktop/Cargo.toml --release --target $Triple `
  -p lemma-locald -p lemma-agent-host -p lemma-runtime
$Built = "desktop/target/$Triple/release"
Copy-Item "$Built/lemma-locald.exe" "$OutDir/lemma-locald-$Triple.exe"
Copy-Item "$Built/lemma-agent-host.exe" "$OutDir/lemma-agent-host-$Triple.exe"
Copy-Item "$Built/lemma-runtime.exe" "$OutDir/lemma-runtime-$Triple.exe"

& "$OutDir/lemma-locald-$Triple.exe" --version | Out-Null
& "$OutDir/lemma-agent-host-$Triple.exe" --version | Out-Null
& "$OutDir/lemma-runtime-$Triple.exe" --version | Out-Null
Write-Host "Windows runtime helpers: smoke ok"
