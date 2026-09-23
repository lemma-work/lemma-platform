"""Reading a schedule's JSONB config from SQL without trusting its shape.

`config` is a blob nothing constrains at the column level, so every predicate
over it has to survive a row that holds the wrong type in the right key. The
one that bites is `operations`: matching a datastore event against it is a
membership test over an array, and the function that walks an array *raises* on
a scalar rather than returning nothing.

Kept beside the repository rather than inside it, like `file_listing_sql` and
`file_tree_sql` next to theirs: the reasoning is the point, and the repository
should read as "run this".
"""

from __future__ import annotations

from sqlalchemy import case, func, literal
from sqlalchemy.dialects.postgresql import JSONB


def datastore_operations_array(config_column):
    """The `operations` array, or an empty one for anything that is not an array.

    The guard belongs inside the argument of `jsonb_array_elements_text`, not
    beside the call. Written as a sibling conjunct --
    ``jsonb_typeof(...) = 'array' AND EXISTS (SELECT ... FROM
    jsonb_array_elements_text(...))`` -- the query is only safe if the planner
    evaluates the type check first, and SQL promises no order between the
    operands of `AND`. It does not hold in practice either: a row whose
    `operations` is a string makes that form fail the whole statement with
    "cannot extract elements from a scalar", which is a datastore write failing
    because some other schedule's config is malformed.

    Substituting an empty array before the call is safe whatever runs first,
    because the substitution *is* the argument.

    The empty array is spelled as a list. `literal("[]", JSONB)` serializes the
    Python string into a JSON string -- a scalar -- so the guard would hand the
    function the exact shape it exists to keep away from it.
    """
    operations = func.jsonb_extract_path(config_column, "operations")
    return case(
        (func.jsonb_typeof(operations) == "array", operations),
        else_=literal([], JSONB),
    )
