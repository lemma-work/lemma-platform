"""Visibility above POD is evaluated on the resource, not the viewer's role.

``PUBLIC`` used to be unreachable: its branch in ``Authorizer.authorize`` sat
*after* the pod-permission gate, so a non-member — who holds no pod permissions —
was denied before it ever ran. These tests pin the ordering fix and the way it
could go wrong in the other direction: handing outsiders anything beyond a read.
"""

from __future__ import annotations

from uuid import uuid4

from app.core.authorization.context import (
    ActorType,
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.service import Authorizer

POD_ID = uuid4()
ORG_ID = uuid4()
USER_ID = uuid4()


def _ctx(
    *,
    actor_type: ActorType = ActorType.USER,
    pod_id=POD_ID,
    permission_ids: frozenset[str] = frozenset(),
) -> Context:
    """A viewer holding no pod permissions unless told otherwise."""
    return Context(
        actor_type=actor_type,
        actor_id=str(USER_ID),
        user_id=USER_ID,
        organization_id=ORG_ID,
        pod_id=pod_id,
        permission_ids=frozenset(permission_ids),
        principal_refs=frozenset(),
        authorizer=object(),
    )


def _resource(visibility: ResourceVisibility, *, pod_id=POD_ID) -> ResourceRef:
    return ResourceRef(
        resource_type=ResourceType.DOCUMENT,
        resource_id=uuid4(),
        pod_id=pod_id,
        organization_id=ORG_ID,
        owner_user_id=uuid4(),
        visibility=visibility,
    )


def _decide(ctx: Context, permission_id: str, resource: ResourceRef):
    return Authorizer(session=None)._visibility_read_decision(ctx, permission_id, resource)


class TestPublic:
    def test_non_member_may_read(self):
        decision = _decide(_ctx(), "folder.read", _resource(ResourceVisibility.PUBLIC))

        assert decision is not None and decision.allowed
        assert decision.reason_code =="PUBLIC_RESOURCE"

    def test_non_member_may_not_write(self):
        # The whole point of the narrow rule: readable never implies editable.
        assert _decide(_ctx(), "folder.write", _resource(ResourceVisibility.PUBLIC)) is None

    def test_a_total_stranger_may_still_read(self):
        # PUBLIC means every Lemma account: no org or pod relationship required.
        decision = _decide(_ctx(), "folder.read", _resource(ResourceVisibility.PUBLIC))

        assert decision is not None and decision.allowed


class TestFallthrough:
    def test_pod_visibility_is_untouched(self):
        assert _decide(_ctx(), "folder.read", _resource(ResourceVisibility.POD)) is None

    def test_personal_visibility_is_untouched(self):
        assert _decide(_ctx(), "folder.read", _resource(ResourceVisibility.PERSONAL)) is None

    def test_restricted_visibility_is_untouched(self):
        assert _decide(_ctx(), "folder.read", _resource(ResourceVisibility.RESTRICTED)) is None

    def test_missing_visibility_defaults_to_pod(self):
        resource = ResourceRef(
            resource_type=ResourceType.DOCUMENT,
            resource_id=uuid4(),
            pod_id=POD_ID,
            visibility=None,
        )

        assert _decide(_ctx(), "folder.read", resource) is None


class TestActorScope:
    def test_delegated_workload_gains_nothing(self):
        # A workload's reach must come from its own grants. If a visibility flip
        # widened it, sharing a doc would silently widen every agent in the pod.
        assert (
            _decide(
                _ctx(actor_type=ActorType.DELEGATED_USER_WORKLOAD),
                "folder.read",
                _resource(ResourceVisibility.PUBLIC),
            )
            is None
        )

    def test_agent_actor_gains_nothing(self):
        assert (
            _decide(
                _ctx(actor_type=ActorType.AGENT),
                "folder.read",
                _resource(ResourceVisibility.PUBLIC),
            )
            is None
        )

    def test_resource_in_another_pod_is_denied(self):
        # The ctx was built for one pod; a resource from a different one must not
        # ride through on it.
        assert (
            _decide(_ctx(), "folder.read", _resource(ResourceVisibility.PUBLIC, pod_id=uuid4()))
            is None
        )
