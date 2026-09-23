"""The two statements a directory tree needs, and the reason there are two.

A tree shows every folder but caps files at `files_per_directory` in each one.
Fetching the pod and slicing in Python reached that shape at O(files) — twice
over, since the visibility filter was a second scan — to render an answer whose
size is O(folders x files_per_directory).

Beside the repository rather than inside it, like `file_visibility_sql`: this is
the piece with the reasoning in it, and the repository should read as "run these
and hydrate the rows".
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import aliased

from app.core.authorization.context import Context
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import allowed_actions_contains
from app.core.infrastructure.db.sql_text import escape_like
from app.modules.datastore.domain.file_entities import FileKind
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.infrastructure.repositories.file_visibility_sql import (
    has_unreadable_ancestor,
)


def tree_statements(
    actions,
    pod_id: UUID,
    ctx: Context,
    *,
    subtree_root: str,
    files_per_directory: int,
    walk_ancestors: bool,
) -> tuple[Select, Select]:
    """``(folders, files)`` for one subtree, both already visibility-filtered.

    Folders come back whole — they are the tree's shape, and there are far fewer
    of them than files. Files are ranked within their own directory and cut at
    ``files_per_directory + 1``; the extra row is not there to be shown, it is
    how the caller still knows the directory was truncated.

    Visibility is applied *inside* the window rather than over its result.
    Ranking first and filtering after would quietly show fewer files than the cap
    in any directory whose first entries the caller cannot read — the rows would
    be spent on files that were then dropped.
    """
    visible = allowed_actions_contains(actions, Permissions.FOLDER_READ)
    if walk_ancestors:
        visible = and_(visible, ~has_unreadable_ancestor(ctx, pod_id))

    scope = [DatastoreFile.pod_id == pod_id, visible]
    if subtree_root != "/":
        scope.append(
            DatastoreFile.path.like(f"{escape_like(subtree_root)}/%", escape="!")
        )

    folders = (
        select(DatastoreFile)
        .where(*scope, DatastoreFile.kind == FileKind.FOLDER.value)
        .order_by(DatastoreFile.path)
    )

    # The parent path, derived rather than stored. It only has to agree with
    # itself — siblings must land in one partition — not with the Python
    # `_parent_path` that groups the rows again once they are back.
    parent_path = func.regexp_replace(DatastoreFile.path, "/[^/]+$", "")
    ranked = (
        select(
            DatastoreFile,
            func.row_number()
            .over(
                partition_by=parent_path,
                # The same order the caller sorts by before it slices, so the
                # rows kept here are the rows it would have shown.
                order_by=func.lower(DatastoreFile.name),
            )
            .label("rank"),
        )
        .where(*scope, DatastoreFile.kind == FileKind.FILE.value)
        .subquery()
    )
    capped = aliased(DatastoreFile, ranked)
    files = select(capped).where(ranked.c.rank <= files_per_directory + 1)
    return folders, files
