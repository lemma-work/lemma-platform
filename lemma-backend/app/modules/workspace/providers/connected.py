"""A connected machine as a sandbox provider: someone's own laptop or VM.

See docs/architecture/connected-machines.md. The machine runs the same
workspace runtime a Docker or E2B sandbox does, so every operation is the
unchanged ``WorkspaceRuntimeClient`` -- inherited from ``DockerOpsMixin`` --
pointed at ``http://<connection>.machine.lemma.internal:<port>``, an address
the sandbox transport sends down the machine's link. Only three things are its
own:

* **Lifecycle is the person's, not ours.** Nothing is created, released or
  destroyed: the machine is there while its link is up. ``create`` and
  ``wait_ready`` answer whether it is, and a machine whose link is down is
  ``MachineNotConnected`` ("<name> is not connected") -- never a reason to run
  the work somewhere else. Compute meters 0 CU here.
* **Paths.** Lemma and its agents name ``/home/user/...``; a host-mode
  machine's home is its ``--root``. Paths going in are moved under the home the
  machine reported and paths coming back are moved out again, so callers --
  and the ``/machines`` journeys -- read the same paths on every fabric.
* **Ports** are reached on the link (``reach_port``), and a secret is
  delivered through the runtime's file API rather than a container archive.

One provider per connection: the machine resolver builds it for a machine
whose fabric is ``connected``.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID

from sandbox_runtime.errors import SandboxError
from sandbox_runtime.paths import SANDBOX_HOME
from sandbox_runtime.protocol import (
    ByteRange,
    CreatePythonSessionRequest,
    FileStat,
    StartProcessRequest,
)

from app.modules.workspace.domain.machine_errors import MachineNotConnected
from app.modules.workspace.domain.machine_connection import (
    MachineConnection,
)
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers.base import (
    ProviderCapability,
    ProviderCreateSpec,
    ProviderInstance,
    ProviderNotReady,
    ProviderObject,
    ProviderRejected,
    ProviderStorageKind,
    SandboxEndpoint,
)
from app.modules.workspace.providers.docker import RuntimeCredentialSigner
from app.modules.workspace.providers.docker_ops import DockerOpsMixin
from app.modules.workspace.providers.runtime_client import WorkspaceRuntimeClient
from app.modules.workspace.providers.runtime_errors import WorkspaceRuntimeError
from app.modules.workspace.services.machine_link.network import machine_url

#: The ports a machine serves the runtime and relay on when it reports none.
DEFAULT_RUNTIME_PORT = 8080


def runtime_token(signer: RuntimeCredentialSigner, connection_id: UUID) -> str:
    """The credential Lemma presents to a machine's runtime.

    Derived, never stored: the link's ``welcome`` carries it to the machine,
    and this side computes it again for every client.
    """
    return signer.token(f"machine:{connection_id}")


class ConnectedMachineProvider(DockerOpsMixin):
    name = "connected"
    # The machine is its own disk; there is no volume to manage or adopt.
    storage_kind = ProviderStorageKind.SANDBOX_NATIVE
    resumes_stopped_instances = True
    capabilities = frozenset(
        {ProviderCapability.PORT_REACH, ProviderCapability.SECRET_DELIVERY}
    )

    def __init__(
        self,
        connection: MachineConnection,
        runtime_credentials: RuntimeCredentialSigner,
    ) -> None:
        self._connection = connection
        self._runtime_credentials = runtime_credentials
        home = connection.home or SANDBOX_HOME
        self._home = home.rstrip("/") or "/"

    @property
    def connection(self) -> MachineConnection:
        return self._connection

    def _not_connected(self) -> MachineNotConnected:
        return MachineNotConnected(self._connection.name)

    @staticmethod
    def _offline(error: BaseException) -> bool:
        """Whether the machine's link being down is why ``error`` happened."""
        seen: BaseException | None = error
        while seen is not None:
            if isinstance(seen, MachineNotConnected):
                return True
            seen = seen.__cause__ or seen.__context__
        return False

    @asynccontextmanager
    async def _ops_client(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> AsyncIterator[WorkspaceRuntimeClient]:
        """The inherited mapping, except that an unreachable machine says so."""
        try:
            async with super()._ops_client(instance, deadline_at=deadline_at) as client:
                yield client
        except MachineNotConnected:
            raise
        except SandboxError as error:
            # The inherited mapping turns every runtime failure into one of these.
            if self._offline(error):
                raise self._not_connected() from error
            raise

    # ------------------------------------------------------------------ paths

    def to_machine(self, path: str) -> str:
        """A path as Lemma names it, as the machine names it."""
        if self._home == SANDBOX_HOME:
            return path
        if path == SANDBOX_HOME:
            return self._home
        if path.startswith(f"{SANDBOX_HOME}/"):
            return self._home + path[len(SANDBOX_HOME) :]
        return path

    def from_machine(self, path: str) -> str:
        if self._home == SANDBOX_HOME:
            return path
        if path == self._home:
            return SANDBOX_HOME
        if path.startswith(f"{self._home}/"):
            return SANDBOX_HOME + path[len(self._home) :]
        return path

    def _stat_out(self, stat: FileStat) -> FileStat:
        return dataclasses.replace(stat, path=self.from_machine(stat.path))

    # -------------------------------------------------------------- lifecycle

    def _instance(self, name: str, *, running: bool) -> ProviderInstance:
        # The container name as the provider id, as the host fabric does: the
        # sandbox client hands it back as the instance's name, which is what
        # ``HostRoutingProvider`` routes on.
        return ProviderInstance(
            provider_id=name,
            name=name,
            running=running,
            storage_adopted=True,
        )

    async def create(self, spec: ProviderCreateSpec) -> ProviderInstance:
        if spec.kind is not SandboxKind.WORKSPACE:
            raise ProviderRejected("a connected machine runs workspaces only")
        return self._instance(spec.name, running=True)

    async def wait_ready(
        self, instance: ProviderInstance, *, kind: SandboxKind, deadline_at: datetime
    ) -> None:
        """Ready once the machine's own runtime answers its health check."""
        client = await self._runtime_client(
            instance.provider_id, deadline_at=deadline_at
        )
        try:
            await client.health(deadline_at=deadline_at)
        except WorkspaceRuntimeError as exc:
            if self._offline(exc):
                raise self._not_connected() from exc
            raise ProviderNotReady(
                f"{self._connection.name}'s runtime is starting", retry_after_ms=500
            ) from exc
        except asyncio.TimeoutError as exc:
            raise ProviderNotReady(
                f"{self._connection.name}'s runtime did not answer in time"
            ) from exc
        finally:
            await client.close()

    async def inspect(
        self, name: str, *, deadline_at: datetime
    ) -> ProviderInstance | None:
        return self._instance(name, running=True)

    async def release(
        self, instance: ProviderInstance, *, kind: SandboxKind, deadline_at: datetime
    ) -> None:
        """Nothing to stop: the machine is the person's and stays on."""

    async def destroy(self, name: str, *, deadline_at: datetime) -> None:
        """Never: forgetting a machine is revoking its connection."""

    async def find_volume(
        self, *, sandbox_id: UUID, deadline_at: datetime
    ) -> str | None:
        return None

    async def destroy_volume(self, name: str, *, deadline_at: datetime) -> None:
        return None

    async def list_objects(
        self, *, deadline_at: datetime
    ) -> tuple[ProviderObject, ...]:
        # Nothing for a sweep to reclaim: the machine is not ours to reclaim.
        return ()

    async def close(self) -> None:
        return None

    # ----------------------------------------------------------- runtime reach

    def _port(self, key: str, default: int | None) -> int | None:
        value = self._connection.capabilities.get(key)
        return value if isinstance(value, int) else default

    async def _runtime_client(
        self, provider_id: str, *, deadline_at: datetime
    ) -> WorkspaceRuntimeClient:
        port = self._port("runtime_port", DEFAULT_RUNTIME_PORT)
        assert port is not None
        return WorkspaceRuntimeClient(
            machine_url(self._connection.id, port),
            runtime_token(self._runtime_credentials, self._connection.id),
        )

    async def reach_port(
        self, instance: ProviderInstance, *, port: int, deadline_at: datetime
    ) -> SandboxEndpoint:
        """The port on the machine's loopback, over its link. Not public: the
        link is the only way in, and only Lemma holds it."""
        return SandboxEndpoint(url=machine_url(self._connection.id, port))

    async def deliver_secret(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        value: bytes,
        deadline_at: datetime,
    ) -> None:
        """Through the runtime's file API: there is no container to archive
        into, and the bytes travel on the encrypted link, never in argv."""

        async def chunks() -> AsyncIterator[bytes]:
            yield value

        directory = path.rpartition("/")[0] or "/"
        await self.create_directory(instance, path=directory, deadline_at=deadline_at)
        await self.write_file(
            instance,
            path=path,
            data=chunks(),
            expected_sha256=None,
            deadline_at=deadline_at,
        )

    # --------------------------------------------- operations, with path moves

    async def start_process(
        self, instance: ProviderInstance, request: StartProcessRequest, *, deadline_at
    ) -> str:
        moved = dataclasses.replace(request, cwd=self.to_machine(request.cwd))
        return await super().start_process(instance, moved, deadline_at=deadline_at)

    async def stat_file(self, instance, *, path: str, deadline_at) -> FileStat:
        stat = await super().stat_file(
            instance, path=self.to_machine(path), deadline_at=deadline_at
        )
        return self._stat_out(stat)

    async def list_files(self, instance, *, path: str, deadline_at):
        listed = await super().list_files(
            instance, path=self.to_machine(path), deadline_at=deadline_at
        )
        return tuple(self._stat_out(stat) for stat in listed)

    async def create_directory(self, instance, *, path: str, deadline_at) -> None:
        await super().create_directory(
            instance, path=self.to_machine(path), deadline_at=deadline_at
        )

    async def open_file(
        self, instance, *, path: str, byte_range: ByteRange, deadline_at
    ) -> AsyncIterator[bytes]:
        async for chunk in super().open_file(
            instance,
            path=self.to_machine(path),
            byte_range=byte_range,
            deadline_at=deadline_at,
        ):
            yield chunk

    async def write_file(
        self,
        instance,
        *,
        path: str,
        data: AsyncIterable[bytes],
        expected_sha256: str | None,
        deadline_at,
    ) -> FileStat:
        stat = await super().write_file(
            instance,
            path=self.to_machine(path),
            data=data,
            expected_sha256=expected_sha256,
            deadline_at=deadline_at,
        )
        return self._stat_out(stat)

    async def move_file(
        self, instance, *, source: str, destination: str, deadline_at
    ) -> None:
        await super().move_file(
            instance,
            source=self.to_machine(source),
            destination=self.to_machine(destination),
            deadline_at=deadline_at,
        )

    async def delete_file(
        self, instance, *, path: str, recursive: bool, deadline_at
    ) -> bool:
        return await super().delete_file(
            instance,
            path=self.to_machine(path),
            recursive=recursive,
            deadline_at=deadline_at,
        )

    async def ensure_python_session(
        self, instance, request: CreatePythonSessionRequest
    ) -> None:
        moved = dataclasses.replace(request, cwd=self.to_machine(request.cwd))
        await super().ensure_python_session(instance, moved)
