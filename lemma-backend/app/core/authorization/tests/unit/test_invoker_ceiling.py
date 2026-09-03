"""The person's half of the delegated intersection, in isolation.

``workload_authority`` refuses anything the invoking person could not do
themselves (PS-ACCESS-020), and it decides who that person *is* here. Two things
have to be exactly right and neither is visible from an HTTP test:

* a run with nobody behind it has no ceiling at all, or every scheduled and
  event-driven run would be refused everything;
* the mirrored context must carry the person's authority and none of the
  workload's, or the ceiling checks the workload against itself and passes
  whatever it is handed.
"""

from __future__ import annotations

from uuid import uuid4

from app.core.authorization.context import (
    ActorType,
    Context,
    PrincipalRef,
)
from app.core.authorization.workload_authority import _invoking_user_context

POD_ID = uuid4()
ORG_ID = uuid4()
USER_ID = uuid4()

INVOKER_REFS = frozenset({PrincipalRef("POD_MEMBER", uuid4())})
WORKLOAD_REFS = frozenset({PrincipalRef("AGENT", uuid4())})


def _delegated_ctx(*, user_id=USER_ID) -> Context:
    """A named workload's context, shaped as ``build_delegated_workload_context``
    leaves it: merged principals and role names, the person's half kept apart."""
    return Context(
        actor_type=ActorType.DELEGATED_USER_WORKLOAD,
        actor_id=f"agent:{uuid4()}",
        user_id=user_id,
        organization_id=ORG_ID,
        pod_id=POD_ID,
        role_names=frozenset({"POD_VIEWER", "AGENT_ROLE"}),
        permission_ids=frozenset({"datastore.record.read"}),
        principal_refs=INVOKER_REFS | WORKLOAD_REFS,
        grant_principal_sets=(INVOKER_REFS, WORKLOAD_REFS),
        workload_principal_refs=WORKLOAD_REFS,
        invoker_principal_refs=INVOKER_REFS,
        invoker_role_names=frozenset({"POD_VIEWER"}),
        delegated_by_user_id=user_id,
        authorizer=object(),
    )


def test_a_run_with_no_invoker_has_no_ceiling():
    # The documented headless answer: the workload's grants alone. Returning a
    # context here would deny every schedule and webhook run instead.
    ctx = _delegated_ctx(user_id=None)

    assert _invoking_user_context(object(), ctx) is None


def test_the_ceiling_is_the_person_and_not_the_workload():
    invoker = _invoking_user_context(object(), _delegated_ctx())

    assert invoker is not None
    assert invoker.principal_refs == INVOKER_REFS
    assert invoker.grant_principal_sets == (INVOKER_REFS,)
    # The workload's role would be authority the person does not have; the
    # merged ``role_names`` on the delegated context carries it and this must
    # not.
    assert invoker.role_names == frozenset({"POD_VIEWER"})


def test_the_ceiling_cannot_re_enter_the_delegated_path():
    # Authorizing the mirror runs the ordinary USER evaluation. If it looked
    # like a workload it would recurse into the very check that built it.
    invoker = _invoking_user_context(object(), _delegated_ctx())

    assert invoker is not None
    assert invoker.actor_type == ActorType.USER
    assert invoker.workload_principal_refs == frozenset()
    assert invoker.delegated_by_user_id is None
