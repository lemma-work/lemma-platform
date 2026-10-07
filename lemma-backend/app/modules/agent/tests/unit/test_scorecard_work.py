"""A work measure's guards and the SQL it becomes, pinned without a database.

The guards are tested one refused thing at a time, because each is a way text
in a scorecard row could stop being one expression over one row. The SQL is
tested as exact strings -- a reader can see what runs -- and then handed to the
datastore's own query parser, which is what will judge it in production.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.modules.agent.domain.scorecard import QueryRejected, Shape, WorkFields
from app.modules.agent.domain.scorecard_work import (
    MAX_EXPRESSION_LENGTH,
    MAX_UNIT_ROWS,
    TimeKind,
    UnitRow,
    UnitTable,
    WorkSpec,
    check_expression,
    check_identifier,
    unit_row,
    work_count_sql,
    work_rows_sql,
    work_spec,
)
from app.modules.datastore.services.sql_introspection import analyze_query

pytestmark = pytest.mark.unit

START = date(2026, 10, 5)
END = date(2026, 10, 12)

COMMITMENTS = UnitTable(
    name="commitments",
    primary_key="id",
    columns={
        "id": "UUID",
        "what": "TEXT",
        "due": "DATE",
        "status": "TEXT",
        "source": "TEXT",
        "followed_up_at": "DATETIME",
        "created_at": "DATETIME",
    },
)

CONVERSATIONS = UnitTable(
    name="conversations",
    primary_key="id",
    columns={
        "id": "UUID",
        "link": "TEXT",
        "customer": "TEXT",
        "started_at": "DATETIME",
        "first_reply_at": "DATETIME",
    },
)


def _fields(**overrides: str | None) -> WorkFields:
    fields: dict[str, str | None] = {
        "unit_table": "commitments",
        "time_column": "due",
        "test": "status = 'done'",
    }
    fields.update(overrides)
    return WorkFields(**fields)


# --- Names --------------------------------------------------------------------


@pytest.mark.parametrize("name", ["commitments", "_x", "a1_b2", "x" * 63])
def test_check_identifier_accepts_a_datastore_name(name):
    assert check_identifier("unit_table", f"  {name} ") == name


@pytest.mark.parametrize(
    "name",
    [
        "Commitments",  # quoted as written, it would name a different table
        "1st",
        "due date",
        "due-date",
        'due"; drop',
        "public.commitments",
        "x" * 64,
    ],
)
def test_check_identifier_refuses_anything_but_a_lower_case_name(name):
    with pytest.raises(QueryRejected, match="lower-case table or column name"):
        check_identifier("time_column", name)


@pytest.mark.parametrize("name", [None, "", "   "])
def test_check_identifier_says_a_missing_name_is_needed(name):
    with pytest.raises(QueryRejected, match="needs a `unit_table`"):
        check_identifier("unit_table", name)


# --- Expressions ----------------------------------------------------------------


@pytest.mark.parametrize("token", [";", "--", "/*", "*/", "{", "}", "$", "\\"])
def test_check_expression_refuses_each_token_that_ends_or_hides_a_statement(token):
    with pytest.raises(QueryRejected, match="may not contain"):
        check_expression("test", f"status = 'done' {token} true")


@pytest.mark.parametrize(
    "word",
    [
        "select",
        "SELECT",
        "insert",
        "update",
        "Delete",
        "drop",
        "alter",
        "create",
        "grant",
        "union",
        "with",
        "into",
    ],
)
def test_check_expression_refuses_each_word_that_starts_something_else(word):
    with pytest.raises(QueryRejected, match=f"may not use `{word.lower()}`"):
        check_expression("test", f"exists ({word} 1)")


@pytest.mark.parametrize(
    "text",
    [
        "deleted_at IS NULL",
        "selected AND withdrawn_at IS NULL",
        "note = ')'",
        "name = 'it''s (fine'",
        "\"by\" = 'kit'",
        "coalesce(done, false)",
        "first_reply_at - started_at <= interval '1 hour'",
    ],
)
def test_check_expression_accepts_one_expression_over_a_row(text):
    assert check_expression("test", f" {text} ") == text


@pytest.mark.parametrize(
    "text",
    [
        "true) OR (true",
        "(status = 'done'",
        "status = 'done')",
        "status = 'done",
        'status = "done',
    ],
)
def test_check_expression_refuses_parentheses_or_quotes_that_do_not_close(text):
    with pytest.raises(QueryRejected, match="do not close"):
        check_expression("test", text)


def test_check_expression_refuses_text_longer_than_a_row_test_needs():
    with pytest.raises(QueryRejected, match="longer than"):
        check_expression("value", "1 + " * MAX_EXPRESSION_LENGTH)


@pytest.mark.parametrize("text", [None, "", "  "])
def test_check_expression_reads_nothing_as_none(text):
    assert check_expression("test", text) is None


# --- Checking a measure against its table -----------------------------------------


def test_work_spec_resolves_a_share_against_its_table():
    spec = work_spec(_fields(), Shape.SHARE, COMMITMENTS)

    assert spec == WorkSpec(
        table="commitments",
        id_column="id",
        time_column="due",
        time_kind=TimeKind.DATE,
        shape=Shape.SHARE,
        test="status = 'done'",
        value=None,
        # `what` is the first of the label columns the table has; it has no
        # `link`, so a row opens to nothing.
        label_column="what",
        link_column=None,
    )


def test_work_spec_uses_the_label_and_link_columns_it_is_given():
    spec = work_spec(
        _fields(label_column="status", link_column="source"),
        Shape.COUNT,
        COMMITMENTS,
    )

    assert (spec.label_column, spec.link_column) == ("status", "source")


def test_work_spec_finds_a_link_column_by_its_name():
    spec = work_spec(
        _fields(unit_table="conversations", time_column="started_at", test=None),
        Shape.COUNT,
        CONVERSATIONS,
    )

    assert (spec.label_column, spec.link_column) == ("customer", "link")
    assert spec.time_kind is TimeKind.DATETIME
    assert spec.test is None


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"time_column": "deadline"}, "`deadline`, which is not a column of"),
        ({"time_column": "status"}, "is TEXT; it must be a DATE or DATETIME"),
        ({"time_column": None}, "needs a `time_column`"),
        ({"label_column": "title"}, "`title`, which is not a column of"),
        ({"link_column": "Source"}, "lower-case table or column name"),
        ({"test": None}, "A share needs a `test`"),
        ({"test": "status = 'done'; drop table x"}, "may not contain `;`"),
        ({"test": "id in (select id from secrets)"}, "may not use `select`"),
    ],
)
def test_work_spec_refuses_a_share_it_cannot_run_and_says_why(overrides, reason):
    with pytest.raises(QueryRejected) as refused:
        work_spec(_fields(**overrides), Shape.SHARE, COMMITMENTS)

    assert reason in str(refused.value)


def test_work_spec_lists_the_tables_columns_when_a_name_is_wrong():
    with pytest.raises(QueryRejected) as refused:
        work_spec(_fields(time_column="deadline"), Shape.SHARE, COMMITMENTS)

    assert "Its columns are: created_at, due, followed_up_at, id" in str(refused.value)


def test_work_spec_refuses_a_median_without_a_value():
    with pytest.raises(QueryRejected, match="A median needs a `value`"):
        work_spec(_fields(test=None), Shape.MEDIAN, COMMITMENTS)


def test_work_spec_refuses_a_total_without_a_value():
    with pytest.raises(QueryRejected, match="A total needs a `value`"):
        work_spec(_fields(test=None), Shape.TOTAL, COMMITMENTS)


def test_work_spec_checks_a_medians_value_like_a_test():
    with pytest.raises(QueryRejected, match="`value` may not contain `--`"):
        work_spec(_fields(value="1 -- 2"), Shape.MEDIAN, COMMITMENTS)


# --- The SQL ----------------------------------------------------------------------


def _spec(**overrides: object) -> WorkSpec:
    fields: dict[str, object] = {
        "table": "commitments",
        "id_column": "id",
        "time_column": "due",
        "time_kind": TimeKind.DATE,
        "shape": Shape.SHARE,
        "test": "status = 'done'",
        "value": None,
        "label_column": "what",
        "link_column": "source",
    }
    fields.update(overrides)
    return WorkSpec(**fields)  # type: ignore[arg-type]  # test builder over a frozen dataclass


def _assert_reads_only(sql: str, table: str) -> None:
    """The datastore's own parser takes it as one read of the unit table."""
    assert analyze_query(sql).tables == frozenset({table})


