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
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    DatastoreSignedLinkEntity,
)
from app.modules.datastore.infrastructure.repositories.signed_link_repository import (
    SignedLinkRepository,
)
from app.modules.datastore.services.files.signed_url import get_signed_url_store


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
        self, pod_id: UUID, user_id: UUID | None, *, include_dead: bool = False
    ) -> list[DatastoreSignedLinkEntity]:
        """The links this person has handed out in this pod, newest first.

        Not per-file — that question cannot be asked one file at a time — and
        not pod-wide either; see ``SignedLinkRepository.list_for_user`` for why
        the caller's own links are the only safe scope.
        """
        return await self._links().list_for_user(
            pod_id, user_id, include_dead=include_dead
        )

    async def revoke(self, pod_id: UUID, code: str) -> bool:
        """Kill a public link. Returns whether it was live until now.

        There was previously no way to do this at all: a link shared by mistake
        ran its full lifetime, which mattered rather more once that became seven
        days instead of three hours.
        """
        return await get_signed_url_store().revoke(pod_id, code, links=self._links())
