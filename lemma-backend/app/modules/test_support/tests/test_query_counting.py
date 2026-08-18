"""The query counter has to watch whichever engine the caller actually uses.

This is a test about a test helper, which normally would not earn its keep. It
does here because the failure mode is silent and it poisons everything
downstream: ``counted_queries`` was attached to the application engine
singleton while the e2e fixtures drive their own, so it observed *nothing*, and
every assertion of the form ``assert statements == []`` passed for the wrong
reason. Two authorization-cost tests were vacuous from the day they were
written.

A counter that sees nothing is worse than no counter, because it reads as
proof.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.modules.test_support.query_counting import (
    counted_queries,
    format_statements,
    statements_touching,
)

pytestmark = pytest.mark.unit


def test_it_observes_an_engine_it_was_never_told_about() -> None:
    """The whole defect in one assertion.

    The engine here is created after the context manager's listener is
    installed and is not the application singleton — exactly the situation the
    e2e fixtures create.
    """
    with counted_queries() as statements:
        engine = create_engine("sqlite://")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    assert statements, (
        "the counter saw no statements at all, so every 'issued no queries' "
        "assertion built on it proves nothing"
    )


def test_it_stops_counting_once_the_block_ends() -> None:
    """A listener left attached would attribute later work to earlier blocks."""
    engine = create_engine("sqlite://")
    with counted_queries() as statements:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    before = len(statements)

    with engine.connect() as conn:
        conn.execute(text("SELECT 2"))

    assert len(statements) == before, (
        "statements executed after the block were still being recorded"
    )


def test_statements_touching_selects_by_table_and_formats_readably() -> None:
    statements = ["SELECT * FROM datastore_files", "SELECT * FROM pods"]

    assert statements_touching(statements, "datastore_files") == [statements[0]]
    assert "datastore_files" in format_statements(statements)
    assert "and 2 more" in format_statements(statements * 11, limit=20)
