"""The measure of what a pod owns: checked when it is set, read for everyone.

A measure is one read-only query over the pod's tables that answers one
number. It runs through the same read-only path as any ad-hoc query -- a
read-only transaction, the statement timeout, the plan cost guard and the
query role that row security applies to -- with one difference: it reads
every row. Everyone in the pod sees the same number and the same weeks behind
it, and the weekly reading has nobody to read as.

So the person is checked when the measure is set, not when it is read. They
must be able to read every table it names, and administer every table with
row security, whose rows they would otherwise see only their own of. A
measure nobody could have read in full is never kept, and a count over rows a
member cannot open shows them that count and nothing more.

Row security can be turned on for a table after a measure counting it is set,
and the check that stood for it never covered that. So the check answers the
tables with row security it covered, the pod keeps them with the measure, and
a reading refuses a table with row security that is not among them: no number,
until someone who administers that table sets the measure again.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authorization.context import Context
from app.core.log.log import get_logger
from app.core.ports.outcome_measures import CheckedMeasure, MeasureReading
from app.modules.datastore.domain.datastore_entities import DatastoreTableEntity
from app.modules.datastore.domain.errors import (
    DatastoreAccessDeniedError,
    DatastoreDomainError,
    DatastoreMeasureError,
    DatastoreMeasureUncheckedError,
    DatastoreQueryError,
    DatastoreTableNotFoundError,
)
from app.modules.datastore.domain.ports import (
    DatastoreRecordRepositoryPort,
    DatastoreTableRepositoryPort,
)
from app.modules.datastore.domain.row_security import RowPrincipal
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.sql_introspection import analyze_query
from app.modules.datastore.services.table_context import TableHydration
from app.modules.datastore.services.table_service import TableService

if TYPE_CHECKING:
    from sqlglot import exp

logger = get_logger(__name__)

type Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def measure_digest(sql: str) -> str:
    """Which measure answered, as ``PodOutcomeService.same_query`` tells one
    measure from another: any edit is a new one."""
    return hashlib.sha256(sql.strip().encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class MeasureResult:
    """A reading, or why there is none, said for the people in the pod."""

    reading: MeasureReading | None
    problem: str | None = None


class OutcomeMeasureService:
    """Checks a measure for the person setting it, and reads it for everyone."""

    def __init__(
        self,
        *,
        tables: TableService,
        table_repository: DatastoreTableRepositoryPort,
        records: DatastoreRecordRepositoryPort,
        authz: DatastoreAuthorization,
        clock: Clock = _utcnow,
    ) -> None:
        self._tables = tables
        self._table_repository = table_repository
        self._records = records
        self._authz = authz
        self._clock = clock

    async def check(self, *, pod_id: UUID, sql: str, ctx: Context) -> CheckedMeasure:
        """Read ``sql`` once for ``ctx``, who is about to set it, or refuse it.

        Answers the tables with row security it covered, which the pod keeps
        with the measure for ``read`` to hold it to.
        """
        names = tables_counted(sql)
        tables = await self._tables.get_tables(pod_id, sorted(names), ctx)
        covered: set[UUID] = set()
        for name in sorted(tables):
            table = tables[name]
            if not table.enable_rls:
                continue
            covered.add(table.id)
            if not await self._authz.can_admin_table(
                pod_id=pod_id,
                table_id=table.id,
                ctx=ctx,
                hydration=TableHydration.of(table),
            ):
                raise DatastoreAccessDeniedError(
                    f"The measure counts every row of {name} for everyone in the "
                    f"pod, and you can read only your own rows there. Someone "
                    f"who administers {name} can set it."
                )
        return CheckedMeasure(
            reading=await self._read(pod_id, sql),
            row_security_checked=frozenset(covered),
        )

    async def unchecked(
        self, *, pod_id: UUID, sql: str, checked: Collection[UUID]
    ) -> frozenset[UUID]:
        """The tables ``sql`` counts with row security now that ``checked``
        does not cover. Nothing for a measure that no longer resolves: reading
        it says what is wrong, and a new check would only say it again."""
        try:
            found = await self._counted(pod_id, sql)
        except DatastoreDomainError:
            return frozenset()
        return frozenset(table.id for table in _unchecked(found, checked))

    async def refusal(
        self, *, pod_id: UUID, sql: str, checked: Collection[UUID]
    ) -> MeasureResult | None:
        """Why the measure may not be read for everyone now, or ``None``.

        Cheaper than ``read`` -- it looks at the tables, not their rows -- so
        a cached number is never served past the moment row security is
        turned on under it.
        """
        try:
            found = await self._counted(pod_id, sql)
        except DatastoreDomainError:
            return None  # `read` says what is wrong with it
        unchecked = _unchecked(found, checked)
        if not unchecked:
            return None
        return MeasureResult(
            reading=None,
            problem=_problem(DatastoreMeasureUncheckedError(unchecked[0].name)),
        )

    async def read(
        self, *, pod_id: UUID, sql: str, checked: Collection[UUID]
    ) -> MeasureResult:
        """What the measure answers now, for everyone in the pod.

        ``checked`` is what the check that let it be set covered: a table it
        counts with row security outside it is refused, unread.

        Never raises for the measure's own sake: a table dropped since it was
        set, row security turned on since, a query that now takes too long, an
        answer that is no longer one number -- each comes back as a
        ``problem`` the page can say, so one broken measure costs its pod a
        number and nothing else.
        """
        try:
            names = tables_counted(sql)
            found = await self._counted(pod_id, sql)
            missing = sorted(names - found.keys())
            if missing:
                raise DatastoreTableNotFoundError(f"Table '{missing[0]}' not found")
            unchecked = _unchecked(found, checked)
            if unchecked:
                raise DatastoreMeasureUncheckedError(unchecked[0].name)
            return MeasureResult(reading=await self._read(pod_id, sql))
        except DatastoreDomainError as exc:
            logger.warning(
                "datastore.outcome_measure.read.degraded",
                pod_id=str(pod_id),
                code=exc.code,
                exc_info=True,
            )
            return MeasureResult(reading=None, problem=_problem(exc))

    async def _counted(self, pod_id: UUID, sql: str) -> dict[str, DatastoreTableEntity]:
        names = tables_counted(sql)
        return await self._table_repository.get_many_by_datastore_and_names(
            pod_id, sorted(names)
        )

    async def _read(self, pod_id: UUID, sql: str) -> MeasureReading:
        rows, _count, _truncated = await self._records.execute_readonly_query(
            pod_id, sql, RowPrincipal.every_row()
        )
        return MeasureReading(value=the_number(rows), measured_at=self._clock())


def _unchecked(
    found: Mapping[str, DatastoreTableEntity], checked: Collection[UUID]
) -> list[DatastoreTableEntity]:
    """Tables with row security, by name, that no check of the measure covered."""
    covered = set(checked)
    return [
        found[name]
        for name in sorted(found)
        if found[name].enable_rls and found[name].id not in covered
    ]


def tables_counted(sql: str) -> frozenset[str]:
    """Every table the measure reads, as the database will resolve them.

    A measure is read with every row in view, so the names are resolved scope
    by scope -- a CTE stands for its own name only inside the query it is
    defined in -- and added to what ``analyze_query`` names. A statement whose
    scopes cannot be resolved is not a measure.
    """
    from sqlglot import exp, parse_one
    from sqlglot.errors import SqlglotError
    from sqlglot.optimizer.scope import traverse_scope

    names = set(analyze_query(sql).tables)
    try:
        scopes = traverse_scope(parse_one(sql, dialect="postgres"))
    except SqlglotError as exc:
        raise DatastoreQueryError(
            "This query can't be read as a measure: its tables could not be resolved."
        ) from exc
    for scope in scopes:
        for source in scope.sources.values():
            if isinstance(source, exp.Table) and source.name:
                names.add(_as_resolved(source))
    if not names:
        raise DatastoreQueryError(
            "A measure counts rows of the pod's own tables, and this one names none."
        )
    return frozenset(names)


def _as_resolved(table: exp.Table) -> str:
    """The name PostgreSQL reads: an unquoted identifier folds to lower case."""
    from sqlglot import exp

    identifier = table.this
    if isinstance(identifier, exp.Identifier) and not identifier.quoted:
        return table.name.lower()
    return table.name


def the_number(rows: Sequence[Mapping[str, object]]) -> float | None:
    """The one number a measure answered: one row, one column, numeric or null."""
    if len(rows) != 1:
        raise DatastoreMeasureError(
            f"The measure answered {len(rows)} rows; it must answer one number."
        )
    (row,) = rows
    if len(row) != 1:
        raise DatastoreMeasureError(
            f"The measure answered {len(row)} columns; it must answer one number."
        )
    (value,) = row.values()
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise DatastoreMeasureError(
            "The measure answered something that is not a number; it must "
            "answer one number."
        )
    number = float(value)
    if not math.isfinite(number):
        raise DatastoreMeasureError("The measure answered a number with no value.")
    return number


#: What the people in the pod are told, by error code. Never the error's own
#: text: it can carry the database's words, which name nothing they can fix.
_PROBLEMS: dict[str, str] = {
    "DATASTORE_TABLE_NOT_FOUND": "The measure counts a table that is not here any more.",
    "DATASTORE_QUERY_ERROR": "The measure can't be read as it is written now.",
    "DATASTORE_QUERY_UNAVAILABLE": "Queries can't run on this server, so the measure can't be read.",
}


def _problem(exc: DatastoreDomainError) -> str:
    if isinstance(exc, DatastoreMeasureError | DatastoreMeasureUncheckedError):
        return exc.message
    return _PROBLEMS.get(exc.code, "The measure could not be read just now.")


__all__ = [
    "MeasureResult",
    "OutcomeMeasureService",
    "measure_digest",
    "tables_counted",
    "the_number",
]
