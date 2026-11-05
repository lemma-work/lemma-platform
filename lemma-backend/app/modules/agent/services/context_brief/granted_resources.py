"""The ``## Granted Resources`` section of a named agent's runtime brief.

Split from ``agent_context_brief`` by use case: the pod assistant's brief is
the pod inventory, a named agent's is what it has been granted, and the two
read different rows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID

from app.core.authorization.context import Context, ResourceType
from app.core.domain.errors import DomainError
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.entities import Agent
from app.modules.agent.services.context_brief.lines import (
    MAX_RESOURCES as _MAX_RESOURCES,
    more_note,
    table_line,
)
from app.modules.agent.infrastructure.context_brief_repository import (
    AuthorizationFactory,
    RepositoryFactory,
)
from app.modules.datastore.contracts.agent_tools import build_table_service


class _TableReader(Protocol):
    """The two reads this makes of the datastore's table service."""

    async def get_tables(
        self, pod_id: UUID, table_names: Sequence[str], ctx: Context
    ) -> Mapping[str, object]: ...

    async def get_table(
        self, pod_id: UUID, table_name: str, ctx: Context
    ) -> object: ...


class _TableReaderFactory(Protocol):
    def __call__(self, uow: SqlAlchemyUnitOfWork) -> _TableReader: ...


class GrantedResourcesBrief:
    """Renders what a named agent has been granted, with table schemas.

    The table service is a constructor seam like the other two, so a test
    hands in a double instead of replacing a name inside this module.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        repository: RepositoryFactory,
        authorization: AuthorizationFactory,
        tables: _TableReaderFactory | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self._repository = repository
        self._authorization = authorization
        self._tables = tables

    async def lines(
        self,
        *,
        agent: Agent,
        pod_id: UUID,
        user_id: UUID,
        grant_rows: list[tuple[str, UUID, str]] | None = None,
    ) -> list[str]:
        """The section; ``grant_rows`` when the run already read the grants."""
        # uow 1: grants + name resolution (plain queries).
        async with self.uow_factory() as uow:
            repo = self._repository(uow)
            rows = (
                grant_rows
                if grant_rows is not None
                else await repo.get_agent_grants(pod_id=pod_id, agent_id=agent.id)
            )
            if not rows:
                return [
                    "\n## Granted Resources",
                    (
                        "- (none) — you have no resource grants yet. If a tool returns "
                        "a permission error (403), call request_approval so the user "
                        "can grant access or run it for you."
                    ),
                ]

            refs: list[tuple[ResourceType, UUID]] = []
            perms_by_ref: dict[tuple[str, UUID], set[str]] = {}
            for resource_type, resource_id, permission_id in rows:
                try:
                    ref_type = ResourceType(resource_type)
                except ValueError:
                    continue
                refs.append((ref_type, resource_id))
                perms_by_ref.setdefault((resource_type, resource_id), set()).add(
                    permission_id
                )
            names = await repo.resolve_resource_names(pod_id=pod_id, refs=refs)

        # Sorted, so the same grants render the same bytes on every rebuild:
        # this half of the brief sits in the system prompt, ahead of the whole
        # replayed history, and the cap below picks *which* grants are shown.
        granted = sorted(
            perms_by_ref.items(), key=lambda item: (item[0][0], str(item[0][1]))
        )
        shown_tables = sorted(
            name
            for (resource_type, resource_id), _ in granted[:_MAX_RESOURCES]
            if resource_type == "datastore_table"
            and (name := names.get((ResourceType.DATASTORE_TABLE, resource_id)))
        )
        table_summaries = await self._table_summaries(
            shown_tables, pod_id=pod_id, user_id=user_id
        )

        lines = [
            "\n## Granted Resources",
            (
                "Resource grants below; the invoking user's permissions and "
                "approval gates also apply. Describe tables for column descriptions."
            ),
        ]
        # Omitted grants must not be mistaken for absent access.
        lines.extend(
            more_note(
                min(len(granted), _MAX_RESOURCES), len(granted), "granted resources"
            )
        )
        for (resource_type, resource_id), perms in granted[:_MAX_RESOURCES]:
            try:
                ref_type = ResourceType(resource_type)
            except ValueError:
                continue
            name = names.get((ref_type, resource_id))
            if name is None:
                continue
            perm_list = ", ".join(sorted(perms))
            if resource_type == "datastore_table" and name in table_summaries:
                lines.append(f"{table_summaries[name]}  (grants: {perm_list})")
            else:
                lines.append(f"- {resource_type}: {name}  (grants: {perm_list})")
        return lines

    async def _table_summaries(
        self, table_names: list[str], *, pod_id: UUID, user_id: UUID
    ) -> dict[str, str]:
        """Schema lines for exactly these tables, read as the invoking user.

        By name, not by listing the pod's tables and filtering: a listing is
        capped, so a granted table past the pod's first page lost its schema.
        The rows are rendered (lazy column access) before the uow closes.
        """
        if not table_names:
            return {}
        async with self.uow_factory() as uow:
            ctx = await self._authorization(uow).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            token = set_current_context(ctx)
            try:
                service = (self._tables or build_table_service)(uow)
                tables = await self._readable_tables(
                    service, table_names, pod_id=pod_id, ctx=ctx
                )
                return {name: table_line(table) for name, table in tables.items()}
            finally:
                reset_current_context(token)

    @staticmethod
    async def _readable_tables(
        service: _TableReader,
        table_names: list[str],
        *,
        pod_id: UUID,
        ctx: Context,
    ) -> dict[str, object]:
        """Every one of them in one read; one at a time only if that fails.

        The batch read refuses the whole set when one table is gone or this
        person cannot read it. Such a grant is still listed, without a schema
        line -- the same as a table the person cannot see.
        """
        try:
            return dict(await service.get_tables(pod_id, table_names, ctx))
        except DomainError:
            pass
        readable: dict[str, object] = {}
        for name in table_names:
            try:
                readable[name] = await service.get_table(pod_id, name, ctx)
            except DomainError:
                continue
        return readable
