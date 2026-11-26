"""Connecting from a Lemma adds the app to that Lemma.

A person who connects GitHub from a Lemma's page meant "so this Lemma can use
it". Before this, the account landed in the organization and the Lemma learned
of it only on the first call (``ensure_for_installation``), so the page went on
offering "Connect GitHub" to someone who just had. Two ways in:

- **a connect made from a pod** -- an OAuth connect request carrying the pod
  (``attributes.pod_id``), or the credential form's ``pod_id`` -- names the
  pod, checked when it is made: the person must be a member. When it
  completes, as that person: the pod's PER_MEMBER connector on the
  installation is reused or declared, and the new account becomes their
  default in the pod. A failure in either step is logged and never fails the
  connect -- the account is connected either way, and the first call declares
  the connector as it always did;
- **"use it here"** (``POST /pods/{pod}/connector-accounts``, an account they
  already have in the organization) ensures the same connector, and makes the
  account their default in the pod when they have none.

Neither widens anything: the account stays PERSONAL, the person's own, and
declaring a connector grants nobody else anything.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.context import Context
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    PodAccountResponse,
)
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.domain.connect_request import ConnectRequestEntity
from app.modules.connectors.domain.errors import ConnectorDomainError
from app.modules.connectors.domain.pod_connector import (
    PodAccountVisibility,
    PodConnectorSource,
)
from app.modules.connectors.services.connect_request_lifecycle import requested_pod
from app.modules.connectors.services.pod_connector_composition import (
    build_pod_connector_resolver,
)

logger = get_logger(__name__)


class ConnectPodNotAvailableError(ConnectorDomainError):
    """The pod a connect request names is not one the person is a member of."""

    def __init__(self, pod_id: UUID):
        super().__init__(
            message=(
                "You can connect an account for a Lemma you are a member of; "
                "this one is not yours, or is not in this organization."
            ),
            code="CONNECT_REQUEST_POD_NOT_MEMBER",
            status_code=403,
            details={"pod_id": str(pod_id)},
        )


async def require_member_of(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, user_id: UUID, organization_id: UUID
) -> None:
    """The person may connect *for* ``pod_id``: a member, in this organization."""
    from app.modules.pod.contracts.members import (
        pod_members_among,
        pod_organization_id,
    )

    if await pod_organization_id(uow, pod_id) != organization_id or user_id not in (
        await pod_members_among(uow, pod_id, [user_id])
    ):
        raise ConnectPodNotAvailableError(pod_id)


async def add_to_requested_pod(
    uow: SqlAlchemyUnitOfWork,
    connect_request: ConnectRequestEntity,
    account: AccountEntity,
) -> None:
    """After a connect from a pod: the app in the pod, the account their default.

    In a savepoint of its own, before the connect's commit: a refusal rolls
    back only this, never the account the person just connected.
    """
    pod_id = requested_pod(connect_request)
    if pod_id is not None:
        await add_connected_to_pod(uow, pod_id, account)


async def add_connected_to_pod(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, account: AccountEntity
) -> None:
    """``add_to_requested_pod`` for a connect that names its pod directly."""
    try:
        # The reads first: they begin the transaction the savepoint below is
        # taken in, which a session the connect just committed has none of.
        resolver = build_pod_connector_resolver(uow)
        if not await resolver.picker.is_pod_member(pod_id, account.user_id):
            # They left the pod between starting the connect and finishing it.
            logger.info("connectors.connect_into_pod.no_longer_member.diagnostic")
            return
        auth_config = await resolver.auth_configs.get(account.auth_config_id)
        if auth_config is None:
            return
        ctx = await resolver.person_context(account.user_id, pod_id)
        async with uow.session.begin_nested():
            await resolver.ensure_for_installation(
                ctx,
                pod_id=pod_id,
                auth_config=auth_config,
                source=PodConnectorSource.AUTO,
            )
            await resolver.picker.add_own(pod_id, account, as_default=True)
    except DomainError, SQLAlchemyError:
        logger.warning(
            "connectors.connect_into_pod.not_added.degraded",
            connector_id=account.connector_id,
            exc_info=True,
        )


async def use_added_account_here(
    uow: SqlAlchemyUnitOfWork,
    ctx: Context,
    *,
    pod_id: UUID,
    added: PodAccountResponse,
) -> PodAccountResponse:
    """After the owner put one of their accounts in the pod for their own use.

    The pod's PER_MEMBER connector on its installation is ensured -- the
    person has an account on it, so declaring is theirs to do -- and the
    account becomes their default here when they have none yet. An account
    added already shared is left as it was added: that is sharing, not using.
    """
    resolver = build_pod_connector_resolver(uow)
    auth_config = await resolver.auth_configs.get(added.auth_config_id)
    if auth_config is None or added.visibility is not PodAccountVisibility.PERSONAL:
        # Added to share it: what it serves is the SHARED connector its owner
        # points at it, and a per-member one beside it would change which the
        # deprecated installation route picks.
        return added
    try:
        # A disabled installation declares nothing; the account is still
        # theirs to have here, as it was before connecting meant declaring.
        async with uow.session.begin_nested():
            await resolver.ensure_for_installation(
                ctx,
                pod_id=pod_id,
                auth_config=auth_config,
                source=PodConnectorSource.AUTO,
            )
    except DomainError, SQLAlchemyError:
        logger.warning(
            "connectors.connect_into_pod.connector_not_ensured.degraded",
            connector_id=auth_config.connector_id,
            exc_info=True,
        )
    if added.is_member_default:
        return added
    current = await resolver.pod_accounts.get_member_default(
        pod_id=pod_id,
        owner_user_id=added.owner_user_id,
        auth_config_id=added.auth_config_id,
    )
    if current is not None:
        return added
    pod_account = await resolver.pod_accounts.get(added.id)
    if pod_account is None:
        return added
    await resolver.pod_accounts.make_member_default(pod_account)
    return added.model_copy(update={"is_member_default": True})


__all__ = [
    "ConnectPodNotAvailableError",
    "add_connected_to_pod",
    "add_to_requested_pod",
    "require_member_of",
    "use_added_account_here",
]
