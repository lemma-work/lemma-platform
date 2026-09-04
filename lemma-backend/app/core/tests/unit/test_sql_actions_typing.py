"""``allowed_actions_contains`` must present itself to SQLAlchemy as a boolean.

This looks like a detail and is not. ``.op("@>")`` inherits the *left* operand's
type, so without an explicit ``return_type`` the expression is labelled
``text[]`` even though PostgreSQL returns a boolean. In a WHERE clause nothing
reads the value back and the mislabelling is invisible; the first time anyone
SELECTed it — projecting visibility as a column rather than filtering on it —
SQLAlchemy handed ``True`` to the array result processor and it raised
``'bool' object is not iterable``.

The bug shipped and reached an e2e run. A unit test that asks the expression
what type it thinks it is costs nothing and would have caught it at the source.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Boolean, Column, MetaData, String, Table
from sqlalchemy.dialects.postgresql import ARRAY

from app.core.authorization.sql_actions import allowed_actions_contains

pytestmark = pytest.mark.unit


def _actions_column():
    table = Table(
        "resources",
        MetaData(),
        Column("allowed_actions", ARRAY(String)),
    )
    return table.c.allowed_actions


def test_the_containment_test_is_typed_as_a_boolean() -> None:
    expression = allowed_actions_contains(_actions_column(), "folder.read")

    assert isinstance(expression.type, Boolean), (
        "the containment test is typed as "
        f"{expression.type!r}; SELECTing it will run the wrong result "
        "processor over a boolean and raise"
    )


def test_it_still_compiles_to_the_array_containment_operator() -> None:
    """The type annotation must not have changed the SQL it generates."""
    expression = allowed_actions_contains(_actions_column(), "folder.read")

    assert "@>" in str(expression), (
        f"the containment test no longer uses the @> operator: {expression}"
    )
