"""A connected machine end to end: paired in the browser, reached over its link.

The machine is the real agent and the real workspace runtime (see
``connected_machine_harness``), against a real backend over TCP. Everything a
sandbox caller uses -- the unchanged ``WorkspaceRuntimeClient`` behind the
``connected`` provider -- reaches it through the mux on the machine's one
outbound socket. Then the link is broken every way it breaks in life.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import websockets

from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine_errors import MachineNotConnected
from app.modules.workspace.providers.base import ProviderInstance
from app.modules.workspace.services.machine_link import dialer
from app.modules.workspace.tests.e2e.connected_machine_harness import (
    connection_row,
    link_url,
    machine_hello,
    provider_for,
    running_machine,
)
from sandbox_runtime.protocol import ByteRange, ProcessState, StartProcessRequest

pytestmark = pytest.mark.e2e

_KEY = "test-runtime-credential-key-32-bytes"


@pytest.fixture(autouse=True)
def machines_on(monkeypatch):
    monkeypatch.setattr(workspace_settings, "machine_connections_enabled", True)
    monkeypatch.setattr(workspace_settings, "runtime_credential_key", f"{_KEY}")
    monkeypatch.setenv("WORKSPACE_RUNTIME_CREDENTIAL_KEY", _KEY)


def _deadline(seconds: float = 30) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _run(provider, instance, command: str, *, cwd: str) -> tuple[int, str]:
    process_id = await provider.start_process(
        instance,
        StartProcessRequest(
            operation_id=uuid4(),
            shell_command=command,
            argv=None,
            cwd=cwd,
            environment=(),
            tty=None,
            output_limit_bytes=1 << 20,
            deadline_at=_deadline(),
        ),
        deadline_at=_deadline(),
    )
    output, after = b"", 0
    for _ in range(100):
        snapshot = await provider.read_process_output(
            instance,
            process_id=process_id,
            after_sequence=after,
            wait_seconds=2,
            deadline_at=_deadline(),
        )
        for chunk in snapshot.chunks:
            output += chunk.data
            after = max(after, chunk.sequence)
        if snapshot.state not in (ProcessState.RUNNING, ProcessState.STARTING):
            return snapshot.exit_code or 0, output.decode()
    raise AssertionError("the process did not finish")


@pytest.mark.asyncio
async def test_a_paired_machine_runs_processes_and_files_through_its_link(
    backend_server, async_client, authenticated_client, tmp_path
):
    async with running_machine(
        backend_server, async_client, authenticated_client, tmp_path / "root"
    ) as machine:
        row = await connection_row(authenticated_client, machine.connection_id)
        assert row["home"] == str(machine.root)
        assert row["capabilities"]["processes"] is True
        # What a host-mode machine installs its first runtime with.
        installer = await async_client.get(
            "/agent-host/compute/runtime/installer",
            headers={"Authorization": f"Bearer {machine.config.secret()}"},
        )
        assert installer.status_code == 200, installer.text
        assert "def install(" in installer.text
        provider = provider_for(row)
        instance = ProviderInstance(provider_id=str(machine.connection_id), name="m")

        # Lemma names /home/user; the machine's home is its --root.
        stat = await provider.write_file(
            instance,
            path="/home/user/lemma/hello.txt",
            data=_chunks(b"over the link\n"),
            expected_sha256=None,
            deadline_at=_deadline(),
        )
        assert stat.path == "/home/user/lemma/hello.txt"
        assert (machine.root / "lemma" / "hello.txt").read_text() == "over the link\n"
        listed = await provider.list_files(
            instance, path="/home/user/lemma", deadline_at=_deadline()
        )
        assert "/home/user/lemma/hello.txt" in {item.path for item in listed}
        chunks = [
            chunk
            async for chunk in provider.open_file(
                instance,
                path="/home/user/lemma/hello.txt",
                byte_range=ByteRange(offset=5, length=3),
                deadline_at=_deadline(),
            )
        ]
        assert b"".join(chunks) == b"the"

        code, output = await _run(
            provider, instance, "pwd && cat hello.txt", cwd="/home/user/lemma"
        )
        assert code == 0
        assert str(machine.root / "lemma") in output
        assert "over the link" in output


@pytest.mark.asyncio
async def test_a_machine_whose_link_is_down_is_not_connected_and_never_replaced(
    backend_server, async_client, authenticated_client, tmp_path
):
    async with running_machine(
        backend_server, async_client, authenticated_client, tmp_path / "root"
    ) as machine:
        row = await connection_row(authenticated_client, machine.connection_id)
        provider = provider_for(row)
        instance = ProviderInstance(provider_id=str(machine.connection_id), name="m")
        await machine.stop()
        await eventually(
            label="the route is released",
            probe=lambda: connection_row(authenticated_client, machine.connection_id),
            done=lambda current: current is not None and not current["online"],
        )
        with pytest.raises(MachineNotConnected) as offline:
            await provider.stat_file(
                instance, path="/home/user/lemma", deadline_at=_deadline()
            )
        assert str(offline.value) == f"{machine.name} is not connected."


@pytest.mark.asyncio
async def test_a_link_killed_mid_stream_fails_the_stream_and_the_machine_comes_back(
    backend_server, async_client, authenticated_client, tmp_path
):
    async with running_machine(
        backend_server, async_client, authenticated_client, tmp_path / "root"
    ) as machine:
        local = dialer.machine_link_registry.get(machine.connection_id)
        assert local is not None
        port = machine.config.runtime_port
        stream = await dialer.open_machine_stream(
            machine.connection_id, {"kind": "port", "port": port}
        )
        await stream.write(b"GET /health HTTP/1.1\r\nHost: m\r\n\r\n")
        # Kill the machine's socket under the open stream, as a dropped
        # network does: the link ends and every stream on it with it.
        await local.mux.close()
        local._stop(1011, "killed")
        with pytest.raises(Exception) as broken:
            await asyncio.wait_for(stream.read(), 5)
        assert "link" in str(broken.value)

        # The agent reconnects with backoff; the next operation works.
        async def back() -> object:
            return dialer.machine_link_registry.get(machine.connection_id)

        await eventually(
            label="the machine linked again",
            probe=back,
            done=lambda session: session is not None and session is not local,
            timeout_seconds=30,
        )
        row = await connection_row(authenticated_client, machine.connection_id)
        provider = provider_for(row)
        instance = ProviderInstance(provider_id=str(machine.connection_id), name="m")
        await eventually(
            label="the runtime answers again",
            probe=lambda: provider.list_files(
                instance, path="/home/user", deadline_at=_deadline()
            ),
            done=lambda listed: listed is not None,
            retry_exceptions=(MachineNotConnected, Exception),
        )


@pytest.mark.asyncio
async def test_a_second_link_for_the_machine_supersedes_the_first_with_4409(
    backend_server, async_client, authenticated_client, tmp_path
):
    async with running_machine(
        backend_server, async_client, authenticated_client, tmp_path / "root"
    ) as machine:
        first = dialer.machine_link_registry.get(machine.connection_id)
        assert first is not None
        headers = {"Authorization": f"Bearer {machine.config.secret()}"}
        async with websockets.connect(
            link_url(backend_server), additional_headers=headers
        ) as newer:
            await newer.send(
                '{"type":"hello","id":"h","body":{"role":"compute","hello":'
                + json.dumps(machine_hello(machine.config.installation_id))
                + ',"machine":{"mode":"host","platform":"linux","home":"/tmp/x",'
                '"runtime_port":1}}}'
            )
            welcome = json.loads(await newer.recv())
            assert welcome["type"] == "welcome"
            await eventually(
                label="the newer link owns the machine",
                probe=lambda: _registered(machine.connection_id),
                done=lambda session: session is not None and session is not first,
            )
        # The agent's link was the one closed; it backs off, then takes the
        # machine back once the intruder is gone.
        await eventually(
            label="the agent's link is back",
            probe=lambda: connection_row(authenticated_client, machine.connection_id),
            done=lambda row: bool(row and row["online"]),
            timeout_seconds=30,
        )


async def _registered(connection_id: UUID):
    return dialer.machine_link_registry.get(connection_id)


@pytest.mark.asyncio
async def test_revoking_a_machine_closes_its_link_and_refuses_it_after(
    backend_server, async_client, authenticated_client, tmp_path
):
    async with running_machine(
        backend_server, async_client, authenticated_client, tmp_path / "root"
    ) as machine:
        deleted = await authenticated_client.delete(
            f"/machine-connections/{machine.connection_id}"
        )
        assert deleted.status_code == 204, deleted.text
        # The agent hears 4401 three times and stops for good.
        await asyncio.wait_for(asyncio.gather(machine.task, return_exceptions=True), 30)
        from sandbox_runtime.machine_link.agent import Stop

        assert isinstance(machine.task.exception(), Stop)
        assert await connection_row(authenticated_client, machine.connection_id) is None
        headers = {"Authorization": f"Bearer {machine.config.secret()}"}
        runtime = await async_client.get("/agent-host/compute/runtime", headers=headers)
        assert runtime.status_code == 401
        async with websockets.connect(
            link_url(backend_server), additional_headers=headers
        ) as link:
            await link.send(
                '{"type":"hello","id":"h","body":{"role":"compute","hello":'
                + json.dumps(machine_hello(machine.config.installation_id))
                + ',"machine":{"mode":"host","platform":"linux","home":"/tmp/x",'
                '"runtime_port":1}}}'
            )
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await link.recv()
        assert closed.value.rcvd.code == 4401


@pytest.mark.asyncio
async def test_another_replica_reaches_the_machine_through_the_holder(
    backend_server, async_client, authenticated_client, tmp_path, monkeypatch
):
    """The holder's route names it; a replica without the link splices through
    it, and the bytes and the half close arrive as on a local stream."""
    # This replica is the holder, and records itself at its real address.
    monkeypatch.setattr(
        workspace_settings,
        "machine_link_replica_url",
        f"{backend_server['host_base_url']}",
    )
    async with running_machine(
        backend_server, async_client, authenticated_client, tmp_path / "root"
    ) as machine:
        # And the caller is another replica: no link here, another address.
        stream = await dialer.open_machine_stream(
            machine.connection_id,
            {"kind": "port", "port": machine.config.runtime_port},
            registry=dialer.MachineLinkRegistry(),
            here="http://elsewhere:1",
        )
        assert isinstance(stream, dialer.RemoteStream)
        await stream.write(
            b"GET /health HTTP/1.1\r\nHost: m\r\nConnection: close\r\n\r\n"
        )
        answer = b""
        while chunk := await asyncio.wait_for(stream.read(), 10):
            answer += chunk
        await stream.aclose()
        # Unauthenticated: the runtime answers, through two replicas' worth of
        # splicing, with its own refusal.
        assert answer.startswith(b"HTTP/1.1 401")


@pytest.mark.asyncio
async def test_an_unsigned_request_to_the_replica_proxy_is_refused(
    backend_server,
):
    url = backend_server["host_base_url"].replace("http://", "ws://")
    with pytest.raises(websockets.InvalidStatus):
        async with websockets.connect(
            f"{url}/agent-host/compute/streams/{uuid4()}?target=%7B%7D"
        ) as socket:
            await socket.recv()
