"""Whether a file is hidden by a folder above it, as one SQL predicate.

Kept beside the repository rather than inside it because this is the piece with
the sharpest edges: it is an authorization rule, it is easy to write in a form
that is correct but quadratic, and it has already been wrong in both directions
once. The reasoning belongs somewhere it can be read on its own.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, func, literal, literal_column, select
from sqlalchemy.orm import aliased

from app.core.authorization.context import Context, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.modules.datastore.infrastructure.models import DatastoreFile


def _ancestor_paths_of(path_col, correlate_with):
    """The ancestor folder paths of *path_col*, as a SQL array.

    ``/a/b/c.md`` -> ``{"/", "/a", "/a/b"}``: the prefix ending at each ``/``,
    with the root spelled ``/`` rather than the empty string. This is the exact
    set ``PathResolver.ancestor_paths`` computes in Python, minus the row
    itself.

    Deriving the ancestors *from the row* rather than testing every other row
    for prefix-hood is what makes this usable. The obvious formulation —
    ``LEFT(descendant.path, LENGTH(ancestor.path) + 1) = ancestor.path || '/'``
    — is a theta-join on a function of both sides, so no index can serve it and
    the planner has no choice but a full inner scan per outer row. Measured on a
    16,000-file pod that was 6.6 seconds; matching these paths by equality
    against the unique ``(pod_id, path)`` index is 0.14.

    Equality also sidesteps a subtler trap. A range formulation
    (``path >= prefix || '/' AND path < prefix || '0'``) is only correct under
    byte ordering, and this database runs ``en_US.utf8`` — it silently returned
    the wrong set when tried.
    """
    position = literal_column("i")
    return func.array(
        select(
            case(
                (position == 1, literal("/")),
                else_=func.left(path_col, position - 1),
            )
        )
        .select_from(func.generate_series(1, func.length(path_col)).alias("i"))
        .where(func.substr(path_col, position, 1) == "/")
        # Without this SQLAlchemy sees ``path_col`` unaccounted for and adds
        # its table to this subquery's FROM, cross-joining the whole table and
        # returning the ancestors of *every* row rather than of this one.
        .correlate(correlate_with)
        .scalar_subquery()
    )


def has_unreadable_ancestor(ctx: Context, pod_id: UUID):
    """A correlated EXISTS over the folders above each row.

    The Python walk this replaces climbed parent by parent and stopped at the
    first ancestor row that was *missing*, so a gap in the folder chain let
    everything above it go unchecked. This does not stop: an unreadable folder
    anywhere above hides the row. That is the direction the rule was reaching
    for — a file under a folder you cannot read should not appear — and it is
    the fail-closed side of the difference.

    Nothing here assumes only folders can be ancestors. That assumption would
    be a cheaper query and the wrong kind of wrong: a non-folder row that did
    have descendants would be skipped, and its descendants would become
    visible.
    """
    ancestor = aliased(DatastoreFile)
    ancestor_actions = allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.DOCUMENT,
        resource_id_col=ancestor.id,
        pod_id_col=ancestor.pod_id,
        owner_user_id_col=ancestor.owner_user_id,
        visibility_col=ancestor.visibility,
        resource_path_col=ancestor.path,
    )
    return (
        select(ancestor.id)
        .where(
            ancestor.pod_id == pod_id,
            ancestor.path
            == func.any(_ancestor_paths_of(DatastoreFile.path, DatastoreFile)),
            ancestor.id != DatastoreFile.id,
            ~allowed_actions_contains(ancestor_actions, Permissions.FOLDER_READ),
        )
        .exists()
    )
