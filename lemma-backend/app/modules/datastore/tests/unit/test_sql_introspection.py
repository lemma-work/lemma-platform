from __future__ import annotations

import pytest

from app.modules.datastore.domain.errors import DatastoreQueryError
from app.modules.datastore.services.sql_introspection import (
    analyze_query,
    extract_referenced_tables,
)


class TestAnalyzeQueryTables:
    def test_simple_select(self):
        assert analyze_query("SELECT id FROM expenses WHERE amount > 100").tables == {
            "expenses"
        }

    def test_join_collects_all_base_tables(self):
        query = "SELECT e.*, p.name FROM expenses e JOIN projects p ON e.pid = p.id"
        assert analyze_query(query).tables == {"expenses", "projects"}

    def test_comma_join_collects_all_base_tables(self):
        assert analyze_query("SELECT * FROM a, b").tables == {"a", "b"}

    def test_subquery_tables_are_included(self):
        query = "SELECT * FROM (SELECT id FROM expenses) s JOIN projects p ON true"
        assert analyze_query(query).tables == {"expenses", "projects"}

    def test_cte_alias_excluded_but_underlying_table_included(self):
        query = "WITH c AS (SELECT * FROM expenses) SELECT * FROM c"
        assert analyze_query(query).tables == {"expenses"}

    def test_union_collects_both_sides(self):
        query = "SELECT id FROM a UNION SELECT id FROM b"
        assert analyze_query(query).tables == {"a", "b"}

    def test_no_table_reference_returns_empty(self):
        assert analyze_query("SELECT 1").tables == set()

    def test_set_returning_function_is_not_a_table(self):
        assert analyze_query("SELECT * FROM generate_series(1, 10)").tables == set()

    def test_string_literal_with_keyword_is_not_mistaken_for_mutation(self):
        # The old keyword-regex would have rejected this on the 'DELETE' literal.
        assert analyze_query("SELECT 'DELETE ME' AS note FROM projects").tables == {
            "projects"
        }


class TestAnalyzeQueryRejections:
    @pytest.mark.parametrize(
        "query",
        [
            "DELETE FROM projects",
            "UPDATE projects SET x = 1",
            "INSERT INTO projects VALUES (1)",
            "TRUNCATE projects",
            "DROP TABLE projects",
            "ALTER TABLE projects ADD COLUMN x int",
            "CREATE TABLE t (a int)",
            "GRANT SELECT ON projects TO public",
        ],
    )
    def test_mutations_and_ddl_rejected(self, query):
        with pytest.raises(DatastoreQueryError):
            analyze_query(query)

    def test_dml_hidden_in_cte_rejected(self):
        query = "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d"
        with pytest.raises(DatastoreQueryError, match="read-only"):
            analyze_query(query)

    def test_multiple_statements_rejected(self):
        with pytest.raises(DatastoreQueryError, match="single SQL statement"):
            analyze_query("SELECT 1; SELECT 2")

    def test_stacked_drop_rejected(self):
        with pytest.raises(DatastoreQueryError, match="single SQL statement"):
            analyze_query("SELECT 1; DROP TABLE projects")

    @pytest.mark.parametrize(
        "query",
        [
            "SELECT * FROM pg_catalog.pg_user",
            "SELECT * FROM information_schema.tables",
            'SELECT * FROM "other_pod"."secrets"',
        ],
    )
    def test_schema_qualified_references_rejected(self, query):
        with pytest.raises(DatastoreQueryError, match="Schema-qualified"):
            analyze_query(query)

    def test_unparseable_rejected(self):
        with pytest.raises(DatastoreQueryError, match="parse"):
            analyze_query("not valid sql ((")

    def test_empty_rejected(self):
        with pytest.raises(DatastoreQueryError, match="Empty"):
            analyze_query("   ")


