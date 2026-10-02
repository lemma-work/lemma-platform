"""Rows for a decision: read from a file, held apart from their known answers,
and written back out with the answers beside them.

Pure functions over bytes and JSON values; the tools do the reading and
writing.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from app.modules.agent.tools.decisions.models import InputRefused
from app.modules.agent.tools.decisions.seams import AnswerValue

if TYPE_CHECKING:
    from app.modules.decisions.contracts.decide import Answer, RowResult

#: Where results land when there is no input file to put them beside, or its
#: folder is not the caller's to write in: the person's own space.
RESULTS_DIRECTORY = "/me/decisions"

_JSONL_SUFFIXES = (".jsonl", ".ndjson")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_TRUE = frozenset({"true", "yes", "y", "1"})
_FALSE = frozenset({"false", "no", "n", "0"})


def parse_rows(path: str, content: bytes, *, limit: int) -> list[JsonValue]:
    """A CSV or JSONL file's rows, reading at most one past `limit`.

    One past, so the caller can tell a file at the limit from one over it
    without this reading all of a large file to count it.
    """
    lowered = path.lower()
    text = io.TextIOWrapper(io.BytesIO(content), encoding="utf-8-sig", newline="")
    try:
        if lowered.endswith(".csv"):
            return _csv_rows(path, text, limit + 1)
        if lowered.endswith(_JSONL_SUFFIXES):
            return _jsonl_rows(path, text, limit + 1)
    except UnicodeDecodeError as exc:
        raise InputRefused(f"`{path}` is not UTF-8 text.") from exc
    raise InputRefused(
        f"`{path}` is not a CSV or JSONL file. Rows can be read from a `.csv` "
        "with a header row or a `.jsonl` with one JSON object per line; pass "
        "anything else to the tool as `items`."
    )


def _csv_rows(path: str, text: io.TextIOWrapper, count: int) -> list[JsonValue]:
    # `restkey`: a row with more cells than the header has keeps them under a
    # name of their own, rather than under a `None` key JSON cannot carry.
    reader = csv.DictReader(text, restkey="_extra_cells")
    try:
        return [_csv_row(row) for row in islice(reader, count)]
    except csv.Error as exc:
        raise InputRefused(
            f"`{path}` could not be read as CSV at line {reader.line_num}: {exc}."
        ) from exc


def _csv_row(row: Mapping[str, str | list[str] | None]) -> JsonValue:
    # Cells are text, a row's surplus cells a list of text, a short row's
    # missing ones None: JSON already, though no checker can see it through
    # `dict`'s invariance.
    return {str(key): cast(JsonValue, value) for key, value in row.items()}


def _jsonl_rows(path: str, text: io.TextIOWrapper, count: int) -> list[JsonValue]:
    rows: list[JsonValue] = []
    for number, line in enumerate(text, start=1):
        if len(rows) >= count:
            break
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as exc:
            raise InputRefused(f"`{path}` line {number} is not valid JSON.") from exc
    return rows


@dataclass(frozen=True, slots=True)
class HeldOut:
    """A row as the decider sees it, and the answers a person gave for it."""

    state: JsonValue
    expected: dict[str, AnswerValue] | None


def hold_out(
    row: JsonValue,
    fields: Mapping[str, str],
    question_types: Mapping[str, str],
) -> HeldOut:
    """The row without its known-answer fields, and those answers as values.

    The fields are removed from what the decider sees: a label sitting in the
    row it is labelling would be answered by copying it.
    """
    if not fields or not isinstance(row, dict):
        return HeldOut(row, None)
    hidden = set(fields.values())
    state: JsonValue = {key: value for key, value in row.items() if key not in hidden}
    expected: dict[str, AnswerValue] = {}
    for question, field in fields.items():
        value = as_answer(row.get(field), question_types.get(question))
        if value is not None:
            expected[question] = value
    return HeldOut(state, expected or None)


def as_answer(value: JsonValue, question_type: str | None) -> AnswerValue | None:
    """A known answer as the question would give it; None when there is none.

    A CSV cell is always text, so `true` and `2` have to become the boolean and
    the level index a yes/no or scale question answers with, or every one of
    them would count as a disagreement.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool | int):
        return value
    if isinstance(value, list):
        return [str(item) for item in value]
    text = str(value).strip()
    if question_type == "yes_no":
        return _as_bool(text)
    if question_type == "scale":
        return int(text) if text.isdigit() else text
    if question_type == "multi_choice":
        return _as_keys(text)
    if question_type == "choice":
        return text
    return _guessed(text)


def _as_bool(text: str) -> AnswerValue:
    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return text


def _as_keys(text: str) -> list[str]:
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return [part.strip() for part in re.split(r"[;,]", text) if part.strip()]


def _guessed(text: str) -> AnswerValue:
    """For a decider whose questions this cannot see: the obvious reading."""
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if text.isdigit():
        return int(text)
    if text.startswith("["):
        return _as_keys(text)
    return text


def row_label(row: JsonValue, key: str | None, index: int) -> str:
    """How a row is named back to the agent: its key's value, else its number."""
    if key is not None and isinstance(row, dict):
        value = row.get(key)
        if isinstance(value, str | int) and not isinstance(value, bool):
            return str(value)
    return str(index + 1)


