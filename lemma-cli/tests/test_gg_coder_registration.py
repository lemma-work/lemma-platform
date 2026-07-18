from __future__ import annotations

import json
from pathlib import Path

from lemma_cli.daemon.catalog import discover_harness, resolve_gg_coder_selection
from lemma_cli.daemon.harnesses.gg_coder import GgCoderHarness
from lemma_cli.daemon.harnesses.registry import get_harness
from lemma_cli.daemon.mcp import provider_command, write_provider_mcp_files


def test_gg_coder_is_discovered_when_binary_is_on_path(monkeypatch):
    monkeypatch.setattr("lemma_cli.daemon.catalog.shutil.which", lambda binary: f"/bin/{binary}")
    monkeypatch.setattr("lemma_cli.daemon.catalog.binary_version", lambda _binary: "5.13.3")

    harness = discover_harness("GG_CODER", "ggcoder")

    # GG Coder does not expose a model-list subcommand; the harness advertises
    # a single ``default`` entry that resolves at runtime to the user's saved
    # provider/model (see ``~/.gg/settings.json`` or
    # ``LEMMA_DAEMON_GG_CODER_{PROVIDER,MODEL}``).
    assert harness == {
        "available": True,
        "binary": "ggcoder",
        "path": "/bin/ggcoder",
        "version": "5.13.3",
        "models": ["default"],
        "model_catalog": [
            {
                "name": "default",
                "display_name": "default",
                "provider_model_name": "default",
                "metadata": {},
            }
        ],
        "display_name": "GG Coder",
    }


def test_gg_coder_is_registered_as_streaming_harness():
    assert isinstance(get_harness("GG_CODER"), GgCoderHarness)


def test_gg_coder_default_command_uses_saved_provider_model_and_prompt(
    tmp_path: Path,
    monkeypatch,
):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"defaultProvider": "minimax", "defaultModel": "MiniMax-M3"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LEMMA_DAEMON_GG_CODER_SETTINGS", str(settings))

    assert resolve_gg_coder_selection("default") == ("minimax", "MiniMax-M3")
    assert provider_command(
        harness_kind="GG_CODER",
        model_name="default",
        prompt_text="work the task",
        mcp={},
    ) == [
        "ggcoder",
        "--json",
        "--provider",
        "minimax",
        "--model",
        "MiniMax-M3",
        "--max-turns",
        "25",
        "work the task",
    ]


def test_gg_coder_receives_lemma_mcp_config(tmp_path: Path, monkeypatch):
    # Chrome-devtools-mcp is default-on in production; turn it off here so the
    # test pins the lemma_tools-only contract instead of needing to enumerate
    # every default-on MCP server.
    monkeypatch.setenv("LEMMA_DAEMON_GG_CODER_CHROME_DEVTOOLS", "0")

    write_provider_mcp_files(
        "GG_CODER",
        tmp_path,
        {
            "server_name": "lemma_tools",
            "url": "https://lemma.test/mcp",
            "authorization": "Bearer secret",
        },
    )

    assert json.loads((tmp_path / ".gg" / "mcp.json").read_text()) == {
        "mcpServers": {
            "lemma_tools": {
                "type": "http",
                "url": "https://lemma.test/mcp",
                "headers": {"Authorization": "Bearer secret"},
            }
        }
    }
