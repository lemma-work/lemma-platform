"""Adopting or minting the disk a sandbox runs on.

A mixin rather than a collaborator because it needs the service's provider and
unit-of-work factory and has no state of its own -- the same reason
``ConversationRunQueriesMixin`` is one. Split out to keep ``sandbox_service``
under the architecture ratchet's file-size limit.

The rule it exists to enforce: a volume is adopted before one is created. The
disk holding a user's files was named from a token this schema never knew, so it
is found by label. Only when there is genuinely nothing to adopt is a name
minted -- and that is also the moment the storage generation moves, because it
is the moment the user's files are actually gone.
"""

from __future__ import annotations

from datetime import datetime

from app.core.log.log import get_logger
from app.modules.workspace.domain.sandbox import Sandbox, SandboxKind
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.base import ProviderStorageKind

logger = get_logger(__name__)


class SandboxVolumeMixin:
    """``_resolve_volume``, mixed into the sandbox service."""

    async def _resolve_volume(
        self, sandbox: Sandbox, *, deadline_at: datetime
    ) -> tuple[str | None, int]:
        """Adopt the sandbox's disk, or mint one and record that it is new.

        A function sandbox has no durable disk at all: it runs an immutable
        artifact refetched from the gateway, so a wiped function sandbox has
        lost nothing and needs no volume.
        """
        if sandbox.kind is SandboxKind.FUNCTION:
            return None, sandbox.storage_generation

        if (
            getattr(self._provider, "storage_kind", ProviderStorageKind.VOLUME)
            is ProviderStorageKind.SANDBOX_NATIVE
        ):
            # The provider's sandbox *is* the disk, so there is no separate
            # volume to manage and adoption happens inside create. The
            # generation is settled afterwards, from whether it adopted.
            return None, sandbox.storage_generation

        adopted = await self._provider.find_volume(
            sandbox_id=sandbox.id, deadline_at=deadline_at
        )
        if adopted is not None:
            return adopted, sandbox.storage_generation

        generation = sandbox.storage_generation
        if sandbox.provider_volume_id is not None:
            # We had a disk and it is not there any more. That is the one event
            # an agent must be told about, or it reads an empty directory as
            # "nothing was ever here".
            async with self._uow_factory() as uow:
                repository = SandboxRepository(uow)
                generation = await repository.bump_storage_generation(sandbox.id)
                await uow.commit()
            logger.info(
                "workspace.sandbox_service.workspace_storage_recreated",
                sandbox_id=str(sandbox.id),
            )

        name = naming.volume_name(sandbox.id, generation)
        created = await self._provider.ensure_volume(
            sandbox_id=sandbox.id, name=name, deadline_at=deadline_at
        )
        return created, generation
