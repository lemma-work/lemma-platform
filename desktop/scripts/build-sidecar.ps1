$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location $RepoRoot

$Triple = if ($env:LEMMA_SIDECAR_TRIPLE) { $env:LEMMA_SIDECAR_TRIPLE } else { "x86_64-pc-windows-msvc" }
$OutDir = Join-Path $RepoRoot "desktop/binaries"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

cargo build --manifest-path locald/Cargo.toml --release --target $Triple
Copy-Item "locald/target/$Triple/release/lemma-locald.exe" "$OutDir/lemma-locald-$Triple.exe"
cargo build --manifest-path local-runtime/hostctl/Cargo.toml --release --target $Triple
Copy-Item "local-runtime/hostctl/target/$Triple/release/lemma-runtime.exe" "$OutDir/lemma-runtime-$Triple.exe"

& "$OutDir/lemma-locald-$Triple.exe" --version | Out-Null
& "$OutDir/lemma-runtime-$Triple.exe" --version | Out-Null
Write-Host "Windows runtime helpers: smoke ok"
