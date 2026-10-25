"""What an org row's deletion must clear in the pods that referenced it.

The foreign keys cascade the pod rows themselves -- an account's pod accounts,
an installation's pod connectors -- but not the grants on them, which carry no
foreign key. Called just before the org row is deleted, in the same unit of
work.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import ResourceType
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodAccountRepository,
    PodConnectorRepository,
)
from app.modules.connectors.infrastructure.repositories.pod_resource_grants import (
    delete_grants_on,
)


async def forget_account_in_pods(uow, account_id: UUID) -> None:
    pod_account_ids = await PodAccountRepository(uow).ids_for_account(account_id)
    await delete_grants_on(
        uow.session,
        resource_type=ResourceType.CONNECTOR_ACCOUNT,
        resource_ids=pod_account_ids,
    )


async def forget_installation_in_pods(uow, auth_config_id: UUID) -> None:
    await delete_grants_on(
        uow.session,
        resource_type=ResourceType.CONNECTOR_ACCOUNT,
        resource_ids=await PodAccountRepository(uow).ids_for_auth_config(
            auth_config_id
        ),
    )
    await delete_grants_on(
        uow.session,
        resource_type=ResourceType.CONNECTOR,
        resource_ids=await PodConnectorRepository(uow).ids_by_auth_config(
            auth_config_id
        ),
    )
