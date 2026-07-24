"""Function module dependencies."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.core.api.dependencies import UoWDep, get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.authorization.context import ResourceType
from app.core.authorization.dependencies import (
    pod_from_path,
    require_action,
    require_resource_admin_or_creator,
    require_resource_action,
)
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.events.message_bus import get_message_bus
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.composition.icons import create_icon_service
from app.composition.function_workspace import (
    get_function_workspace_runtime,
)
from app.modules.function.infrastructure.repositories import (
    FunctionRepository,
    FunctionRunRepository,
)
from app.modules.function.application.function_definition_compiler import (
    FunctionDefinitionCompiler,
)
from app.modules.function.application.function_use_cases import FunctionUseCases
from app.modules.function.services.function_file_manager import FunctionFileManager
from app.modules.function.services.function_service import FunctionService
from app.core.config import reveal_secret, settings
from app.core.object_storage import build_object_store, local_file_storage_path
from app.core.request_context import correlation_headers
from app.composition.workspace_identity import (
    mint_workspace_token,
    resolve_workspace_organization_id,
)
from app.modules.function.application.function_callback_credentials import (
    FunctionCallbackCredentialSigner,
)
from app.modules.function.application.function_runtime_gateway import (
    FunctionRuntimeGateway,
)
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionTokenCache,
)
from app.modules.function.application.function_dispatcher import FunctionDispatcher
from app.modules.function.application.function_runtime_http_client import (
    FunctionRuntimeHttpClientPool,
)
from app.modules.function.infrastructure.function_run_queue import (
    StreaqFunctionRunQueue,
)
from agentbox_client import AgentBoxClient


_function_session_token_cache = FunctionSessionTokenCache(
    ttl_seconds=settings.function_session_token_cache_ttl_seconds,
    max_entries=settings.function_session_token_cache_max_entries,
)
_function_runtime_endpoint_cache = FunctionRuntimeEndpointCache(
    ttl_seconds=settings.function_runtime_endpoint_cache_ttl_seconds,
    max_entries=settings.function_runtime_endpoint_cache_max_entries,
)
_function_runtime_http_clients = FunctionRuntimeHttpClientPool()


async def close_function_runtime_http_clients() -> None:
    await _function_runtime_http_clients.close()


def get_function_storage_factory():
    root = local_file_storage_path("common")

    def build(function_id: UUID) -> FunctionFileManager:
        if settings.effective_storage_backend() == "local":
            return FunctionFileManager(function_id, root_path=root)
        return FunctionFileManager(
            function_id,
            store=build_object_store(
                local_prefix=root,
                remote_prefix=f"functions/{function_id}",
            ),
        )

    return build


def build_function_callback_credential_signer() -> FunctionCallbackCredentialSigner:
    secret = reveal_secret(settings.function_runtime_secret)
    if not secret:
        raise RuntimeError("FUNCTION_RUNTIME_SECRET must be configured")
    return FunctionCallbackCredentialSigner(secret)


def get_function_runtime_gateway(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> FunctionRuntimeGateway:
    return FunctionRuntimeGateway(
        uow_factory=uow_factory,
        storage_factory=get_function_storage_factory(),
        credential_signer=build_function_callback_credential_signer(),
        organization_resolver=resolve_workspace_organization_id,
        lemma_base_url=settings.function_runtime_gateway_url or settings.api_url,
        delegated_tokens_enabled=settings.authz_delegated_tokens_enabled,
    )


def build_function_service(uow) -> FunctionService:
    """Construct a bound FunctionService (single wiring source). Used by read
    endpoints and as the per-phase collaborator the use case builds inside each
    short UoW."""
    message_bus = get_message_bus()
    return FunctionService(
        function_repository=FunctionRepository(uow, message_bus=message_bus),
        run_repository=FunctionRunRepository(
            uow,
            message_bus=message_bus,
        ),
        storage_factory=get_function_storage_factory(),
        icon_service=create_icon_service(),
    )


def get_function_service(uow: UoWDep) -> FunctionService:
    """Provide FunctionService."""
    return build_function_service(uow)


def build_function_definition_compiler() -> FunctionDefinitionCompiler:
    """Construct the DB-free function definition build collaborator."""
    return FunctionDefinitionCompiler(
        workspace_service=get_function_workspace_runtime(),
        storage_factory=get_function_storage_factory(),
    )


def build_function_dispatcher(uow_factory: UnitOfWorkFactory) -> FunctionDispatcher:
    api_url = settings.agentbox_api_url
    api_key = settings.agentbox_api_key
    if not api_url or not api_key:
        raise RuntimeError("AGENTBOX_API_URL and AGENTBOX_API_KEY are required")

    def client_factory() -> AgentBoxClient:
        return AgentBoxClient(
            base_url=api_url,
            api_key=api_key,
            timeout_seconds=120,
            context_headers_provider=correlation_headers,
        )

    return FunctionDispatcher(
        uow_factory=uow_factory,
        credential_signer=build_function_callback_credential_signer(),
        agentbox_client_factory=client_factory,
        token_minter=mint_workspace_token,
        token_cache=_function_session_token_cache,
        endpoint_cache=_function_runtime_endpoint_cache,
        runtime_http_client_factory=_function_runtime_http_clients.get,
        delegated_tokens_enabled=settings.authz_delegated_tokens_enabled,
    )


def build_function_use_cases(uow_factory: UnitOfWorkFactory) -> FunctionUseCases:
    """Construct the function use-case layer. The API and the worker build the
    same object so they share one saga implementation."""
    return FunctionUseCases(
        uow_factory,
        build_function_service,
        build_function_definition_compiler(),
        build_function_dispatcher(uow_factory),
        StreaqFunctionRunQueue(get_streaq_job_queue()),
    )


def get_function_use_cases(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> FunctionUseCases:
    return build_function_use_cases(uow_factory)


FunctionServiceDep = Annotated[FunctionService, Depends(get_function_service)]
FunctionUseCasesDep = Annotated[FunctionUseCases, Depends(get_function_use_cases)]
FunctionRuntimeGatewayDep = Annotated[
    FunctionRuntimeGateway, Depends(get_function_runtime_gateway)
]

# Auth dependencies for controller routes
FunctionViewerDep = require_action(Permissions.FUNCTION_READ, pod_from_path)
FunctionEditorDep = require_action(Permissions.FUNCTION_UPDATE, pod_from_path)
FunctionAdminDep = require_action(Permissions.FUNCTION_DELETE, pod_from_path)
FunctionExecuteDep = require_action(Permissions.FUNCTION_EXECUTE, pod_from_path)
FunctionResourceViewerDep = require_resource_action(
    Permissions.FUNCTION_READ,
    resource_type=ResourceType.FUNCTION,
    name_param="function_name",
)
FunctionResourceEditorDep = require_resource_action(
    Permissions.FUNCTION_UPDATE,
    resource_type=ResourceType.FUNCTION,
    name_param="function_name",
)
FunctionResourceAdminDep = require_resource_action(
    Permissions.FUNCTION_DELETE,
    resource_type=ResourceType.FUNCTION,
    name_param="function_name",
)
FunctionResourceDeleteDep = require_resource_admin_or_creator(
    Permissions.FUNCTION_DELETE,
    resource_type=ResourceType.FUNCTION,
    name_param="function_name",
)
FunctionResourceExecuteDep = require_resource_action(
    Permissions.FUNCTION_EXECUTE,
    resource_type=ResourceType.FUNCTION,
    name_param="function_name",
)
