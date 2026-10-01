"""Reading rows for a decision, and writing them back with their answers."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.agent.tools.decisions.models import InputRefused
from app.modules.agent.tools.decisions.rows import (
    as_answer,
    hold_out,
    parse_rows,
    results_csv,
    results_place,
)
from app.modules.decisions.contracts.decide import Answer, RowResult, Rung

pytestmark = pytest.mark.unit


def test_a_csv_reads_one_row_past_the_limit_and_no_further() -> None:
    content = "﻿id,subject\n" + "".join(f"{n},hello {n}\n" for n in range(10))

    rows = parse_rows("/me/t.csv", content.encode(), limit=3)

    assert rows == [
        {"id": "0", "subject": "hello 0"},
        {"id": "1", "subject": "hello 1"},
        {"id": "2", "subject": "hello 2"},
        {"id": "3", "subject": "hello 3"},
    ]


def test_a_jsonl_file_names_the_line_that_is_not_json() -> None:
    content = b'{"a": 1}\n\n{"a": 2}\nnot json\n'

    with pytest.raises(InputRefused, match="line 4 is not valid JSON"):
        parse_rows("/me/t.jsonl", content, limit=10)


@pytest.mark.parametrize(
    ("path", "content", "message"),
    [
        ("/me/t.xlsx", b"PK", "is not a CSV or JSONL file"),
        ("/me/t.csv", b"id\n\xff\xfe\n", "is not UTF-8 text"),
    ],
)
def test_a_file_that_is_not_rows_is_refused_with_what_to_do(
    path: str, content: bytes, message: str
) -> None:
    with pytest.raises(InputRefused, match=message):
        parse_rows(path, content, limit=10)


@pytest.mark.parametrize(
    ("value", "question_type", "expected"),
    [
        ("true", "yes_no", True),
        ("No", "yes_no", False),
        ("2", "scale", 2),
        ("billing; legal", "multi_choice", ["billing", "legal"]),
        ('["a", "b"]', "multi_choice", ["a", "b"]),
        ("1", "choice", "1"),
        ("true", None, True),
        ("", "yes_no", None),
        (None, "choice", None),
    ],
)
def test_a_known_answer_is_read_as_its_question_answers(
    value: str | None, question_type: str | None, expected: object
) -> None:
    assert as_answer(value, question_type) == expected


def test_holding_out_a_row_hides_its_labels_from_the_decider() -> None:
    held = hold_out(
        {"subject": "hi", "label": "yes"},
        {"urgent": "label"},
        {"urgent": "yes_no"},
    )

    assert held.state == {"subject": "hi"}
    assert held.expected == {"urgent": True}


def test_results_go_beside_the_input_or_into_the_persons_own_folder() -> None:
    now = datetime(2026, 10, 1, 14, 22, 33, tzinfo=UTC)

    beside = results_place(
        input_path="/sales/q3 leads.csv", source="", decider="x", now=now
    )
    mine = results_place(
        input_path=None, source="tickets", decider="system:reply", now=now
    )

    assert (beside.directory, beside.name) == (
        "/sales",
        "q3-leads.decided-20261001-142233.csv",
    )
    assert beside.fallback == "/me/decisions"
    assert (mine.directory, mine.name) == (
        "/me/decisions",
        "tickets-system-reply-20261001-142233.csv",
    )


def test_the_results_file_keeps_every_column_and_never_overwrites_one() -> None:
    decided = uuid4()
    rows = [{"id": "1", "urgent": "maybe"}, "plain text"]
    results = [
        RowResult(
            0,
            "1",
            {"urgent": Answer(value=True, by=Rung.SYSTEM_ONE, confidence=0.9123)},
            [],
            decided,
        ),
        RowResult(1, "2", {}, ["urgent"], None),
    ]

    content, columns = results_csv(rows, results)

    assert columns == [
        "id",
        "urgent",
        "value",
        "urgent_decided",
        "urgent_confidence",
        "open_questions",
        "decision_id",
    ]
    written = list(csv.reader(io.StringIO(content.decode())))
    assert written[1] == ["1", "maybe", "", "true", "0.912", "", str(decided)]
    assert written[2] == ["", "", "plain text", "", "", "urgent", ""]
