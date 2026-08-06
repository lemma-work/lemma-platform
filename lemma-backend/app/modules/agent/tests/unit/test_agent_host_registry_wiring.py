"""The remote harness has to be built with the parts it needs to be complete.

``PodFileAgentHostArtifactWriter`` is a fully implemented, individually tested
component that nothing ever constructed, so ``RemoteHarness`` ran with
``artifact_writer=None``. Nothing failed: an image block simply renders to no
text, so every image an agent produced was received and silently dropped —
including the ones the host has a whole publish pipeline for.

A dependency that is optional in the constructor and mandatory in practice
cannot be caught by testing either side, only by asserting the wiring.
"""

from __future__ import annotations

from app.modules.agent.domain.value_objects import HarnessKind
from app.modules.agent.events.handlers import build_harness_registry


def test_the_remote_harness_is_built_with_an_artifact_writer() -> None:
    harness = build_harness_registry().get(HarnessKind.HARNESS)

    assert harness.artifact_writer is not None, (
        "without a writer every image an agent produces is dropped in silence"
    )
    assert callable(harness.artifact_writer.materialize_event)


def test_both_harness_kinds_are_registered() -> None:
    registry = build_harness_registry()

    assert registry.get(HarnessKind.LEMMA).kind is HarnessKind.LEMMA
    assert registry.get(HarnessKind.HARNESS).kind is HarnessKind.HARNESS
