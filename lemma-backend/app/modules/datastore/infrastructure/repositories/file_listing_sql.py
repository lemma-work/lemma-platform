"""Statements for the two questions a file listing can ask.

"What is in this folder" and "what is under this folder" differ by one pattern,
and that one pattern is the difference between `PS-DATA-031` -- list a folder's
contents without loading the tree -- and loading the tree. Keeping both here
makes the pair visible: a caller reaching for the second when it wants the first
is the shape of every listing defect this module has had.

Beside the repository rather than inside it because that file is at the
architecture ratchet's per-file ceiling, and because the patterns below are
worth reading as a rule rather than as four near-identical `where` clauses --
the same reason `file_visibility_sql` and `file_tree_sql` sit next to it.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, Update, func, select, tuple_, update

from app.core.infrastructure.db.sql_text import escape_like
from app.modules.datastore.infrastructure.models import DatastoreFile


def direct_child_patterns(directory_path: str) -> tuple[str, str]:
    """``(direct, nested)`` LIKE patterns for one directory's own entries.

    The second is the one that matters: a path matching ``/a/%`` but not
    ``/a/%/%`` is a child of ``/a`` and not a grandchild. Without it a listing
    returns the whole subtree, which is right for a tree view and wrong for a
    folder.

    ``/`` is special-cased because it is the one path that does not end a
    segment: ``escape_like("/") + "/%"`` would look for ``//...``.
    """
    if directory_path == "/":
        return "/%", "/%/%"
    escaped = escape_like(directory_path)
    return f"{escaped}/%", f"{escaped}/%/%"


def descendant_pattern(path_prefix: str) -> str:
    """The LIKE pattern for everything under a path, at any depth."""
    return f"{escape_like(path_prefix)}/%"


def descendants_of(pod_id: UUID, path_prefix: str) -> Select:
    """Everything under a path, at any depth, in path order."""
    return (
        select(DatastoreFile)
        .where(
            DatastoreFile.pod_id == pod_id,
            DatastoreFile.path.like(descendant_pattern(path_prefix), escape="!"),
        )
        .order_by(DatastoreFile.path)
    )


def direct_children_of(pod_id: UUID, directory_path: str) -> Select:
    """One directory's own entries, in path order."""
    direct, nested = direct_child_patterns(directory_path)
    return (
        select(DatastoreFile)
        .where(
            DatastoreFile.pod_id == pod_id,
            DatastoreFile.path.like(direct, escape="!"),
            ~DatastoreFile.path.like(nested, escape="!"),
        )
        .order_by(DatastoreFile.path)
    )


def repoint_descendants(
    pod_id: UUID,
    *,
    previous_prefix: str,
    new_prefix: str,
    planned: Sequence[tuple[UUID, str]],
) -> Update:
    """Repoint the descendants a rename staged, in one statement.

    A prefix substitution the database can do itself: keep the part of the path
    after the old prefix, put the new one in front of it. `substring` is
    1-indexed, which is why the offset is the prefix length plus one.

    Matched on ``(id, path)`` pairs rather than on the prefix, and that pairing
    is the point. The copy plan is taken before the storage phase and the bytes
    are copied from it, so the only rows whose new path names an object that
    exists are the rows that still hold the path they were copied from. An
    earlier version matched the prefix and checked the row *count* afterwards,
    which is a fence against rows arriving or leaving and no fence at all
    against one being swapped for another: rename a child inside the folder
    while the copy runs and the count is unchanged, while the row that moved
    now points at a key nobody wrote.

    Row-valued ``IN`` is exactly the pairing, and PostgreSQL indexes the id
    half of it. What this statement cannot see is a row that arrived under the
    old prefix after the plan was taken -- it is not in the pairs, so it is not
    moved, and it would be left under a folder that no longer exists.
    ``rewrite_descendant_paths`` looks for that separately.
    """
    return (
        update(DatastoreFile)
        .where(
            DatastoreFile.pod_id == pod_id,
            tuple_(DatastoreFile.id, DatastoreFile.path).in_(list(planned)),
        )
        .values(
            path=func.concat(
                new_prefix,
                func.substring(DatastoreFile.path, len(previous_prefix) + 1),
            )
        )
    )


def stragglers_under(pod_id: UUID, previous_prefix: str) -> Select:
    """Anything still under a renamed folder's old path, capped at one row.

    Read after the repoint. Whatever this finds arrived while the rename was
    running: it has no copied bytes and its parent folder no longer exists at
    that path. One row is enough to refuse on, so it never reads more.
    """
    return (
        select(DatastoreFile.id)
        .where(
            DatastoreFile.pod_id == pod_id,
            DatastoreFile.path.like(descendant_pattern(previous_prefix), escape="!"),
        )
        .limit(1)
    )