@dataclass(frozen=True, slots=True)
class ResultsPlace:
    directory: str
    name: str
    #: Where to put it instead when `directory` is not the caller's to write in.
    fallback: str | None


def results_place(
    *, input_path: str | None, source: str, decider: str, now: datetime
) -> ResultsPlace:
    """Beside the input file, or in the person's own decisions folder."""
    stamp = now.strftime("%Y%m%d-%H%M%S")
    if input_path:
        directory, _, file_name = input_path.rstrip("/").rpartition("/")
        stem = file_name.rsplit(".", 1)[0] or "rows"
        return ResultsPlace(
            directory=directory or "/",
            name=f"{_safe(stem)}.decided-{stamp}.csv",
            fallback=RESULTS_DIRECTORY,
        )
    return ResultsPlace(
        directory=RESULTS_DIRECTORY,
        name=f"{_safe(source)}-{_safe(decider)}-{stamp}.csv",
        fallback=None,
    )


def _safe(text: str) -> str:
    return _UNSAFE_NAME.sub("-", text).strip("-.") or "decisions"


def results_csv(
    rows: Sequence[JsonValue], results: Sequence[RowResult]
) -> tuple[bytes, list[str]]:
    """The rows with their answers beside them, and the columns in order.

    The row's own columns first, then one per question, then the System One
    confidence for a question it answered, which rows are still open, which
    failed, and each decision's id -- the handle `answer_decision` takes.
    """
    source_columns = _source_columns(rows)
    layout = _Layout.build(source_columns, results)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(layout.header)
    for row, result in zip(rows, results, strict=True):
        writer.writerow(
            [_cell(_field(row, column)) for column in source_columns]
            + layout.answer_cells(result)
        )
    return buffer.getvalue().encode("utf-8"), layout.header


#: The column a row that is not an object keeps its value under.
_VALUE_COLUMN = "value"


def _source_columns(rows: Iterable[JsonValue]) -> list[str]:
    columns: dict[str, None] = {}
    for row in rows:
        if isinstance(row, dict):
            columns.update(dict.fromkeys(row))
        else:
            columns[_VALUE_COLUMN] = None
    return list(columns)


def _field(row: JsonValue, column: str) -> JsonValue:
    if isinstance(row, dict):
        return row.get(column)
    return row if column == _VALUE_COLUMN else None


@dataclass(frozen=True, slots=True)
class _Layout:
    questions: list[str]
    #: Question -> its answer column, and its confidence column when it has one.
    answer_columns: dict[str, str]
    confidence_columns: dict[str, str]
    open_column: str | None
    failed_column: str | None
    id_column: str | None
    header: list[str]

    @classmethod
    def build(cls, taken: Sequence[str], results: Sequence[RowResult]) -> _Layout:
        names = set(taken)
        questions = _questions(results)
        answers = {question: _claim(question, names) for question in questions}
        confident = {
            question: _claim(f"{question}_confidence", names)
            for question in questions
            if any(
                _confidence(result.answers.get(question)) is not None
                for result in results
            )
        }
        open_column = (
            _claim("open_questions", names)
            if any(result.open and not result.failed for result in results)
            else None
        )
        failed_column = (
            _claim("failed", names) if any(r.failed for r in results) else None
        )
        id_column = (
            _claim("decision_id", names)
            if any(r.decision_id for r in results)
            else None
        )
        header = [*taken]
        for question in questions:
            header.append(answers[question])
            if question in confident:
                header.append(confident[question])
        header.extend(c for c in (open_column, failed_column, id_column) if c)
        return cls(
            questions,
            answers,
            confident,
            open_column,
            failed_column,
            id_column,
            header,
        )

    def answer_cells(self, result: RowResult) -> list[str]:
        cells: list[str] = []
        for question in self.questions:
            answer = result.answers.get(question)
            cells.append(_cell(answer.value) if answer is not None else "")
            if question in self.confidence_columns:
                confidence = _confidence(answer)
                cells.append("" if confidence is None else f"{confidence:.3f}")
        if self.open_column:
            cells.append("" if result.failed else ";".join(result.open))
        if self.failed_column:
            cells.append("true" if result.failed else "")
        if self.id_column:
            cells.append(str(result.decision_id) if result.decision_id else "")
        return cells


def _questions(results: Sequence[RowResult]) -> list[str]:
    seen: dict[str, None] = {}
    for result in results:
        seen.update(dict.fromkeys(result.answers))
        seen.update(dict.fromkeys(result.open))
    return list(seen)


def _confidence(answer: Answer | None) -> float | None:
    return None if answer is None else answer.confidence


def _claim(name: str, taken: set[str]) -> str:
    """`name`, or a variant of it no source column already uses."""
    candidate = name if name not in taken else f"{name}_decided"
    suffix = 2
    while candidate in taken:
        candidate = f"{name}_decided_{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


def _cell(value: JsonValue | AnswerValue) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return ";".join(item for item in value if isinstance(item, str))
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