def test_work_count_sql_counts_a_share_over_the_window_on_a_date_column():
    sql = work_count_sql(_spec(), START, END)

    assert sql == (
        "SELECT count(*) FILTER (WHERE (status = 'done')) AS counted, "
        'count(*) AS total FROM "commitments" '
        "WHERE \"due\" >= '2026-10-05'::date AND \"due\" < '2026-10-12'::date"
    )
    _assert_reads_only(sql, "commitments")


def test_work_count_sql_bounds_a_datetime_column_at_utc_midnight():
    sql = work_count_sql(
        _spec(time_column="followed_up_at", time_kind=TimeKind.DATETIME),
        START,
        END,
    )

    assert (
        "WHERE \"followed_up_at\" >= '2026-10-05T00:00:00+00:00'::timestamptz "
        "AND \"followed_up_at\" < '2026-10-12T00:00:00+00:00'::timestamptz"
    ) in sql
    _assert_reads_only(sql, "commitments")


def test_work_count_sql_counts_every_row_for_a_count_with_no_test():
    sql = work_count_sql(_spec(shape=Shape.COUNT, test=None), START, END)

    assert sql.startswith("SELECT count(*) AS counted, count(*) AS total FROM ")
    _assert_reads_only(sql, "commitments")


def test_work_count_sql_takes_a_median_over_the_rows_passing_the_test():
    spec = _spec(
        table="conversations",
        time_column="started_at",
        time_kind=TimeKind.DATETIME,
        shape=Shape.MEDIAN,
        test="first_reply_at IS NOT NULL",
        value="extract(epoch from first_reply_at - started_at) / 60",
    )

    sql = work_count_sql(spec, START, END)

    value = "((extract(epoch from first_reply_at - started_at) / 60))::double precision"
    passing = "FILTER (WHERE (first_reply_at IS NOT NULL))"
    assert sql.startswith(
        f"SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY {value}) {passing} "
        f"AS counted, count({value}) {passing} AS total "
        'FROM "conversations" WHERE "started_at" >= '
    )
    _assert_reads_only(sql, "conversations")


