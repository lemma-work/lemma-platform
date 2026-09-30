"""Reads and writes for clients, grants and token digests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.mcp_access.domain.entities import (
    ConnectedApp,
    TokenKind,
    parse_scopes,
)
from app.modules.mcp_access.infrastructure.models import (
    McpOAuthClient,
    McpOAuthGrant,
    McpOAuthToken,
)

GRANT_MAX_AGE = timedelta(days=180)
"""However regularly a connection is used, it ends this long after the person
allowed it, and the client asks them again. Without a ceiling a sliding refresh
token never expires."""

LAST_USED_RESOLUTION = timedelta(minutes=5)
"""How stale ``last_used_at`` may be. Writing it on every tool call would put a
row update on the hot path of every MCP request, for a column a person reads
as "used today" or "used last month"."""


@dataclass(frozen=True, slots=True)
class StoredClient:
    client_id: str
    registration: str
    client_metadata: dict[str, object]
    client_secret_hash: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class LiveToken:
    """A token digest that matched, with the grant it belongs to."""

    token_id: UUID
    kind: TokenKind
    scopes: list[str]
    expires_at: datetime
    rotated_at: datetime | None
    grant_id: UUID
    user_id: UUID
    pod_id: UUID
    client_id: str
    client_name: str
    resource: str
    grant_scopes: list[str]
    grant_revoked: bool
    grant_last_used_at: datetime | None


def _has_live_token(now: datetime):  # noqa: ANN202 - an EXISTS clause for a WHERE
    """Whether a grant still holds a token someone could use: unexpired, and
    not a refresh token already exchanged."""
    return (
        select(McpOAuthToken.id)
        .where(
            McpOAuthToken.grant_id == McpOAuthGrant.id,
            McpOAuthToken.expires_at > now,
            McpOAuthToken.rotated_at.is_(None),
        )
        .exists()
    )


def _client_name(metadata: dict[str, object], client_id: str) -> str:
    name = metadata.get("client_name")
    return name if isinstance(name, str) and name.strip() else client_id


class McpAccessRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._session = uow.session

    # --- clients -----------------------------------------------------------

    async def get_client(self, client_id: str) -> StoredClient | None:
        row = await self._session.get(McpOAuthClient, client_id)
        if row is None:
            return None
        return StoredClient(
            client_id=row.client_id,
            registration=row.registration,
            client_metadata=dict(row.client_metadata),
            client_secret_hash=row.client_secret_hash,
            updated_at=row.updated_at,
        )

    async def save_client(
        self,
        *,
        client_id: str,
        registration: str,
        client_metadata: dict[str, object],
        client_secret_hash: str | None,
        now: datetime,
    ) -> None:
        statement = insert(McpOAuthClient).values(
            client_id=client_id,
            registration=registration,
            client_metadata=client_metadata,
            client_secret_hash=client_secret_hash,
            created_at=now,
            updated_at=now,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[McpOAuthClient.client_id],
                set_={
                    "client_metadata": statement.excluded.client_metadata,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )

    # --- grants ------------------------------------------------------------

    async def create_grant(
        self,
        *,
        user_id: UUID,
        client_id: str,
        pod_id: UUID,
        scopes: list[str],
        resource: str,
    ) -> UUID:
        """One grant per consent, not one per (person, client, pod).

        Claude Code on two laptops is one client id, and a shared grant meant
        disconnecting one -- or a refresh-token replay on one -- ended both. A
        grant per consent is a connection per device, each listed and ended on
        its own.
        """
        grant = McpOAuthGrant(
            user_id=user_id,
            client_id=client_id,
            pod_id=pod_id,
            scopes=scopes,
            resource=resource,
        )
        self._session.add(grant)
        await self._session.flush()
        return grant.id

    async def list_connected_apps(
        self, *, user_id: UUID | None, pod_id: UUID | None, limit: int
    ) -> list[ConnectedApp]:
        """A person's connections, or -- with no ``user_id`` -- everyone's in
        ``pod_id``, for its admins."""
        query = (
            select(McpOAuthGrant, McpOAuthClient.client_metadata)
            .join(
                McpOAuthClient,
                McpOAuthClient.client_id == McpOAuthGrant.client_id,
            )
            .where(McpOAuthGrant.revoked_at.is_(None))
            .order_by(McpOAuthGrant.created_at.desc())
            .limit(limit)
        )
        if user_id is not None:
            query = query.where(McpOAuthGrant.user_id == user_id)
        if pod_id is not None:
            query = query.where(McpOAuthGrant.pod_id == pod_id)
        rows = (await self._session.execute(query)).all()
        apps: list[ConnectedApp] = []
        for grant, metadata in rows:
            client_uri = metadata.get("client_uri")
            apps.append(
                ConnectedApp(
                    grant_id=grant.id,
                    user_id=grant.user_id,
                    pod_id=grant.pod_id,
                    client_id=grant.client_id,
                    client_name=_client_name(metadata, grant.client_id),
                    client_uri=client_uri if isinstance(client_uri, str) else None,
                    scopes=parse_scopes(list(grant.scopes)),
                    created_at=grant.created_at,
                    last_used_at=grant.last_used_at,
                )
            )
        return apps

    async def revoke_grant(
        self, *, grant_id: UUID, now: datetime, user_id: UUID | None = None
    ) -> bool:
        """End a grant and delete its tokens. ``user_id`` confines it to the
        person's own grants; without it, the caller has already proved the
        grant is the one to end (a refresh token presented twice)."""
        statement = (
            update(McpOAuthGrant)
            .where(McpOAuthGrant.id == grant_id, McpOAuthGrant.revoked_at.is_(None))
            .values(revoked_at=now)
            .returning(McpOAuthGrant.id)
        )
        if user_id is not None:
            statement = statement.where(McpOAuthGrant.user_id == user_id)
        revoked = (await self._session.execute(statement)).scalar_one_or_none()
        if revoked is None:
            return False
        await self._session.execute(
            delete(McpOAuthToken).where(McpOAuthToken.grant_id == grant_id)
        )
        return True

    async def grant_owner(self, grant_id: UUID) -> tuple[UUID, UUID] | None:
        """(user, pod) of a live grant."""
        row = (
            await self._session.execute(
                select(McpOAuthGrant.user_id, McpOAuthGrant.pod_id).where(
                    McpOAuthGrant.id == grant_id, McpOAuthGrant.revoked_at.is_(None)
                )
            )
        ).first()
        return (row[0], row[1]) if row is not None else None

    async def live_grant_scopes(self, grant_id: UUID) -> list[str] | None:
        """The scopes the person last agreed to, or ``None`` when the grant is
        gone or revoked. Read at every issuance, so consenting again with less
        narrows the tokens that follow, not just the list the person sees."""
        scopes = await self._session.scalar(
            select(McpOAuthGrant.scopes).where(
                McpOAuthGrant.id == grant_id,
                McpOAuthGrant.revoked_at.is_(None),
                McpOAuthGrant.created_at > datetime.now(timezone.utc) - GRANT_MAX_AGE,
            )
        )
        return list(scopes) if scopes is not None else None

    async def touch_grant(self, *, grant_id: UUID, now: datetime) -> None:
        await self._session.execute(
            update(McpOAuthGrant)
            .where(
                McpOAuthGrant.id == grant_id,
                (McpOAuthGrant.last_used_at.is_(None))
                | (McpOAuthGrant.last_used_at < now - LAST_USED_RESOLUTION),
            )
            .values(last_used_at=now)
        )

    # --- tokens ------------------------------------------------------------

    async def add_token(
        self,
        *,
        token_hash: str,
        grant_id: UUID,
        kind: TokenKind,
        scopes: list[str],
        expires_at: datetime,
    ) -> None:
        self._session.add(
            McpOAuthToken(
                token_hash=token_hash,
                grant_id=grant_id,
                kind=kind.value,
                scopes=scopes,
                expires_at=expires_at,
            )
        )
        await self._session.flush()

    async def find_token(self, token_hash: str) -> LiveToken | None:
        row = (
            await self._session.execute(
                select(McpOAuthToken, McpOAuthGrant, McpOAuthClient.client_metadata)
                .join(McpOAuthGrant, McpOAuthGrant.id == McpOAuthToken.grant_id)
                .join(
                    McpOAuthClient,
                    McpOAuthClient.client_id == McpOAuthGrant.client_id,
                )
                .where(McpOAuthToken.token_hash == token_hash)
            )
        ).first()
        if row is None:
            return None
        token, grant, metadata = row
        return LiveToken(
            token_id=token.id,
            kind=TokenKind(token.kind),
            scopes=list(token.scopes),
            expires_at=token.expires_at,
            rotated_at=token.rotated_at,
            grant_id=grant.id,
            user_id=grant.user_id,
            pod_id=grant.pod_id,
            client_id=grant.client_id,
            client_name=_client_name(metadata, grant.client_id),
            resource=grant.resource,
            grant_scopes=list(grant.scopes),
            grant_revoked=grant.revoked_at is not None,
            grant_last_used_at=grant.last_used_at,
        )

    async def mark_rotated(self, *, token_id: UUID, now: datetime) -> bool:
        """Claim a refresh token for rotation. False when another request
        already did: exactly one of two concurrent refreshes wins."""
        claimed = (
            await self._session.execute(
                update(McpOAuthToken)
                .where(McpOAuthToken.id == token_id, McpOAuthToken.rotated_at.is_(None))
                .values(rotated_at=now)
                .returning(McpOAuthToken.id)
            )
        ).scalar_one_or_none()
        return claimed is not None

    async def delete_token(self, token_id: UUID) -> None:
        await self._session.execute(
            delete(McpOAuthToken).where(McpOAuthToken.id == token_id)
        )

    async def prune_grant_tokens(
        self, *, grant_id: UUID, keep_rotated: UUID, now: datetime
    ) -> None:
        """Keep a grant to its live tokens and the one refresh token just
        rotated -- the one a replay would present. Older rotated tokens and
        expired ones go, so a grant holds a handful of rows however often it
        refreshes, rather than one per hour for thirty days."""
        await self._session.execute(
            delete(McpOAuthToken).where(
                McpOAuthToken.grant_id == grant_id,
                (McpOAuthToken.expires_at < now)
                | (
                    McpOAuthToken.rotated_at.is_not(None)
                    & (McpOAuthToken.id != keep_rotated)
                ),
            )
        )

    async def sweep(self, *, now: datetime, batch: int) -> int:
        """End grants nobody can use any more, and delete what they held.

        A grant is dead when it has passed `GRANT_MAX_AGE`, or when it has no
        token left that has not expired: its refresh token lapsed unused. At
        most ``batch`` grants per call, so one run is bounded however far
        behind the sweep is.
        """
        live_token = _has_live_token(now)
        ended = list(
            (
                await self._session.execute(
                    select(McpOAuthGrant.id)
                    .where(
                        McpOAuthGrant.revoked_at.is_(None),
                        McpOAuthGrant.created_at < now - timedelta(minutes=10),
                        (McpOAuthGrant.created_at < now - GRANT_MAX_AGE) | ~live_token,
                    )
                    .limit(batch)
                )
            ).scalars()
        )
        if not ended:
            return 0
        await self._session.execute(
            update(McpOAuthGrant)
            .where(McpOAuthGrant.id.in_(ended))
            .values(revoked_at=now)
        )
        await self._session.execute(
            delete(McpOAuthToken).where(McpOAuthToken.grant_id.in_(ended))
        )
        return len(ended)
