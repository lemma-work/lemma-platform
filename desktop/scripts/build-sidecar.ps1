$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location $RepoRoot

$Triple = if ($env:LEMMA_SIDECAR_TRIPLE) { $env:LEMMA_SIDECAR_TRIPLE } else { "x86_64-pc-windows-msvc" }
$OutDir = Join-Path $RepoRoot "desktop/binaries"
$WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("lemma-sidecar-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $OutDir, $WorkDir | Out-Null

try {
    cargo build --manifest-path locald/Cargo.toml --release --target $Triple
    Copy-Item "locald/target/$Triple/release/lemma-locald.exe" "$OutDir/lemma-locald-$Triple.exe"
    cargo build --manifest-path local-runtime/hostctl/Cargo.toml --release --target $Triple
    Copy-Item "local-runtime/hostctl/target/$Triple/release/lemma-runtime.exe" "$OutDir/lemma-runtime-$Triple.exe"

    uv run --project lemma-stack --with pyinstaller pyinstaller `
        --onefile --noconfirm `
        --name lemma-supervisor `
        --collect-data lemma_stack `
        --distpath $OutDir `
        --workpath "$WorkDir/build" `
        --specpath $WorkDir `
        lemma-stack/lemma_stack/sidecar_main.py

    Move-Item "$OutDir/lemma-supervisor.exe" "$OutDir/lemma-supervisor-$Triple.exe" -Force
    $UvBinary = (Get-Command uv).Source
    Copy-Item $UvBinary "$OutDir/uv-$Triple.exe"

    & "$OutDir/lemma-supervisor-$Triple.exe" --help | Out-Null
    & "$OutDir/lemma-locald-$Triple.exe" --version | Out-Null
    & "$OutDir/lemma-runtime-$Triple.exe" --version | Out-Null
    & "$OutDir/uv-$Triple.exe" --version | Out-Null
    Write-Host "Windows sidecars: smoke ok"
}
finally {
    Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
}
