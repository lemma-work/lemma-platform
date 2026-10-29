"""The metadata keys code decides on, read typed and leniently."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.conversation_metadata import (
    ConversationMetadataFields,
    origin_metadata,
    source_of,
)
from app.modules.agent.domain.wait import AgentWaitWakeReason, WaitSpec

pytestmark = pytest.mark.unit


def test_the_keys_read_typed() -> None:
    run_id = uuid4()
    fields = ConversationMetadataFields.of(
        {
            "cwd": "/home/user/lemma/c/2026-10-13/notes",
            "source": "schedule",
            "workflow_run_id": str(run_id),
            "anything_else": {"kept": "out of this view"},
        }
    )
    assert fields.cwd == "/home/user/lemma/c/2026-10-13/notes"
    assert fields.source == "schedule"
    assert fields.workflow_run_id == run_id


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        "not a mapping",
        {"cwd": 5, "source": ["x"], "workflow_run_id": "not-a-uuid"},
        {"cwd": "", "source": ""},
    ],
)
def test_an_unreadable_value_reads_as_absent(metadata: object) -> None:
    """Stored by every release since the key existed: a bad value must not
    fail whatever asked."""
    assert ConversationMetadataFields.of(metadata) == ConversationMetadataFields()


def test_source_reads_off_any_metadata() -> None:
    assert source_of({"source": "manual_retry"}) == "manual_retry"
    assert source_of({"source": 3}) is None
    assert source_of(None) is None


def test_the_origin_is_written_in_its_stored_shape() -> None:
    run_id = uuid4()
    assert origin_metadata(source="workflow", workflow_run_id=run_id) == {
        "source": "workflow",
        "workflow_run_id": str(run_id),
    }
    assert origin_metadata(source="schedule", workflow_run_id=None) == {
        "source": "schedule"
    }


def test_a_wait_reads_its_wake_reason_as_the_enum() -> None:
    assert (
        WaitSpec.model_validate({"woke_because": "TARGET_FINISHED"}).woke_because
        is AgentWaitWakeReason.TARGET_FINISHED
    )
    # A reason this release does not know does not make the wait unreadable.
    assert (
        WaitSpec.model_validate({"woke_because": "SOMETHING_NEW"}).woke_because is None
    )
    # And the stored shape is unchanged.
    assert (
        WaitSpec(woke_because=AgentWaitWakeReason.TIMER).to_json()["woke_because"]
        == "TIMER"
    )
