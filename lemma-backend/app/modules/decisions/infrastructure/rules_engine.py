"""The first rung: deterministic rules, free and instant.

Today's exact-match gates -- approval phrases, pod-choice numbers, JMESPath
branches -- are this rung. They are not replaced by the engines above; they go
first, and what they answer never reaches a third party.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache

import jmespath
from jmespath.exceptions import JMESPathError
from jmespath.parser import ParsedResult
from pydantic import JsonValue

from app.core.log.log import get_logger
from app.modules.decisions.domain.deciders import Rule
from app.modules.decisions.domain.decisions import Answer, Rung
from app.modules.decisions.domain.questions import Question, validate_answer

logger = get_logger(__name__)


@lru_cache(maxsize=512)
def compile_expression(expression: str) -> ParsedResult:
    return jmespath.compile(expression)


def normalize_phrase(text: str) -> str:
    """Fold typed text to its comparable form: "Yes!" and "yes" are one answer.

    Apostrophes are kept, so "don't" and "don’t" can both be listed.
    """
    collapsed = " ".join(text.strip().lower().split())
    return collapsed.strip(".!?,;:").strip()


def answer_by_rules(
    rules: list[Rule],
    state: JsonValue,
    questions: Mapping[str, Question],
) -> dict[str, Answer]:
    """The first matching rule's answer for each question it names.

    A rule answering a question that is no longer open, or with a value the
    question cannot take (a pod that was not offered this time), is skipped
    for that question rather than failing the decision.
    """
    answers: dict[str, Answer] = {}
    for rule in rules:
        if not _matches(rule, state):
            continue
        for key, value in rule.answer.items():
            question = questions.get(key)
            if question is None or key in answers:
                continue
            try:
                checked = validate_answer(question, value)
            except ValueError:
                continue
            answers[key] = Answer(value=checked, by=Rung.RULES)
        if len(answers) == len(questions):
            break
    return answers


def _matches(rule: Rule, state: JsonValue) -> bool:
    if rule.phrases is not None:
        found = _field_text(state, rule.field)
        if found is None:
            return False
        normalized = normalize_phrase(found)
        return any(normalize_phrase(phrase) == normalized for phrase in rule.phrases)
    if rule.when is None:
        return False
    try:
        result = compile_expression(rule.when).search(state)
    except JMESPathError:
        # Expressions are compiled when a definition is saved, so this is a
        # runtime type error inside a valid expression (comparing a string to
        # a number). The rule does not answer; the next rung will.
        logger.warning(
            "decisions.rules_engine.rule_evaluation_failed.degraded",
            expression=rule.when,
            exc_info=True,
        )
        return False
    return _truthy(result)


def _field_text(state: JsonValue, field: str) -> str | None:
    if isinstance(state, str):
        return state if field == "text" else None
    current: JsonValue = state
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, str) else None


def _truthy(value: JsonValue) -> bool:
    """JMESPath truthiness: null, false, empty string, list and object are false."""
    if value is None or value is False:
        return False
    if isinstance(value, str | list | dict) and not value:
        return False
    return True


def check_expressions(rules: list[Rule]) -> None:
    """Compile every `when`, so a bad expression is refused when it is saved."""
    for index, rule in enumerate(rules):
        if rule.when is None:
            continue
        try:
            compile_expression(rule.when)
        except JMESPathError as exc:
            raise ValueError(f"rule {index}: invalid JMESPath expression") from exc
