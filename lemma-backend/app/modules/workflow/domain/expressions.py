"""The single expression system for workflows: JMESPath everywhere.

Used for input bindings, decision conditions, loop items paths, and form
assignee expressions. Compilation errors are surfaced at flow save time so a
bad expression can never reach a run.
"""

from functools import lru_cache
from typing import Any

import jmespath
from jmespath.exceptions import JMESPathError
from jmespath.parser import ParsedResult

from app.modules.workflow.domain.errors import (
    ContextPathError,
    ExpressionSyntaxError,
)


@lru_cache(maxsize=2048)
def _compile(expression: str) -> ParsedResult:
    return jmespath.compile(expression)


class ExpressionEngine:
    """JMESPath facade with explicit error semantics."""

    @staticmethod
    def compile(expression: str) -> None:
        """Validate an expression, raising ExpressionSyntaxError when invalid."""
        if not isinstance(expression, str) or not expression.strip():
            raise ExpressionSyntaxError(
                str(expression), "expression must be a non-empty string"
            )
        try:
            _compile(expression)
        except JMESPathError as exc:
            raise ExpressionSyntaxError(expression, str(exc)) from exc

    @staticmethod
    def evaluate(expression: str, data: dict[str, Any]) -> Any:
        """Evaluate an expression; missing paths yield None."""
        try:
            return _compile(expression).search(data)
        except JMESPathError as exc:
            raise ExpressionSyntaxError(expression, str(exc)) from exc

    @classmethod
    def evaluate_required(cls, expression: str, data: dict[str, Any]) -> Any:
        """Evaluate an expression; a None result raises ContextPathError."""
        result = cls.evaluate(expression, data)
        if result is None:
            raise ContextPathError(expression)
        return result

    @classmethod
    def evaluate_condition(cls, expression: str, data: dict[str, Any]) -> bool:
        """Evaluate a condition with explicit truthiness.

        Falsy: None, False, "", [], {}. Everything else (including 0, per
        JMESPath convention) is truthy. Missing paths are falsy by design —
        decision conditions probe the context, unlike input bindings which
        fail loudly.
        """
        result = cls.evaluate(expression, data)
        if result is None or result is False:
            return False
        if isinstance(result, (str, list, dict)) and len(result) == 0:
            return False
        return True
