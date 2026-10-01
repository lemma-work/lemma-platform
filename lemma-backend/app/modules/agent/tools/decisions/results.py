"""What the decisions tools say back to the model.

Every answer carries who gave it, because that is what the model needs to
weigh it: a rule is certain, System One carries its confidence, and a model's
answer carries neither. A choice nothing was sure of shows `fallback`, and the
question stays listed as open, so a safe default is never read as a verdict.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.tools.decisions.seams import AnswerValue

if TYPE_CHECKING:
    from app.modules.decisions.contracts.decide import (
        Answer,
        DecisionEntity,
        RowResult,
        RowsResult,
    )
    from app.modules.decisions.contracts.deciders import (
        DeciderEntity,
        SampleResult,
    )

#: Rows at or under this many come back inline; more go to a results file.
INLINE_RESULT_ROWS = 25
#: Open or failed rows listed by name. The rest are counted, and the results
#: file has every one.
LISTED_ROWS = 20
LISTED_DISAGREEMENTS = 25

_OPEN_NOTE = (
    "Nothing on the ladder was sure of {questions}. If it matters, ask the "
    "person with ask_user and record their answer with answer_decision."
)
_AGENT_ANSWER_NOTE = (
    "Recorded as your answer, not the person's. An agent's answer never "
    "becomes an example the decider learns from, even when it passes on what "
    "someone said. If they want to teach the decider, they can correct this "
    "decision themselves in Lemma."
)


def answer_view(answer: Answer) -> JsonObject:
    view: JsonObject = {"value": answer.value, "by": str(answer.by)}
    if answer.confidence is not None:
        view["confidence"] = round(answer.confidence, 3)
    if answer.abstained:
        view["fallback"] = True
    return view


def decision_view(decision: DecisionEntity, *, decider: str) -> JsonObject:
    """One decision: its answers, who gave each, and the id to answer it by."""
    view: JsonObject = {
        "success": True,
        "decision_id": str(decision.id),
        "decider": decider,
        "status": str(decision.status),
        "answers": {
            key: answer_view(answer) for key, answer in decision.answers.items()
        },
    }
    if decision.open:
        view["open"] = list(decision.open)
        view["note"] = _OPEN_NOTE.format(questions=", ".join(decision.open))
    return view


def answered_view(decision: DecisionEntity) -> JsonObject:
    view = decision_view(decision, decider=decision.decider_name or "inline")
    view["note"] = _AGENT_ANSWER_NOTE
    return view


def defined_view(
    decider: DeciderEntity, warnings: Sequence[str], *, created: bool
) -> JsonObject:
    return {
        "success": True,
        "name": decider.name,
        "version": decider.version,
        "created": created,
        "definition": decider.definition.model_dump(mode="json", exclude_none=True),
        "warnings": list(warnings),
        "note": (
            f"Ask it with decide(decider={decider.name!r}). Before saving a "
            "change to it, try the change with test_decider on rows whose "
            "answers people gave."
        ),
    }


def _label(row: RowResult) -> str:
    return row.row_id or str(row.index + 1)


def _answered_by(answers: Sequence[dict[str, Answer]]) -> dict[str, int]:
    tally: Counter[str] = Counter(
        str(answer.by) for row in answers for answer in row.values()
    )
    return dict(sorted(tally.items()))


def rows_view(result: RowsResult, *, decider: str) -> JsonObject:
    """Counts per answer, who answered, and the rows left open or failed."""
    decided = [row for row in result.rows if not row.failed]
    open_rows = [row for row in decided if row.open]
    failed = [row for row in result.rows if row.failed]
    view: JsonObject = {
        "success": True,
        "decider": decider,
        "decided": len(decided),
        "counts": result.counts,
        "answered_by": _answered_by([row.answers for row in decided]),
    }
    if open_rows:
        view["open_rows"] = {
            "count": len(open_rows),
            "rows": [
                {
                    "row": _label(row),
                    "open": list(row.open),
                    "decision_id": str(row.decision_id) if row.decision_id else None,
                }
                for row in open_rows[:LISTED_ROWS]
            ],
            "note": _OPEN_NOTE.format(questions="these rows' open questions"),
        }
    if failed:
        view["failed_rows"] = {
            "count": len(failed),
            "rows": [_label(row) for row in failed[:LISTED_ROWS]],
            "note": (
                "No rung could be asked about these rows; they have no answers "
                "and nothing was recorded for them. Try them again later."
            ),
        }
    return view


def inline_results(result: RowsResult) -> list[JsonObject]:
    return [_row_result(row) for row in result.rows]


def _row_result(row: RowResult) -> JsonObject:
    item: JsonObject = {
        "row": _label(row),
        "answers": {key: answer.value for key, answer in row.answers.items()},
    }
    if row.open and not row.failed:
        item["open"] = list(row.open)
    if row.failed:
        item["failed"] = True
    if row.decision_id:
        item["decision_id"] = str(row.decision_id)
    return item


def trial_view(
    result: SampleResult, *, decider: str, labels: Sequence[str]
) -> JsonObject:
    """How a decider did on sample rows, and where it parted from people."""
    view: JsonObject = {
        "success": True,
        "decider": decider,
        "rows": len(result.answers),
        "counts": _counts(result.answers),
        "answered_by": _answered_by(result.answers),
        "recorded": False,
    }
    open_rows = [
        {"row": labels[index], "open": list(open_keys)}
        for index, open_keys in enumerate(result.open)
        if open_keys
    ]
    if open_rows:
        view["open_rows"] = {"count": len(open_rows), "rows": open_rows[:LISTED_ROWS]}
    if result.agreement:
        view["agreement"] = {
            key: {"agreed": agreed, "total": total, "rate": round(agreed / total, 3)}
            for key, (agreed, total) in result.agreement.items()
            if total
        }
        view["disagreements"] = {
            "count": len(result.disagreements),
            "items": [
                {
                    "row": labels[item.row],
                    "question": item.question,
                    "expected": item.expected,
                    "answer": answer_view(item.answer) if item.answer else None,
                }
                for item in result.disagreements[:LISTED_DISAGREEMENTS]
            ],
        }
    if len(result.answers) <= INLINE_RESULT_ROWS:
        view["results"] = [
            {
                "row": labels[index],
                "answers": {key: answer.value for key, answer in answers.items()},
            }
            for index, answers in enumerate(result.answers)
        ]
    return view


def _counts(answers: Sequence[dict[str, Answer]]) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = {}
    for row in answers:
        for key, answer in row.items():
            counts.setdefault(key, Counter()).update(_count_labels(answer.value))
    return {key: dict(sorted(tally.items())) for key, tally in counts.items()}


def _count_labels(value: AnswerValue) -> list[str]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, bool):
        return ["true" if value else "false"]
    return [str(value)]
