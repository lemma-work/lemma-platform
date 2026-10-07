"""The authority of another pod, asking this one over a link.

One pod (A) can be linked to another (B): B's people grant pod A access to B,
and to what in B it may use. When A asks B over that link, the run in B that
answers is authorized as A -- not as any person, so what it can reach does not
depend on who set the link up or who happens to be asking:

* what B has marked Public, as an anonymous context may read it;
* whatever B granted ``POD:A`` -- a ``resource_permission_grants`` row with
  grantee type ``POD`` -- which grant resolution already matches by principal;
* nothing in any other pod, whatever was granted there.

Pinned to B, like ``anonymous``, and built without a database read for the same
reason: the caller already holds the organization.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.authorizer import Authorizer
from app.core.authorization.context import ActorType, Context, PrincipalRef

#: ``grantee_type`` on a grant to another pod, and the principal type it matches.
POD_PRINCIPAL_TYPE = "POD"


def pod_principal(pod_id: UUID) -> PrincipalRef:
    return PrincipalRef(POD_PRINCIPAL_TYPE, pod_id)


def build_pod_context(
    *,
    session: AsyncSession,
    pod_id: UUID,
    organization_id: UUID | None,
    asking_pod_id: UUID,
    request_id: str | None = None,
) -> Context:
    """A context acting in ``pod_id`` as ``asking_pod_id``, with only its grants."""
    principals = frozenset({pod_principal(asking_pod_id)})
    return Context(
        actor_type=ActorType.POD,
        actor_id=f"pod:{asking_pod_id}",
        authorizer=Authorizer(session),
        request_id=request_id,
        organization_id=organization_id,
        pod_id=pod_id,
        principal_refs=principals,
        grant_principal_sets=(principals,),
    )
