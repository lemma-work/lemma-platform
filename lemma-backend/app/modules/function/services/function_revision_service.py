"""Revision history for a function: record it, list it, promote one.

The artifacts and source of every revision were always kept -- content-addressed
at ``artifacts/<hash>.zip`` and ``revisions/<hash>/function.py``, deleted by
nothing -- so going back to an older build needs no new bytes, only an index and
a way to move ``functions.revision_hash``.

Separate from :class:`~app.modules.function.services.function_service.
FunctionService` because that file is already at the architecture ratchet's
per-file ceiling, and because the seam is real: that service owns running and
editing a function, this owns which built revision is the live one.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.permissions import Permissions
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRevisionEntity,
)
from app.modules.function.domain.errors import (
    FunctionNotFoundError,
    FunctionRevisionNotFoundError,
    FunctionRevisionPrunedError,
)
from app.modules.function.domain.ports import (
    FunctionRepositoryPort,
    FunctionStorageFactoryPort,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RevisionListing:
    revision: FunctionRevisionEntity
    is_live: bool


@dataclass(frozen=True, slots=True)
class PromotionResult:
    """A promoted revision, plus whether it changes the function's contract.

    Callers surface ``schema_changed`` as a warning: the schemas move with the
    revision, so promoting one whose contract differs can break agents and
    workflows that were built against the schemas that were live a moment ago.
    """

    revision: FunctionRevisionEntity
    function: FunctionEntity
    schema_changed: bool


def parse_revision_ref(ref: str) -> tuple[int | None, str | None]:
    """Split a revision reference into ``(revision_number, hash_prefix)``.

    A reference is either the counter people read -- ``12`` or ``r12`` -- or a
    prefix of the revision hash, for when someone has the hash from a run.
    """
    candidate = (ref or "").strip().lower()
    if not candidate:
        raise FunctionRevisionNotFoundError("No revision was named")
    numeric = candidate[1:] if candidate[0] in {"v", "r"} else candidate
    if numeric.isdigit():
        return int(numeric), None
    return None, candidate.removeprefix("sha256:")


class FunctionRevisionService:
    def __init__(
        self,
        function_repository: FunctionRepositoryPort,
        storage_factory: FunctionStorageFactoryPort | None = None,
    ):
        self.repository = function_repository
        self.storage_factory = storage_factory

    async def _load_function(
        self, pod_id: UUID, name: str, *, permission: str, ctx: Context
    ) -> FunctionEntity:
        function = await self.repository.get_by_name(pod_id, name, ctx=ctx)
        if function is None:
            raise FunctionNotFoundError(f"Function {name} not found")
        assert function.id is not None
        await ctx.require(
            permission,
            ResourceRef(
                resource_type=ResourceType.FUNCTION,
                resource_id=function.id,
                pod_id=pod_id,
            ),
        )
        return function

    async def record(
        self,
        function: FunctionEntity,
        *,
        created_by: UUID | None = None,
    ) -> FunctionRevisionEntity | None:
        """Index the revision a just-compiled function is now pointing at.

        Called from the persist phase, inside the caller's short unit of work, so
        indexing a revision costs no extra transaction and cannot be interrupted
        between the function row and its history.
        """
        if function.id is None or function.revision_hash is None:
            return None
        if function.code_path is None:
            return None
        return await self.repository.record_revision(
            FunctionRevisionEntity(
                function_id=function.id,
                # Replaced by the repository's atomic per-function counter; the
                # entity requires a value, and only the INSERT can allocate one
                # without racing a concurrent save.
                revision_number=0,
                revision_hash=function.revision_hash,
                code_path=function.code_path,
                input_schema=function.input_schema,
                output_schema=function.output_schema,
                config_schema=function.config_schema,
                created_by=created_by or function.user_id,
            )
        )

    async def resolve_revision(
        self,
        function: FunctionEntity,
        ref: str,
        *,
        allow_pruned: bool = False,
    ) -> FunctionRevisionEntity:
        assert function.id is not None
        number, hash_prefix = parse_revision_ref(ref)
        revision: FunctionRevisionEntity | None = None
        if number is not None:
            revision = await self.repository.get_revision_by_number(
                function.id, number
            )
            # A hash is hex, so a short prefix can be all decimal digits and read
            # as a revision number. Fall through rather than 404.
            if revision is None:
                hash_prefix = ref.strip().lower().removeprefix("sha256:")
        if revision is None and hash_prefix is not None:
            matches = [
                candidate
                for candidate in await self.repository.list_revisions(function.id)
                if candidate.revision_hash.removeprefix("sha256:").startswith(
                    hash_prefix
                )
            ]
            if len(matches) > 1:
                raise FunctionRevisionNotFoundError(
                    f"Revision '{ref}' is ambiguous -- it matches "
                    f"{len(matches)} revisions. Use the full hash or the "
                    "revision number."
                )
            revision = matches[0] if matches else None
        if revision is None:
            raise FunctionRevisionNotFoundError(
                f"Function '{function.name}' has no revision '{ref}'"
            )
        if revision.is_pruned and not allow_pruned:
            raise FunctionRevisionPrunedError(
                f"Revision r{revision.revision_number} of '{function.name}' was "
                "removed by retention, so it can no longer be run or promoted."
            )
        return revision

    async def list_revisions(
        self, pod_id: UUID, name: str, *, ctx: Context
    ) -> list[RevisionListing]:
        function = await self._load_function(
            pod_id, name, permission=Permissions.FUNCTION_READ, ctx=ctx
        )
        assert function.id is not None
        revisions = await self.repository.list_revisions(function.id)
        return [
            RevisionListing(
                revision=revision,
                is_live=revision.revision_hash == function.revision_hash,
            )
            for revision in revisions
        ]

    async def get_revision(
        self, pod_id: UUID, name: str, ref: str, *, ctx: Context
    ) -> tuple[FunctionRevisionEntity, bool]:
        """Resolve one revision. DB only -- the caller reads its code afterwards,
        outside the unit of work, so no connection is held across storage."""
        function = await self._load_function(
            pod_id, name, permission=Permissions.FUNCTION_READ, ctx=ctx
        )
        revision = await self.resolve_revision(function, ref, allow_pruned=True)
        return revision, revision.revision_hash == function.revision_hash

    async def read_revision_code(
        self, function_id: UUID, revision: FunctionRevisionEntity
    ) -> str | None:
        """Read a revision's source. Storage only -- holds no DB connection."""
        if self.storage_factory is None or revision.is_pruned:
            return None
        code = await self.storage_factory(function_id).read_file(revision.code_path)
        return code.decode("utf-8") if isinstance(code, bytes) else code

    async def promote_revision(
        self, pod_id: UUID, name: str, ref: str, *, ctx: Context
    ) -> PromotionResult:
        function = await self._load_function(
            pod_id, name, permission=Permissions.FUNCTION_UPDATE, ctx=ctx
        )
        assert function.id is not None
        revision = await self.resolve_revision(function, ref)

        schema_changed = (
            revision.input_schema != function.input_schema
            or revision.output_schema != function.output_schema
            or revision.config_schema != function.config_schema
        )
        updated = await self.repository.activate_revision(function.id, revision)
        if updated is None:
            raise FunctionNotFoundError(f"Function {name} not found")
        logger.info(
            "function.function_revision_service.revision_promoted",
            function_id=str(function.id),
            pod_id=str(pod_id),
            revision_number=revision.revision_number,
            schema_changed=schema_changed,
        )
        return PromotionResult(
            revision=revision, function=updated, schema_changed=schema_changed
        )
