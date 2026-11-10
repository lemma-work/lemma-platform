"""What the Agent Host link route runs for a first frame naming ``compute``.

Assembled here so the link route, in the agent module, needs to know only that
a compute handler exists; everything a compute link is made of is this
module's. Also where the rest of the workspace module gets a connected
machine's provider.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from app.core.domain.realtime import RealtimeChannel
from app.modules.agent.contracts.compute_link import LinkCloseCode, LinkFrame
from app.modules.agent.contracts.compute_link import (
    LinkClose,
    LinkSocket,
    LinkWriter,
)
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine_connection import MachineConnection
from app.modules.workspace.providers.connected import (
    ConnectedMachineProvider,
    runtime_token,
)
from app.modules.workspace.providers.connected_fabric import ConnectedFabricProvider
from app.modules.workspace.providers.docker import RuntimeCredentialSigner
from app.modules.workspace.services.machine_link.dialer import (
    link_routes,
    machine_link_registry,
)
from app.modules.workspace.services.machine_link.runtime_push import bundle_version
from app.modules.workspace.services.machine_link.session import ComputeLinkSession
from app.modules.workspace.services.machine_link.store import MachineLinkStore

_store = MachineLinkStore()


def credential_signer() -> RuntimeCredentialSigner:
    key = workspace_settings.runtime_credential_key
    if not key:
        raise RuntimeError(
            "WORKSPACE_RUNTIME_CREDENTIAL_KEY is required for connected machines"
        )
    return RuntimeCredentialSigner(key=key.encode())


def machine_runtime_token(connection_id: UUID) -> str:
    return runtime_token(credential_signer(), connection_id)


def connected_machine_provider(
    connection: MachineConnection,
) -> ConnectedMachineProvider:
    """The provider for one connected machine, as its row describes it."""
    return ConnectedMachineProvider(connection, credential_signer())


def connected_fabric_provider() -> ConnectedFabricProvider:
    """Every connected machine, for ``HostRoutingProvider``'s connected route."""
    return ConnectedFabricProvider(_store.connection, credential_signer())


def compute_link_handler(channels: RealtimeChannel):
    """The handler ``AgentHostLinkSession`` delegates a compute link to."""

    async def serve(
        socket: LinkSocket,
        writer: LinkWriter,
        stop: Callable[[int, str], None],
        first: LinkFrame,
    ) -> None:
        if not workspace_settings.machine_connections_enabled:
            raise LinkClose(
                LinkCloseCode.INVALID_CREDENTIAL, "connected machines are off here"
            )
        await ComputeLinkSession(
            socket,
            writer,
            stop=stop,
            store=_store,
            channels=channels,
            registry=machine_link_registry,
            routes=link_routes,
            runtime_token=machine_runtime_token,
            bundle_version=bundle_version,
        ).serve(first)

    return serve
