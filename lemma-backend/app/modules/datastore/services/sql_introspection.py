from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.modules.datastore.domain.errors import DatastoreQueryError

# Mutation / DDL node types. If any of these appear anywhere in the parsed tree
# (including inside a CTE, e.g. ``WITH d AS (DELETE ... RETURNING *) SELECT ...``),
# the statement is not read-only and is rejected. ``exp.Command`` captures
# statements sqlglot does not model structurally (SET, VACUUM, etc.).
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
    exp.Copy,
    exp.Command,
)

# Functions that change session state. `SET` as a statement is already blocked
# by ``exp.Command``, but ``set_config()`` is an ordinary function call and so
# rides along inside an otherwise read-only SELECT. That matters because RLS is
# enforced through GUCs (``app.current_user_id``,
# ``app.current_user_is_pod_admin``) which any session role may overwrite: put
# the call in a MATERIALIZED CTE and it runs before the outer scan, so the query
# reads every user's rows. Revoking the parameters at the database level does
# not help -- PostgreSQL keeps no ACL for customized options, so
# ``REVOKE SET ON PARAMETER app.foo`` silently leaves the placeholder settable.
_FORBIDDEN_FUNCTIONS = frozenset({"set_config"})

# Read-only statement roots permitted as the top-level expression.
_ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Union,
    exp.Intersect,
    exp.Except,
    exp.Subquery,
    exp.With,
)


@dataclass(frozen=True)
class QueryAnalysis:
    """Result of statically analyzing an ad-hoc datastore SQL query."""

    tables: frozenset[str]


def analyze_query(sql: str) -> QueryAnalysis:
    """Parse and validate an ad-hoc datastore SQL query.

    Enforces, raising :class:`DatastoreQueryError` (HTTP 400) on any violation:

    * exactly one statement (blocks stacked statements like ``...; DROP ...``),
    * read-only — the root must be a SELECT-family expression and the tree must
      contain no mutation / DDL nodes,
    * no schema- or catalog-qualified table references (pod tables are referenced
      by their bare name under the pod ``search_path``; this blocks
      ``pg_catalog.*``, ``information_schema.*`` and other pods' schemas).

    Returns the set of bare base-table names referenced (CTE aliases excluded) so
    the caller can enforce per-table read authorization. Names that are not
    registered datastore tables are rejected downstream by ``get_table``.
    """
    try:
        statements = [
            stmt
            for stmt in sqlglot.parse(sql, dialect="postgres")
            if stmt is not None
        ]
    except SqlglotError as exc:
        raise DatastoreQueryError(f"Could not parse SQL query: {exc}") from exc

    if not statements:
        raise DatastoreQueryError("Empty SQL query")
    if len(statements) > 1:
        raise DatastoreQueryError("Only a single SQL statement is allowed")

    statement = statements[0]

    if not isinstance(statement, _ALLOWED_ROOTS) or statement.find(*_FORBIDDEN_NODES):
        raise DatastoreQueryError("Only read-only SELECT queries are allowed")

    for function in statement.find_all(exp.Func):
        # sqlglot parses functions it does not model -- `set_config` among them
        # -- as `Anonymous`, whose name is the function name. Named nodes report
        # theirs through `sql_name()`; a schema qualifier does not change either.
        name = (
            function.name
            if isinstance(function, exp.Anonymous)
            else function.sql_name()
        )
        if name.lower() in _FORBIDDEN_FUNCTIONS:
            raise DatastoreQueryError(
                f"{name}() is not allowed in datastore queries"
            )

    cte_aliases = {cte.alias for cte in statement.find_all(exp.CTE) if cte.alias}

    tables: set[str] = set()
    for table in statement.find_all(exp.Table):
        if table.catalog or table.db:
            raise DatastoreQueryError(
                "Schema-qualified table references are not allowed; "
                "reference datastore tables by their bare name."
            )
        name = table.name
        if not name or name in cte_aliases:
            continue
        tables.add(name)

    return QueryAnalysis(tables=frozenset(tables))


def extract_referenced_tables(query: str) -> set[str]:
    """Backwards-compatible helper returning referenced base-table names.

    Delegates to :func:`analyze_query`, so it now also validates the statement.
    """
    return set(analyze_query(query).tables)