class TestSetConfigIsRejected:
    """RLS reads GUCs any session role may overwrite.

    `SET` as a statement was already blocked, but `set_config()` is an ordinary
    function call, so it rode along inside a read-only SELECT. In a MATERIALIZED
    CTE it runs before the outer scan, flipping the policy's `is_pod_admin`
    branch to true and returning every user's rows.
    """

    @pytest.mark.parametrize(
        "query",
        [
            # The reported exploit, verbatim in shape.
            "WITH escalate AS MATERIALIZED ("
            "SELECT set_config('app.current_user_is_pod_admin','true','true')"
            ") SELECT t.* FROM escalate, expenses t",
            # Impersonating another user rather than escalating.
            "WITH s AS MATERIALIZED ("
            "SELECT set_config('app.current_user_id','00000000-0000-0000-0000-000000000001','true')"
            ") SELECT t.* FROM s, expenses t",
            "SELECT set_config('app.current_user_is_pod_admin','true',true)",
            "SELECT pg_catalog.set_config('app.current_user_is_pod_admin','true',true)",
            "SELECT SET_CONFIG('app.current_user_is_pod_admin','true',true)",
            # Buried in a subquery and in a projection expression.
            "SELECT * FROM expenses WHERE id IN ("
            "SELECT set_config('app.current_user_is_pod_admin','true',true)::int)",
            "SELECT id, coalesce("
            "set_config('app.current_user_is_pod_admin','true',true), '') FROM expenses",
        ],
    )
    def test_set_config_rejected_anywhere_in_the_tree(self, query):
        with pytest.raises(DatastoreQueryError, match="not allowed"):
            analyze_query(query)

    @pytest.mark.parametrize(
        "query",
        [
            # Reading settings is harmless -- only writing them forges the context.
            "SELECT current_setting('app.current_user_id', TRUE) FROM expenses",
            "SELECT count(*), max(amount) FROM expenses",
            "SELECT id FROM expenses WHERE name = 'set_config'",
        ],
    )
    def test_ordinary_queries_still_pass(self, query):
        analyze_query(query)


def test_extract_referenced_tables_delegates_to_analyze_query():
    assert extract_referenced_tables(
        "SELECT * FROM customers c JOIN orders o ON o.cid = c.id"
    ) == {"customers", "orders"}


class TestAnalyzeQueryFoldsUnquotedNamesLikePostgres:
    """The name authorization is checked against must be the name Postgres reads.

    PostgreSQL folds an unquoted identifier to lower case at parse time, so
    ``SELECT * FROM Users`` reads the relation ``users``. The authorizer
    enforces ``DATASTORE_TABLE_READ`` on whatever this analysis returns, so
    returning ``Users`` for that statement checks the permission on one table
    and serves the rows of another — in a pod holding both, a table-level
    grant on ``Users`` reads ``users``.
    """

    def test_an_unquoted_name_is_the_relation_postgres_resolves(self):
        assert analyze_query("SELECT * FROM Users").tables == {"users"}

    def test_a_quoted_name_keeps_its_case(self):
        assert analyze_query('SELECT * FROM "Users"').tables == {"Users"}

    def test_the_two_spellings_are_not_the_same_table(self):
        """Both are authorizable names; the point is that they differ."""
        unquoted = analyze_query("SELECT * FROM Users").tables
        quoted = analyze_query('SELECT * FROM "Users"').tables

        assert unquoted != quoted

    def test_folding_applies_to_every_relation_in_the_statement(self):
        query = 'SELECT * FROM UsErS u JOIN "Orders" o ON u.id = o.uid'

        assert analyze_query(query).tables == {"users", "Orders"}

    def test_an_unquoted_cte_reference_is_still_not_a_table(self):
        query = "WITH Recent AS (SELECT * FROM expenses) SELECT * FROM Recent"

        assert analyze_query(query).tables == {"expenses"}


class TestSelectIntoIsNotReadOnly:
    """`PS-DATA-020`: a query that attempts to change data is refused.

    ``SELECT * INTO t FROM x`` creates a table in PostgreSQL, and sqlglot models
    it as a ``Select`` carrying an ``Into`` node -- so the read-only root check
    accepted it. Nothing was ever written (the transaction is READ ONLY), but
    the refusal came from the driver rather than from the analyzer, after the
    authorizer had been asked for READ on the invented name.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "SELECT * INTO newt FROM expenses",
            "SELECT id, amount INTO TEMP scratch FROM expenses WHERE amount > 1",
            "WITH c AS (SELECT * FROM expenses) SELECT * INTO copied FROM c",
        ],
    )
    def test_select_into_is_refused_by_the_analyzer(self, query):
        with pytest.raises(DatastoreQueryError, match="read-only"):
            analyze_query(query)

    def test_the_invented_table_is_never_offered_for_authorization(self):
        """The refusal has to come first, or the name reaches ``get_tables``."""
        with pytest.raises(DatastoreQueryError):
            analyze_query("SELECT * INTO newt FROM expenses")
