"""Harness registry."""

from app.modules.agent.domain.errors import HarnessNotFoundError
from app.modules.agent.domain.ports import Harness
from app.modules.agent.domain.value_objects import HarnessKind


class HarnessRegistry:
    """Resolve harnesses by kind."""

    def __init__(self, harnesses: list[Harness]):
        self._harnesses = {harness.kind: harness for harness in harnesses}

    def get(self, kind: HarnessKind) -> Harness:
        harness = self._harnesses.get(kind)
        if harness is None:
            raise HarnessNotFoundError(f"No harness registered for {kind.value}")
        return harness


def build_harness_registry() -> HarnessRegistry:
    """The harnesses a worker runs agent turns on.

    Built per turn by the turn handler; the harness imports live behind this
    call, not at the top of a module every process that starts a turn loads.
    """
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.agent.infrastructure.harness.lemma.harness import (
        PydanticAIHarness,
    )
    from app.modules.agent.agent_host.api import (
        PodFileAgentHostArtifactWriter,
    )
    from app.modules.agent.agent_host.api import (
        RemoteHarness,
    )

    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
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
                # discarded.
                artifact_writer=PodFileAgentHostArtifactWriter(uow_factory),
            ),
        ]
    )
