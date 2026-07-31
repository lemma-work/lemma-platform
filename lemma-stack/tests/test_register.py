from __future__ import annotations

import json
import subprocess

from lemma_stack import register


def test_register_creates_cli_config(tmp_path, monkeypatch):
    cli_config = tmp_path / "config.json"
    monkeypatch.setenv("LEMMA_CONFIG_FILE", str(cli_config))

    register.register_local_server(
        base_url="http://localhost:8711/",
        auth_url="http://localhost:3711/auth",
        make_active=True,
    )

    config = json.loads(cli_config.read_text())
    server = config["servers"]["local"]
    assert server["base_url"] == "http://localhost:8711"  # trailing slash stripped
    assert server["auth_url"] == "http://localhost:3711/auth"
    assert server["defaults"] == {}
    assert config["active_server"] == "local"


def test_register_preserves_existing_servers_and_tokens(tmp_path, monkeypatch):
    cli_config = tmp_path / "config.json"
    # shape produced by lemma-cli's upsert_server()
    cli_config.write_text(
        json.dumps(
            {
                "active_server": "cloud",
                "servers": {
                    "cloud": {
                        "base_url": "https://api.lemma.work",
                        "auth_url": "https://lemma.work/auth",
                        "token": "tok-cloud",
                        "defaults": {"org_id": "o-1"},
                    },
                    "local": {
                        "base_url": "http://localhost:9999",
                        "token": "tok-local",
                        "defaults": {"pod_id": "p-1"},
                    },
                },
            }
        )
    )
    monkeypatch.setenv("LEMMA_CONFIG_FILE", str(cli_config))

    register.register_local_server(
        base_url="http://localhost:8711",
        auth_url="http://localhost:3711/auth",
    )

    config = json.loads(cli_config.read_text())
    # active server untouched (already set), cloud untouched, local token/defaults kept
    assert config["active_server"] == "cloud"
    assert config["servers"]["cloud"]["token"] == "tok-cloud"
    local = config["servers"]["local"]
    assert local["base_url"] == "http://localhost:8711"
    assert local["token"] == "tok-local"
    assert local["defaults"] == {"pod_id": "p-1"}


def test_cli_install_uses_release_aligned_terminal_distribution(monkeypatch):
    commands: list[list[str]] = []

    monkeypatch.setattr(register, "_detect_cli_source", lambda: None)
    monkeypatch.setattr(
        register,
        "_find_user_binary",
        lambda name: f"/tools/{name}" if name in {"uv", "lemma"} else None,
    )

    def run(command):
        command = list(command)
        commands.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="lemma 0.5.1\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(register, "_run", run)

    assert register.install_lemma_cli(version="0.6.1") is True
    assert commands[-1] == [
        "/tools/uv",
        "tool",
        "install",
        "--force",
        "lemma-terminal==0.6.1",
    ]


def test_current_cli_version_is_not_reinstalled(monkeypatch):
    commands: list[list[str]] = []

    monkeypatch.setattr(register, "_detect_cli_source", lambda: None)
    monkeypatch.setattr(
        register,
        "_find_user_binary",
        lambda name: f"/tools/{name}" if name in {"uv", "lemma"} else None,
    )

    def run(command):
        command = list(command)
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="lemma 0.6.1\n", stderr="")

    monkeypatch.setattr(register, "_run", run)

    assert register.install_lemma_cli(version="0.6.1") is True
    assert commands == [["/tools/lemma", "--version"]]


def test_skill_install_upserts_curated_bundle_for_all_user_agents(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(
        register,
        "_find_user_binary",
        lambda name: "/tools/lemma" if name == "lemma" else None,
    )

    def run(command):
        command = list(command)
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(register, "_run", run)

    assert register.install_lemma_skills() is True
    assert commands == [["/tools/lemma", "skills", "install", "--target", "all", "--yes"]]
