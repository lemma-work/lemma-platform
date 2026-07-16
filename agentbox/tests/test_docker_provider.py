from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentbox.config import settings  # noqa: E402
from agentbox.apps import SANDBOX_APPS  # noqa: E402
from agentbox.providers.docker import DockerSandboxProvider  # noqa: E402
from agentbox.providers.podman import PodmanSandboxProvider  # noqa: E402
from agentbox.schemas import SandboxEnsureRequest, SandboxInternalStatus  # noqa: E402


def test_docker_provider_purges_only_the_validated_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(settings, "agentbox_storage_root", str(tmp_path))
    monkeypatch.setattr(settings, "agentbox_storage_host_root", None)
    provider = DockerSandboxProvider()
    workspace = tmp_path / "sandbox-1"
    workspace.mkdir()
    (workspace / "sentinel.txt").write_text("private")
    neighbor = tmp_path / "keep-me"
    neighbor.mkdir()

    assert asyncio.run(provider.purge_storage("sandbox-1")) is True
    assert not workspace.exists()
    assert neighbor.exists()
    assert asyncio.run(provider.purge_storage("sandbox-1")) is False


def test_docker_provider_purges_runtime_owned_workspace_through_daemon(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/docker")
    storage_root = tmp_path / "manager-storage"
    host_root = tmp_path / "daemon-storage"
    monkeypatch.setattr(settings, "agentbox_storage_root", str(storage_root))
    monkeypatch.setattr(settings, "agentbox_storage_host_root", str(host_root))
    monkeypatch.setattr(settings, "agentbox_runtime_image", "runtime@sha256:test")
    provider = DockerSandboxProvider()
    workspace = storage_root / "sandbox-1"
    workspace.mkdir()
    (workspace / "runtime-owned.txt").write_text("private")
    original_rmtree = shutil.rmtree
    commands: list[tuple[str, ...]] = []

    def permission_denied(_path: Path) -> None:
        raise PermissionError("runtime UID owns the workspace")

    async def fake_run_docker(*args: str) -> str:
        commands.append(args)
        original_rmtree(workspace)
        return ""

    monkeypatch.setattr("agentbox.providers.docker.shutil.rmtree", permission_denied)
    monkeypatch.setattr(provider, "_run_docker", fake_run_docker)

    assert asyncio.run(provider.purge_storage("sandbox-1")) is True
    assert not workspace.exists()
    assert commands == [
        (
            "run",
            "--rm",
            "--user",
            "0:0",
            "--entrypoint",
            "/bin/rm",
            "-v",
            f"{host_root}:/agentbox-storage",
            "runtime@sha256:test",
            "-rf",
            "--",
            "/agentbox-storage/sandbox-1",
        )
    ]


def test_docker_provider_uses_default_image_without_image_type(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(settings, "agentbox_runtime_image", "agentbox-runtime:default")
    monkeypatch.setattr(settings, "agentbox_storage_root", str(tmp_path))
    monkeypatch.setattr(settings, "agentbox_storage_host_root", None)

    provider = DockerSandboxProvider()
    commands: list[tuple[str, ...]] = []

    async def fake_run_docker(*args: str) -> str:
        commands.append(args)
        return "container-id"

    async def fake_inspect_sandbox(_sandbox_id: str) -> None:
        return None

    async def fake_get_status(sandbox_id: str) -> SandboxInternalStatus:
        return SandboxInternalStatus(
            id=sandbox_id,
            status="RUNNING",
            ready=True,
            runtime_url="http://127.0.0.1:12345",
        )

    async def fake_wait_until_runtime_ready(_sandbox_id: str) -> None:
        return None

    monkeypatch.setattr(provider, "_run_docker", fake_run_docker)
    monkeypatch.setattr(provider, "_inspect_sandbox", fake_inspect_sandbox)
    monkeypatch.setattr(provider, "get_status", fake_get_status)
    monkeypatch.setattr(
        provider, "_wait_until_runtime_ready", fake_wait_until_runtime_ready
    )

    result = asyncio.run(
        provider.create(
            "sandbox-1",
            SandboxEnsureRequest(),
        )
    )

    assert result.status == "RUNNING"
    assert (tmp_path / "sandbox-1").stat().st_mode & 0o777 == 0o777
    assert commands
    run_args = commands[0]
    assert run_args[0] == "run"
    assert "agentbox-runtime:default" in run_args
    for app in SANDBOX_APPS.values():
        assert f"127.0.0.1::{app.port}" in run_args
    assert not any("image-type" in arg for arg in run_args)
    assert not any("disk-size" in arg for arg in run_args)


def test_docker_provider_uses_host_storage_root_for_sandbox_mount(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(settings, "agentbox_runtime_image", "agentbox-runtime:default")
    monkeypatch.setattr(settings, "agentbox_storage_root", str(tmp_path / "container"))
    monkeypatch.setattr(settings, "agentbox_storage_host_root", str(tmp_path / "host"))

    provider = DockerSandboxProvider()
    commands: list[tuple[str, ...]] = []

    async def fake_run_docker(*args: str) -> str:
        commands.append(args)
        return "container-id"

    async def fake_inspect_sandbox(_sandbox_id: str) -> None:
        return None

    async def fake_get_status(sandbox_id: str) -> SandboxInternalStatus:
        return SandboxInternalStatus(
            id=sandbox_id,
            status="RUNNING",
            ready=True,
            runtime_url="http://127.0.0.1:12345",
        )

    async def fake_wait_until_runtime_ready(_sandbox_id: str) -> None:
        return None

    monkeypatch.setattr(provider, "_run_docker", fake_run_docker)
    monkeypatch.setattr(provider, "_inspect_sandbox", fake_inspect_sandbox)
    monkeypatch.setattr(provider, "get_status", fake_get_status)
    monkeypatch.setattr(
        provider, "_wait_until_runtime_ready", fake_wait_until_runtime_ready
    )

    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))

    assert (tmp_path / "container" / "sandbox-1").exists()
    assert f"{tmp_path / 'host' / 'sandbox-1'}:/workspace" in commands[0]


def test_docker_status_reads_runtime_url_from_published_port(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(settings, "agentbox_storage_root", str(tmp_path))
    monkeypatch.setattr(settings, "agentbox_storage_host_root", None)

    provider = DockerSandboxProvider()
    status = provider._status_from_inspect(
        "sandbox-1",
        {
            "Created": "2026-05-30T00:00:00Z",
            "State": {"Running": True, "Status": "running"},
            "Config": {
                "Image": "agentbox-runtime:default",
                "Labels": {
                    "agentbox.work/sandbox-id": "sandbox-1",
                    "agentbox.work/disk-size-gb": "3",
                },
            },
            "NetworkSettings": {
                "Ports": {
                    "8080/tcp": [{"HostPort": "49152"}],
                    "4848/tcp": [{"HostPort": "49153"}],
                },
            },
        },
    )

    assert status.ready is True
    assert status.status == "RUNNING"
    assert status.runtime_url == "http://127.0.0.1:49152"
    assert status.apps["runtime"].private_url == "http://127.0.0.1:49152"
    assert status.apps["browser"].private_url == "http://127.0.0.1:49153"


def test_docker_release_stops_without_removing_container(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(settings, "agentbox_storage_root", str(tmp_path))
    monkeypatch.setattr(settings, "agentbox_storage_host_root", None)
    provider = DockerSandboxProvider()
    commands: list[tuple[str, ...]] = []

    async def fake_inspect(_sandbox_id: str) -> SandboxInternalStatus:
        return SandboxInternalStatus(id="sandbox-1", status="RUNNING", ready=True)

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    monkeypatch.setattr(provider, "_inspect_sandbox", fake_inspect)
    monkeypatch.setattr(provider, "_run_docker", fake_run)

    assert asyncio.run(provider.release("sandbox-1")) is True
    assert commands == [("stop", "agentbox-sandbox-1")]


def test_docker_provider_network_mode_joins_network_without_published_ports(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(settings, "agentbox_runtime_image", "agentbox-runtime:default")
    monkeypatch.setattr(settings, "agentbox_storage_root", str(tmp_path))
    monkeypatch.setattr(settings, "agentbox_storage_host_root", None)
    monkeypatch.setattr(settings, "agentbox_network", "lemma-local-net")

    provider = DockerSandboxProvider()
    commands: list[tuple[str, ...]] = []

    async def fake_run_docker(*args: str) -> str:
        commands.append(args)
        return "container-id"

    async def fake_inspect_sandbox(_sandbox_id: str) -> None:
        return None

    async def fake_get_status(sandbox_id: str) -> SandboxInternalStatus:
        return SandboxInternalStatus(
            id=sandbox_id,
            status="RUNNING",
            ready=True,
            runtime_url="http://agentbox-sandbox-1:8080",
        )

    async def fake_wait_until_runtime_ready(_sandbox_id: str) -> None:
        return None

    monkeypatch.setattr(provider, "_run_docker", fake_run_docker)
    monkeypatch.setattr(provider, "_inspect_sandbox", fake_inspect_sandbox)
    monkeypatch.setattr(provider, "get_status", fake_get_status)
    monkeypatch.setattr(
        provider, "_wait_until_runtime_ready", fake_wait_until_runtime_ready
    )

    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))

    run_args = commands[0]
    assert "--network" in run_args
    assert "lemma-local-net" in run_args
    assert not any(arg.startswith("127.0.0.1::") for arg in run_args)
    assert "--add-host" in run_args
    assert "host.docker.internal:host-gateway" in run_args


def test_docker_status_network_mode_uses_container_dns(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(settings, "agentbox_storage_root", str(tmp_path))
    monkeypatch.setattr(settings, "agentbox_storage_host_root", None)
    monkeypatch.setattr(settings, "agentbox_network", "lemma-local-net")

    provider = DockerSandboxProvider()
    status = provider._status_from_inspect(
        "sandbox-1",
        {
            "Created": "2026-05-30T00:00:00Z",
            "State": {"Running": True, "Status": "running"},
            "Config": {"Image": "agentbox-runtime:default"},
            "NetworkSettings": {"Ports": {}},
        },
    )

    assert status.ready is True
    assert status.runtime_url == "http://agentbox-sandbox-1:8080"
    assert status.apps["runtime"].private_url == "http://agentbox-sandbox-1:8080"
    assert status.apps["browser"].private_url == "http://agentbox-sandbox-1:4848"
    assert status.apps["browser"].ready is True


def test_podman_provider_uses_shared_container_config(monkeypatch, tmp_path):
    seen_cli_names: list[str] = []

    def fake_which(name: str) -> str:
        seen_cli_names.append(name)
        return f"/usr/bin/{name}"

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr(settings, "agentbox_storage_root", str(tmp_path / "podman"))
    monkeypatch.setattr(settings, "agentbox_endpoint_host", "127.0.0.1")

    provider = PodmanSandboxProvider()
    status = provider._status_from_inspect(
        "sandbox-1",
        {
            "Created": "2026-05-30T00:00:00Z",
            "State": {"Running": True, "Status": "running"},
            "Config": {
                "Image": "agentbox-runtime:default",
                "Labels": {
                    "agentbox.work/sandbox-id": "sandbox-1",
                    "agentbox.work/disk-size-gb": "2",
                },
            },
            "NetworkSettings": {
                "Ports": {
                    "8080/tcp": [{"HostPort": "50100"}],
                },
            },
        },
    )

    assert seen_cli_names == ["podman"]
    assert provider.storage_root == tmp_path / "podman"
    assert status.status == "RUNNING"
    assert status.runtime_url == "http://127.0.0.1:50100"
