<#
.SYNOPSIS
    Build, test, and lint Lemma Desktop on Windows.

.DESCRIPTION
    The repository's Makefile is the macOS and Linux entrypoint. Windows has no
    `make`, so the same verbs live here, one per `desktop-*` target, over the
    same underlying scripts — build-sidecar.ps1, cargo, extract-concepts.mjs,
    prepare_desktop_test_runtime.py. Neither entrypoint reimplements the other,
    so they cannot drift.

    There is deliberately no `dev` verb. Running the app from source is macOS
    only for now: dev-local.sh has no Windows counterpart, and the WSL
    distribution name the managed runtime uses is a global constant, so a dev
    run in a throwaway state root would adopt and mutate the distro a real
    install owns. On Windows, build and install the app instead — `exe`.

.EXAMPLE
    pwsh desktop\scripts\desktop.ps1 test
    pwsh desktop\scripts\desktop.ps1 runtime-fetch -Run 12345678901
    pwsh desktop\scripts\desktop.ps1 exe
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('sidecars', 'test', 'test-app', 'fmt', 'lint', 'concepts',
                 'runtime-fetch', 'exe', 'clean', 'version-check', 'help')]
    [string]$Verb,

    # fmt: rewrite instead of checking.
    [switch]$Fix,
    # concepts: fail on drift instead of baking.
    [switch]$Check,
    # runtime-fetch: the Actions run to pull host packs and guest runtimes from.
    [string]$Run
)

$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$DesktopDir = Join-Path $RepoRoot 'desktop'
$DownloadDir = Join-Path $DesktopDir 'runtime/download'
$BundledDir = Join-Path $DesktopDir 'runtime/bundled'
$Triple = 'x86_64-pc-windows-msvc'
$GuestTarget = 'windows-x86_64'
$TauriCli = "@tauri-apps/cli@$((Get-Content (Join-Path $PSScriptRoot 'tauri-cli-version.txt')).Trim())"

# lemma-guestd is the Linux guest daemon and reaches for std::os::unix
# unconditionally, so it does not compile for Windows at all. The macOS and
# Linux runs cover it; here it has to be skipped by name.
$CargoScope = @('--workspace', '--exclude', 'lemma-guestd')

function Step($message) { Write-Host "→ $message" }
function Ok($message) { Write-Host "  ✓ $message" }
function Fail($message) { Write-Host "  ✗ $message"; exit 1 }

function Require-Command($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { Fail "$name not found — $hint" }
}

function Invoke-Checked($description) {
    if ($LASTEXITCODE -ne 0) { Fail "$description failed (exit $LASTEXITCODE)" }
}

function Invoke-Concepts([switch]$Strict) {
    Require-Command node 'install Node.js 22 from https://nodejs.org'
    Step 'Baking splash concepts…'
    node (Join-Path $DesktopDir 'scripts/extract-concepts.mjs')
    Invoke-Checked 'extract-concepts.mjs'
    if ($Strict) {
        git -C $RepoRoot diff --exit-code desktop/ui/concepts.gen.json
        if ($LASTEXITCODE -ne 0) {
            Fail 'desktop/ui/concepts.gen.json is stale — commit the regenerated file'
        }
    }
}

