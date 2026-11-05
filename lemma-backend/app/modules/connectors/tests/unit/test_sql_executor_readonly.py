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


def test_select_into_and_set_config_are_refused_like_the_datastore_refuses_them():
    """The connector says it reuses the datastore's read-only rules, so it must.

    ``SELECT * INTO t FROM x`` parses as a Select carrying an Into node, so a
    root check alone lets it through, and in PostgreSQL it creates a table.
    ``set_config()`` is an ordinary function call that rewrites session
    settings from inside an otherwise read-only SELECT. The read-only
    transaction stops the first one later; nothing stops the second.
    """
    import pytest

    from app.modules.connectors.domain.errors import (
        OperationExecutionValidationError,
    )
    from app.modules.connectors.infrastructure.adapters.sql_executor import (
        _ensure_read_only,
    )

    for sql in (
        "SELECT * INTO copied FROM users",
        "SELECT set_config('search_path', 'other', false)",
        "WITH s AS MATERIALIZED (SELECT set_config('role', 'x', true)) SELECT * FROM s",
    ):
        with pytest.raises(OperationExecutionValidationError):
            _ensure_read_only(sql)

    _ensure_read_only("SELECT current_setting('search_path')")
