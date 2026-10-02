"""The pod side of the decisions tools, with this tool call's authority.

A session is `pod_services`: one short unit of work whose authorization
context comes from `tool_authorization_context` -- the agent's own grants, or
the person's for a call they approved -- and which is set as the ambient
context, because record reads apply row-level security from it. The tools
open one to authorize and read their rows, close it, ask the decisions
contract with no session held, and open another only to land a results file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID

from pydantic import JsonValue

from app.core.authorization.context import ResourceRef, ResourceType
from app.modules.agent.domain.value_objects import to_json_value
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.decisions.models import TableRows
from app.modules.agent.tools.decisions.seams import (
    Caller,
    PodSession,
    SavedFile,
    TableBatch,
)
from app.modules.agent.tools.pod.pod_data_access import PodServices, pod_services
from app.modules.datastore.contracts import TableContext, pod_files


class _DatastoreSession:
    def __init__(self, services: PodServices, deps: BaseAgentContext) -> None:
        self._services = services
        self._deps = deps

    @property
    def caller(self) -> Caller:
        return Caller(
            user_id=self._deps.user_id,
            pod_id=self._deps.pod_id,
            organization_id=self._deps.org_id or self._services.ctx.organization_id,
        )

    async def require(self, permission: str, decider_id: UUID | None) -> None:
        pod_id = self._deps.pod_id
        resource = (
            ResourceRef.pod(pod_id)
            if decider_id is None
            else ResourceRef(
                resource_type=ResourceType.DECIDER,
                resource_id=decider_id,
                pod_id=pod_id,
            )
        )
        await self._services.ctx.require(permission, resource)

    async def read_pod_file(self, path: str) -> bytes:
        found = await pod_files.read_pod_file(
            self._services.uow,
            pod_id=self._deps.pod_id,
            path=path,
            ctx=self._services.ctx,
        )
        return found.content

    async def read_table(self, source: TableRows, *, limit: int) -> TableBatch:
        services = self._services
        pod_id = self._deps.pod_id
        table = await services.table.get_table(pod_id, source.table_name, services.ctx)
        table_ctx = TableContext.from_table_entity(
            table, services.table.schema_manager.get_schema_name(pod_id)
        )
        records, total = await services.record.list_records(
            table_ctx,
            self._deps.user_id,
            limit=limit,
            offset=source.offset,
            sorts=[(sort.column, sort.direction) for sort in source.sorts] or None,
            filters=[(item.column, item.op, item.value) for item in source.filters]
            or None,
        )
        return TableBatch(
            # A record's data is already JSON-shaped apart from dates and ids,
            # which `to_json_value` renders as strings.
            rows=[cast(JsonValue, to_json_value(record.data)) for record in records],
            total=total,
            primary_key=table.primary_key_column,
        )

    async def write_pod_file(
        self, *, directory: str, name: str, content: bytes
    ) -> SavedFile:
        stored = await pod_files.write_pod_file(
            self._services.uow,
            pod_id=self._deps.pod_id,
            directory=directory,
            name=name,
            content=content,
            ctx=self._services.ctx,
        )
        return SavedFile(pod_path=stored.pod_path, size_bytes=stored.size_bytes)


class DatastorePodAccess:
    """`PodAccess` over `pod_services` and the datastore's contracts."""

    @asynccontextmanager
    async def decision_pod_scope(
        self, deps: BaseAgentContext
    ) -> AsyncIterator[PodSession]:
        async with pod_services(deps) as services:
            yield _DatastoreSession(services, deps)
