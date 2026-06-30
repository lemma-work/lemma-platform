from typing import Annotated

from fastapi import Depends

from app.core.api.dependencies import UoWDep, get_uow_factory
from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.pod.services.authorization_factory import create_authorization_service
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
from app.modules.connectors.infrastructure.adapters.organization_access_adapter import (
    SqlAlchemyOrganizationAccessAdapter,
)
from app.modules.connectors.infrastructure.adapters.routing_operation_gateway import (
    RoutingOperationGateway,
)
from app.modules.connectors.infrastructure.adapters.oauth_redirect_uri_builder import (
    OAuthRedirectUriBuilder,
)
from app.modules.connectors.infrastructure.adapters.schema_compiler import (
    PydanticCodeSchemaCompiler,
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


def get_connector_service(uow: UoWDep) -> ConnectorService:
    connector_repository = _connector_repository(uow)
    return ConnectorService(
        uow=uow,
        connector_repository=connector_repository,
        auth_config_repository=_auth_config_repository(uow),
        account_repository=_account_repository(uow),
        connect_request_repository=_connect_request_repository(uow),
        auth_provider_registry=_auth_provider_registry(uow),
        redirect_uri_builder=OAuthRedirectUriBuilder(),
        organization_access=SqlAlchemyOrganizationAccessAdapter(uow),
        system_oauth_config=EnvSystemOAuthConfigAdapter(),
        operation_gateway=RoutingOperationGateway(
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
        connector_repository=connector_repository,
        operation_repository=_operation_repository(uow),
        operation_gateway=RoutingOperationGateway(
            connector_repository=connector_repository
        ),
        schema_compiler=PydanticCodeSchemaCompiler(),
        account_resolution_service=get_account_resolution_service(uow),
        connector_service=get_connector_service(uow),
    )


def get_account_resolution_service(uow: UoWDep) -> AccountResolutionService:
    return AccountResolutionService(
        account_repository=_account_repository(uow),
        authorization_service=create_authorization_service(uow),
    )

ConnectorServiceDep = Annotated[ConnectorService, Depends(get_connector_service)]
ConnectorTriggerServiceDep = Annotated[
    ConnectorTriggerService, Depends(get_connector_trigger_service)
]
ConnectorOperationServiceDep = Annotated[
    ConnectorOperationService, Depends(get_connector_operation_service)
]


def build_connector_operation_use_cases(
    uow_factory: UnitOfWorkFactory,
) -> ConnectorOperationUseCases:
    # Factory mode: the use-case opens its own short UoWs per phase (via
    # build_connector_operation_service as the per-phase builder) so no pooled
    # connection is held across the external operation call.
    return ConnectorOperationUseCases(uow_factory, build_connector_operation_service)


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
