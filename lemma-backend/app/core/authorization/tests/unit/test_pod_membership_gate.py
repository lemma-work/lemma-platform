"""Enumeration stays membership-gated even as reads widen.

Listing endpoints carry no permission dependency — the visibility projection in
``sql_actions`` decides what a caller sees. Once ORGANIZATION and PUBLIC project
read actions for non-members, that projection alone would let anyone holding one
shared link enumerate the pod. ``require_pod_membership`` is the counterweight.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.authorization.context import ActorType, Context, PrincipalRef
from app.core.authorization.dependencies import require_pod_membership
from app.core.domain.errors import DomainError

POD_ID = uuid4()


def _ctx(
    *,
    actor_type: ActorType = ActorType.USER,
    is_pod_member: bool = False,
    role_names: frozenset[str] = frozenset(),
    is_superuser: bool = False,
) -> Context:
    principal_refs = set()
    if is_pod_member:
        principal_refs.add(PrincipalRef("POD_MEMBER", uuid4()))
    return Context(
        actor_type=actor_type,
        actor_id="actor",
        user_id=uuid4(),
        pod_id=POD_ID,
        role_names=frozenset(role_names),
        principal_refs=frozenset(principal_refs),
        is_superuser=is_superuser,
        authorizer=object(),
    )


class _Session:
    """Just enough session to answer the liveness lookup."""

    def __init__(self, pod: object | None):
        self._pod = pod

    async def get(self, _model, _pk):
        return self._pod


class _UowFactory:
    """A unit-of-work factory whose UoW is a context manager, as the real one is.

    Shaped this way on purpose: the gate takes a *factory* rather than the
    request-scoped ``UoWDep`` precisely so the connection it borrows is released
    before the handler runs, and a double that is not a context manager would
    let that regress without a test noticing.
    """

    def __init__(self, *, pod_exists: bool = True, pod_deleted: bool = False):
        self.pod = (
            SimpleNamespace(id=POD_ID, is_deleted=pod_deleted) if pod_exists else None
        )
        self.exited = False

    @asynccontextmanager
    async def __call__(self):
        try:
            yield SimpleNamespace(session=_Session(self.pod))
        finally:
            self.exited = True


async def _run(ctx: Context) -> None:
    """A point read: the membership rule alone, and no database at all."""
    await require_pod_membership("browse files").dependency(ctx, _UowFactory())


async def _run_liveness(ctx: Context, uow_factory: _UowFactory | None = None) -> None:
    await require_pod_membership("browse files", enumerates=True).dependency(
        ctx, uow_factory or _UowFactory()
    )


@pytest.mark.asyncio
async def test_denies_authenticated_non_member():
    with pytest.raises(DomainError) as exc:
        await _run(_ctx())

    assert exc.value.status_code == 403
    assert exc.value.code == "POD_MEMBERSHIP_REQUIRED"


@pytest.mark.asyncio
async def test_allows_pod_member():
    await _run(_ctx(is_pod_member=True))


@pytest.mark.asyncio
async def test_allows_org_owner_without_membership_row():
    # Org owners hold authority over every pod in their org without necessarily
    # having a pod_member row; gating them would break the pods they own.
    await _run(_ctx(role_names=frozenset({"ORG_OWNER"})))


@pytest.mark.asyncio
async def test_allows_superuser():
    await _run(_ctx(is_superuser=True))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor_type",
    [
        ActorType.DELEGATED_USER_WORKLOAD,
        ActorType.AGENT,
        ActorType.FUNCTION,
        ActorType.SYSTEM,
    ],
)
async def test_workload_actors_are_not_gated(actor_type: ActorType):
    # Workloads keep the grant-first projection this change never widened, so
    # gating them here would break agents listing files they already may read.
    await _run(_ctx(actor_type=actor_type))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ctx",
    [
        _ctx(role_names=frozenset({"ORG_OWNER"})),
        _ctx(is_pod_member=True),
        _ctx(is_superuser=True),
        _ctx(actor_type=ActorType.AGENT),
        _ctx(actor_type=ActorType.FUNCTION),
    ],
    ids=["org-owner", "pod-member", "superuser", "agent", "function"],
)
async def test_denies_everyone_once_the_pod_is_deleted(ctx: Context):
    """Deletion stops the pod being shown, to its owner as much as anyone.

    An enumeration route that answers 200 with an empty list is still showing
    the pod. Every caller here passes the membership rule -- including the two
    it never gates at all -- so this holds liveness to being about the pod's
    existence rather than about who is asking. Being a separate dependency is
    what makes that possible: folded into the membership rule it would sit
    behind that rule's exemptions and never run for these callers at all.
    See PS-POD-050 and PS-OPS-020.
    """
    with pytest.raises(DomainError) as exc:
        await _run_liveness(ctx, _UowFactory(pod_deleted=True))

    assert exc.value.status_code == 404
    assert exc.value.code == "POD_NOT_FOUND"


@pytest.mark.asyncio
async def test_denies_when_the_pod_never_existed():
    with pytest.raises(DomainError) as exc:
        await _run_liveness(_ctx(is_pod_member=True), _UowFactory(pod_exists=False))

    assert exc.value.status_code == 404
    assert exc.value.code == "POD_NOT_FOUND"


@pytest.mark.asyncio
async def test_the_liveness_read_releases_its_unit_of_work():
    """The whole reason liveness takes a factory instead of ``UoWDep``.

    ``get_pod_context`` commits on the way out to hand its pooled connection
    back; borrowing that same unit of work here would check it straight out
    again and hold an open transaction through the handler -- across the app
    asset route's storage read, and across a conversation's whole SSE stream.
    """
    factory = _UowFactory()

    await _run_liveness(_ctx(is_pod_member=True), factory)

    assert factory.exited, "the liveness read must not outlive its own scope"


@pytest.mark.asyncio
async def test_a_point_read_never_opens_a_unit_of_work():
    """Only enumeration checks the pod is still there.

    `app.asset.get` serves every static file of a running app through this
    dependency. A liveness read on that path is one pod lookup per file, on the
    highest-traffic authenticated route there is.
    """
    factory = _UowFactory()

    await require_pod_membership("read an app").dependency(
        _ctx(is_pod_member=True), factory
    )

    assert not factory.exited, "a point read must not touch the database"
