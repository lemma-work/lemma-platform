"""App module dependencies."""

from functools import partial
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from app.core.api.dependencies import UoWDep, get_uow_factory
from app.core.authorization.context import ResourceType
from app.core.authorization.dependencies import (
    pod_from_path,
    require_action,
    require_resource_admin_or_creator,
    require_resource_action,
)
from app.core.authorization.permissions import Permissions
from app.core.config import settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.events.message_bus import get_message_bus
from app.core.ports.widget_content import WidgetContentReader
from app.composition.widget_content import create_widget_content_reader
from app.modules.apps.application.app_use_cases import AppUseCases
from app.modules.apps.infrastructure.repositories import AppRepository
from app.modules.apps.services.app_file_manager import AppFileManager
from app.modules.apps.services.app_service import AppService
from app.composition.authorization import create_authorization_service


def _get_app_storage_factory():
    if settings.effective_storage_backend() == "gcs":
        if not settings.gcs_storage_bucket:
            raise ValueError("GCS storage requires GCS_STORAGE_BUCKET")
        return partial(AppFileManager, bucket_name=settings.gcs_storage_bucket)
    return partial(
        AppFileManager,
        root_path=Path(settings.local_file_storage_root) / "common",
    )


def build_app_service(uow) -> AppService:
    """Construct an AppService from a unit of work (single wiring source)."""
    message_bus = get_message_bus()
    return AppService(
        app_repository=AppRepository(uow, message_bus=message_bus),
        file_manager_factory=_get_app_storage_factory(),
        authorization_service=create_authorization_service(uow),
    )


def get_app_service(uow: UoWDep) -> AppService:
    return build_app_service(uow)


AppServiceDep = Annotated[AppService, Depends(get_app_service)]


def build_app_use_cases(uow_factory: UnitOfWorkFactory) -> AppUseCases:
    """Construct the app use-case layer (factory mode). The API and the worker
    build the same object so they share one saga implementation."""
    return AppUseCases(uow_factory, build_app_service)


def get_app_use_cases(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> AppUseCases:
    return build_app_use_cases(uow_factory)


AppUseCasesDep = Annotated[AppUseCases, Depends(get_app_use_cases)]


def get_widget_content_reader(uow: UoWDep) -> WidgetContentReader:
    # DI wiring edge: the agent module owns widget content, but the app module's
    # business logic depends only on the core WidgetContentReader port — this
    # provider is the single place the two modules are wired together.
    return create_widget_content_reader(uow)


WidgetContentReaderDep = Annotated[
    WidgetContentReader, Depends(get_widget_content_reader)
]

# Auth dependencies for controller routes
AppViewerDep = require_action(Permissions.APP_READ, pod_from_path)
AppEditorDep = require_action(Permissions.APP_UPDATE, pod_from_path)
AppAdminDep = require_action(Permissions.APP_DELETE, pod_from_path)
AppResourceViewerDep = require_resource_action(
    Permissions.APP_READ,
    resource_type=ResourceType.APP,
    name_param="app_name",
)
AppResourceEditorDep = require_resource_action(
    Permissions.APP_UPDATE,
    resource_type=ResourceType.APP,
    name_param="app_name",
)
AppResourceAdminDep = require_resource_action(
    Permissions.APP_DELETE,
    resource_type=ResourceType.APP,
    name_param="app_name",
)
AppResourceDeleteDep = require_resource_admin_or_creator(
    Permissions.APP_DELETE,
    resource_type=ResourceType.APP,
    name_param="app_name",
)
