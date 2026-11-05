"""Which refusals become a card offering a named agent a connector.

Only one: a named agent missing ``connector.use`` on a pod connector. The
pod's assistant, another permission, the person's own ceiling -- none of those
is a grant to give, and each is reported as it was. What approving does with
an offer is pinned against a real database in the e2e suite.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.agent.infrastructure.tools.approval.grant_outcome import (
    confer_offered_grant,
)
from app.modules.agent.infrastructure.tools.connectors.grant_offer import (
    connector_grant_offer,
    grant_card_title,
)
from app.modules.agent.domain.context import ToolContext

pytestmark = pytest.mark.unit


def _deps(**overrides) -> ToolContext:
    fields = {
        "user_id": uuid4(),
        "pod_id": uuid4(),
        "conversation_id": uuid4(),
        "agent_name": "triage",
        "workload_id": uuid4(),
    }
    fields.update(overrides)
    return ToolContext(**fields)


def _refusal(code="MISSING_WORKLOAD_RESOURCE_GRANT", **details) -> DomainError:
    return DomainError(
        "Missing permission connector.use on connector 'gmail'",
        code=code,
        status_code=403,
        details={
            "permission_ids": ["connector.use"],
            "resource_type": "connector",
            "resource_name": "gmail",
            **details,
        },
    )


def test_the_card_asks_in_the_person_s_terms():
    assert grant_card_title("triage", "gmail", per_member=True) == (
        "Let triage use gmail (your own account)?"
    )
    assert grant_card_title("triage", "support", per_member=False) == (
        "Let triage use support (the account shared with the pod)?"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("deps_overrides", "refusal"),
    [
        # The pod's assistant is never asked: it reaches the person's apps.
        ({"is_pod_default_agent": True}, _refusal()),
        # The person's own ceiling: no grant to the agent would help.
        ({}, _refusal(code="DELEGATION_EXCEEDS_INVOKER")),
        # Another permission on the connector's account.
        ({}, _refusal(permission_ids=["connector_account.use"])),
        # Not about a connector at all.
        ({}, _refusal(resource_type="datastore_table")),
    ],
)
async def test_only_a_named_agent_missing_the_connector_is_offered_it(
    deps_overrides, refusal
):
    offered = await connector_grant_offer(
        _deps(**deps_overrides),
        refusal,
        connector="gmail",
        tool_name="run_connector_operation",
        args={"connector": "gmail", "operation": "send_email"},
    )

    assert offered is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offer",
    [
        {},
        {"kind": "folder.read"},
        # An offer written in one pod is not one to apply in another.
        {"kind": "connector.use", "pod_id": str(uuid4()), "connector": "gmail"},
    ],
)
async def test_an_offer_that_is_not_this_agent_s_grants_nothing(offer):
    def no_database():  # pragma: no cover - reaching it is the failure
        raise AssertionError("nothing may be written for an offer that is not one")

    with pytest.raises(DomainError) as refused:
        await confer_offered_grant(no_database, deps=_deps(), offer=offer)  # type: ignore[arg-type]

    assert refused.value.code == "GRANT_OFFER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_the_pod_assistant_is_never_given_a_grant_by_a_card():
    deps = _deps(is_pod_default_agent=True)

    with pytest.raises(DomainError):
        await confer_offered_grant(
            lambda: None,  # type: ignore[arg-type,return-value]
            deps=deps,
            offer={"kind": "connector.use", "pod_id": str(deps.pod_id)},
        )
