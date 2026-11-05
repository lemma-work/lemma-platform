"""`require_agent_actions` authorizes the person it is asked about.

It checks against the ambient authorization context and used to discard its
`user_id` argument, so a caller passing one user while another's context was
bound authorized the wrong subject without a sound.
"""

from __future__ import annotations

from contextlib import nullcontext as does_not_raise
from dataclasses import replace
from uuid import uuid4

import pytest

from app.core.authorization.context import OtherPod, Workload, WorkloadKind
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.modules.agent.services.conversation_access import require_agent_actions
from app.modules.test_support.authz import allow_all_context

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_a_context_for_someone_else_is_refused() -> None:
    pod_id = uuid4()
    token = set_current_context(allow_all_context(user_id=uuid4(), pod_id=pod_id))
    try:
        with pytest.raises(DomainError) as refused:
            await require_agent_actions(
                user_id=uuid4(),
                pod_id=pod_id,
                agent_id=None,
                actions=[Permissions.AGENT_EXECUTE],
            )
    finally:
        reset_current_context(token)

    assert refused.value.code == "CONTEXT_USER_MISMATCH"
    assert refused.value.status_code == 403


@pytest.mark.asyncio
async def test_the_person_s_own_context_is_checked_as_usual() -> None:
    user_id, pod_id = uuid4(), uuid4()
    token = set_current_context(allow_all_context(user_id=user_id, pod_id=pod_id))
    try:
        with does_not_raise():
            await require_agent_actions(
                user_id=user_id,
                pod_id=pod_id,
                agent_id=None,
                actions=[Permissions.AGENT_EXECUTE],
            )
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_a_workload_acting_for_the_person_is_accepted() -> None:
    owner, pod_id, agent_id = uuid4(), uuid4(), uuid4()
    delegated = replace(
        allow_all_context(user_id=None, pod_id=pod_id),
        actor=Workload(
            kind=WorkloadKind.AGENT, workload_id=agent_id, for_user_id=owner
        ),
        actor_id=f"agent:{agent_id}",
        user_id=None,
    )
    token = set_current_context(delegated)
    try:
        with does_not_raise():
            await require_agent_actions(
                user_id=owner,
                pod_id=pod_id,
                agent_id=agent_id,
                actions=[Permissions.AGENT_READ],
            )
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_another_pod_asking_over_a_link_is_held_to_its_grants_alone() -> None:
    """A pod's context is nobody's, so it has no person to match: the thread it
    opens belongs to the link's steward, and the link grant is what is checked."""
    steward, pod_id, asking_pod_id = uuid4(), uuid4(), uuid4()
    asking_pod = replace(
        allow_all_context(user_id=None, pod_id=pod_id),
        actor=OtherPod(pod_id=asking_pod_id),
        actor_id=f"pod:{asking_pod_id}",
        user_id=None,
    )
    token = set_current_context(asking_pod)
    try:
        with does_not_raise():
            await require_agent_actions(
                user_id=steward,
                pod_id=pod_id,
                agent_id=None,
                actions=[Permissions.AGENT_EXECUTE],
            )
    finally:
        reset_current_context(token)
