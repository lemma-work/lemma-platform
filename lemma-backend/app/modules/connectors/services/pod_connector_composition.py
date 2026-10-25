"""Building the pod connector resolver and service from a unit of work.

Here rather than in ``api/dependencies.py`` so the module's contracts can build
them without reaching into its API layer; the FastAPI dependencies call these
too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authorization.context import Context
from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.infrastructure.adapters.organization_access import (
    SqlAlchemyOrganizationAccessAdapter,
)
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.infrastructure.repositories.auth_config_repository import (
    AuthConfigRepository,
)
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodAccountRepository,
    PodConnectorRepository,
)
from app.modules.connectors.services.pod_connector_resolution import (
    PodConnectorResolver,
)
from app.modules.connectors.services.pod_connector_service import (
    PodConnectorService,
)

if TYPE_CHECKING:
    from app.modules.connectors.services.installation_reach import (
        InstallationReach,
    )


def build_pod_connector_resolver(uow: SqlAlchemyUnitOfWork) -> PodConnectorResolver:
    async def is_pod_member(pod_id: UUID, user_id: UUID) -> bool:
        from app.modules.pod.contracts.members import pod_members_among

        return user_id in await pod_members_among(uow, pod_id, [user_id])

    async def person_context(user_id: UUID, pod_id: UUID) -> Context:
        from app.core.authorization.factory import create_authorization_data_service

        return await create_authorization_data_service(uow).build_user_context(
            user_id=user_id, pod_id=pod_id
        )

    return PodConnectorResolver(
        pod_connectors=PodConnectorRepository(uow),
        pod_accounts=PodAccountRepository(uow),
        auth_configs=AuthConfigRepository(uow=uow, encryption=get_secret_cipher()),
        accounts=AccountRepository(uow=uow, encryption=get_secret_cipher()),
        is_pod_member=is_pod_member,
        person_context=person_context,
    )


def build_pod_connector_service(uow: SqlAlchemyUnitOfWork) -> PodConnectorService:
    return PodConnectorService(
        uow=uow,
        pod_connectors=PodConnectorRepository(uow),
        pod_accounts=PodAccountRepository(uow),
        auth_configs=AuthConfigRepository(uow=uow, encryption=get_secret_cipher()),
        accounts=AccountRepository(uow=uow, encryption=get_secret_cipher()),
        organization_access=SqlAlchemyOrganizationAccessAdapter(uow),
    )


def build_installation_reach(uow: SqlAlchemyUnitOfWork) -> InstallationReach:
    from app.modules.connectors.services.installation_reach import (
        InstallationReach,
    )

    return InstallationReach(build_pod_connector_resolver(uow))


__all__ = [
    "build_installation_reach",
    "build_pod_connector_resolver",
    "build_pod_connector_service",
]
