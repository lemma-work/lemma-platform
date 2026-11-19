"""`lemma machine connect|disconnect`: what is asked, written, run and removed.

The agent the command starts and the server it talks to are the backend's
(`sandbox_runtime.machine_link`, exercised end to end in the backend's
connected-machine suite). Here: the consent, the files the agent is given and
their permissions, the pairing frame on the wire, and what each mode runs.
"""

from __future__ import annotations

import asyncio
import io
import json
import stat
import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lemma_cli.cli_core import machine_setup as setup
from lemma_cli.cli_core.app import app

runner = CliRunner()

PAIRED = setup.Paired(
    connection_id="01a16000-0000-7000-8000-000000000001",
    user_id="01a16000-0000-7000-8000-000000000002",
    name="laptop",
    machine_secret="s3cret",
)


SERVER = ["--base-url", "https://api.example.test"]


def _quiet(*_args) -> None:
    return None


def _steps(seen: dict[str, object], **overrides) -> setup.Steps:
    def pair(api_url, **kwargs):
        seen["pair"] = {"api_url": api_url, **kwargs}
        return PAIRED

    def provision_host(api_url, secret, root, *, progress):
        seen["provisioned"] = (api_url, secret, root)
        return root / ".lemma" / "env" / "bin" / "python"

    def install_service(name, command, root):
        seen["service"] = (name, command)
        return Path(f"/units/lemma-machine-{name}.service")

    def start_image(**kwargs):
        seen["image"] = kwargs

    async def revoke(api_url, secret, *, installation, home):
        seen["revoked"] = {"secret": secret, "installation": installation}

    defaults = {
        "pair": pair,
        "provision_host": provision_host,
        "install_service": install_service,
        "start_image": start_image,
        "runtime_description": lambda api, secret: {
            "workspace_image": "ghcr.io/lemma-work/lemma-workspace:v9"
        },
        "revoke": revoke,
        "remove_service": lambda name: seen.__setitem__("unit_removed", name),
        "remove_container": lambda name: seen.__setitem__("container_removed", name),
        "free_port": lambda: 18123,
        "require_host_tools": lambda: None,
    }
    return setup.Steps(**{**defaults, **overrides})


