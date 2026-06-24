"""Cross-platform install experience tests.

Verifies that a fresh installation of lemma-cli and lemma-stack works correctly
on Linux (via Docker containers) and macOS (native). Windows is covered by a
dedicated CI job on windows-latest (see ci.yml `windows-cli`).

Run locally:
    cd lemma-stack && uv run pytest tests/test_install_experience.py -v

Skip Docker-based tests:
    uv run pytest tests/test_install_experience.py -v -k "not docker"
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# --- Version helpers ---------------------------------------------------------

def _read_cli_version() -> str:
    init_file = REPO_ROOT / "lemma-cli" / "lemma_cli" / "__init__.py"
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init_file.read_text())
    if not match:
        raise ValueError("Could not find __version__ in lemma_cli/__init__.py")
    return match.group(1)


def _read_sdk_version() -> str:
    pyproject = REPO_ROOT / "lemma-python" / "pyproject.toml"
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.MULTILINE)
    if not match:
        raise ValueError("Could not find version in lemma-python/pyproject.toml")
    return match.group(1)


def _read_ts_version() -> str:
    version_ts = REPO_ROOT / "lemma-typescript" / "src" / "version.ts"
    match = re.search(r'SDK_VERSION\s*=\s*"([^"]+)"', version_ts.read_text())
    if not match:
        raise ValueError("Could not find SDK_VERSION in version.ts")
    return match.group(1)


CLI_VERSION = _read_cli_version()
SDK_VERSION = _read_sdk_version()
TS_VERSION = _read_ts_version()


# --- Docker helpers ----------------------------------------------------------

def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"], capture_output=True, text=True, check=False
    )
    return result.returncode == 0


def run_in_container(
    image: str,
    script: str,
    *,
    timeout: int = 300,
    writable: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    mount_mode = "" if writable else ":ro"
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{REPO_ROOT}:/repo{mount_mode}",
        "-e", "DEBIAN_FRONTEND=noninteractive",
    ]
    if env:
        for key, val in env.items():
            cmd += ["-e", f"{key}={val}"]
    cmd += [image, "bash", "-c", script]
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


# --- Linux CLI install (Docker: python:3.12-slim) ---------------------------

@pytest.mark.install_experience
@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_docker_linux_lemma_cli_install():
    """Fresh install of lemma-cli in a python:3.12-slim container.

    Simulates a Python-savvy user who installs via ``uv tool install`` from a
    local checkout. Verifies ``lemma --version``, ``lemma --help``, and
    ``lemma doctor`` all work and report the correct version.
    """
    result = run_in_container(
        "python:3.12-slim",
        f"""
set -e
pip install --quiet uv
cd /repo/lemma-cli
uv tool install . 2>&1 | tail -3
export PATH="$HOME/.local/bin:$PATH"

# --version
VERSION=$(lemma --version 2>&1)
echo "$VERSION"
echo "$VERSION" | grep -q "lemma {CLI_VERSION}" || {{ echo "FAIL: CLI version"; exit 1; }}
echo "$VERSION" | grep -q "lemma-sdk {SDK_VERSION}" || {{ echo "FAIL: SDK version"; exit 1; }}

# --help
HELP=$(lemma --help 2>&1)
echo "$HELP" | grep -q "Usage:" || {{ echo "FAIL: no Usage in help"; exit 1; }}
echo "$HELP" | grep -q "auth" || {{ echo "FAIL: no auth command"; exit 1; }}
echo "$HELP" | grep -q "pod" || {{ echo "FAIL: no pod command"; exit 1; }}

# doctor (runs without crashing, even with no server)
DOCTOR=$(lemma doctor 2>&1)
echo "$DOCTOR" | grep -q "lemma {CLI_VERSION}" || {{ echo "FAIL: doctor missing version"; exit 1; }}

echo "ALL CHECKS PASSED"
""",
    )
    assert result.returncode == 0, (
        f"Linux CLI install failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert "ALL CHECKS PASSED" in result.stdout


# --- Linux install.sh (Docker: ubuntu:24.04) --------------------------------

@pytest.mark.install_experience
@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_docker_linux_install_sh_lemma_stack():
    """Full install.sh flow in an ubuntu:24.04 container.

    Simulates a real user who runs the one-line install script. Verifies that
    uv is installed, lemma-stack is installed as a uv tool, and basic
    lemma-stack commands respond. The ``--no-start`` flag skips starting the
    actual stack (no container runtime inside the container).
    """
    result = run_in_container(
        "ubuntu:24.04",
        f"""
set -e
apt-get update -qq && apt-get install -y -qq curl ca-certificates python3 2>&1 | tail -3

export LEMMA_STACK_SOURCE=/repo/lemma-stack
export LEMMA_CLI_SOURCE=/repo/lemma-cli
/repo/install.sh --runtime docker -y --no-start 2>&1 | tail -5 || true
export PATH="$HOME/.local/bin:$PATH"

# lemma-stack should be installed and responding
STACK_VER=$(lemma-stack self version 2>&1)
echo "$STACK_VER"
echo "$STACK_VER" | grep -q "lemma-stack" || {{ echo "FAIL: lemma-stack not responding"; exit 1; }}

# lemma-stack doctor should run (expected: no runtime = fails, but no crash)
# doctor exits 1 when checks fail; capture output without triggering set -e
DOCTOR=$(lemma-stack doctor 2>&1 || true)
echo "$DOCTOR" | grep -q "runtime:docker" || {{ echo "FAIL: doctor missing runtime check"; exit 1; }}

