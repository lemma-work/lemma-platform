"""Pod bundle module dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.pod_bundle.application.export_use_cases import ExportUseCases
from app.modules.pod_bundle.application.import_use_cases import ImportUseCases


def build_export_use_cases(uow_factory: UnitOfWorkFactory) -> ExportUseCases:
    """Construct the export use-case layer (factory mode). The API builds it as a
    request dependency; a worker could build the same object from its factory."""
    return ExportUseCases(uow_factory)


def get_export_use_cases(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> ExportUseCases:
    return build_export_use_cases(uow_factory)


ExportUseCasesDep = Annotated[ExportUseCases, Depends(get_export_use_cases)]


def build_import_use_cases(uow_factory: UnitOfWorkFactory) -> ImportUseCases:
    return ImportUseCases(uow_factory)


def get_import_use_cases(
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> ImportUseCases:
    return build_import_use_cases(uow_factory)


ImportUseCasesDep = Annotated[ImportUseCases, Depends(get_import_use_cases)]
