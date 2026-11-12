"""Checking the measure of what a pod owns, for the modules that set one.

A pod's outcome lives on its config, in ``pod``; its measure is a read-only
query over the pod's tables, which only ``datastore`` can run. Whoever sets a
measure -- a person through the API, the pod's agent through its tool, or a
Lemma being started from a template -- has it checked here before it is kept:
that it is one read-only query, that the person setting it may read every row
it counts, and that it answers one number.

Every row, because the measure is read for the whole pod at once: everyone in
the pod sees the same number and the same weeks behind it, including members
who cannot open some of the rows it counts. The check is what keeps that from
being a way to read rows out a number at a time: nobody can define a count over
rows they could not read themselves.

The check runs once, so what it covered is kept with the measure: the tables
with row security it was checked against. Row security turned on for a table
after that is one the check never covered, and the measure is not read again
until somebody who administers that table sets it again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Collection
from typing import Protocol
from uuid import UUID

from app.core.authorization.context import Context


@dataclass(frozen=True, slots=True)
class MeasureReading:
    """What a measure answered, and when."""

    #: ``None`` when it answered null: nothing to count yet, such as a share of
    #: a week with no rows in it.
    value: float | None
    measured_at: datetime


@dataclass(frozen=True, slots=True)
class CheckedMeasure:
    """What a measure answered when it was checked, and what the check covered."""

    reading: MeasureReading
    #: The tables it counts that had row security when it was checked, by id:
    #: the person setting it administers each of them.
    row_security_checked: frozenset[UUID]


class OutcomeMeasures(Protocol):
    """Checks a measure for the person about to set it."""

    async def check(self, *, pod_id: UUID, sql: str, ctx: Context) -> CheckedMeasure:
        """Read ``sql`` once and answer what it counts, or refuse it.

        Refused with the datastore's own errors, each carrying its status: a
        statement that is not one read-only query (400), a table or row
        ``ctx`` may not read in full (403), a table that is not there (404),
        and an answer that is not one number (422).
        """
        ...

    async def unchecked(
        self, *, pod_id: UUID, sql: str, checked: Collection[UUID]
    ) -> frozenset[UUID]:
        """The tables ``sql`` counts that have row security now and are not in
        ``checked``: what a check has to cover before it is read again."""
        ...


__all__ = ["CheckedMeasure", "MeasureReading", "OutcomeMeasures"]
