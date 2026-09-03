"""The assistant's token has to still read as the assistant.

`_is_default_pod_agent_claims` decides whether a delegated token acts with its
invoking user's permissions or as a named workload limited to its own explicit
resource grants. The pod's own assistant is the first; every named agent is the
second. The assistant carries no resource grants at all, so getting this wrong
in that direction is not a narrower assistant -- it is one that can do nothing.

The three id shapes below are three eras, not three cases. A signed token
outlives the deploy that stopped issuing its shape, so the retired sentinel has
to keep working alongside the `agents` row id that replaced it.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
    DelegationClaims,
    WorkloadPrincipalType,
)
from app.core.authorization.dependencies import _is_default_pod_agent_claims


def _claims(
    *,
    actor_id: UUID,
    pod_id: UUID,
    actor_name: str | None = DEFAULT_POD_AGENT_NAME,
) -> DelegationClaims:
    return DelegationClaims(
        actor_type=WorkloadPrincipalType.AGENT,
        actor_id=actor_id,
        actor_name=actor_name,
        pod_id=pod_id,
        session_id="session",
        scope=[],
        invoked_by_user_id=uuid4(),
        delegation_version=1,
    )


def test_the_assistants_row_id_reads_as_the_assistant() -> None:
    """The shape production actually mints: `agents.id`, which is `pod_id`."""
    pod_id = uuid4()

    assert _is_default_pod_agent_claims(_claims(actor_id=pod_id, pod_id=pod_id))


def test_the_retired_sentinel_still_reads_as_the_assistant() -> None:
    """Signed tokens outlive the deploy that stopped issuing this id."""
    assert _is_default_pod_agent_claims(
        _claims(actor_id=DEFAULT_POD_AGENT_ID, pod_id=uuid4())
    )


def test_a_named_agent_does_not_read_as_the_assistant() -> None:
    """The whole point of the check: a named agent gets no ambient authority."""
    assert not _is_default_pod_agent_claims(
        _claims(actor_id=uuid4(), pod_id=uuid4(), actor_name="batman")
    )


def test_the_assistants_id_in_the_wrong_pod_does_not_match() -> None:
    """`pod_id` is what makes the id arm mean anything; without it the id is
    just a uuid that happens to name some other pod's assistant."""
    assert not _is_default_pod_agent_claims(_claims(actor_id=uuid4(), pod_id=uuid4()))


def test_a_matching_id_under_another_name_does_not_match() -> None:
    """Both claims have to agree before a token acts as its user."""
    pod_id = uuid4()

    assert not _is_default_pod_agent_claims(
        _claims(actor_id=pod_id, pod_id=pod_id, actor_name="batman")
    )


def test_absent_claims_are_not_the_assistant() -> None:
    assert not _is_default_pod_agent_claims(None)
