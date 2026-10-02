"""What a group's people from outside the pod can be answered from, for its page.

The switch on a group's page reads "answer them from what is Public", and the
page used to stop there. Public is one mark with two meanings -- the Share
sheet's "anyone with a Lemma account" and the bot's "anyone in a group opened
to strangers" -- so the page lists what carries it, read the way the stranger's
run reads it (``datastore.contracts.public_reach``).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.contracts.public_reach import public_reach


class PublicFileResponse(BaseModel):
    name: str
    path: str | None = Field(
        default=None,
        description="Absent for a file in somebody's personal files.",
    )


class GroupPublicResponse(BaseModel):
    """The pod's Public files and tables, the first few of each."""

    files: list[PublicFileResponse]
    tables: list[str]
    more: bool = Field(description="More is Public than is listed here.")


async def group_public(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, ctx: Context
) -> GroupPublicResponse:
    """Read with the outsider's authority, not the member's asking: see above."""
    reach = await public_reach(uow, pod_id=pod_id, organization_id=ctx.organization_id)
    return GroupPublicResponse(
        files=[PublicFileResponse(name=f.name, path=f.path) for f in reach.files],
        tables=reach.tables,
        more=reach.more,
    )
