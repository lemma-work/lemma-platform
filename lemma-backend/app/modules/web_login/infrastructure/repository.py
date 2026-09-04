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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_cipher
from app.core.crypto.ports import SecretCipher
from app.modules.web_login.domain.entities import (
    WebLogin,
    WebLoginKind,
    WebLoginSecret,
)
from app.modules.web_login.infrastructure.models import (
    SecretEnvelope,
    WebLoginAuditModel,
    WebLoginModel,
)


class WebLoginNotFound(Exception):
    """No saved login for that id, or it is not this person's."""


class WebLoginRepository:
    def __init__(
        self, session: AsyncSession, cipher: SecretCipher | None = None
    ) -> None:
        self._session = session
        self._cipher = cipher or get_secret_cipher()

    async def list_for_user(self, user_id: UUID) -> list[WebLogin]:
        rows = (
            await self._session.execute(
                select(WebLoginModel)
                .where(WebLoginModel.user_id == user_id)
                .order_by(WebLoginModel.origin)
            )
        ).scalars()
        return [_to_entity(row) for row in rows]

    async def get_for_origin(self, user_id: UUID, origin: str) -> WebLogin | None:
        row = await self._row_for_origin(user_id, origin)
        return _to_entity(row) if row is not None else None

    async def save(
        self,
        *,
        user_id: UUID,
        origin: str,
        label: str,
        kind: WebLoginKind,
        secret: WebLoginSecret,
        expires_hint_at: datetime | None = None,
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
                label=label,
                kind=kind.value,
                secret=encrypted,
                expires_hint_at=expires_hint_at,
            )
            self._session.add(row)
        else:
            row.label = label
            row.kind = kind.value
            row.secret = encrypted
            row.expires_hint_at = expires_hint_at
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
            state=payload.get("state"),
            username=payload.get("username"),
            password=payload.get("password"),
            totp_seed=payload.get("totp_seed"),
        )

    async def mark_used(self, user_id: UUID, origin: str) -> None:
        row = await self._row_for_origin(user_id, origin)
        if row is not None:
            row.last_used_at = datetime.now(timezone.utc)
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
        web_login_id: UUID | None = None,
        conversation_id: UUID | None = None,
        actor: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Append to the audit trail.

        Takes no secret and no page content by construction: there is nowhere to
        put either.
        """
        self._session.add(
            WebLoginAuditModel(
                web_login_id=web_login_id,
                user_id=user_id,
                conversation_id=conversation_id,
                origin=origin,
                action=action,
                outcome=outcome,
                actor=actor,
                detail=(detail or None) and detail[:500],
            )
        )
        await self._session.flush()

    async def history_for_user(
        self, user_id: UUID, *, limit: int = 100
    ) -> list[WebLoginAuditModel]:
        return list(
            (
                await self._session.execute(
                    select(WebLoginAuditModel)
                    .where(WebLoginAuditModel.user_id == user_id)
                    .order_by(WebLoginAuditModel.created_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )

    async def _row_for_origin(self, user_id: UUID, origin: str) -> WebLoginModel | None:
        return (
            await self._session.execute(
                select(WebLoginModel).where(
                    WebLoginModel.user_id == user_id,
                    WebLoginModel.origin == origin,
                )
            )
        ).scalar_one_or_none()


def _secret_to_json(secret: WebLoginSecret) -> dict[str, str | None]:
    return {
        "state": secret.state,
        "username": secret.username,
        "password": secret.password,
        "totp_seed": secret.totp_seed,
    }


def _to_entity(row: WebLoginModel) -> WebLogin:
    return WebLogin(
        id=row.id,
        user_id=row.user_id,
        origin=row.origin,
        label=row.label,
        kind=WebLoginKind(row.kind),
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_used_at=row.last_used_at,
        expires_hint_at=row.expires_hint_at,
    )
