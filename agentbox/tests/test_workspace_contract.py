from pathlib import Path

import pytest

from agentbox.apps import sandbox_app


AGENTBOX_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_image_keeps_browser_state_out_of_workspace() -> None:
    dockerfile = (AGENTBOX_ROOT / "Dockerfile.runtime").read_text()

    assert "AGENT_BROWSER_PROFILE=/tmp/agentbox-browser/profile" in dockerfile
    assert "AGENT_BROWSER_CONFIG=/tmp/agentbox-browser/config.json" in dockerfile
    assert "AGENT_BROWSER_SESSION_NAME=" not in dockerfile
    assert "/workspace/.browser-profile" not in dockerfile
    assert "/workspace/agent-browser.json" not in dockerfile


def test_runtime_start_clears_ephemeral_and_legacy_browser_state() -> None:
    script = (AGENTBOX_ROOT / "scripts" / "start-runtime.sh").read_text()

    assert (
        'PROFILE_DIR="${AGENT_BROWSER_PROFILE:-/tmp/agentbox-browser/profile}"'
        in script
    )
    assert (
        'CONFIG_PATH="${AGENT_BROWSER_CONFIG:-/tmp/agentbox-browser/config.json}"'
        in script
    )
    assert "unset AGENT_BROWSER_SESSION_NAME" in script
    assert 'rm -rf -- "$HOME_DIR/.agent-browser" /workspace/.browser-profile' in script
    assert "rm -f -- /workspace/agent-browser.json" in script


def test_function_executor_is_part_of_sandbox_readiness() -> None:
    script = (AGENTBOX_ROOT / "scripts" / "start-runtime.sh").read_text()

    assert "python -m uvicorn agentbox.function_executor:app" in script
    assert sandbox_app("function_executor").startup == "eager"


@pytest.mark.parametrize("name", ["start-runtime.sh", "start-browser.sh"])
def test_runtime_scripts_fall_back_from_an_unwritable_home(name: str) -> None:
    script = (AGENTBOX_ROOT / "scripts" / name).read_text()

    assert 'HOME_DIR="/tmp/agentbox-home-${UID:-10001}"' in script
    assert 'NPM_CACHE_DIR="/tmp/agentbox-npm-${UID:-10001}"' in script
    assert 'export HOME="$HOME_DIR"' in script
    assert 'export NPM_CONFIG_CACHE="$NPM_CACHE_DIR"' in script
