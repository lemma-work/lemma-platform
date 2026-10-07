"""What a ``work`` measure counts, as SQL that may run.

A work measure names a table with one row per unit of the teammate's work, the
column that places each row in a week, a test over one row, and -- for a
median or a total -- a value. Every part of it is text somebody wrote into the scorecard,
so every part is checked here before any of it reaches a statement.

These checks are the first of two lines, not the only one. The statement still
runs through the datastore's read-only query path as the caller, which parses
it, refuses anything but a single read, authorizes every table it names and
applies row security. What this file adds is that a measure which could not
mean what it says is refused with a reason a person or a model can act on, and
that the text spliced into a statement can only ever be one expression over
one row: no second statement, no subquery, no comment hiding the rest.

Pure, like the rest of the scorecard's domain: the table a measure names is
handed in as a :class:`UnitTable`, already read.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from app.modules.agent.domain.scorecard import QueryRejected, Shape, WorkFields

#: A table or column a measure may name: the form the datastore's own names
#: take, lower case, so the quoted name in the statement is the name the
#: datastore authorizes.
IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}")

#: Long enough for any test a person would write about one row.
MAX_EXPRESSION_LENGTH = 1_000

#: The most rows of one week a measure's number opens to.
MAX_UNIT_ROWS = 200

#: Text that would let an expression end the statement, hide what follows it,
#: or quote past the end of itself: a statement separator, both comment forms,
#: braces (never SQL here, and the scorecard's one template syntax), dollar
#: quoting, and the backslash an escape string reads as a quote.
_REFUSED_TOKENS = (";", "--", "/*", "*/", "{", "}", "$", "\\")

#: Words that only appear in an expression as the start of something that is
#: not one: a subquery, a write, a schema change. Matched anywhere, string
#: literals included -- a test that compares against the word "update" is
#: refused, and that is the price of a check simple enough to trust.
_REFUSED_WORDS = re.compile(
    r"\b(select|insert|update|delete|drop|alter|create|grant|union|with|into)\b",
    re.IGNORECASE,
)

#: Where a unit's name is looked for, in order, when the measure names none.
LABEL_COLUMNS = ("title", "name", "what", "problem", "customer")
LINK_COLUMN = "link"

_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class TimeKind(StrEnum):
    """The datastore column types a row can be placed in a week by."""

    DATE = "DATE"
    DATETIME = "DATETIME"


@dataclass(frozen=True, slots=True)
class UnitTable:
    """The table a work measure counts, as the datastore describes it."""

    name: str
    primary_key: str
    #: Column name to datastore type ("DATE", "TEXT", ...).
    columns: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class UnitRow:
    """One unit of work behind a number, as a person opening it sees it."""

    id: str
    label: str | None
    link: str | None
    #: The time column's value, as an ISO date or timestamp.
    at: str | None
    #: Whether the number counted this row.
    passed: bool | None


def _shown_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def unit_row(row: Mapping[str, object]) -> UnitRow:
    """Read one row of :func:`work_rows_sql`'s result."""
    passed = row.get("passed")
    return UnitRow(
        id=_shown_text(row.get("id")) or "",
        label=_shown_text(row.get("label")),
        link=_shown_text(row.get("link")),
        at=_shown_text(row.get("at")),
        passed=passed if isinstance(passed, bool) else None,
    )


@dataclass(frozen=True, slots=True)
class WorkSpec:
    """A work measure that passed every check, ready to become SQL."""

    table: str
    id_column: str
    time_column: str
    time_kind: TimeKind
    shape: Shape
    test: str | None
    value: str | None
    label_column: str | None
    link_column: str | None


def check_identifier(field: str, name: str | None) -> str:
    """A table or column name the measure gives, or why it cannot be one."""
    text = (name or "").strip()
    if not text:
        raise QueryRejected(f"A work measure needs a `{field}`.")
    if IDENTIFIER.fullmatch(text) is None:
        raise QueryRejected(
            f"`{field}` must be a lower-case table or column name (letters, "
            f"digits and underscores); `{text}` is not one."
        )
    return text


