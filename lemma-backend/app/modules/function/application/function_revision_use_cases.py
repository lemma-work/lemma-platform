"""Revision-history sagas for functions.

Split out of ``function_use_cases`` because that module is at the architecture
ratchet's per-file ceiling. It stays a mixin rather than a separate injected
collaborator so callers keep one use-case object: how the file divides is not a
distinction the API layer should have to know about.

Same discipline as the rest of the layer: authorization runs inside a short unit
of work, and the one storage read (a revision's source) happens after that unit
of work has closed.
"""

from __future__ import annotations

from uuid import UUID
from collections.abc import Callable
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.function.services.function_service import FunctionService
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.function.services.function_revision_service import (
        FunctionRevisionService,
        PromotionResult,
        RevisionListing,
    )
    from app.modules.function.domain.entities import FunctionRevisionEntity

from fastapi import Request

from app.core.authorization.scope import pod_context_scope


class FunctionRevisionUseCasesMixin:
    """Requires ``_uow_factory`` and ``_build`` from the host class."""

    _uow_factory: UnitOfWorkFactory
    _build: Callable[[SqlAlchemyUnitOfWork], FunctionService]

    # -- revision history -----------------------------------------------------

    def _build_revisions(self, uow: SqlAlchemyUnitOfWork) -> FunctionRevisionService:
        from app.modules.function.services.function_revision_service import (
            FunctionRevisionService,
        )

        service = self._build(uow)
        return FunctionRevisionService(service.repository, service.storage_factory)

    async def list_revisions(
        self, *, pod_id: UUID, name: str, user_id: UUID, request: Request
    ) -> list[RevisionListing]:
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            return await self._build_revisions(scope.uow).list_revisions(
                pod_id, name, ctx=scope.ctx
            )

    async def get_revision(
        self, *, pod_id: UUID, name: str, ref: str, user_id: UUID, request: Request
    ) -> tuple[FunctionRevisionEntity, bool]:
        """Resolve the revision in a short UoW, then read its code from storage
        with no pooled connection held."""
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            service = self._build_revisions(scope.uow)
            revision, is_live = await service.get_revision(
                pod_id, name, ref, ctx=scope.ctx
            )
            function_id = revision.function_id
        revision.code = await service.read_revision_code(function_id, revision)
        return revision, is_live

    async def promote_revision(
        self, *, pod_id: UUID, name: str, ref: str, user_id: UUID, request: Request
    ) -> PromotionResult:
        async with pod_context_scope(
            self._uow_factory, request=request, user_id=user_id, pod_id=pod_id
        ) as scope:
            return await self._build_revisions(scope.uow).promote_revision(
                pod_id, name, ref, ctx=scope.ctx
            )
