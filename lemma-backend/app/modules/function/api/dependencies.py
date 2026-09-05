"""Function module dependencies."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.modules.function.config import function_settings
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
from app.modules.icon.contracts.provisioning import create_icon_service
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
from app.core.config import settings
from app.core.object_storage import build_object_store, local_file_storage_path
from app.modules.identity.contracts.delegated_tokens import (
    mint_delegated_token_with_expiry,
)
from app.modules.pod.contracts.detached_reads import (
    pod_organization_id_detached,
)
from app.modules.function.application.function_runtime_gateway import (
    FunctionRuntimeGateway,
)
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionToken,
    FunctionSessionTokenCache,
)
from app.modules.function.application.function_dispatcher import FunctionDispatcher
from app.modules.function.application.function_schema_dispatcher import (
    FunctionSchemaDispatcher,
)
from app.modules.function.application.function_runtime_http_client import (
    FunctionRuntimeHttpClientPool,
)
from app.modules.function.infrastructure.function_run_queue import (
    StreaqFunctionRunQueue,
)
from app.modules.function.application.function_runtime_route_resolver import (
    endpoint_reuse_seconds,
)
from app.modules.workspace.services.local_sandbox_client import (
    LocalSandboxClient,
)


_function_session_token_cache = FunctionSessionTokenCache(
    ttl_seconds=function_settings.function_session_token_cache_ttl_seconds,
    max_entries=function_settings.function_session_token_cache_max_entries,
)
_function_runtime_endpoint_cache = FunctionRuntimeEndpointCache(
    # Clamped against the idle release that invalidates it; see the helper.
    ttl_seconds=endpoint_reuse_seconds(),
    max_entries=function_settings.function_runtime_endpoint_cache_max_entries,
)
_function_runtime_http_clients = FunctionRuntimeHttpClientPool()


async def close_function_runtime_http_clients() -> None:
    await _function_runtime_http_clients.close()


async def mint_function_session_token(
    *,
    user_id: UUID,
    workload_type: str | None,
    workload_id: UUID | None,
    pod_id: UUID | None,
    session_id: str,
    workload_name: str | None,
    scope: list[str] | None,
    delegated_tokens_enabled: bool,
) -> FunctionSessionToken:
    """Identity's delegated token, in the shape the session cache stores.

    The expiry-bearing mint, not the plain one: the cache checks the issuer's
    own expiry against the run's deadline before handing a token back, and a
    local TTL standing in for that is how a cache serves a token that has
    already died.
    """
    issued = await mint_delegated_token_with_expiry(
        user_id=user_id,
        workload_type=workload_type,
        workload_id=workload_id,
        pod_id=pod_id,
        session_id=session_id,
        workload_name=workload_name,
        scope=scope,
        delegated_tokens_enabled=delegated_tokens_enabled,
    )
    return FunctionSessionToken(value=issued.value, expires_at=issued.expires_at)


async def resolve_function_organization_id(pod_id: UUID | None) -> str | None:
    """The organization scope a function run's sandbox is given."""
    if pod_id is None:
        return None
    organization_id = await pod_organization_id_detached(pod_id)
    return str(organization_id) if organization_id else None


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


def get_function_runtime_gateway(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> FunctionRuntimeGateway:
    return FunctionRuntimeGateway(
        uow_factory=uow_factory,
        storage_factory=get_function_storage_factory(),
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


def build_function_definition_compiler(
    schema_executor: FunctionSchemaDispatcher,
) -> FunctionDefinitionCompiler:
    """Construct the DB-free function definition build collaborator."""
    return FunctionDefinitionCompiler(
        schema_executor=schema_executor,
        storage_factory=get_function_storage_factory(),
    )


def _function_sandbox_client() -> LocalSandboxClient:
    """The client the function runtime is reached through.

    Function sandboxes are provisioned by the same machinery as workspaces --
    one per pod, a different image, a narrower capability set.
    """
    from app.modules.workspace.services.sandbox_composition import (
        build_local_client,
    )

    return build_local_client()


def build_function_dispatcher(uow_factory: UnitOfWorkFactory) -> FunctionDispatcher:
    return FunctionDispatcher(
        uow_factory=uow_factory,
        sandbox_client_factory=_function_sandbox_client,
        token_minter=mint_function_session_token,
        token_cache=_function_session_token_cache,
        endpoint_cache=_function_runtime_endpoint_cache,
        runtime_http_client_factory=_function_runtime_http_clients.get,
        organization_resolver=resolve_function_organization_id,
        delegated_tokens_enabled=settings.authz_delegated_tokens_enabled,
    )


def build_function_schema_dispatcher() -> FunctionSchemaDispatcher:
    return FunctionSchemaDispatcher(
        sandbox_client_factory=_function_sandbox_client,
        token_minter=mint_function_session_token,
        token_cache=_function_session_token_cache,
        endpoint_cache=_function_runtime_endpoint_cache,
        runtime_http_client_factory=_function_runtime_http_clients.get,
        delegated_tokens_enabled=settings.authz_delegated_tokens_enabled,
    )


def build_function_use_cases(uow_factory: UnitOfWorkFactory) -> FunctionUseCases:
    """Construct the function use-case layer. The API and the worker build the
    same object so they share one saga implementation."""
    dispatcher = build_function_dispatcher(uow_factory)
    return FunctionUseCases(
        uow_factory,
        build_function_service,
        build_function_definition_compiler(build_function_schema_dispatcher()),
        dispatcher,
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
