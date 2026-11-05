"""The harnesses a worker runs agent turns on.

Moved out of ``events/handlers.py`` with the streaq task that used it: the turn
handler (``events/turn_work.py``) builds one per turn, and is imported by every
process that starts a turn -- so the harness imports live here, behind a call,
rather than at the top of a module the API loads.
"""

from __future__ import annotations

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.modules.agent.infrastructure.harnesses import (
    HarnessRegistry,
    PydanticAIHarness,
    RemoteHarness,
)
from app.modules.agent.infrastructure.harnesses.agent_host.artifacts import (
    PodFileAgentHostArtifactWriter,
)


def worker_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def build_harness_registry() -> HarnessRegistry:
    uow_factory = worker_uow_factory()
    return HarnessRegistry(
        [
            PydanticAIHarness(),
            # One harness for every Agent Host tool; which one runs is decided
            # by the profile's harness_id, not by the registry.
            RemoteHarness(
                uow_factory,
                # Without a writer the harness drops every image an agent
                # produces: a content block with no text renders to nothing,
                # so Codex's `$imagegen` output reached this process and was
                # discarded. The host already publishes those blocks and the
                # writer already knows how to store them.
                artifact_writer=PodFileAgentHostArtifactWriter(uow_factory),
            ),
        ]
    )


__all__ = ["build_harness_registry", "worker_uow_factory"]
