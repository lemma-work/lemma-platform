"""What hydration fills in, for every resource type there is.

Hydration exists to answer one question the caller usually cannot: which pod
does this resource belong to. The answer gates the cross-pod clamp in
`authorize`, which is what confines a pod's default agent -- the most common
actor in the product -- to the pod it was invoked in:

    if clamp_to_pod and hydrated.pod_id is not None and hydrated.pod_id != ctx.pod_id:
        deny

That `is not None` is load-bearing in the wrong direction. A resource type
hydration does not know returns unchanged, `pod_id` stays `None`, and the clamp
is skipped rather than enforced. So "which types does hydration know" is a
security property, and until this file there was no test of it at all.

These are characterization tests: they record what the code does today, so the
registry that replaces the literal mapping can be shown to preserve it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.authorization.context import (
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.service import Authorizer

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _RecordingSession:
    """A session that answers every query with one row and counts the asking."""

    def __init__(self, row=None):
        self.queries = 0
        self._row = row

    async def execute(self, _stmt):
        self.queries += 1
        return _Result(self._row)


POD_ID = uuid4()
OWNER_ID = uuid4()
ORG_ID = uuid4()

#: What each hydrator's `select` returns, per type. Four different shapes, which
#: is itself why the mapping is easy to get wrong.
ROWS = {
    #: `select(pod_col, owner_col, visibility_col)`
    "generic": (POD_ID, OWNER_ID, "POD"),
    #: CONVERSATION has no visibility column, so the statement is two-wide.
    "no_visibility": (POD_ID, OWNER_ID),
    #: FOLDER/DOCUMENT also fetch the path, for the folder-grant cascade.
    "datastore_file": (POD_ID, OWNER_ID, "POD", "/some/path"),
    #: The connector tables are keyed by organization, not pod.
    "org_scoped": (ORG_ID, OWNER_ID),
}

#: Types whose hydrator resolves a **pod** — the ones the cross-pod clamp works for.
LEARNS_POD = {
    ResourceType.AGENT: "generic",
    ResourceType.FUNCTION: "generic",
    ResourceType.DATASTORE_TABLE: "generic",
    ResourceType.APP: "generic",
    ResourceType.WORKFLOW: "generic",
    ResourceType.SCHEDULE: "generic",
    ResourceType.CONVERSATION: "no_visibility",
    ResourceType.FOLDER: "datastore_file",
    ResourceType.DOCUMENT: "datastore_file",
}

#: Types whose hydrator reads a row but resolves only an **organization**. The
#: connector tables have no pod column, so `pod_id` is passed through untouched
#: and the cross-pod clamp has nothing to compare. Deliberate: a connector is an
#: org-wide capability and the real boundary is the connected *account*,
#: enforced in `account_resolution_service`.
LEARNS_ORG_ONLY = {
    ResourceType.CONNECTOR_ACCOUNT: "org_scoped",
    ResourceType.CONNECTOR_AUTH_CONFIG: "org_scoped",
}

#: A pod's own id *is* its pod, so this one needs no table. Every caller passes
#: `pod_id` today, which is why the clamp worked; deriving it here is what stops
#: the clamp depending on that continuing to be true.
LEARNS_POD_WITHOUT_A_QUERY = {ResourceType.POD}

#: Types hydration does not look up at all.
#:
#: `CONNECTOR` is deliberate — its hydrator returns early without a pod, and
#: says why in prose. `ORGANIZATION` and `ROLE` have no pod: a pod-default agent
#: is refused an org-scoped action earlier, by `_is_pod_scoped_permission`.
#: `POD_MEMBER` and `DATASTORE_RECORD` are pod-scoped but nothing in production
#: builds a `ResourceRef` for either — they exist as permission ids and grant
#: targets only. They are named in `_NO_REFS_CONSTRUCTED` so that if something
#: ever does build one, the clamp denies instead of waving it through.
NO_LOOKUP = {
    ResourceType.CONNECTOR,
    ResourceType.ORGANIZATION,
    ResourceType.ROLE,
    ResourceType.POD_MEMBER,
    ResourceType.DATASTORE_RECORD,
}


def test_every_resource_type_is_accounted_for():
    """A new `ResourceType` lands in neither set, and this is where it shows up.

    Without it, adding a type silently opts it out of the cross-pod clamp.
    """
    covered = (
        set(LEARNS_POD) | set(LEARNS_ORG_ONLY) | NO_LOOKUP | LEARNS_POD_WITHOUT_A_QUERY
    )
    assert covered == set(ResourceType), (
        f"a ResourceType is in no set: {set(ResourceType) - covered}"
    )
    assert len(LEARNS_POD) + len(LEARNS_ORG_ONLY) + len(NO_LOOKUP) + len(
        LEARNS_POD_WITHOUT_A_QUERY
    ) == len(set(ResourceType)), "a ResourceType is in more than one set"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource_type", sorted(LEARNS_POD, key=lambda t: t.name), ids=lambda t: t.name
)
async def test_a_pod_scoped_type_learns_its_pod_from_the_row(resource_type):
    session = _RecordingSession(ROWS[LEARNS_POD[resource_type]])
    service = Authorizer(session)

    hydrated = await service._hydrate_resource(
        ResourceRef(resource_type=resource_type, resource_id=uuid4())
    )

    assert session.queries == 1, "hydration did not ask the database"
    assert hydrated.pod_id == POD_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource_type",
    sorted(LEARNS_ORG_ONLY, key=lambda t: t.name),
    ids=lambda t: t.name,
)
async def test_an_org_scoped_type_learns_an_org_and_no_pod(resource_type):
    session = _RecordingSession(ROWS[LEARNS_ORG_ONLY[resource_type]])
    service = Authorizer(session)

    hydrated = await service._hydrate_resource(
        ResourceRef(resource_type=resource_type, resource_id=uuid4())
    )

    assert session.queries == 1
    assert hydrated.organization_id == ORG_ID
    assert hydrated.pod_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource_type", sorted(NO_LOOKUP, key=lambda t: t.name), ids=lambda t: t.name
)
async def test_a_type_with_no_lookup_keeps_a_null_pod_and_skips_the_clamp(
    resource_type,
):
    """The behaviour this file exists to record.

    No query, no `pod_id`, and therefore no cross-pod check on the caller's
    side, because that check is guarded by `hydrated.pod_id is not None`.
    """
    session = _RecordingSession(ROWS["generic"])
    service = Authorizer(session)

    hydrated = await service._hydrate_resource(
        ResourceRef(resource_type=resource_type, resource_id=uuid4())
    )

    assert session.queries == 0
    assert hydrated.pod_id is None


@pytest.mark.asyncio
async def test_a_ref_that_already_knows_its_visibility_is_not_re_read():
    """The caller has done the work; hydration is a lookup, not a refresh."""
    session = _RecordingSession(ROWS["generic"])
    service = Authorizer(session)
    ref = ResourceRef(
        resource_type=ResourceType.AGENT,
        resource_id=uuid4(),
        visibility=ResourceVisibility.POD,
    )

    assert await service._hydrate_resource(ref) is ref
    assert session.queries == 0


@pytest.mark.asyncio
async def test_a_ref_with_no_id_has_nothing_to_look_up():
    session = _RecordingSession(ROWS["generic"])
    service = Authorizer(session)
    ref = ResourceRef(resource_type=ResourceType.AGENT, pod_id=uuid4())

    assert await service._hydrate_resource(ref) is ref
    assert session.queries == 0


@pytest.mark.asyncio
async def test_a_row_that_is_gone_leaves_the_ref_alone():
    """A deleted resource hydrates to nothing, so the clamp is skipped for it too."""
    session = _RecordingSession(None)
    service = Authorizer(session)

    hydrated = await service._hydrate_resource(
        ResourceRef(resource_type=ResourceType.AGENT, resource_id=uuid4())
    )

    assert session.queries == 1
    assert hydrated.pod_id is None


@pytest.mark.asyncio
async def test_a_pod_ref_learns_its_pod_from_its_own_id():
    """The caller does not have to remember to pass `pod_id`."""
    session = _RecordingSession(ROWS["generic"])
    service = Authorizer(session)
    pod_id = uuid4()

    hydrated = await service._hydrate_resource(
        ResourceRef(resource_type=ResourceType.POD, resource_id=pod_id)
    )

    assert session.queries == 0
    assert hydrated.pod_id == pod_id


def test_the_registry_refuses_a_resource_type_nobody_classified():
    """The check that runs at import, exercised.

    Adding a member to `ResourceType` used to be enough to opt it out of the
    cross-pod clamp, silently. Now it fails loudly, naming the type.
    """
    from app.core.authorization import service as authorization_service

    original = authorization_service._NOT_POD_SCOPED
    try:
        authorization_service._NOT_POD_SCOPED = frozenset()
        with pytest.raises(RuntimeError, match="ORGANIZATION|ROLE"):
            authorization_service._assert_every_resource_type_is_classified()
    finally:
        authorization_service._NOT_POD_SCOPED = original

    # And passes as shipped.
    authorization_service._assert_every_resource_type_is_classified()


@pytest.mark.parametrize(
    ("resource_type", "expected", "why"),
    [
        (ResourceType.AGENT, True, "pod-scoped and its pod was not established"),
        (ResourceType.ORGANIZATION, False, "has no pod to be outside of"),
        (ResourceType.ROLE, False, "has no pod to be outside of"),
        (
            ResourceType.POD_MEMBER,
            True,
            "pod-scoped; nothing builds a ref today, and if it does it is refused",
        ),
    ],
    ids=lambda v: str(v)[:40],
)
def test_which_resources_a_pod_scoped_check_refuses_for_want_of_a_pod(
    resource_type, expected, why
):
    from app.core.authorization.service import _pod_is_unknowable

    ref = ResourceRef(resource_type=resource_type, resource_id=uuid4())
    assert _pod_is_unknowable(ref) is expected, why


def test_a_resource_that_knows_its_pod_is_never_refused_for_want_of_one():
    from app.core.authorization.service import _pod_is_unknowable

    ref = ResourceRef(
        resource_type=ResourceType.AGENT, resource_id=uuid4(), pod_id=uuid4()
    )
    assert _pod_is_unknowable(ref) is False
