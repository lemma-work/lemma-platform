def test_an_oversized_query_is_refused_before_it_is_parsed():
    """Parsing is CPU on the event loop and the query is tenant-supplied.

    Without a bound, the only limit on how long sqlglot spends is how much SQL
    the caller felt like sending. Refused by length first, so the parser never
    sees it.
    """
    import pytest

    from app.modules.connectors.domain.errors import (
        OperationExecutionValidationError,
    )
    from app.modules.connectors.infrastructure.adapters.sql_executor import (
        _MAX_SQL_CHARS,
        _ensure_read_only,
    )

    oversized = "SELECT 1 -- " + ("x" * (_MAX_SQL_CHARS + 1))
    with pytest.raises(OperationExecutionValidationError) as excinfo:
        _ensure_read_only(oversized)
    # The public message is deliberately generic; the reason rides in details.
    assert excinfo.value.details.get("reason") == "query_too_long"

    # And an ordinary query is untouched.
    _ensure_read_only(
        "SELECT id FROM users WHERE created_at > now() - interval '1 day'"
    )
