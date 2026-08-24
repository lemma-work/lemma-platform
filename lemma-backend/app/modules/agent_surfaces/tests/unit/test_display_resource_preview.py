"""How a resource reads once it has to fit in a chat message."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.modules.agent_surfaces.services.display_resource_preview import (
    PREVIEW_COLUMN_LIMIT,
    PREVIEW_LINE_BUDGET,
    PREVIEW_ROW_LIMIT,
    describe_file,
    format_bytes,
    format_record_count,
    format_record_table,
)


_ORDERS = [
    {"id": 1, "customer": "Ada Lovelace", "total": 42.0, "status": "OPEN"},
    {"id": 2, "customer": "Alan Turing", "total": 7.5, "status": "PAID"},
    {"id": 3, "customer": "Grace Hopper the Third", "total": 1200.25, "status": "OPEN"},
]


def test_a_table_block_stays_inside_one_phone_line():
    """Every line fits the budget, so the columns stay columns.

    A wrapped fixed-width table is worse than none: the alignment that was the
    only reason to send it is exactly what wrapping destroys.
    """
    block = format_record_table(_ORDERS)

    assert block is not None
    lines = block.splitlines()
    assert all(len(line) <= PREVIEW_LINE_BUDGET for line in lines), lines
    # Header, rule, and one line per record.
    assert len(lines) == len(_ORDERS) + 2
    assert lines[0].split() == ["id", "customer", "total", "status"]
    assert set(lines[1]) <= {"-", " "}
    assert "Ada Lovelace" in lines[2]


def test_an_oversized_cell_is_cut_rather_than_allowed_to_wrap():
    block = format_record_table(_ORDERS)

    assert block is not None
    assert "Grace Hopper the Third" not in block
    assert "Grace Hopper" in block
    assert "…" in block


def test_columns_past_the_budget_are_left_to_the_link():
    """Wide leading columns spend the budget; the rest do not silently wrap."""
    rows = [
        {
            "reference": "INV-2026-000481",
            "counterparty": "Ada Lovelace Ltd",
            "memo": "Q3 consulting retainer",
            "status": "OPEN",
        }
    ]

    block = format_record_table(rows)

    assert block is not None
    assert all(len(line) <= PREVIEW_LINE_BUDGET for line in block.splitlines())
    assert "reference" in block
    assert "status" not in block


def test_no_more_than_four_columns_even_when_they_all_fit():
    rows = [{"a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6}]

    block = format_record_table(rows)

    assert block is not None
    assert block.splitlines()[0].split() == ["a", "b", "c", "d"]
    assert len(block.splitlines()[0].split()) == PREVIEW_COLUMN_LIMIT


def test_the_table_shows_a_page_of_rows_not_the_table():
    rows = [{"n": index} for index in range(50)]

    block = format_record_table(rows)

    assert block is not None
    assert len(block.splitlines()) == PREVIEW_ROW_LIMIT + 2


def test_the_declared_column_order_wins_over_the_rows():
    """A table's own schema order beats whatever order the records came back in."""
    rows = [{"total": 42.0, "id": 1}]

    block = format_record_table(rows, columns=["id", "total"])

    assert block is not None
    assert block.splitlines()[0].split() == ["id", "total"]


def test_values_that_are_not_strings_still_read_as_one_line():
    rows = [
        {
            "flag": True,
            "when": datetime(2026, 8, 24, 14, 23, tzinfo=timezone.utc),
            "day": date(2026, 8, 24),
            "tags": ["a", "b"],
            "missing": None,
        }
    ]

    block = format_record_table(rows, columns=["flag", "when", "day", "missing"])

    assert block is not None
    row = block.splitlines()[2]
    assert "yes" in row
    assert "2026-08-24" in row
    # A missing value is blank, not the word "None".
    assert "None" not in block


def test_an_empty_result_has_no_block_to_show():
    assert format_record_table([]) is None
    assert format_record_count(0, 0) == "No records match."


def test_the_count_says_how_much_of_the_table_is_showing():
    assert format_record_count(5, 128) == "5 of 128 records"
    assert format_record_count(3, 3) == "3 records"
    assert format_record_count(1, 1) == "1 record"
    assert format_record_count(2, None) == "2 records"


def test_a_file_is_described_by_what_it_is():
    assert (
        describe_file(
            name="lemma-aug-2026-shiplog.pdf",
            size_bytes=2_400_000,
            mime_type="application/pdf",
        )
        == "PDF · 2.3 MB"
    )
    # The extension is the word the person already has for the file.
    assert describe_file(name="chart.png", size_bytes=812, mime_type="image/png") == (
        "Image · 812 bytes"
    )
    # No extension: fall back to the MIME type rather than saying nothing.
    assert describe_file(name="LICENSE", size_bytes=None, mime_type="text/plain") == (
        "PLAIN"
    )
    assert describe_file(name=None, size_bytes=None, mime_type=None) is None


def test_sizes_read_the_way_a_file_manager_writes_them():
    assert format_bytes(0) == "0 bytes"
    assert format_bytes(999) == "999 bytes"
    assert format_bytes(2 * 1024 * 1024) == "2.0 MB"
    assert format_bytes(None) is None
