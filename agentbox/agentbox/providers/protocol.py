from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentbox.apps import SandboxAppSpec
from agentbox.schemas import SandboxEnsureRequest, SandboxInternalStatus

from .models import (
    EndpointProtocol,
    ManagedSandbox,
    ProviderCapabilities,
    ProviderCapacityPolicy,
    SandboxEndpoint,
    SandboxRef,
)


@runtime_checkable
class SandboxLifecycleProvider(Protocol):
    """Compute lifecycle and connection contract implemented by providers."""

    provider_name: str

    async def create(
        self,
        sandbox_id: str,
        request: SandboxEnsureRequest,
    ) -> SandboxInternalStatus: ...

    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus: ...

    async def list_managed(self) -> list[ManagedSandbox]: ...

    async def delete(self, sandbox_id: str) -> bool: ...

    async def resolve_endpoint(
        self,
        sandbox_id: str,
        app: SandboxAppSpec,
        *,
        protocol: EndpointProtocol = "http",
    ) -> SandboxEndpoint: ...

    async def close(self) -> None: ...


@runtime_checkable
class SandboxBootstrapProvider(Protocol):
    """Optional narrow hook for providers whose templates snapshot too early."""

    async def bootstrap(
        self,
        sandbox_id: str,
        request: SandboxEnsureRequest,
    ) -> None: ...


@runtime_checkable
class SandboxReleaseProvider(Protocol):
    """Optional provider capability for releasing idle compute without purge."""

    async def release(self, sandbox_id: str) -> bool: ...


@runtime_checkable
class SandboxLeaseProvider(Protocol):
    """Optional hook for extending a running provider sandbox's lease."""

    async def renew_lease(self, sandbox_id: str) -> None: ...


@runtime_checkable
class SandboxAdoptionProvider(Protocol):
    """Reconnect an exact durable provider generation after manager restart."""

    async def adopt(self, sandbox_id: str, provider_id: str) -> bool: ...


@runtime_checkable
class SandboxStoragePurgeProvider(Protocol):
    """Optional permanent removal of storage owned by one logical sandbox."""

    async def purge_storage(self, sandbox_id: str) -> bool: ...


@runtime_checkable
class SandboxCacheProvider(Protocol):
    """Optional cache hook used after a proxy observes a stale endpoint."""

    def invalidate_sandbox_cache(self, sandbox_id: str) -> None: ...


@runtime_checkable
class SandboxCapabilitiesProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...


@runtime_checkable
class SandboxCapacityProvider(Protocol):
    @property
    def capacity_policy(self) -> ProviderCapacityPolicy: ...


@runtime_checkable
class SandboxGenerationCreateProvider(Protocol):
    """Create or recover only the provider object for one durable intent."""

    async def create_generation(
        self,
        sandbox_id: str,
        request: SandboxEnsureRequest,
        *,
        generation_token: str,
    ) -> SandboxInternalStatus: ...


@runtime_checkable
class SandboxManagedPurgeProvider(Protocol):
    """Optional exact-instance purge used by orphan reconciliation."""

    async def purge_managed(self, ref: SandboxRef) -> bool: ...
