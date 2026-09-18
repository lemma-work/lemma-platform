"""Reading and writing saved logins, with the secret encrypted at rest.

Two rules run through this file.

**The secret leaves only through one door.** Listing, reading and auditing all
return :class:`WebLogin`, which has no secret field at all — a type that cannot
carry it cannot leak it. Exactly one method returns the decrypted secret, it is
named for what it does, and its only caller is the injection bridge.

**Encryption goes through the shared cipher**, so a saved session rotates with
everything else the platform encrypts. That is why ``web_logins.secret`` joins
``app/core/crypto/rotation.py``'s registry rather than inventing its own scheme.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_cipher
from app.core.crypto.ports import SecretCipher
from app.modules.web_login.services.scope import BrowserState, pick_for_site
from app.modules.web_login.domain.entities import (
    WebLogin,
    WebLoginSecret,
    WebLoginStatus,
)
from app.modules.web_login.infrastructure.models import (
    SecretEnvelope,
    WebLoginAuditModel,
    WebLoginModel,
)


#: How many saved logins one listing returns. Far above what a person
#: accumulates in practice, and still a number rather than "all of them".
MAX_LOGINS_LISTED = 500


class WebLoginNotFound(Exception):
    """No saved login for that id, or it is not this person's."""


class WebLoginRepository:
    def __init__(
        self, session: AsyncSession, cipher: SecretCipher | None = None
    ) -> None:
        self._session = session
        self._cipher = cipher or get_secret_cipher()

    async def list_for_user(
        self, user_id: UUID, *, limit: int = MAX_LOGINS_LISTED
    ) -> list[WebLogin]:
        """Every site this person has a saved login for, up to `limit`.

        The unpaged read, for callers that want the whole set in memory --
        `pick_for_site` is the one that matters, because choosing between
        saved logins cannot be done a page at a time. `page_for_user` is what
        the API serves.
        """
        page, _ = await self.page_for_user(user_id, limit=limit, after_origin=None)
        return page

    async def page_for_user(
        self, user_id: UUID, *, limit: int, after_origin: str | None
    ) -> tuple[list[WebLogin], str | None]:
        """One page of saved logins, and the origin to continue after.

        Keyset rather than offset: `(user_id, origin)` is unique and is
        already the sort key, so a cursor on it cannot skip or repeat a row
        when the set changes between pages -- which it does, because forgetting
        a login is the main thing people do on this screen.

        One row is read beyond the page to decide whether there is another,
        because a full page is not itself proof that more exist.
        """
        statement = (
            select(WebLoginModel)
            .where(WebLoginModel.user_id == user_id)
            .order_by(WebLoginModel.origin)
            .limit(limit + 1)
        )
        if after_origin:
            statement = statement.where(WebLoginModel.origin > after_origin)
        rows = list((await self._session.execute(statement)).scalars())
        has_more = len(rows) > limit
        page = [_to_entity(row) for row in rows[:limit]]
        return page, (page[-1].origin if has_more and page else None)

    async def get_for_origin(self, user_id: UUID, origin: str) -> WebLogin | None:
        row = await self._row_for_origin(user_id, origin)
        return _to_entity(row) if row is not None else None

    async def get_for_site(self, user_id: UUID, origin: str) -> WebLogin | None:
        """The saved login that authenticates `origin`, exact or same-site.

        The choice is `pick_for_site`; this is only the query that feeds it.
        """
        exact = await self.get_for_origin(user_id, origin)
        if exact is not None:
            return exact
        return pick_for_site(origin, await self.list_for_user(user_id))

    async def save(
        self,
        *,
        user_id: UUID,
        origin: str,
        secret: WebLoginSecret,
    ) -> WebLogin:
        """Store a login for an origin, replacing any the person already had.

        Replacing rather than adding, because an agent handed two sessions for
        one site has no way to choose between them.
        """
        # The cipher's signature is `dict[str, Any]` because it serves every
        # encrypted column; what it returns for this one is the v2 envelope,
        # whose values are all strings.
        encrypted = cast(
            SecretEnvelope,
            await self._cipher.encrypt_json_async(_secret_to_json(secret)),
        )
        row = await self._row_for_origin(user_id, origin)
        if row is None:
            row = WebLoginModel(
                user_id=user_id,
                origin=origin,
                status=WebLoginStatus.ACTIVE.value,
                secret=encrypted,
            )
            self._session.add(row)
        else:
            # A replacement is a working session by definition: somebody just
            # signed in. Anything previously marked dead is alive again.
            row.status = WebLoginStatus.ACTIVE.value
            row.secret = encrypted
        await self._session.flush()
        return _to_entity(row)

    async def reveal_secret(self, user_id: UUID, origin: str) -> WebLoginSecret | None:
        """The decrypted secret, for injection only.

        The one method that returns it. Everything else in this repository
        deals in :class:`WebLogin`, which has no field to put it in.
        """
        row = await self._row_for_origin(user_id, origin)
        if row is None:
            return None
        payload = await self._cipher.decrypt_json_async(row.secret)
        return WebLoginSecret(
            cookies=list(payload.get("cookies") or []),
            origins=list(payload.get("origins") or []),
        )

    async def mark_used(self, user_id: UUID, origin: str) -> None:
        row = await self._row_for_origin(user_id, origin)
        if row is not None:
            row.last_used_at = datetime.now(timezone.utc)
            await self._session.flush()

    async def mark_dead(self, user_id: UUID, origin: str) -> None:
        """Record that a stored session no longer signs anybody in.

        Set when an injection lands on a page that still wants a login. The row
        stays: the person should see that the login is there and has stopped
        working, rather than find it silently gone.
        """
        row = await self._row_for_origin(user_id, origin)
        if row is not None:
            row.status = WebLoginStatus.DEAD.value
            await self._session.flush()

    async def delete(self, user_id: UUID, origin: str) -> WebLogin:
        row = await self._row_for_origin(user_id, origin)
        if row is None:
            raise WebLoginNotFound(origin)
        entity = _to_entity(row)
        await self._session.delete(row)
        await self._session.flush()
        return entity

    async def record(
        self,
        *,
        user_id: UUID,
        origin: str,
        action: str,
        outcome: str,
        conversation_id: UUID | None = None,
        detail: str | None = None,
    ) -> None:
        """Append to the audit trail.

        Takes no secret and no page content by construction: there is nowhere to
        put either.
        """
        self._session.add(
            WebLoginAuditModel(
                user_id=user_id,
                conversation_id=conversation_id,
                origin=origin,
                action=action,
                outcome=outcome,
                detail=(detail or None) and detail[:500],
            )
        )
        await self._session.flush()

    async def history_for_user(
        self, user_id: UUID, *, limit: int = 100
    ) -> list[WebLoginAuditModel]:
        """The newest `limit` audit rows. `page_history_for_user` paginates."""
        page, _ = await self.page_history_for_user(user_id, limit=limit, after=None)
        return page

    async def page_history_for_user(
        self, user_id: UUID, *, limit: int, after: tuple[datetime, UUID] | None
    ) -> tuple[list[WebLoginAuditModel], tuple[datetime, UUID] | None]:
        """One page of the audit trail, newest first, and where to continue.

        Keyed on `(created_at, id)` rather than `created_at` alone: several
        rows can share a timestamp -- a capture and the inject that follows it
        land in the same millisecond often enough -- and a cursor that cannot
        break that tie either repeats them or drops them.

        Offset paging would be worse than untidy here. This log is append-only
        and read newest-first, so anything written between two pages shifts
        every later row down and the reader sees the same entry twice.
        """
        statement = (
            select(WebLoginAuditModel)
            .where(WebLoginAuditModel.user_id == user_id)
            .order_by(
                WebLoginAuditModel.created_at.desc(), WebLoginAuditModel.id.desc()
            )
            .limit(limit + 1)
        )
        if after is not None:
            created_at, row_id = after
            statement = statement.where(
                tuple_(WebLoginAuditModel.created_at, WebLoginAuditModel.id)
                < tuple_(created_at, row_id)
            )
        rows = list((await self._session.execute(statement)).scalars())
        has_more = len(rows) > limit
        page = rows[:limit]
        if not (has_more and page):
            return page, None
        return page, (page[-1].created_at, page[-1].id)

    async def _row_for_origin(self, user_id: UUID, origin: str) -> WebLoginModel | None:
        return (
            await self._session.execute(
                select(WebLoginModel).where(
                    WebLoginModel.user_id == user_id,
                    WebLoginModel.origin == origin,
                )
            )
        ).scalar_one_or_none()


def _secret_to_json(secret: WebLoginSecret) -> BrowserState:
    return {"cookies": secret.cookies, "origins": secret.origins}


def _to_entity(row: WebLoginModel) -> WebLogin:
    return WebLogin(
        id=row.id,
        user_id=row.user_id,
        origin=row.origin,
        status=WebLoginStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_used_at=row.last_used_at,
    )
