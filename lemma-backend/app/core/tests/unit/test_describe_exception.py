"""A failure reason must name the failure, not just its category.

Four workflow runs failed in production with the stored reason `"ReadError:"` —
a type, a colon, and nothing. `httpx.ReadError()` stringifies to the empty
string, so the usual `f"{type(exc).__name__}: {exc}"` produced a trailing colon
and no information at all for exactly the exceptions that carry the least on
their own.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.errors.describe import describe_exception

pytestmark = pytest.mark.unit


def test_an_exception_with_no_message_renders_as_the_type_alone() -> None:
    """The regression, pinned by the exact string that reached the database."""
    rendered = describe_exception(httpx.ReadError(""))

    assert rendered == "ReadError"
    assert not rendered.endswith(":")


def test_an_exception_with_a_message_keeps_both() -> None:
    assert describe_exception(ValueError("bad port")) == "ValueError: bad port"


def test_the_cause_is_appended_because_it_is_the_part_that_identifies_the_event() -> (
    None
):
    """A wrapped transport failure's own message is usually generic; the cause
    is where the host, port or timeout lives."""
    try:
        try:
            raise httpx.ConnectTimeout("timed out connecting to 10.0.0.4:443")
        except httpx.ConnectTimeout as inner:
            raise RuntimeError("sandbox unreachable") from inner
    except RuntimeError as exc:
        rendered = describe_exception(exc)

    assert rendered.startswith("RuntimeError: sandbox unreachable")
    assert "10.0.0.4:443" in rendered


def test_a_messageless_cause_of_the_same_type_adds_nothing() -> None:
    """A re-raise of the same empty error must not become "X (caused by X)"."""
    try:
        try:
            raise httpx.ReadError("")
        except httpx.ReadError as inner:
            raise httpx.ReadError("") from inner
    except httpx.ReadError as exc:
        assert describe_exception(exc) == "ReadError"


def test_the_cause_can_be_suppressed_where_the_caller_adds_its_own_context() -> None:
    try:
        try:
            raise ValueError("inner")
        except ValueError as inner:
            raise RuntimeError("outer") from inner
    except RuntimeError as exc:
        assert describe_exception(exc, with_cause=False) == "RuntimeError: outer"


def test_multi_line_messages_collapse_to_one_line() -> None:
    """These are stored in a single database column and read in a table."""
    rendered = describe_exception(ValueError("line one\n  line two"))

    assert rendered == "ValueError: line one line two"


def test_no_workspace_error_string_can_still_produce_a_bare_type_and_colon() -> None:
    """The producers that fed the database. If one reverts to the raw f-string,
    a messageless transport error goes back to being `"ReadError: "`."""
    import inspect

    from app.modules.workspace import sandbox_session, session_support

    for module in (sandbox_session, session_support):
        source = inspect.getsource(module)
        assert "{type(exc).__name__}: {exc}" not in source, module.__name__
