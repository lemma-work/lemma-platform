"""Converting catalog rows to entities without letting one bad row fail a list.

A catalog row can outlive the enum value it was written with: the ``package``
connector kind was removed while rows carrying it were still in the database,
and one such row made every list that included it raise -- a 500 on the whole
connector page for a row nobody could act on. List methods skip those rows and
say so; single-row getters keep raising, because a caller asking for exactly
that row should hear that it is unreadable.
"""

from collections.abc import Awaitable, Callable, Iterable
from typing import TypeVar

from pydantic import ValidationError

from app.core.bounded import BoundedSet
from app.core.log.log import get_logger

logger = get_logger(__name__)

E = TypeVar("E")
R = TypeVar("R")

# One warning per (table, kind) per process: the same stale rows are read on
# every list request, and a line each time would be a flood.
_REPORTED = BoundedSet[tuple[str, str]](
    256, name="connectors.catalog_row_invalid_reported"
)


def _report_invalid(row: object, error: ValidationError) -> None:
    table = str(getattr(row, "__tablename__", type(row).__name__))
    kind = str(getattr(row, "kind", None))
    key = (table, kind)
    if key in _REPORTED:
        return
    _REPORTED.add(key)
    logger.warning(
        "connectors.catalog_row.invalid.skipped",
        table=table,
        row_id=str(getattr(row, "id", None)),
        kind=kind,
        # Locations and error types only. The error's text quotes the input,
        # and an auth-config row's input can be credential material.
        invalid_fields=",".join(
            f"{'.'.join(map(str, detail['loc']))}:{detail['type']}"
            for detail in error.errors(include_input=False, include_url=False)
        ),
    )


def convert_valid_rows(rows: Iterable[R], to_entity: Callable[[R], E]) -> list[E]:
    """``to_entity`` applied to each row, skipping rows that no longer validate."""
    entities: list[E] = []
    for row in rows:
        try:
            entities.append(to_entity(row))
        except ValidationError as error:
            _report_invalid(row, error)
    return entities


async def aconvert_valid_rows(
    rows: Iterable[R], to_entity: Callable[[R], Awaitable[E]]
) -> list[E]:
    """The async counterpart, for repositories whose conversion awaits."""
    entities: list[E] = []
    for row in rows:
        try:
            entities.append(await to_entity(row))
        except ValidationError as error:
            _report_invalid(row, error)
    return entities
