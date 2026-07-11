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
from app.modules.function.application.function_run_executor import FunctionRunExecutor
from app.modules.function.application.function_use_cases import FunctionUseCases
from app.modules.function.services.function_file_manager import FunctionFileManager
from app.modules.function.services.function_service import FunctionService
from app.composition.authorization import create_authorization_service
from app.core.config import settings
from app.core.object_storage import build_object_store, local_file_storage_path


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


def build_function_service(uow) -> FunctionService:
    """Construct a bound FunctionService (single wiring source). Used by read
    endpoints and as the per-phase collaborator the use case builds inside each
    short UoW."""
    message_bus = get_message_bus()
    return FunctionService(
        function_repository=FunctionRepository(uow, message_bus=message_bus),
        run_repository=FunctionRunRepository(uow, message_bus=message_bus),
        workspace_service=get_function_workspace_runtime(),
        storage_factory=get_function_storage_factory(),
        job_queue=get_streaq_job_queue(),
        icon_service=create_icon_service(),
        authorization_service=create_authorization_service(uow),
    )


def get_function_service(uow: UoWDep) -> FunctionService:
    """Provide FunctionService."""
    return build_function_service(uow)


def build_function_run_executor(uow_factory: UnitOfWorkFactory) -> FunctionRunExecutor:
    """Construct the sandbox execution engine (factory mode — short-UoW status
    writes). No repos held, no ctx."""
    return FunctionRunExecutor(
        uow_factory=uow_factory,
        workspace_service=get_function_workspace_runtime(),
        storage_factory=get_function_storage_factory(),
    )


def build_function_use_cases(uow_factory: UnitOfWorkFactory) -> FunctionUseCases:
    """Construct the function use-case layer. The API and the worker build the
    same object so they share one saga implementation."""
    return FunctionUseCases(
        uow_factory,
        build_function_service,
        build_function_run_executor(uow_factory),
    )


def get_function_use_cases(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> FunctionUseCases:
    return build_function_use_cases(uow_factory)


FunctionServiceDep = Annotated[FunctionService, Depends(get_function_service)]
FunctionUseCasesDep = Annotated[FunctionUseCases, Depends(get_function_use_cases)]

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
