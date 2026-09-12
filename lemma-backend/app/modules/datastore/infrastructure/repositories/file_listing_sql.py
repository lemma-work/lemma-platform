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

from uuid import UUID

from sqlalchemy import Select, select

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
