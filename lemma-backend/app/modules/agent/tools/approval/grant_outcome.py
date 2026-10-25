"""What approving a grant card does: the agent is given the grant, standing.

Most approvals lend: the one approved call runs with what it was refused, and
the agent's next call is judged on its own grants again. A named agent refused
a pod connector is different. Whether it may use the person's Gmail is a
decision about the agent, not about one message, so the card asks it as one --
"Let triage use gmail?" -- and a yes adds ``connector.use`` on that pod
connector to the agent, for every later call too.

The offer is the server's (``connectors/grant_offer``), written when the call
was refused and carried on the card when it was shown; nothing the model wrote
names what is granted. And the grant is conferred as the approver: they give
only what they could use themselves (``conferral.assert_can_confer_grants``),
so a person who cannot use the connector cannot hand it to an agent either.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from app.core.authorization.conferral import assert_can_confer_grants
from app.core.authorization.context import ResourceType
from app.core.authorization.grant_generation import AGENT_GRANTEE
from app.core.authorization.grants import (
    NormalizedResourceGrant,
    replace_resource_grantee_grant,
)
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.tools.authority import approver_context
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.connectors.contracts.pod_connectors import get_pod_connector

logger = get_logger(__name__)

#: The one kind of standing grant a card may offer today. Spelled as the
#: permission it grants, which ``connectors/grant_offer`` writes as the kind.
CONNECTOR_USE_OFFER = Permissions.CONNECTOR_USE


def _refused(message: str) -> DomainError:
    return DomainError(message, code="GRANT_OFFER_UNAVAILABLE", status_code=409)


async def confer_offered_grant(
    uow_factory: UnitOfWorkFactory,
    *,
    deps: BaseAgentContext,
    offer: Mapping,
) -> None:
    """Give the agent the grant its card offered, as the person who approved.

    Raises ``DomainError`` -- and grants nothing -- when the approver may not
    confer it, the connector is gone, or the offer is not one for this agent
    in this pod.
    """
    agent_id = deps.workload_id
    if (
        offer.get("kind") != CONNECTOR_USE_OFFER
        or deps.is_pod_default_agent
        or agent_id is None
        or deps.pod_id is None
        or offer.get("pod_id") != str(deps.pod_id)
    ):
        raise _refused("This approval offers no grant Lemma can give this agent.")
    slug = str(offer.get("connector") or "")
    async with uow_factory() as uow:
        connector = await get_pod_connector(uow, pod_id=deps.pod_id, slug=slug)
        if connector is None or str(connector.id) != offer.get("pod_connector_id"):
            raise _refused(f"The pod no longer has the connector {slug!r}.")
        approver = await approver_context(uow, deps)
        await assert_can_confer_grants(
            approver,
            [
                NormalizedResourceGrant(
                    resource_type=ResourceType.CONNECTOR,
                    resource_id=connector.id,
                    permission_ids=[Permissions.CONNECTOR_USE],
                )
            ],
            pod_id=deps.pod_id,
            action="let an agent use a connector you could not use yourself",
        )
        await replace_resource_grantee_grant(
            uow.session,
            pod_id=deps.pod_id,
            resource_type=ResourceType.CONNECTOR,
            resource_id=connector.id,
            grantee_type=AGENT_GRANTEE,
            grantee_id=UUID(str(agent_id)),
            permission_ids=[Permissions.CONNECTOR_USE],
            created_by_user_id=deps.user_id,
        )
        await uow.commit()
    logger.info(
        "agent.approval.grant_conferred",
        conversation_id=str(deps.conversation_id),
        agent_id=str(agent_id),
        user_id=str(deps.user_id),
        connector=slug,
    )
