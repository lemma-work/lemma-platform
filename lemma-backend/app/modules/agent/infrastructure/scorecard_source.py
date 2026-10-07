"""The scorecard's reads, answered from the datastore and the platform's records.

One adapter for both doors in: a pod tool call, which brings datastore services
built under the agent's delegated authority, and a request, which brings the
person's own context. Either way every read here is the caller's -- the
datastore authorizes each table and applies row security, and the platform
counts read only what the caller may read (``scorecard_counts``,
``count_standing_work``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.scorecard import (
    QUESTION_PATIENCE,
    SCORECARD_TABLE,
    Counted,
    CounterName,
    ScorecardPage,
    ScoringWindow,
)
from app.modules.agent.domain.scorecard_work import UnitTable
from app.modules.agent.infrastructure.scorecard_counts import (
    tally_approvals,
    tally_open_questions,
)
from app.modules.datastore.contracts import (
    DatastoreTableNotFoundError,
    TableContext,
)
from app.modules.datastore.contracts.agent_tools import (
    RecordService,
    TableService,
    build_record_service,
    build_table_service,
)
from app.modules.schedule.contracts.standing_work import count_standing_work


class DatastoreScorecardSource:
    """``ScorecardSource`` over the datastore services, as one caller."""

    def __init__(
        self,
        *,
        tables: TableService,
        records: RecordService,
        session: AsyncSession,
        ctx: Context,
        pod_id: UUID,
        user_id: UUID,
    ) -> None:
        self._tables = tables
        self._records = records
        self._session = session
        self._ctx = ctx
        self._pod_id = pod_id
        self._user_id = user_id

    @classmethod
    def for_unit_of_work(
        cls,
        uow: SqlAlchemyUnitOfWork,
        *,
        ctx: Context,
        pod_id: UUID,
        user_id: UUID,
    ) -> DatastoreScorecardSource:
        return cls(
            tables=build_table_service(uow),
            records=build_record_service(uow),
            session=uow.session,
            ctx=ctx,
            pod_id=pod_id,
            user_id=user_id,
        )

    async def scorecard_rows(self, limit: int) -> ScorecardPage | None:
        try:
            table = await self._tables.get_table(
                self._pod_id, SCORECARD_TABLE, self._ctx
            )
        except DatastoreTableNotFoundError:
            return None
        schema = self._tables.schema_manager.get_schema_name(self._pod_id)
        records, total = await self._records.list_records(
            TableContext.from_table_entity(table, schema), self._user_id, limit=limit
        )
        return ScorecardPage(rows=[record.data for record in records], total=total)

    async def unit_table(self, name: str) -> UnitTable | None:
        try:
            table = await self._tables.get_table(self._pod_id, name, self._ctx)
        except DatastoreTableNotFoundError:
            return None
        return UnitTable(
            name=table.table_name,
            primary_key=table.primary_key_column,
            columns={column.name: column.type.value for column in table.columns},
        )

    async def query(self, sql: str) -> Sequence[Mapping[str, object]]:
        rows, _, _ = await self._records.execute_readonly_query(
            pod_id=self._pod_id,
            query=sql,
            user_id=self._user_id,
            table_service=self._tables,
            ctx=self._ctx,
        )
        return rows

    async def count_platform(
        self, counter: CounterName, window: ScoringWindow
    ) -> Counted:
        if counter is CounterName.APPROVALS:
            approvals = await tally_approvals(
                self._session,
                pod_id=self._pod_id,
                user_id=self._user_id,
                start=window.start_at,
                end=window.end_at,
            )
            return Counted(counted=approvals.approved, total=approvals.decided)
        if counter is CounterName.OPEN_QUESTIONS:
            questions = await tally_open_questions(
                self._session,
                pod_id=self._pod_id,
                user_id=self._user_id,
                start=window.start_at,
                end=window.end_at,
                patience=QUESTION_PATIENCE,
            )
            return Counted(counted=questions.still_waiting, total=questions.asked)
        if counter is CounterName.STANDING_WORK:
            runs = await count_standing_work(
                session=self._session,
                pod_id=self._pod_id,
                ctx=self._ctx,
                start=window.start_at,
                end=window.end_at,
            )
            return Counted(counted=runs.on_time, total=runs.due)
        raise ValueError(f"`{counter}` is not counted by the platform.")


__all__ = ["DatastoreScorecardSource"]
