"""A ``ScorecardSource`` with nothing behind it but what a test hands it.

Type-checked against the port, so a change to what counting reads fails here
first. The statements it is asked to run are kept, so a test can say what ran
-- and, as often, that nothing did.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.core.domain.errors import DomainError
from app.modules.agent.domain.ports import ScorecardSource
from app.modules.agent.domain.scorecard import (
    Counted,
    CounterName,
    ScorecardPage,
    ScorecardRow,
    ScoringWindow,
)
from app.modules.agent.domain.scorecard_work import UnitTable

QueryRows = list[dict[str, object]]


@dataclass
class FakeScorecardSource:
    #: The scorecard's rows; ``None`` for a pod without one.
    rows: list[ScorecardRow] | None = field(default_factory=list)
    tables: dict[str, UnitTable] = field(default_factory=dict)
    #: Answers by the ISO start date a statement's window carries; ``default``
    #: for any statement no key appears in.
    answers: dict[str, QueryRows] = field(default_factory=dict)
    default: QueryRows = field(default_factory=lambda: [{"counted": 0, "total": 0}])
    #: Raised by ``query``, as the datastore raises a refusal.
    refusal: DomainError | None = None
    #: Tables the caller may not read.
    forbidden: set[str] = field(default_factory=set)
    platform: dict[CounterName, Counted] = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    platform_counts: list[tuple[CounterName, ScoringWindow]] = field(
        default_factory=list
    )

    async def scorecard_rows(self, limit: int) -> ScorecardPage | None:
        if self.rows is None:
            return None
        return ScorecardPage(rows=self.rows[:limit], total=len(self.rows))

    async def unit_table(self, name: str) -> UnitTable | None:
        if name in self.forbidden:
            raise DomainError(
                f"Not allowed to read table '{name}'", code="FORBIDDEN", status_code=403
            )
        return self.tables.get(name)

    async def query(self, sql: str) -> Sequence[Mapping[str, object]]:
        self.queries.append(sql)
        if self.refusal is not None:
            raise self.refusal
        for start, rows in self.answers.items():
            if f"'{start}" in sql:
                return rows
        return self.default

    async def count_platform(
        self, counter: CounterName, window: ScoringWindow
    ) -> Counted:
        self.platform_counts.append((counter, window))
        return self.platform.get(counter, Counted(counted=0, total=0))


def _conforms(source: FakeScorecardSource) -> ScorecardSource:
    """Fails the type check, not a test run, when the port moves."""
    return source
