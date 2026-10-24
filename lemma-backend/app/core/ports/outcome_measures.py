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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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


class OutcomeMeasures(Protocol):
    """Checks a measure for the person about to set it."""

    async def check(self, *, pod_id: UUID, sql: str, ctx: Context) -> MeasureReading:
        """Read ``sql`` once and answer what it counts, or refuse it.

        Refused with the datastore's own errors, each carrying its status: a
        statement that is not one read-only query (400), a table or row
        ``ctx`` may not read in full (403), a table that is not there (404),
        and an answer that is not one number (422).
        """
        ...


__all__ = ["MeasureReading", "OutcomeMeasures"]