def _check_balanced(field: str, text: str) -> None:
    """Parentheses that close, outside quotes that close.

    The expression is spliced inside parentheses of the statement's own; one
    that closed more than it opened would continue the statement in its place.
    A doubled quote inside a literal (``'it''s'``) closes and reopens it, which
    this reads correctly without treating it specially.
    """
    depth = 0
    quote: str | None = None
    for char in text:
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                break
    if quote is not None or depth != 0:
        raise QueryRejected(
            f"`{field}` must be one expression: its parentheses and quotes do "
            "not close."
        )


def check_expression(field: str, text: str | None) -> str | None:
    """A test or value as one SQL expression over a row, or why it is not one.

    ``None`` when the measure gives none; whether it needed one is the
    caller's question.
    """
    body = (text or "").strip()
    if not body:
        return None
    if len(body) > MAX_EXPRESSION_LENGTH:
        raise QueryRejected(
            f"`{field}` is longer than {MAX_EXPRESSION_LENGTH} characters; a "
            "test over one row does not need to be."
        )
    for token in _REFUSED_TOKENS:
        if token in body:
            raise QueryRejected(
                f"`{field}` may not contain `{token}`: it must be one "
                "expression over a row."
            )
    word = _REFUSED_WORDS.search(body)
    if word is not None:
        raise QueryRejected(
            f"`{field}` may not use `{word.group(0).lower()}`: it must be one "
            "expression over a row, with no subquery and nothing that writes."
        )
    _check_balanced(field, body)
    return body


def _column(table: UnitTable, field: str, name: str | None) -> str:
    column = check_identifier(field, name)
    if column not in table.columns:
        raise QueryRejected(
            f"`{field}` names `{column}`, which is not a column of "
            f"`{table.name}`. Its columns are: {', '.join(sorted(table.columns))}."
        )
    return column


def _time_kind(table: UnitTable, column: str) -> TimeKind:
    kind = table.columns[column].upper()
    if kind not in {member.value for member in TimeKind}:
        raise QueryRejected(
            f"`time_column` `{column}` is {kind}; it must be a DATE or "
            "DATETIME column, the day or moment that places a row in a week."
        )
    return TimeKind(kind)


def _optional_column(
    table: UnitTable, field: str, named: str | None, fallbacks: tuple[str, ...]
) -> str | None:
    if named and named.strip():
        return _column(table, field, named)
    return next((name for name in fallbacks if name in table.columns), None)


def _expressions(fields: WorkFields, shape: Shape) -> tuple[str | None, str | None]:
    test = check_expression("test", fields.test)
    if shape is Shape.SHARE and test is None:
        raise QueryRejected(
            "A share needs a `test`: the rows that pass it are counted out of "
            "every row in the week."
        )
    if shape not in _VALUED:
        return test, None
    value = check_expression("value", fields.value)
    if value is None:
        raise QueryRejected(
            f"A {shape.value} needs a `value`: the number taken from each row, "
            "such as `impressions`, or `extract(epoch from replied_at - "
            "asked_at) / 3600` for hours."
        )
    return test, value


def work_spec(fields: WorkFields, shape: Shape, table: UnitTable) -> WorkSpec:
    """Check a work measure against the table it names, or say why it fails.

    Every name must be a real column of that table, and the time column a DATE
    or DATETIME. The label and link columns are optional: without them the
    first of ``LABEL_COLUMNS`` and ``LINK_COLUMN`` the table has stand in.
    """
    time_column = _column(table, "time_column", fields.time_column)
    test, value = _expressions(fields, shape)
    return WorkSpec(
        table=table.name,
        id_column=table.primary_key,
        time_column=time_column,
        time_kind=_time_kind(table, time_column),
        shape=shape,
        test=test,
        value=value,
        label_column=_optional_column(
            table, "label_column", fields.label_column, LABEL_COLUMNS
        ),
        link_column=_optional_column(
            table, "link_column", fields.link_column, (LINK_COLUMN,)
        ),
    )


# --- SQL ------------------------------------------------------------------------


def _quoted(name: str) -> str:
    """A name as a quoted identifier. Doubling the quote makes this safe for
    any name, though every name the measure gives is checked to need none."""
    return '"' + name.replace('"', '""') + '"'