def test_connect_refuses_to_run_commands_as_you_without_consent(tmp_path):
    result = runner.invoke(
        app, [*SERVER, "machine", "connect", "--root", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert "run commands on this machine as" in result.stderr
    assert "--yes" in result.stderr
    assert not (tmp_path / ".lemma" / "machine-secret").exists()


def test_docker_without_image_is_refused(tmp_path):
    result = runner.invoke(
        app,
        [*SERVER, "machine", "connect", "--root", str(tmp_path), "--yes", "--docker"],
    )
    assert result.exit_code == 1
    assert "--image" in result.stderr


def test_host_mode_writes_a_private_secret_and_names_the_agent(tmp_path):
    seen: dict[str, object] = {}
    connected = setup.connect(
        setup.ConnectOptions(
            api_url="https://api.example.test", root=tmp_path, pod="pod-1"
        ),
        announce=_quiet,
        progress=_quiet,
        steps=_steps(seen),
    )

    assert seen["pair"]["pod"] == "pod-1"
    assert seen["pair"]["mode"] == "host"
    secret = tmp_path / ".lemma" / "machine-secret"
    assert secret.read_text().strip() == "s3cret"
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    config_file = tmp_path / ".lemma" / "machine.json"
    config = json.loads(config_file.read_text())
    assert config["mode"] == "host"
    assert config["home"] == str(tmp_path)
    assert config["runtime_port"] == 18123
    assert config["secret_file"] == str(secret)
    assert "s3cret" not in json.dumps(config)
    python = tmp_path / ".lemma" / "env" / "bin" / "python"
    assert connected.command == [
        str(python),
        "-m",
        "sandbox_runtime.machine_link.agent",
        "--config",
        str(config_file),
    ]


def test_install_service_registers_the_agent_instead_of_running_it(tmp_path):
    seen: dict[str, object] = {}
    connected = setup.connect(
        setup.ConnectOptions(
            api_url="https://api.example.test", root=tmp_path, install_service=True
        ),
        announce=_quiet,
        progress=_quiet,
        steps=_steps(seen),
    )

    assert connected.command is None
    assert str(connected.service).endswith("lemma-machine-laptop.service")
    name, command = seen["service"]
    assert name == "laptop"
    assert command[1:3] == ["-m", setup.AGENT_MODULE]


def test_image_mode_runs_the_servers_image_with_the_agent_inside(tmp_path):
    seen: dict[str, object] = {}
    connected = setup.connect(
        setup.ConnectOptions(
            api_url="http://localhost:8000",
            root=tmp_path,
            image=True,
            mount_docker=True,
        ),
        announce=_quiet,
        progress=_quiet,
        steps=_steps(seen),
    )

    assert connected.container == "lemma-machine-laptop"
    started = seen["image"]
    assert started["image"] == "ghcr.io/lemma-work/lemma-workspace:v9"
    assert started["mount_docker"] is True
    assert started["secret"] == "s3cret"
    config = started["config"]
    assert config["mode"] == "image"
    assert config["home"] == "/home/user"
    assert config["api_url"] == "http://host.docker.internal:8000"
    assert config["secret_file"] == "/run/lemma-bootstrap/machine-secret"


def test_image_mode_without_an_image_says_how_to_name_one(tmp_path):
    with pytest.raises(setup.SetupError, match="--image-ref"):
        setup.connect(
            setup.ConnectOptions(api_url="https://a.test", root=tmp_path, image=True),
            announce=_quiet,
            progress=_quiet,
            steps=_steps({}, runtime_description=lambda api, secret: {}),
        )


def test_disconnect_revokes_and_forgets_the_secret(tmp_path):
    state = tmp_path / ".lemma"
    setup.write_private(state / "machine-secret", "s3cret\n")
    (state / "machine.json").write_text(
        json.dumps(
            {
                "name": "laptop",
                "mode": "image",
                "installation_id": "machine-1",
                "home": "/home/user",
            }
        )
    )
    seen: dict[str, object] = {}

    name = setup.disconnect(
        "https://api.example.test", tmp_path, warn=_quiet, steps=_steps(seen)
    )

    assert name == "laptop"
    assert seen["revoked"] == {"secret": "s3cret", "installation": "machine-1"}
    assert seen["unit_removed"] == "laptop"
    assert seen["container_removed"] == "laptop"
    assert not (state / "machine-secret").exists()


def test_disconnect_with_nothing_connected_says_so(tmp_path):
    result = runner.invoke(
        app, [*SERVER, "machine", "disconnect", "--root", str(tmp_path), "--yes"]
    )
    assert result.exit_code == 1
    assert "No machine is connected" in result.stderr


def test_the_pairing_frame_names_the_compute_role_and_the_claim_key():
    """The wire frame, against a stand-in link on loopback."""
    import websockets

    received: dict[str, object] = {}

    async def scenario() -> setup.Paired:
        async def handler(socket):
            frame = json.loads(await socket.recv())
            received.update(frame)
            await socket.send(
                json.dumps(
                    {
                        "type": "paired",
                        "re": frame["id"],
                        "body": {
                            "connection_id": PAIRED.connection_id,
                            "user_id": PAIRED.user_id,
                            "name": "laptop",
                            "machine_secret": "s3cret",
                        },
                    }
                )
            )

        async with websockets.serve(handler, "127.0.0.1", 0) as stand_in:
            port = stand_in.sockets[0].getsockname()[1]
            return await setup.claim(
                f"http://127.0.0.1:{port}",
                code="ABCD-EFGH-JKMN",
                claim_key="k" * 43,
                installation_id="machine-1",
                timeout=5,
            )

    paired = asyncio.run(scenario())

    assert paired == PAIRED
    assert received["type"] == "pair"
    body = received["body"]
    assert body["role"] == "compute"
    assert body["claim_key"] == "k" * 43
    assert body["hello"]["protocol_version"] == 4


def test_a_refused_pairing_says_why():
    import websockets

    async def scenario() -> None:
        async def handler(socket):
            await socket.recv()
            await socket.send(
                json.dumps({"type": "error", "body": {"message": "the code expired"}})
            )

        async with websockets.serve(handler, "127.0.0.1", 0) as stand_in:
            port = stand_in.sockets[0].getsockname()[1]
            await setup.claim(
                f"http://127.0.0.1:{port}",
                code="ABCD",
                claim_key="k" * 43,
                installation_id="m",
                timeout=5,
            )

    with pytest.raises(setup.SetupError, match="the code expired"):
        asyncio.run(scenario())


def test_the_link_and_loopback_addresses_a_container_can_use():
    assert (
        setup.link_url("https://api.lemma.work")
        == "wss://api.lemma.work/agent-host/link"
    )
    assert (
        setup.link_url("http://localhost:8000/")
        == "ws://localhost:8000/agent-host/link"
    )
    assert (
        setup.reachable_from_docker("http://localhost:8000")
        == "http://host.docker.internal:8000"
    )
    assert (
        setup.reachable_from_docker("https://api.lemma.work")
        == "https://api.lemma.work"
    )


def test_the_container_runs_the_agent_and_keeps_its_home():
    command = setup.image_run_command(image="img:1", name="laptop", mount_docker=False)
    assert command[:4] == ["docker", "create", "--name", "lemma-machine-laptop"]
    assert "unless-stopped" in command
    assert "lemma-machine-laptop-home:/home/user" in command
    assert "/var/run/docker.sock:/var/run/docker.sock" not in command
    assert command[-5:] == [
        "/opt/lemma-python/bin/python",
        "-m",
        setup.AGENT_MODULE,
        "--config",
        "/run/lemma-bootstrap/machine.json",
    ]
    with_docker = setup.image_run_command(
        image="img:1", name="laptop", mount_docker=True
    )
    assert "/var/run/docker.sock:/var/run/docker.sock" in with_docker


def test_the_containers_credential_belongs_to_the_images_user_only():
    archive = setup.state_archive({"machine-secret": b"s3cret\n"})
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        (member,) = tar.getmembers()
    assert (member.uid, member.gid, member.mode) == (10001, 10001, 0o600)


def test_service_units_restart_crashes_but_not_a_revoked_machine(tmp_path):
    command = ["/env/bin/python", "-m", setup.AGENT_MODULE, "--config", "/r/m.json"]
    unit = setup.systemd_unit("laptop", command, tmp_path)
    assert "Restart=on-failure" in unit
    assert f"ExecStart={' '.join(command)}" in unit
    plist = setup.launchd_plist("laptop", command, tmp_path)
    assert "<key>SuccessfulExit</key><false/>" in plist
    assert "<string>--config</string>" in plist
    assert "work.lemma.machine.laptop" in plist


def test_connecting_again_serves_the_same_machine(tmp_path):
    """QA: Ctrl-C and `connect` again paired a new machine every time, so a
    person's list filled with dead copies of one computer."""
    first: dict[str, object] = {}
    setup.connect(
        setup.ConnectOptions(api_url="https://api.example.test", root=tmp_path),
        announce=_quiet,
        progress=_quiet,
        steps=_steps(first),
    )
    again: dict[str, object] = {}

    def pair_again(api_url, **kwargs):
        raise AssertionError("a machine already joined must not pair again")

    connected = setup.connect(
        setup.ConnectOptions(api_url="https://api.example.test", root=tmp_path),
        announce=_quiet,
        progress=_quiet,
        steps=_steps(again, pair=pair_again),
    )

    assert connected.name == "laptop"
    assert again["provisioned"][1] == "s3cret"


def test_a_missing_tool_is_said_before_anything_is_paired(tmp_path):
    """QA: host mode found uv missing after the person had approved it, and
    left a machine that could never be served."""
    seen: dict[str, object] = {}

    def no_uv() -> None:
        raise setup.SetupError("Host mode needs uv")

    with pytest.raises(setup.SetupError, match="uv"):
        setup.connect(
            setup.ConnectOptions(api_url="https://api.example.test", root=tmp_path),
            announce=_quiet,
            progress=_quiet,
            steps=_steps(seen, require_host_tools=no_uv),
        )
    assert "pair" not in seen


def _never_pair(api_url, **kwargs):
    raise AssertionError("a machine already joined must not pair again")


def _connect(root, seen, *, api_url="https://api.example.test", image=False, **steps):
    return setup.connect(
        setup.ConnectOptions(api_url=api_url, root=root, image=image),
        announce=_quiet,
        progress=_quiet,
        steps=_steps(seen, **steps),
    )


def test_a_root_serving_one_mode_is_not_repaired_in_the_other(tmp_path):
    """Review: host mode over an image root paired a second machine and left
    the container running with its credential, beyond `disconnect`."""
    _connect(tmp_path, {}, image=True)
    seen: dict[str, object] = {}

    with pytest.raises(setup.SetupError, match="disconnect"):
        _connect(tmp_path, seen, pair=_never_pair)

    assert "provisioned" not in seen
    record = json.loads((tmp_path / ".lemma" / "pairing.json").read_text())
    assert record["mode"] == "image"


def test_a_secret_is_never_sent_to_a_server_it_was_not_issued_by(tmp_path):
    """Review: joining to server A then connecting against B sent A's bearer
    secret to B."""
    _connect(tmp_path, {}, api_url="https://a.example.test")
    seen: dict[str, object] = {}

    def no_io(*args, **kwargs):
        raise AssertionError("nothing may reach the other server")

    with pytest.raises(setup.SetupError, match="a.example.test"):
        _connect(
            tmp_path,
            seen,
            api_url="https://b.example.test",
            pair=_never_pair,
            provision_host=no_io,
            runtime_description=no_io,
        )
    with pytest.raises(setup.SetupError, match="a.example.test"):
        setup.disconnect(
            "https://b.example.test", tmp_path, warn=_quiet, steps=_steps(seen)
        )
    assert "revoked" not in seen
    assert (tmp_path / ".lemma" / "machine-secret").exists()


def test_image_mode_on_a_local_server_serves_the_same_machine_again(tmp_path):
    """The container's address for a local server is rewritten; the record
    keeps the one the person named, so reconnecting still matches."""
    _connect(tmp_path, {}, api_url="http://localhost:8000", image=True)
    seen: dict[str, object] = {}

    connected = _connect(
        tmp_path, seen, api_url="http://localhost:8000", image=True, pair=_never_pair
    )

    assert connected.name == "laptop"
    assert seen["image"]["config"]["api_url"] == "http://host.docker.internal:8000"


def test_a_failed_install_after_approval_resumes_without_another_approval(tmp_path):
    """Review: the server spends the code on approval, so a retry that paired
    again asked twice and overwrote the first machine's secret."""

    def fails(api_url, secret, root, *, progress):
        raise setup.SetupError("Installing the runtime's dependencies failed")

    with pytest.raises(setup.SetupError, match="dependencies"):
        _connect(tmp_path, {}, provision_host=fails)
    assert not (tmp_path / ".lemma" / "machine.json").exists()
    seen: dict[str, object] = {}

    connected = _connect(tmp_path, seen, pair=_never_pair)

    assert connected.name == "laptop"
    assert seen["provisioned"][1] == "s3cret"


def test_a_root_whose_install_failed_can_still_be_disconnected(tmp_path):
    def fails(api_url, secret, root, *, progress):
        raise setup.SetupError("Downloading the runtime failed")

    with pytest.raises(setup.SetupError):
        _connect(tmp_path, {}, provision_host=fails)
    seen: dict[str, object] = {}

    name = setup.disconnect(
        "https://api.example.test", tmp_path, warn=_quiet, steps=_steps(seen)
    )

    assert name == "laptop"
    assert seen["revoked"]["secret"] == "s3cret"
    assert not list((tmp_path / ".lemma").glob("pairing.json"))
    assert not (tmp_path / ".lemma" / "machine-secret").exists()


def test_disconnect_cleans_up_when_the_link_drops_mid_revoke(tmp_path):
    """Review: a closed socket raised past the warning, so the container kept
    running and the credential stayed on disk."""
    from websockets.exceptions import ConnectionClosed

    _connect(tmp_path, {}, image=True)
    seen: dict[str, object] = {}
    warnings: list[str] = []

    async def dropped(api_url, secret, *, installation, home):
        raise ConnectionClosed(None, None)

    name = setup.disconnect(
        "https://api.example.test",
        tmp_path,
        warn=warnings.append,
        steps=_steps(seen, revoke=dropped),
    )

    assert name == "laptop"
    assert warnings and "remove laptop there too" in warnings[0]
    assert seen["container_removed"] == "laptop"
    assert not (tmp_path / ".lemma" / "machine-secret").exists()


def test_host_mode_is_refused_on_windows_before_anything_is_paired(monkeypatch):
    from lemma_cli.cli_core import machine_setup

    monkeypatch.setattr(machine_setup.sys, "platform", "win32")
    with pytest.raises(machine_setup.SetupError, match="--image"):
        machine_setup.require_host_tools()
