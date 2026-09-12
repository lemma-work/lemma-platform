"""Minting, listing and revoking the public short links to a pod's files.

The collaborator behind ``DatastoreFileService``'s three ``*_signed_url``
methods. Split out because a link has its own record, its own lifetime and its
own revocation, none of which the file it points at knows anything about — and
because the facade is meant to stay a facade.

``services/files/signed_url.py`` is the store underneath: which of Postgres and
Redis owns what, and why, is documented there.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.domain.errors import (
    DatastoreRevocationIncompleteError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    DatastoreSignedLinkEntity,
)
from app.modules.datastore.infrastructure.repositories.signed_link_repository import (
    SignedLinkRepository,
)
from app.modules.datastore.services.files.signed_url import (
    SignedUrlRevocationIncomplete,
    get_signed_url_store,
)


class SignedLinks:
    """Public short links (``/s/{code}``) for one pod's datastore files."""

    def __init__(self, reader, file_repository):
        self._reader = reader
        # The request's own unit of work. Every one of these operations runs
        # inside a request that already holds a pooled connection, so opening a
        # second session here would double this endpoint's connection demand —
        # measured as pool exhaustion under ten concurrent mints.
        self._file_repository = file_repository

    def _links(self) -> SignedLinkRepository:
        return SignedLinkRepository(self._file_repository.uow)

    async def create(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
        expires_seconds: int | None = None,
        max_hits: int | None = None,
    ) -> tuple[DatastoreFileEntity, str, datetime, int]:
        """Mint a public, hit-capped short signed URL for a pod file.

        The returned ``{api_url}/s/{code}`` link needs no auth to open, expires
        after ``expires_seconds`` (clamped to the configured ceiling), and serves
        the bytes at most ``max_hits`` times over (also clamped). Authorization
        to create one mirrors a normal file read — the read is what proves the
        caller could have handed the bytes over anyway.
        """
        entity = await self._reader.get_file_by_path(pod_id, path, ctx.user_id, ctx=ctx)
        if entity.is_folder:
            raise DatastoreValidationError("Folders do not have a downloadable URL")
        (
            _code,
            signed_url,
            expires_at,
            effective_max_hits,
        ) = await get_signed_url_store().create(
            file=entity,
            links=self._links(),
            created_by_user_id=ctx.user_id,
            expires_seconds=expires_seconds,
            max_hits=max_hits,
        )
        return entity, signed_url, expires_at, effective_max_hits

    async def list(
        self,
        pod_id: UUID,
        ctx: Context,
        *,
        include_dead: bool = False,
        limit: int = 100,
        before: datetime | None = None,
        before_id: UUID | None = None,
    ) -> list[DatastoreSignedLinkEntity]:
        """The links this caller handed out and may still read, newest first.

        Not per-file — that question cannot be asked one file at a time — and
        not pod-wide either; see ``SignedLinkRepository.list_visible`` for why
        the scope is minted-by *and* readable-by, and why the second half is not
        the same question as the first for a delegated agent.
        """
        return await self._links().list_visible(
            pod_id,
            ctx,
            include_dead=include_dead,
            limit=limit,
            before=before,
            before_id=before_id,
        )

    async def revoke(self, pod_id: UUID, code: str, ctx: Context) -> bool:
        """Kill a public link. Returns whether it was live until now.

        There was previously no way to do this at all: a link shared by mistake
        ran its full lifetime, which mattered rather more once that became seven
        days instead of three hours.

        Authorized the same way the listing is, and for the same reason: taking
        only the pod let a delegated agent retire its principal's link to a file
        the agent may not read — a capability removed by something that could
        not have been given it.
        """
        links = self._links()
        if not await links.is_visible(pod_id, code, ctx):
            return False
        try:
            return await get_signed_url_store().revoke(pod_id, code, links=links)
        except SignedUrlRevocationIncomplete as exc:
            raise DatastoreRevocationIncompleteError() from exc