switch ($Verb) {
    'help' {
        Write-Host ''
        Write-Host 'Lemma Desktop on Windows'
        Write-Host ''
        Write-Host '  desktop.ps1 sidecars              build locald, Agent Host, runtime bridge'
        Write-Host '  desktop.ps1 test                  Rust tests across the desktop workspace'
        Write-Host '  desktop.ps1 test-app              desktop crate tests only (fast loop)'
        Write-Host '  desktop.ps1 lint                  clippy, warnings are errors'
        Write-Host '  desktop.ps1 fmt [-Fix]            rustfmt check, or rewrite'
        Write-Host '  desktop.ps1 concepts [-Check]     bake desktop/ui/concepts.gen.json'
        Write-Host '  desktop.ps1 runtime-fetch -Run <id>  download runtime artifacts from a CI run'
        Write-Host '  desktop.ps1 exe                   self-contained Windows installer'
        Write-Host '  desktop.ps1 clean                 remove build output and staged runtime'
        Write-Host '  desktop.ps1 version-check         every component declares the same version'
        Write-Host ''
        Write-Host '  Running the app from source is macOS only; install the .exe instead.'
        Write-Host ''
    }

    'sidecars' {
        Require-Command cargo 'install Rust from https://rustup.rs'
        & (Join-Path $PSScriptRoot 'build-sidecar.ps1')
        Invoke-Checked 'build-sidecar.ps1'
    }

    'test' {
        Require-Command cargo 'install Rust from https://rustup.rs'
        Step 'Desktop workspace tests…'
        Push-Location $DesktopDir
        try {
            cargo test @CargoScope --locked
            Invoke-Checked 'cargo test'
        } finally { Pop-Location }
        Ok 'desktop workspace tests pass'
    }

    'test-app' {
        Require-Command cargo 'install Rust from https://rustup.rs'
        Step 'Desktop app tests…'
        Push-Location $DesktopDir
        try {
            cargo test -p lemma-desktop --locked
            Invoke-Checked 'cargo test -p lemma-desktop'
        } finally { Pop-Location }
    }

    'fmt' {
        Require-Command cargo 'install Rust from https://rustup.rs'
        Push-Location $DesktopDir
        try {
            if ($Fix) {
                Step 'Rewriting the desktop workspace with rustfmt…'
                cargo fmt --all
            } else {
                Step 'Desktop rustfmt…'
                cargo fmt --all --check
            }
            Invoke-Checked 'cargo fmt'
        } finally { Pop-Location }
    }

    'lint' {
        Require-Command cargo 'install Rust from https://rustup.rs'
        Step 'Desktop workspace clippy…'
        Push-Location $DesktopDir
        try {
            cargo clippy @CargoScope --locked --all-targets -- -D warnings
            Invoke-Checked 'cargo clippy'
        } finally { Pop-Location }
        Ok 'clippy clean'
    }

    'concepts' { Invoke-Concepts -Strict:$Check }

    'runtime-fetch' {
        Require-Command gh 'install from https://cli.github.com'
        if (-not $Run) {
            Write-Host '-Run is required, e.g. desktop.ps1 runtime-fetch -Run 12345678901'
            Write-Host ''
            Write-Host '  Cut one first:'
            Write-Host '    gh workflow run release-local-images.yml -f version=0.7.0 -f publish=false'
            Write-Host '    gh run list --workflow release-local-images.yml'
            exit 1
        }
        Step "Downloading runtime artifacts from run $Run…"
        if (Test-Path $DownloadDir) { Remove-Item -Recurse -Force $DownloadDir }
        gh run download $Run --dir $DownloadDir `
            --pattern 'host-pack-*' `
            --pattern 'guest-runtime-*' `
            --pattern 'lemma-local-test-manifest-*'
        if ($LASTEXITCODE -ne 0) {
            Fail "download failed — is $Run a Release Local Stack Images run with publish: false?"
        }
        Ok "artifacts in $DownloadDir"
    }

    # The one command that turns this checkout into an installable Windows
    # build. "Self-contained" means the installer carries the host pack and the
    # guest runtime as app resources instead of downloading them on first
    # launch, so it installs on a machine with no network and against no
    # published release.
    #
    # This mirrors release-local-images.yml's windows-test-desktop job and
    # shares its staging engine, so a green local build and a green CI build
    # mean the same thing.
    'exe' {
        Require-Command cargo 'install Rust from https://rustup.rs'
        Require-Command node 'install Node.js 22 from https://nodejs.org'
        Require-Command python 'install Python 3 from https://python.org'
        if (-not (Test-Path $DownloadDir)) {
            Write-Host "  ✗ no runtime artifacts in $DownloadDir"
            Fail 'fetch them first: desktop.ps1 runtime-fetch -Run <run-id>'
        }

        Step "Staging the bundled runtime for $Triple…"
        python (Join-Path $RepoRoot 'scripts/prepare_desktop_test_runtime.py') `
            --artifacts-dir $DownloadDir `
            --mode bundled `
            --host-target $Triple `
            --guest-target $GuestTarget `
            --stage-dir $BundledDir `
            --output (Join-Path $BundledDir 'lemma-local.json')
        Invoke-Checked 'prepare_desktop_test_runtime.py'
        Ok 'host and guest archives verified and staged'

        Invoke-Concepts -Strict
        Step 'Building native sidecars…'
        & (Join-Path $PSScriptRoot 'build-sidecar.ps1')
        Invoke-Checked 'build-sidecar.ps1'

        Step 'Bundling the self-contained installer…'
        Push-Location $DesktopDir
        try {
            npx -y $TauriCli build --config tauri.dist.conf.json --bundles nsis
            Invoke-Checked 'tauri build'
        } finally { Pop-Location }

        # NSIS is an LZMA self-extractor, so unlike a macOS .app the payload
        # cannot be walked after packaging without a tool that understands the
        # container. What can be checked directly is what went in.
        $payload = @(
            "$DesktopDir/target/release/lemma-desktop.exe",
            "$DesktopDir/binaries/lemma-locald-$Triple.exe",
            "$DesktopDir/binaries/lemma-agent-host-$Triple.exe",
            "$DesktopDir/binaries/lemma-runtime-$Triple.exe",
            "$BundledDir/lemma-local.json",
            "$BundledDir/host-runtime.zip",
            "$BundledDir/guest-runtime.zip"
        )
        $bytes = ($payload | ForEach-Object {
            if (-not (Test-Path $_)) { Fail "bundled payload is missing: $_" }
            (Get-Item $_).Length
        } | Measure-Object -Sum).Sum
        if ($bytes -gt 850MB) {
            Fail "bundled payload is $bytes bytes; the gate is 850 MiB"
        }

        $manifest = Get-Content (Join-Path $BundledDir 'lemma-local.json') | ConvertFrom-Json
        $conf = Get-Content (Join-Path $DesktopDir 'tauri.conf.json') | ConvertFrom-Json
        if ($manifest.version -ne $conf.version) {
            Fail 'the bundled runtime manifest and Desktop disagree on the version'
        }

        # SilentlyContinue so a missing bundle reports the sentence below rather
        # than an ItemNotFoundException from the glob itself.
        $installers = @(Get-ChildItem "$DesktopDir/target/release/bundle/nsis/Lemma_*-setup.exe" `
            -ErrorAction SilentlyContinue)
        if ($installers.Count -ne 1) {
            Fail "expected exactly one NSIS installer, found $($installers.Count)"
        }
        Write-Host ''
        Ok $installers[0].FullName
        Write-Host '    Unsigned: SmartScreen will warn on first run.'
    }

    'clean' {
        Step 'Removing desktop build output…'
        foreach ($path in @(
            "$DesktopDir/target", "$DesktopDir/binaries", "$DesktopDir/gen",
            "$DesktopDir/permissions/autogenerated", $DownloadDir, $BundledDir
        )) {
            if (Test-Path $path) { Remove-Item -Recurse -Force $path }
        }
        Ok 'clean'
    }

    'version-check' {
        Require-Command python 'install Python 3 from https://python.org'
        Step 'Component versions…'
        python (Join-Path $RepoRoot 'scripts/check_version_consistency.py')
        Invoke-Checked 'check_version_consistency.py'
    }
}
