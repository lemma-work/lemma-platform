"""The Desktop provider against the real `lemma-runtime` bridge binary.

The unit tests drive a Python script standing in for the bridge, which proves
the provider's half of the contract. This proves the other half: the actual
Rust binary Desktop ships, doing its real work -- reading a line of JSON,
injecting the guest capability, exchanging it over a unix socket, and parsing
what comes back.

What is still stubbed is the guest behind the socket, because that is a VZ
virtual machine requiring virtualization entitlements. So this covers
everything between the provider and the guest boundary; it does not cover the
guest itself.

Build the bridge with:
    cargo build --release --manifest-path local-runtime/hostctl/Cargo.toml
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import (
    ProviderCreateSpec,
    ProviderStorageKind,
)
from app.modules.workspace.providers.docker import RuntimeCredentialSigner
from app.modules.workspace.providers.lemma_local import (
    LemmaLocalProviderConfig,
    LemmaLocalSandboxProvider,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

PINNED = "lemma-workspace@sha256:" + "d" * 64


def _bridge_binary() -> Path | None:
    override = os.getenv("LEMMA_MANAGED_RUNTIME_CLI")
    if override and Path(override).is_file():
        return Path(override)
    # Walk up to the repo root rather than counting directories, which is
    # brittle and was already wrong once.
    for parent in Path(__file__).resolve().parents:
        built = parent / "local-runtime/hostctl/target/release/lemma-runtime"
        if built.is_file():
            return built
    found = shutil.which("lemma-runtime")
    return Path(found) if found else None


class StubGuest:
    """A guest daemon that speaks the socket protocol the bridge expects.

    Only the guest is stubbed. Everything the bridge does -- framing, capability
    injection, the socket exchange -- is the shipped implementation.
    """

    def __init__(self, capability: str) -> None:
        self.capability = capability
        self.sandboxes: dict[str, dict] = {}
        self.requests: list[dict] = []
        self.rejected_capabilities: list[str | None] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            raw = await reader.read(1024 * 1024)
            if not raw:
                return
            payload = json.loads(raw)
            self.requests.append(payload)

            # The bridge is what injects this. A guest that accepted a request
            # without it would be reachable by anything on the machine.
            supplied = payload.get("capability")
            if supplied != self.capability:
                self.rejected_capabilities.append(supplied)
                writer.write(
                    json.dumps(
                        {
                            "ok": False,
                            "error": {
                                "code": "unauthorized",
                                "message": "bad capability",
                                "retryable": False,
                            },
                        }
                    ).encode()
                )
                await writer.drain()
                return

            writer.write(json.dumps(self._respond(payload)).encode())
            await writer.drain()
        finally:
            writer.close()

    def _respond(self, payload: dict) -> dict:
        operation = payload.get("operation")
        parameters = payload.get("parameters") or {}
        sandbox_id = parameters.get("sandbox_id")
        status = {
            "state": "running",
            "runtime_url": "http://127.0.0.1:9",
            "apps": {
                "runtime": {"port": 8080, "private_url": "http://127.0.0.1:9"},
                "browser": {"port": 4848, "private_url": "http://127.0.0.1:10"},
            },
        }
        if operation == "sandbox.ensure":
            existing = self.sandboxes.get(sandbox_id, {})
            self.sandboxes[sandbox_id] = {
                "metadata": parameters.get("metadata", {}),
                "creates": existing.get("creates", 0) + 1,
            }
            return {"ok": True, "result": {"status": status, "provider_id": sandbox_id}}
        if operation == "sandbox.status":
            if sandbox_id not in self.sandboxes:
                return {
                    "ok": False,
                    "error": {
                        "code": "not_found",
                        "message": "no such sandbox",
                        "retryable": False,
                    },
                }
            return {"ok": True, "result": {"status": status, "provider_id": sandbox_id}}
        if operation in {"sandbox.release", "sandbox.purge_storage"}:
            return {"ok": True, "result": {}}
        if operation == "sandbox.delete":
            self.sandboxes.pop(sandbox_id, None)
            return {"ok": True, "result": {}}
        if operation == "sandbox.list":
            return {
                "ok": True,
                "result": {
                    "sandboxes": [
                        {
                            "sandbox_id": name,
                            "metadata": entry["metadata"],
                            "status": {"state": "running"},
                        }
                        for name, entry in self.sandboxes.items()
                    ]
                },
            }
        return {
            "ok": False,
            "error": {
                "code": "unsupported",
                "message": f"unknown operation {operation}",
                "retryable": False,
            },
        }


@pytest_asyncio.fixture
async def real_bridge(monkeypatch) -> AsyncIterator[tuple]:
    binary = _bridge_binary()
    if binary is None:
        pytest.skip(
            "build the bridge first: cargo build --release "
            "--manifest-path local-runtime/hostctl/Cargo.toml"
        )

    # A unix socket path is capped near 104 bytes, and pytest's tmp_path under
    # a worktree is already longer than that, so the socket gets its own short
    # directory rather than inheriting a deep one.
    short_root = Path(tempfile.mkdtemp(prefix="lg-", dir="/tmp"))

    capability = "cap-" + uuid4().hex
    capability_file = short_root / "guest.capability"
    capability_file.write_text(capability)
    # The bridge refuses a capability file any other user could read.
    capability_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    socket_path = short_root / "guest.sock"
    guest = StubGuest(capability)
    server = await asyncio.start_unix_server(guest.handle, path=str(socket_path))

    monkeypatch.setenv("LEMMA_GUEST_CAPABILITY_FILE", str(capability_file))
    monkeypatch.setenv("LEMMA_GUEST_CONTROL_SOCKET", str(socket_path))

    provider = LemmaLocalSandboxProvider(
        LemmaLocalProviderConfig(executable=str(binary)),
        RuntimeCredentialSigner(key=b"k" * 32),
    )
    try:
        yield provider, guest, capability_file
    finally:
        server.close()
        await server.wait_closed()
        shutil.rmtree(short_root, ignore_errors=True)


def _deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=30)


def _spec(sandbox_id, *, epoch: int = 1, kind=SandboxKind.WORKSPACE):
    return ProviderCreateSpec(
        sandbox_id=sandbox_id,
        kind=kind,
        epoch=epoch,
        name=naming.container_name(sandbox_id, kind, epoch),
        image=PINNED,
        profile_name="workspace",
        profile_digest="sha256:" + "a" * 64,
        deadline_at=_deadline(),
    )


async def test_the_shipped_bridge_carries_a_create_through(real_bridge) -> None:
    """Provider -> real Rust binary -> socket -> guest, and back."""
    provider, guest, _ = real_bridge
    sandbox_id = uuid4()

    instance = await provider.create(_spec(sandbox_id))

    assert instance.provider_id == f"w-{sandbox_id.hex}"
    assert instance.running is True
    ensures = [r for r in guest.requests if r["operation"] == "sandbox.ensure"]
    assert len(ensures) == 1
    assert ensures[0]["parameters"]["image"] == PINNED
    assert ensures[0]["parameters"]["metadata"]["lemma-sandbox-id"] == str(sandbox_id)


async def test_the_bridge_injects_the_capability_itself(real_bridge) -> None:
    """The provider never sees or sends the guest capability.

    That is the whole point of the bridge being a separate signed binary: the
    secret lives in a file only it reads, so a compromised backend process
    cannot forge guest calls.
    """
    provider, guest, capability_file = real_bridge
    await provider.create(_spec(uuid4()))

    sent = guest.requests[-1]
    assert sent["capability"] == capability_file.read_text()
    assert guest.rejected_capabilities == []


async def test_a_caller_cannot_supply_its_own_capability(real_bridge) -> None:
    """The bridge refuses a request that already carries one, so a caller
    cannot smuggle in a capability it should not have."""
    _, _, capability_file = real_bridge
    binary = _bridge_binary()
    assert binary is not None

    result = subprocess.run(
        [str(binary), "request"],
        input=json.dumps(
            {
                "version": 1,
                "operation": "sandbox.status",
                "parameters": {"sandbox_id": "w-x"},
                "capability": "forged",
            }
        )
        + "\n",
        capture_output=True,
        text=True,
        env={**os.environ},
    )

    assert result.returncode != 0
    assert "capability" in result.stderr.lower()


async def test_the_bridge_refuses_a_world_readable_capability(
    real_bridge, capsys
) -> None:
    """A capability any process could read is not a capability."""
    provider, _, capability_file = real_bridge
    capability_file.chmod(0o644)

    from app.modules.workspace.providers.base import ProviderCreateAmbiguous

    with pytest.raises((ProviderCreateAmbiguous, RuntimeError)):
        await provider.create(_spec(uuid4()))


async def test_ensure_is_idempotent_through_the_real_bridge(real_bridge) -> None:
    provider, guest, _ = real_bridge
    sandbox_id = uuid4()

    first = await provider.create(_spec(sandbox_id, epoch=1))
    second = await provider.create(_spec(sandbox_id, epoch=2))

    assert first.provider_id == second.provider_id
    assert len(guest.sandboxes) == 1
    # A new epoch adopts rather than replacing, because the guest sandbox owns
    # the disk.
    assert first.storage_adopted is False
    assert second.storage_adopted is True


async def test_lifecycle_round_trips_through_the_real_bridge(real_bridge) -> None:
    provider, guest, _ = real_bridge
    sandbox_id = uuid4()
    instance = await provider.create(_spec(sandbox_id))

    found = await provider.inspect(
        naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1),
        deadline_at=_deadline(),
    )
    assert found is not None and found.provider_id == instance.provider_id

    assert (
        await provider.port_base_url(instance, port=4848, deadline_at=_deadline())
        == "http://127.0.0.1:10"
    )

    await provider.release(
        instance, kind=SandboxKind.WORKSPACE, deadline_at=_deadline()
    )
    assert instance.provider_id in guest.sandboxes, "release must not delete"

    objects = await provider.list_objects(deadline_at=_deadline())
    assert [obj.sandbox_id for obj in objects] == [sandbox_id]

    await provider.destroy(
        naming.container_name(sandbox_id, SandboxKind.WORKSPACE, 1),
        deadline_at=_deadline(),
    )
    assert guest.sandboxes == {}


async def test_a_missing_sandbox_is_definitively_gone_through_the_real_bridge(
    real_bridge,
) -> None:
    """The guest's not_found must survive the whole round trip as a definitive
    answer, or a caller retries something that will never appear."""
    provider, _, _ = real_bridge
    from app.modules.workspace.providers.base import ProviderGone

    instance = await provider.create(_spec(uuid4()))
    await provider.destroy(
        naming.container_name(
            naming.parse_container_name(instance.name)[0], SandboxKind.WORKSPACE, 1
        ),
        deadline_at=_deadline(),
    )

    with pytest.raises(ProviderGone):
        await provider.port_base_url(instance, port=8080, deadline_at=_deadline())


async def test_storage_model_is_declared_the_same_way_here(real_bridge) -> None:
    provider, _, _ = real_bridge
    assert provider.storage_kind is ProviderStorageKind.SANDBOX_NATIVE