def test_work_count_sql_adds_up_a_total_over_the_rows_passing_the_test():
    spec = _spec(shape=Shape.TOTAL, test="status = 'posted'", value="reports")

    sql = work_count_sql(spec, START, END)

    value = "((reports))::double precision"
    passing = "FILTER (WHERE (status = 'posted'))"
    # Zero rather than NULL over no rows: a week that added nothing added 0.
    assert sql.startswith(
        f"SELECT coalesce(sum({value}) {passing}, 0) AS counted, "
        f"count({value}) {passing} AS total "
    )
    _assert_reads_only(sql, "commitments")


def test_work_count_sql_takes_a_median_over_every_row_with_no_test():
    spec = _spec(shape=Shape.MEDIAN, test=None, value="reports")

    sql = work_count_sql(spec, START, END)

    assert "FILTER" not in sql
    assert "count(((reports))::double precision) AS total" in sql
    _assert_reads_only(sql, "commitments")


def test_work_count_sql_refuses_a_window_with_a_time_of_day():
    with pytest.raises(QueryRejected, match="whole day"):
        work_count_sql(_spec(), datetime(2026, 10, 5, 9, tzinfo=timezone.utc), END)


def test_work_rows_sql_lists_a_shares_misses_first_one_past_the_limit():
    sql = work_rows_sql(_spec(), START, END, 50)

    assert sql == (
        'SELECT ("id")::text AS "id", ("what")::text AS "label", '
        '("source")::text AS "link", "due" AS "at", '
        "coalesce((status = 'done'), false) AS \"passed\" "
        'FROM "commitments" '
        "WHERE \"due\" >= '2026-10-05'::date AND \"due\" < '2026-10-12'::date "
        'ORDER BY "passed" ASC, "at", "id" LIMIT 51'
    )
    _assert_reads_only(sql, "commitments")


def test_work_rows_sql_lists_a_counts_counted_rows_first():
    sql = work_rows_sql(_spec(shape=Shape.COUNT, link_column=None), START, END, 10)

    assert 'NULL::text AS "link"' in sql
    assert 'ORDER BY "passed" DESC' in sql
    _assert_reads_only(sql, "commitments")


def test_work_rows_sql_passes_a_count_with_no_test_on_every_row():
    sql = work_rows_sql(_spec(shape=Shape.COUNT, test=None), START, END, 10)

    assert 'true AS "passed"' in sql


def test_work_rows_sql_passes_a_median_row_only_when_it_has_a_value():
    spec = _spec(shape=Shape.MEDIAN, test="status = 'done'", value="reports")

    sql = work_rows_sql(spec, START, END, 10)

    assert (
        "coalesce((status = 'done'), false) AND ((reports))::double precision "
        'IS NOT NULL AS "passed"'
    ) in sql
    _assert_reads_only(sql, "commitments")


@pytest.mark.parametrize("limit", [0, MAX_UNIT_ROWS + 1])
def test_work_rows_sql_refuses_a_limit_outside_its_range(limit):
    with pytest.raises(QueryRejected, match="`limit` must be between"):
        work_rows_sql(_spec(), START, END, limit)


def test_unit_row_reads_a_result_row_as_text_a_person_can_open():
    row = unit_row(
        {
            "id": UUID("5f0f4a4e-0d1c-4a52-9a49-6f3f0f0d9a11"),
            "label": "Send the deck",
            "link": None,
            "at": date(2026, 10, 7),
            "passed": False,
        }
    )

    assert row == UnitRow(
        id="5f0f4a4e-0d1c-4a52-9a49-6f3f0f0d9a11",
        label="Send the deck",
        link=None,
        at="2026-10-07",
        passed=False,
    )


def test_unit_row_writes_a_moment_in_full_and_refuses_to_guess_a_verdict():
    row = unit_row(
        {
            "id": 7,
            "label": Decimal("12"),
            "at": datetime(2026, 10, 7, 9, 30, tzinfo=timezone.utc),
            "passed": None,
        }
    )

    assert (row.id, row.label, row.at, row.passed) == (
        "7",
        "12",
        "2026-10-07T09:30:00+00:00",
        None,
    )
