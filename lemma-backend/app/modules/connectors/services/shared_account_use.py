"""The account half of a SHARED call: may this caller run as that account?

Out of ``pod_connector_resolution`` so that file stays under the size ceiling;
the resolver asks it for a call made now, and the connections read and
``can_use`` ask the same question as a bool.
"""

from __future__ import annotations

from app.core.authorization.context import Context, ResourceRef, Workload
from app.core.authorization.permissions import Permissions
from app.core.authorization.workload_authority import invoking_person_context
from app.core.domain.errors import DomainError
from app.modules.connectors.domain.errors import ConnectorAccessDeniedError
from app.modules.connectors.domain.pod_connector import (
    PodAccountEntity,
    PodAccountVisibility,
)

#: Authorization answers that are not a refusal of this connector.
NOT_A_REFUSAL = frozenset({"AUTH_REQUIRED", "NOT_FOUND"})


async def require_account_use(ctx: Context, pod_account: PodAccountEntity) -> None:
    """The account half of a SHARED call made now.

    A named agent or function is checked as the person behind it, not as the
    intersection: its grant is on the pod connector, never on an account (an
    agent is granted ``support-inbox``, and which account that means was
    decided by its owner). Asking the workload for a grant on the account too
    would bring back exactly the pinning this replaced.

    A run with no person behind it may use what its owner shared with the
    whole pod, and nothing narrower: there is nobody to check a RESTRICTED
    grant against.
    """
    ref = ResourceRef.connector_account(pod_account.pod_id, pod_account.id)
    asking = ctx
    if ctx.is_named_workload:
        asking = invoking_person_context(ctx.authorizer, ctx)
        if asking is None:
            raise _restricted_without_person()
    elif isinstance(ctx.actor, Workload):
        if pod_account.visibility is PodAccountVisibility.POD:
            return
        raise _restricted_without_person()
    try:
        await asking.require(Permissions.CONNECTOR_ACCOUNT_USE, ref)
    except DomainError as refusal:
        if refusal.code in NOT_A_REFUSAL:
            raise
        # Typed, so the deprecated route can step past it by kind; the
        # permission it names is kept for whoever renders the refusal.
        details = refusal.details if isinstance(refusal.details, dict) else {}
        raise ConnectorAccessDeniedError(
            refusal.message,
            details={**details, "reason": "account_not_shared_with_you"},
        ) from refusal


async def may_use_shared_account(ctx: Context, pod_account: PodAccountEntity) -> bool:
    """Whether a call made now by ``ctx`` could run as this shared account --
    the same question ``require_account_use`` asks, answered as a bool."""
    try:
        await require_account_use(ctx, pod_account)
    except ConnectorAccessDeniedError:
        return False
    return True


def _restricted_without_person() -> ConnectorAccessDeniedError:
    return ConnectorAccessDeniedError(
        "This shared account is restricted to some members, and this run "
        "has no member behind it.",
        details={"reason": "restricted_account_without_person"},
    )


__all__ = ["NOT_A_REFUSAL", "may_use_shared_account", "require_account_use"]
