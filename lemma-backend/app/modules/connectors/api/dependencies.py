from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import Depends

from app.core.api.dependencies import UoWDep, get_uow_factory
from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.events.message_bus import get_message_bus
from app.core.authorization.factory import create_authorization_data_service
from app.modules.connectors.application.connector_operation_use_cases import (
    ConnectorOperationUseCases,
)
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.connectors.infrastructure.adapters.auth_provider_registry import (
    AuthProviderRegistry,
)
from app.modules.connectors.infrastructure.adapters.env_system_oauth_config import (
    EnvSystemOAuthConfigAdapter,
)
from app.modules.connectors.domain.ports import PodFileGatewayPort
from app.modules.connectors.infrastructure.adapters.organization_access import (
    SqlAlchemyOrganizationAccessAdapter,
)
from app.modules.connectors.infrastructure.adapters.bounded_composio_gateway import (
    BoundedComposioGateway,
)
from app.modules.connectors.infrastructure.adapters.oauth_redirect_uri_builder import (
    OAuthRedirectUriBuilder,
)
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_repository import (
    ConnectorRepository,
)
from app.modules.connectors.infrastructure.repositories.auth_config_repository import (
    AuthConfigRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_operation_repository import (
    ConnectorOperationRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_trigger_repository import (
    ConnectorTriggerRepository,
)
from app.modules.connectors.infrastructure.repositories.connect_request_repository import (
    ConnectRequestRepository,
)
from app.modules.connectors.services.account_resolution_service import (
    AccountResolutionService,
)
from app.modules.connectors.services.connector_operation_service import (
    ConnectorOperationService,
)
from app.modules.connectors.services.auth.composio_auth_provider import (
    ComposioAuthProvider,
)
from app.modules.connectors.services.auth.lemma_auth_provider import LemmaAuthProvider
from app.modules.connectors.services.connector_service import ConnectorService
from app.modules.connectors.services.trigger_service import ConnectorTriggerService

if TYPE_CHECKING:
    from app.core.authorization.context import Context
    from app.modules.connectors.api.schemas.connector_operation_schemas import (
        OperationExecutionResponse,
    )
    from app.modules.connectors.services.files.operation_files import (
        FoundFile,
        OperationFiles,
    )


def _connector_repository(uow: UoWDep) -> ConnectorRepository:
    return ConnectorRepository(uow=uow, message_bus=get_message_bus())


def _account_repository(uow: UoWDep) -> AccountRepository:
    return AccountRepository(
        uow=uow,
        encryption=get_secret_cipher(),
        message_bus=get_message_bus(),
    )


def _auth_config_repository(uow: UoWDep) -> AuthConfigRepository:
    return AuthConfigRepository(
        uow=uow,
        encryption=get_secret_cipher(),
        message_bus=get_message_bus(),
    )


def _connect_request_repository(uow: UoWDep) -> ConnectRequestRepository:
    return ConnectRequestRepository(uow=uow, message_bus=get_message_bus())


def _trigger_repository(uow: UoWDep) -> ConnectorTriggerRepository:
    return ConnectorTriggerRepository(uow=uow, message_bus=get_message_bus())


def _operation_repository(uow: UoWDep) -> ConnectorOperationRepository:
    return ConnectorOperationRepository(uow=uow, message_bus=get_message_bus())


def _auth_provider_registry(uow: UoWDep) -> AuthProviderRegistry:
    connector_repository = _connector_repository(uow)
    return AuthProviderRegistry(
        providers={
            AuthProvider.LEMMA.value: LemmaAuthProvider(),
            AuthProvider.COMPOSIO.value: ComposioAuthProvider(
                connector_repository=connector_repository
            ),
        }
    )


def _auth_config_operation_repository(uow: UoWDep):
    from app.modules.connectors.infrastructure.repositories.auth_config_operation_repository import (
        AuthConfigOperationRepository,
    )

    return AuthConfigOperationRepository(uow.session)


def get_connector_service(uow: UoWDep) -> ConnectorService:
    connector_repository = _connector_repository(uow)
    return ConnectorService(
        auth_config_operation_repository=_auth_config_operation_repository(uow),
        uow=uow,
        connector_repository=connector_repository,
        auth_config_repository=_auth_config_repository(uow),
        account_repository=_account_repository(uow),
        connect_request_repository=_connect_request_repository(uow),
        auth_provider_registry=_auth_provider_registry(uow),
        redirect_uri_builder=OAuthRedirectUriBuilder(),
        organization_access=SqlAlchemyOrganizationAccessAdapter(uow),
        system_oauth_config=EnvSystemOAuthConfigAdapter(),
        operation_gateway=BoundedComposioGateway(
            connector_repository=connector_repository
        ),
        operation_repository=_operation_repository(uow),
    )


def get_connector_trigger_service(uow: UoWDep) -> ConnectorTriggerService:
    return ConnectorTriggerService(
        trigger_repository=_trigger_repository(uow),
        connector_repository=_connector_repository(uow),
        connector_service=get_connector_service(uow),
    )


def get_connector_operation_service(uow: UoWDep) -> ConnectorOperationService:
    return build_connector_operation_service(uow)


def build_connector_operation_service(
    uow: UoWDep,
) -> ConnectorOperationService:
    connector_repository = _connector_repository(uow)
    return ConnectorOperationService(
        auth_config_operation_repository=_auth_config_operation_repository(uow),
        connector_repository=connector_repository,
        operation_repository=_operation_repository(uow),
        operation_gateway=BoundedComposioGateway(
            connector_repository=connector_repository
        ),
        account_resolution_service=get_account_resolution_service(uow),
        connector_service=get_connector_service(uow),
    )


def get_account_resolution_service(uow: UoWDep) -> AccountResolutionService:
    return AccountResolutionService(
        account_repository=_account_repository(uow),
        authorization_service=create_authorization_data_service(uow),
        organization_access=SqlAlchemyOrganizationAccessAdapter(uow),
    )


ConnectorServiceDep = Annotated[ConnectorService, Depends(get_connector_service)]
ConnectorTriggerServiceDep = Annotated[
    ConnectorTriggerService, Depends(get_connector_trigger_service)
]
ConnectorOperationServiceDep = Annotated[
    ConnectorOperationService, Depends(get_connector_operation_service)
]


def build_pod_file_gateway(uow: object) -> PodFileGatewayPort:
    """Pod file reads and writes for connector operations, on ``uow``.

    Public because the agent's connector tools need the same one: they read an
    attachment and land a download exactly as the REST route does.
    """
    # Imported here, not at module scope, for the import budget: the adapter
    # reaches `datastore/contracts/pod_files.py`, which pulls datastore's
    # service layer into every process that touches a connector route. This
    # runs per operation execution, not per import.
    from app.modules.connectors.infrastructure.adapters.pod_file_gateway import (
        DatastorePodFileGateway,
    )

    return DatastorePodFileGateway(uow)


def build_operation_files(
    uow: object, *, pod_id: UUID | None, ctx: "Context"
) -> "OperationFiles":
    """File inputs and results for one caller's operation calls, in one pod."""
    from app.modules.connectors.services.files.operation_files import OperationFiles

    return OperationFiles(build_pod_file_gateway(uow), pod_id=pod_id, ctx=ctx)


async def find_file_result(
    response: "OperationExecutionResponse",
) -> "FoundFile | None":
    """The file an operation result carries, fetched. Needs no session."""
    from app.modules.connectors.services.files.operation_files import (
        find_file_result as find,
    )

    return await find(response)


def build_connector_operation_use_cases(
    uow_factory: UnitOfWorkFactory,
) -> ConnectorOperationUseCases:
    # Factory mode: the use-case opens its own short UoWs per phase (via
    # build_connector_operation_service as the per-phase builder) so no pooled
    # connection is held across the external operation call.
    return ConnectorOperationUseCases(
        uow_factory, build_connector_operation_service, build_pod_file_gateway
    )


def get_connector_operation_use_cases(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> ConnectorOperationUseCases:
    return build_connector_operation_use_cases(uow_factory)


ConnectorOperationUseCasesDep = Annotated[
    ConnectorOperationUseCases, Depends(get_connector_operation_use_cases)
]
AccountResolutionServiceDep = Annotated[
    AccountResolutionService, Depends(get_account_resolution_service)
]