echo "ALL CHECKS PASSED"
""",
        timeout=300,
    )
    assert result.returncode == 0, (
        f"Linux install.sh test failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert "ALL CHECKS PASSED" in result.stdout


# --- Linux lemma-stack direct install (Docker: python:3.12-slim) ------------

@pytest.mark.install_experience
@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_docker_linux_lemma_stack_install():
    """Direct uv tool install of lemma-stack in python:3.12-slim.

    Verifies lemma-stack installs cleanly and its CLI commands work.
    """
    result = run_in_container(
        "python:3.12-slim",
        """
set -e
pip install --quiet uv
cd /repo/lemma-stack
uv tool install . 2>&1 | tail -3
export PATH="$HOME/.local/bin:$PATH"

# self version
VER=$(lemma-stack self version 2>&1)
echo "$VER"
echo "$VER" | grep -q "lemma-stack" || { echo "FAIL: self version"; exit 1; }

# doctor (no runtime = fails, but no crash)
# doctor exits 1 when checks fail; capture output without triggering set -e
DOCTOR=$(lemma-stack doctor 2>&1 || true)
echo "$DOCTOR" | grep -q "runtime:" || { echo "FAIL: doctor missing runtime checks"; exit 1; }

echo "ALL CHECKS PASSED"
""",
    )
    assert result.returncode == 0, (
        f"Linux lemma-stack install failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert "ALL CHECKS PASSED" in result.stdout


# --- macOS native CLI smoke test --------------------------------------------

@pytest.mark.install_experience
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_macos_lemma_cli_smoke():
    """Smoke test the lemma CLI on the macOS host.

    Uses the lemma-cli venv if present, else falls back to the system lemma.
    """
    venv_lemma = REPO_ROOT / "lemma-cli" / ".venv" / "bin" / "lemma"
    lemma_bin = str(venv_lemma) if venv_lemma.exists() else shutil.which("lemma")
    if not lemma_bin:
        pytest.skip("lemma CLI not installed on this host")

    # --version
    result = subprocess.run(
        [lemma_bin, "--version"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"lemma --version failed:\n{result.stderr}"
    assert f"lemma {CLI_VERSION}" in result.stdout, (
        f"Expected 'lemma {CLI_VERSION}' in:\n{result.stdout}"
    )

    # --help
    result = subprocess.run(
        [lemma_bin, "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"lemma --help failed:\n{result.stderr}"
    assert "Usage:" in result.stdout
    assert "auth" in result.stdout

    # doctor (may fail if no server, but shouldn't crash)
    result = subprocess.run(
        [lemma_bin, "doctor"], capture_output=True, text=True, check=False
    )
    assert f"lemma {CLI_VERSION}" in result.stdout, (
        f"Doctor output missing version:\n{result.stdout}"
    )


# --- Windows install.ps1 validation -----------------------------------------

def test_install_ps1_structure():
    """Validate install.ps1 has the expected structure (no PowerShell needed).

    This is a static check — the actual Windows install is tested by the
    ``windows-cli`` CI job on windows-latest.
    """
    ps1 = REPO_ROOT / "install.ps1"
    content = ps1.read_text()

    assert "uv tool install" in content, "install.ps1 must install uv tool"
    assert "lemma-stack" in content, "install.ps1 must install lemma-stack"
    assert "lemma-stack install" in content, "install.ps1 must hand off to lemma-stack install"
    assert "ErrorActionPreference" in content, "install.ps1 must set error handling"
    assert "Get-Command uv" in content, "install.ps1 must check for uv"
    assert "LEMMA_STACK_SOURCE" in content, "install.ps1 must support local checkout override"


def test_install_sh_structure():
    """Validate install.sh has the expected structure."""
    sh = REPO_ROOT / "install.sh"
    content = sh.read_text()

    assert "uv tool install" in content, "install.sh must install uv tool"
    assert "lemma-stack" in content, "install.sh must install lemma-stack"
    assert "lemma-stack install" in content, "install.sh must hand off to lemma-stack install"
    assert "LEMMA_STACK_SOURCE" in content, "install.sh must support local checkout override"
    assert "curl" in content, "install.sh must use curl to install uv"


# --- Version consistency ----------------------------------------------------

def test_mono_version_consistency():
    """Verify the mono-version is consistent across all packages."""
    assert CLI_VERSION == SDK_VERSION, (
        f"CLI version ({CLI_VERSION}) != SDK version ({SDK_VERSION})"
    )
    assert CLI_VERSION == TS_VERSION, (
        f"CLI version ({CLI_VERSION}) != TS SDK version ({TS_VERSION})"
    )

    # CLI pyproject dependency floor must be >= current version
    cli_pyproject = (REPO_ROOT / "lemma-cli" / "pyproject.toml").read_text()
    match = re.search(r'lemma-sdk>=(\d+\.\d+\.\d+)', cli_pyproject)
    assert match, "lemma-cli pyproject must declare lemma-sdk dependency"
    floor = match.group(1)
    assert floor == CLI_VERSION, (
        f"lemma-sdk>={floor} in pyproject.toml != CLI version {CLI_VERSION}"
    )


# --- TS SDK version in browser bundle ---------------------------------------

def test_ts_bundle_version_matches_source():
    """The committed browser bundle must embed the current SDK_VERSION."""
    bundle = REPO_ROOT / "lemma-typescript" / "public" / "lemma-client.js"
    if not bundle.exists():
        pytest.skip("lemma-client.js bundle not found")
    content = bundle.read_text()
    assert f'SDK_VERSION = "{TS_VERSION}"' in content, (
        f"Browser bundle does not contain SDK_VERSION = \"{TS_VERSION}\". "
        f"Run: cd lemma-typescript && npm run build:bundle"
    )
