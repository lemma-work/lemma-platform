"""A real connected machine for e2e tests: paired, linked, serving a runtime.

The machine is the real agent (``sandbox_runtime.machine_link.agent``) in host
mode, run in this process against a real backend over TCP, serving the real
workspace runtime as a subprocess of this interpreter under a temporary
``--root``. Only the CLI's own work -- the uv environment and the bootstrap
download -- is not exercised here; the parity suite's container lanes and the
CLI's tests cover those.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import socket
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import websockets

from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.domain.machine_connection import MachineConnection
from sandbox_runtime.machine_link.agent import MachineAgent
from sandbox_runtime.machine_link.config import (
    PROTOCOL_VERSION,
    MachineConfig,
    write_secret,
)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def link_url(server: dict[str, str]) -> str:
    return server["host_base_url"].replace("http://", "ws://") + "/agent-host/link"


def machine_hello(installation_id: str) -> dict[str, object]:
    return {
        "installation_id": installation_id,
        "host_release": "e2e",
        "protocol_version": PROTOCOL_VERSION,
    }


async def request_code(async_client, *, claim_key: str, display_name: str) -> dict:
    response = await async_client.post(
        "/agent-host/compute/pairings",
        json={
            "claim_key_sha256": hashlib.sha256(claim_key.encode()).hexdigest(),
            "display_name": display_name,
            "mode": "host",
            "platform": "linux",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def claim(
    server: dict[str, str], *, code: str, claim_key: str, installation_id: str
) -> dict:
    """The machine's ``pair``: waits on its link until the person approves."""
    async with websockets.connect(link_url(server)) as link:
        await link.send(
            json.dumps(
                {
                    "type": "pair",
                    "id": "p1",
                    "body": {
                        "role": "compute",
                        "pairing_code": code,
                        "claim_key": claim_key,
                        "hello": machine_hello(installation_id),
                    },
                }
            )
        )
        frame = json.loads(await asyncio.wait_for(link.recv(), 60))
    assert frame["type"] == "paired", frame
    return frame["body"]


async def pair_machine(
    server: dict[str, str],
    async_client,
    owner_client,
    *,
    name: str = "laptop",
    display_name: str = "E2E Laptop",
) -> tuple[dict, str]:
    """Ask, approve and claim: the connection and the installation id."""
    claim_key = secrets.token_urlsafe(32)
    installation_id = f"e2e-{uuid4()}"
    code = (
        await request_code(async_client, claim_key=claim_key, display_name=display_name)
    )["code"]
    claiming = asyncio.ensure_future(
        claim(server, code=code, claim_key=claim_key, installation_id=installation_id)
    )
    await asyncio.sleep(0.2)
    approved = await owner_client.post(
        "/machine-connections:pair", json={"code": code, "name": name}
    )
    assert approved.status_code == 201, approved.text
    paired = await claiming
    return paired, installation_id


@dataclass
class RunningMachine:
    agent: MachineAgent
    task: asyncio.Task[None]
    config: MachineConfig
    connection_id: UUID
    name: str
    root: Path

    async def stop(self) -> None:
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        await self.agent.runtime.stop()

    def restart(self) -> None:
        self.task = asyncio.ensure_future(self.agent.run())


def machine_config(
    *,
    server: dict[str, str],
    paired: dict,
    installation_id: str,
    root: Path,
) -> MachineConfig:
    state = root / ".lemma"
    secret_file = state / "machine-secret"
    write_secret(secret_file, paired["machine_secret"])
    return MachineConfig(
        api_url=server["host_base_url"],
        connection_id=paired["connection_id"],
        name=paired["name"],
        mode="host",
        home=str(root),
        overlay_root=str(state / "runtime"),
        python=sys.executable,
        installation_id=installation_id,
        secret_file=str(secret_file),
        runtime_port=free_port(),
    )


async def connection_row(owner_client, connection_id: UUID) -> dict | None:
    listed = await owner_client.get("/machine-connections")
    assert listed.status_code == 200, listed.text
    return next(
        (item for item in listed.json()["items"] if item["id"] == str(connection_id)),
        None,
    )


@asynccontextmanager
async def running_machine(server, async_client, owner_client, root: Path, **kwargs):
    paired, installation_id = await pair_machine(
        server, async_client, owner_client, **kwargs
    )
    config = machine_config(
        server=server, paired=paired, installation_id=installation_id, root=root
    )
    agent = MachineAgent(config)
    machine = RunningMachine(
        agent=agent,
        task=asyncio.ensure_future(agent.run()),
        config=config,
        connection_id=UUID(paired["connection_id"]),
        name=paired["name"],
        root=root,
    )

    try:
        await eventually(
            label="the machine is online with its runtime ready",
            probe=lambda: connection_row(owner_client, machine.connection_id),
            done=lambda row: bool(
                row and row["online"] and row["capabilities"].get("runtime_ready")
            ),
            timeout_seconds=60,
        )
        yield machine
    finally:
        await machine.stop()


def provider_for(row: dict):
    from app.modules.workspace.services.machine_link.handler import (
        connected_machine_provider,
    )

    return connected_machine_provider(
        MachineConnection(
            id=UUID(row["id"]),
            user_id=uuid4(),
            name=row["name"],
            display_name=row["display_name"],
            mode=row["mode"],
            pod_id=None,
            platform=row["platform"],
            home=row["home"],
            capabilities=row["capabilities"],
        )
    )
