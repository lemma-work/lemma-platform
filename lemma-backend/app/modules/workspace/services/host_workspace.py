"""Opening a paired user's host workspace, and the plumbing the host provider needs.

See docs/architecture/desktop-host-execution.md. The selection itself -- is
this run the owner's, is their Mac there -- belongs to the agent module, which
knows about runs. This module is told the answer and does what a workspace
does with it: make the sandbox row, record which host it is bound to, open it,
and hand back the root the host chose.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.workspace.domain.host_execution import (
    HostBinding,
    HostWorkspace,
    host_sandbox_id,
    host_sandbox_slug,
)
from app.modules.workspace.domain.sandbox import SandboxKind, SandboxOwnerKind
from app.modules.workspace.infrastructure.host_binding_repository import (
    HostBindingRepository,
)
from app.modules.workspace.providers.agent_host import (
    AgentHostSandboxProvider,
    HostOpRefused,
    sandbox_error,
)

logger = get_logger(__name__)

_OPEN_SECONDS = 30.0


class LinkTransport:
    """The Agent Host link client, speaking the provider's error vocabulary."""

    def __init__(self, client=None) -> None:
        self._client = client

    def _link(self):
        if self._client is None:
            from app.modules.agent.contracts.host_execution import AgentHostOpClient

            self._client = AgentHostOpClient()
        return self._client

    async def request(
        self,
        *,
        host_id: UUID,
        workspace: UUID,
        method: str,
        params: dict[str, object],
        deadline_at: datetime,
    ) -> dict[str, object]:
        from app.modules.agent.contracts.host_execution import AgentHostOpError

        try:
            return await self._link().request(
                host_id=host_id,
                workspace=workspace,
                method=method,
                params=params,
                deadline_at=deadline_at,
            )
        except AgentHostOpError as exc:
            raise HostOpRefused(exc.kind, exc.message, retryable=exc.retryable) from exc


class SqlHostBindingStore:
    def __init__(self, uow_factory=None) -> None:
        self._uow_factory = uow_factory or SessionUnitOfWorkFactory(async_session_maker)

    async def get(self, sandbox_id: UUID) -> HostBinding | None:
        async with self._uow_factory() as uow:
            return await HostBindingRepository(uow).get(sandbox_id)

    async def set_root(self, sandbox_id: UUID, root: str) -> None:
        async with self._uow_factory() as uow:
            await HostBindingRepository(uow).set_root(sandbox_id, root)
            await uow.commit()

    async def bind(self, binding: HostBinding) -> None:
        async with self._uow_factory() as uow:
            await HostBindingRepository(uow).bind(binding)
            await uow.commit()


def build_host_provider() -> AgentHostSandboxProvider:
    return AgentHostSandboxProvider(LinkTransport(), SqlHostBindingStore())


def host_provider_of(service) -> AgentHostSandboxProvider | None:
    """The host provider behind a sandbox service, when this install has one."""
    host = getattr(getattr(service, "_provider", None), "host", None)
    return host if isinstance(host, AgentHostSandboxProvider) else None


async def open_host_workspace(
    *,
    owner_id: UUID,
    conversation_id: UUID,
    host_id: UUID,
    day: str,
    slug: str,
    root_hint: str | None,
    service=None,
    bindings: SqlHostBindingStore | None = None,
) -> HostWorkspace:
    """Bind a conversation's host sandbox to this host, open it, name its root.

    Raises a ``sandbox_runtime`` error when the Mac cannot open it -- which,
    at selection time, is the caller's cue to run in the VM instead: nothing
    has run anywhere yet, so nothing moves.
    """
    from app.modules.workspace.services.sandbox_composition import (
        get_sandbox_service,
    )

    service = service or get_sandbox_service()
    provider = host_provider_of(service)
    if provider is None:
        from sandbox_runtime.errors import SandboxRejected

        raise SandboxRejected("this installation does not run commands on a host")
    sandbox_id = host_sandbox_id(conversation_id)
    await service.resolve(
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=owner_id,
        slug=host_sandbox_slug(conversation_id),
        sandbox_id=sandbox_id,
    )
    await (bindings or SqlHostBindingStore()).bind(
        HostBinding(
            sandbox_id=sandbox_id,
            host_id=host_id,
            owner_id=owner_id,
            conversation_id=conversation_id,
            slug=slug,
            day=day,
            root_hint=root_hint,
        )
    )
    handle = await service.ensure(sandbox_id)
    root = await provider.open_workspace(
        handle.sandbox_id,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=_OPEN_SECONDS),
    )
    logger.info(
        "workspace.host_workspace.opened",
        sandbox_id=str(sandbox_id),
        host_id=str(host_id),
        bound_folder=root_hint is not None,
    )
    return HostWorkspace(sandbox_id=sandbox_id, root=root)


__all__ = [
    "HostOpRefused",
    "LinkTransport",
    "SqlHostBindingStore",
    "build_host_provider",
    "host_provider_of",
    "open_host_workspace",
    "sandbox_error",
]
