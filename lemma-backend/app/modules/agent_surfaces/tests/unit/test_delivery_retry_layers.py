"""A failed send is retried in one place, and at most three times in total.

Adapter, job and inbox each retrying multiplies the attempts against an API that
is already failing. These pin the rule so a new consumer cannot quietly add a
layer: see `domain/delivery_limits.py` for why it is the adapter that retries.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.core.domain.errors import DomainError
from app.modules.agent_surfaces.domain.delivery_limits import (
    CONSUMER_ATTEMPTS,
    MAX_ATTEMPTS,
    MAX_DELIVERY_ATTEMPTS,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfacePlatformError
from app.modules.agent_surfaces.events import handlers
from app.modules.agent_surfaces.platforms.delivery import RetryPolicy


def test_the_adapter_retry_is_capped_at_three_attempts_in_total():
    assert 1 < MAX_DELIVERY_ATTEMPTS <= 3
    assert RetryPolicy().max_attempts == MAX_DELIVERY_ATTEMPTS


def test_no_layer_retries_more_than_three_times_in_total():
    assert MAX_ATTEMPTS == 3
    assert CONSUMER_ATTEMPTS <= MAX_ATTEMPTS
    assert MAX_DELIVERY_ATTEMPTS <= MAX_ATTEMPTS


def test_a_send_that_gave_up_is_terminal_so_no_layer_above_repeats_it():
    """The other half of the rule: the adapter retries, and nothing above does.

    The inbox retries what raises, except a domain error that is not a 503. So a
    send that exhausted its attempts must surface as one.
    """
    error = AgentSurfacePlatformError("SLACK", "nothing reached the person.")

    assert isinstance(error, DomainError)
    assert error.status_code != 503


def _handlers_tree() -> ast.Module:
    return ast.parse(Path(handlers.__file__).read_text(encoding="utf-8"))


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def test_every_surface_inbox_consumer_passes_the_cap():
    """A consumer that omits it falls back to the inbox default of ten."""
    calls = [
        node
        for node in ast.walk(_handlers_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "process"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "inbox"
    ]
    assert calls, "found no inbox.process calls to check"
    for call in calls:
        limit = _keyword(call, "max_attempts")
        assert isinstance(limit, ast.Name) and limit.id == "CONSUMER_ATTEMPTS", (
            f"line {call.lineno}: inbox.process must pass max_attempts=CONSUMER_ATTEMPTS"
        )


def test_the_surface_job_states_its_try_limit():
    """streaq retries a raising task up to `max_tries`; say which number is meant."""
    jobs = [
        decorator
        for node in ast.walk(_handlers_tree())
        if isinstance(node, ast.AsyncFunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and getattr(decorator.func, "id", "") == "streaq_task"
    ]
    assert jobs, "found no streaq_task registrations to check"
    for decorator in jobs:
        limit = _keyword(decorator, "max_tries")
        assert isinstance(limit, ast.Name) and limit.id == "CONSUMER_ATTEMPTS"
