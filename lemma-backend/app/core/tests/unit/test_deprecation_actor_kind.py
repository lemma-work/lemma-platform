"""The deprecation log says who is still calling a retired shape.

A delegated workload's kind comes from its token's ``actor_type``: an agent
and a function are retired differently, so "workload" is not enough to chase.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.api.deprecation import _actor_kind
from app.core.authorization.delegation import (
    DelegationClaims,
    WorkloadPrincipalType,
)

pytestmark = pytest.mark.unit


def _request(**state: object) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            function_run_claims=state.get("function_run_claims"),
            delegation_claims=state.get("delegation_claims"),
        )
    )


@pytest.mark.parametrize(
    ("actor_type", "kind"),
    [
        (WorkloadPrincipalType.AGENT, "agent"),
        (WorkloadPrincipalType.FUNCTION, "function"),
    ],
)
def test_a_delegated_caller_is_logged_by_its_actor_type(actor_type, kind) -> None:
    claims = DelegationClaims(
        actor_type=actor_type,
        actor_id=uuid4(),
        pod_id=uuid4(),
        session_id="s",
        invoked_by_user_id=uuid4(),
        delegation_version=1,
    )

    assert _actor_kind(_request(delegation_claims=claims)) == kind


def test_a_person_and_a_function_run() -> None:
    assert _actor_kind(_request()) == "person"
    assert _actor_kind(_request(function_run_claims=object())) == "function"
