"""Function adapters used by dynamic agent tools and context projection."""

from app.modules.function.api.dependencies import build_function_use_cases
from app.modules.function.infrastructure.repositories import (
    FunctionRepository,
    FunctionRunRepository,
)


def create_function_repository(uow) -> FunctionRepository:
    return FunctionRepository(uow)


def create_function_run_repository(uow) -> FunctionRunRepository:
    return FunctionRunRepository(uow)


def create_function_use_cases(uow_factory):
    return build_function_use_cases(uow_factory)