def _bound(day: date, kind: TimeKind) -> str:
    """One end of the window, as a literal of the time column's own type.

    A whole day, written as an ISO date the code made, never text from the
    row: a ``datetime`` is refused because it would carry a time of day the
    window does not have. A DATETIME column is compared at UTC midnight with
    the zone written out, so the bound means one instant whatever the session's
    zone.
    """
    if isinstance(day, datetime):
        raise QueryRejected("A window ends on a whole day, not at a time of day.")
    text = day.isoformat()
    if _ISO_DAY.fullmatch(text) is None:
        raise QueryRejected(f"`{text}` is not a day a window can end on.")
    if kind is TimeKind.DATE:
        return f"'{text}'::date"
    return f"'{text}T00:00:00+00:00'::timestamptz"


def _in_window(spec: WorkSpec, start: date, end: date) -> str:
    column = _quoted(spec.time_column)
    return (
        f"{column} >= {_bound(start, spec.time_kind)} "
        f"AND {column} < {_bound(end, spec.time_kind)}"
    )


def _passing(test: str | None) -> str:
    return f" FILTER (WHERE ({test}))" if test else ""


#: The shapes whose number is taken from each row's ``value``.
_VALUED = frozenset({Shape.MEDIAN, Shape.TOTAL})


def _row_value(spec: WorkSpec) -> str:
    return f"(({spec.value}))::double precision"


def work_count_sql(spec: WorkSpec, start: date, end: date) -> str:
    """One row, ``counted`` and ``total``, for the rows placed in ``[start, end)``.

    A share or a count: ``counted`` is the rows passing the test (every row,
    for a count with no test) and ``total`` every row. A median: ``counted`` is
    the median value over the rows passing the test, and ``total`` how many
    values it was taken over -- a row whose value is null is in neither. A
    total: ``counted`` is those values added up, zero when there are none, and
    ``total`` again how many there were.
    """
    source = f"FROM {_quoted(spec.table)} WHERE {_in_window(spec, start, end)}"
    if spec.shape in _VALUED:
        value = _row_value(spec)
        passing = _passing(spec.test)
        number = (
            f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {value}){passing}"
            if spec.shape is Shape.MEDIAN
            else f"coalesce(sum({value}){passing}, 0)"
        )
        return f"SELECT {number} AS counted, count({value}){passing} AS total {source}"
    return (
        f"SELECT count(*){_passing(spec.test)} AS counted, count(*) AS total {source}"
    )


def _passed(spec: WorkSpec) -> str:
    """Whether a row is one the number counted. A null test is a fail."""
    test = f"coalesce(({spec.test}), false)" if spec.test else None
    if spec.shape not in _VALUED:
        return test or "true"
    has_value = f"{_row_value(spec)} IS NOT NULL"
    return f"{test} AND {has_value}" if test else has_value


def _text_column(name: str | None, alias: str) -> str:
    if name is None:
        return f"NULL::text AS {_quoted(alias)}"
    return f"({_quoted(name)})::text AS {_quoted(alias)}"


def work_rows_sql(spec: WorkSpec, start: date, end: date, limit: int) -> str:
    """The rows behind :func:`work_count_sql`'s number: ``id``, ``label``,
    ``link``, ``at`` and ``passed``, one per unit placed in ``[start, end)``.

    At most ``limit + 1`` rows, so the caller can tell a list that was cut from
    one that was not. A share lists its misses first and a count or a median
    its counted rows first: those are the rows the number is about.
    """
    if not 1 <= limit <= MAX_UNIT_ROWS:
        raise QueryRejected(f"`limit` must be between 1 and {MAX_UNIT_ROWS}.")
    first = "ASC" if spec.shape is Shape.SHARE else "DESC"
    columns = ", ".join(
        (
            _text_column(spec.id_column, "id"),
            _text_column(spec.label_column, "label"),
            _text_column(spec.link_column, "link"),
            f'{_quoted(spec.time_column)} AS "at"',
            f'{_passed(spec)} AS "passed"',
        )
    )
    return (
        f"SELECT {columns} FROM {_quoted(spec.table)} "
        f"WHERE {_in_window(spec, start, end)} "
        f'ORDER BY "passed" {first}, "at", "id" LIMIT {limit + 1}'
    )
