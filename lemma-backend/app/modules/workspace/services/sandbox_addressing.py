"""How a ready sandbox is addressed, and how a caller reaches into it.

A mixin rather than a collaborator for the same reason ``SandboxVolumeMixin``
is one: it needs the service's provider and has no state of its own. Split out
to keep ``sandbox_service`` under the architecture ratchet's file-size limit.

Both methods answer the same question in two shapes. ``_handle`` is what a
caller carries so the epoch travels with every operation. ``reach`` is the pair
a caller needs to make a provider call of its own, and it exists so that
``reach_port`` and ``deliver_secret`` -- the two calls a feature outside this
file legitimately makes -- do not each read ``service._provider`` and build the
instance by hand.
"""

from __future__ import annotations

from app.modules.workspace.domain.sandbox import (
    Sandbox,
    SandboxHandle,
    capabilities_for,
)
from app.modules.workspace.providers.base import (
    ProviderInstance,
    provider_name_for,
)


class SandboxAddressingMixin:
    """Turning a row and an instance into something a caller can act through."""

    def reach(self, handle: SandboxHandle) -> tuple[object, ProviderInstance]:
        """The provider and the instance to address, for reaching into a sandbox.

        A seam rather than a private: `reach_port` and `deliver_secret` are the
        two provider calls a feature outside this file legitimately needs -- the
        browser relay is reached that way on every fabric -- and the alternative
        was each of them reading `service._provider` and building the instance
        by hand, which is how the provider's shape leaks into five places at
        once.
        """
        return self._provider, ProviderInstance(
            provider_id=handle.provider_id,
            name=handle.provider_id,
            running=True,
        )

    # ------------------------------------------------------------------

    def _handle(
        self,
        sandbox: Sandbox,
        instance: ProviderInstance,
        *,
        epoch: int | None = None,
        storage_generation: int | None = None,
    ) -> SandboxHandle:
        return SandboxHandle(
            sandbox_id=sandbox.id,
            kind=sandbox.kind,
            epoch=epoch if epoch is not None else sandbox.epoch,
            provider=provider_name_for(self._provider, sandbox.id),
            provider_id=instance.provider_id,
            capabilities=capabilities_for(sandbox.kind),
            storage_generation=(
                storage_generation
                if storage_generation is not None
                else sandbox.storage_generation
            ),
        )
