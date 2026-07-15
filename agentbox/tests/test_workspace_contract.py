from pathlib import Path


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
