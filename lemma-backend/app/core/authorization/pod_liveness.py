"""A deleted pod stops answering for its contents.

Deleting a pod is a soft delete: the row is flagged and the schedules disarmed,
but memberships survive, so a caller's role snapshot still authorizes them. Both
halves of the rule that closes that hole live here — the per-request check every
pod-scoped route passes through, and the stricter enumeration check — because
they are one policy and were written as one.

Kept out of ``dependencies`` so that module stays under the 600-line ceiling the
architecture ratchet enforces. Nothing here resolves a FastAPI dependency, so it
has no import back.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Request

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory


_OPERATIONS_A_DELETED_POD_STILL_ANSWERS = frozenset({"pod.delete"})


def _refuse_a_deleted_pod(request: Request, ctx: Context) -> None:
    """Stop a deleted pod answering for its contents, on every route.

    Deleting a pod is a soft delete: the row is flagged, the name is freed and
    the schedules are disarmed, but memberships survive -- so the caller's role
    snapshot still authorizes them and every pod-scoped route went on
    answering. Only the routes carrying `require_pod_membership(enumerates=
    True)` refused, which made whether a deleted pod was visible depend on
    which dependency a route happened to use: its schedule *list* 404'd while
    one schedule, its run history, its agents and its records all answered 200.
    That is `DEV-OPS-007`, and it is the trap version of a bug -- a new
    pod-scoped route inherited the hole by default.

    It lives here because this is the one place every pod-scoped route passes
    through, so a route added tomorrow gets the rule without knowing it exists.
    It costs no query: `pod_is_deleted` rides in the role snapshot, written
    where the `Pod` row was already being read.
    """
    if not ctx.pod_is_deleted:
        return
    route = request.scope.get("route")
    if getattr(route, "operation_id", None) in _OPERATIONS_A_DELETED_POD_STILL_ANSWERS:
        return
    from app.core.domain.errors import DomainError

    raise DomainError("Pod not found", code="POD_NOT_FOUND", status_code=404)


async def _assert_pod_is_live(
    uow_factory: UnitOfWorkFactory, pod_id: UUID | None
) -> None:
    """Refuse a pod that has been deleted, for every caller.

    PS-POD-050 and PS-OPS-020 say deletion stops the pod being shown, and a
    listing route that answers 200 with an empty list is still showing it --
    which is how a deleted pod went on reporting its schedule list to its org
    owner.

    Three deliberate choices, each of which was a way to get this wrong.

    It runs only where something is enumerated. Point reads -- opening one app,
    one asset, one conversation someone sent you -- do not pay for it, so a pod's
    app does not do a pod lookup per static file it serves.

    It runs *before* the membership rule and has none of its exemptions. A
    deleted pod is a fact about the pod, not about who is asking, so superusers
    and workload actors are refused too.

    It does not run in the shared pod context, and does not borrow the
    request-scoped ``UoWDep``. ``get_pod_context`` ends by committing precisely
    to hand that pooled connection back (see ``_release_after_authorization``);
    reading on the same unit of work would check it straight out again and hold
    an open transaction through the handler. A short unit of work releases
    before the handler starts. Keeping it out of the context proper is also what
    lets a retried ``pod.delete`` keep reporting success (PS-POD-050's
    idempotency clause): the pod stays addressable for the routes that act on
    it, and only enumeration stops.
    """
    if pod_id is None:
        return
    from app.core.domain.errors import DomainError
    from app.modules.pod.infrastructure.models import Pod

    async with uow_factory() as uow:
        pod = await uow.session.get(Pod, pod_id)
        is_live = pod is not None and not pod.is_deleted
    if not is_live:
        raise DomainError("Pod not found", code="POD_NOT_FOUND", status_code=404)
