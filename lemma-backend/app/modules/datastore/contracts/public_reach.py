"""What a pod has made Public, as a run answering somebody outside it finds it.

A group's page offers "answer people outside the pod from what is Public", and
until now never said what that was. A file shared with "anyone with a Lemma
account" is Public too -- the same mark -- so a member could open a group to
strangers without knowing that a changelog they had shared with one colleague
was now the bot's to read out. This lists it.

Read through the anonymous context the stranger's run itself is given, so the
list and what the run can reach cannot disagree: a change to what Public means
changes both.

A submodule for the same reason as its siblings: it reaches the repositories,
and ``contracts/__init__`` is imported by anything that wants any contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.anonymous import build_anonymous_context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.infrastructure.repositories.file_repository import (
    DatastoreFileRepository,
)
from app.modules.datastore.infrastructure.repositories.table_repository import (
    DatastoreTableRepository,
)


@dataclass(frozen=True, slots=True)
class PublicFile:
    """A Public file or folder, named the way a member can find it."""

    name: str
    #: The pod path; ``None`` for one in somebody's personal files, whose path
    #: begins with their user id and means nothing to anybody else.
    path: str | None


@dataclass(frozen=True, slots=True)
class PublicReach:
    """The Public files and tables of one pod, the first ``limit`` of each."""

    #: Folders as well as files, in path order.
    files: list[PublicFile]
    tables: list[str]
    #: More of either exists than was listed.
    more: bool


async def public_reach(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    organization_id: UUID | None,
    limit: int = 20,
) -> PublicReach:
    """What a run answering an outsider in this pod may read."""
    ctx = build_anonymous_context(
        session=uow.session,
        pod_id=pod_id,
        organization_id=organization_id,
        actor_id=f"public-reach:{pod_id}",
    )
    files = await DatastoreFileRepository(uow).list_readable_anywhere(
        pod_id, ctx, limit=limit + 1
    )
    tables, next_cursor = await DatastoreTableRepository(uow).list_visible_by_datastore(
        pod_id, ctx, limit=limit
    )
    return PublicReach(
        files=[_public_file(entry.path, entry.name) for entry in files[:limit]],
        tables=[table.table_name for table in tables],
        more=len(files) > limit or next_cursor is not None,
    )


def _public_file(path: str, name: str) -> PublicFile:
    first = path.strip("/").split("/", 1)[0]
    try:
        UUID(first)
    except ValueError:
        return PublicFile(name=name, path=path)
    return PublicFile(name=name, path=None)


__all__ = ["PublicFile", "PublicReach", "public_reach"]
