"""Slack configuration tokens, encrypted at rest; the only reader of the columns.

Every write that stores a pair is guarded by ``generation``, because a Slack
refresh token is single-use and two writers must never both believe they hold
the current one: a paste replaces whatever is there and moves the generation
on, and a rotation stores its pair only if the generation it started from is
still current. The rotation lease is taken and checked on the database clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from pydantic import SecretStr
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_cipher
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.errors import (
    SlackAppCreationTokenExpiredError,
)
from app.modules.agent_surfaces.infrastructure.slack_configuration_models import (
    SlackConfigurationTokenModel,
)
from app.modules.agent_surfaces.platforms.slack.app_configuration import (
    RotatedConfigurationToken,
)

logger = get_logger(__name__)

#: An organization keeps a token per Slack workspace it makes apps in: a
#: handful, so this is a ceiling on the read rather than a page size.
MAX_WORKSPACES_PER_ORGANIZATION = 50

_Token = SlackConfigurationTokenModel


@dataclass(frozen=True)
class ConfigurationTokenPair:
    """The decrypted pair. ``SecretStr``, so printing it prints nothing."""

    access_token: SecretStr
    refresh_token: SecretStr


class SlackConfigurationTokenRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_organization(
        self, organization_id: UUID
    ) -> list[SlackConfigurationTokenModel]:
        result = await self.session.execute(
            select(_Token)
            .where(_Token.organization_id == organization_id)
            .order_by(_Token.saved_at)
            .limit(MAX_WORKSPACES_PER_ORGANIZATION)
        )
        return list(result.scalars().all())

    async def get(
        self, organization_id: UUID, team_id: str
    ) -> SlackConfigurationTokenModel | None:
        """The row as the database has it now, not as this session last saw it:
        a caller waiting for another's rotation re-reads through this."""
        result = await self.session.execute(
            select(_Token)
            .where(_Token.organization_id == organization_id, _Token.team_id == team_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def save_pasted(
        self,
        *,
        organization_id: UUID,
        token: RotatedConfigurationToken,
        team_name: str | None,
        saved_by_user_id: UUID,
    ) -> SlackConfigurationTokenModel:
        """Keep the pair a person's paste rotated to, replacing any older one.

        One statement, so two people saving the same workspace at once both
        succeed and the later wins, rather than one of them hitting the unique
        constraint. The generation moves on, so a rotation of the replaced pair
        that is still in flight stores nothing over it.
        """
        cipher = get_secret_cipher()
        values = {
            "access_token": cipher.encrypt_str(token.access_token.get_secret_value()),
            "refresh_token": cipher.encrypt_str(token.refresh_token.get_secret_value()),
            "access_expires_at": token.expires_at,
            "slack_user_id": token.slack_user_id,
            "team_name": team_name,
            "saved_by_user_id": saved_by_user_id,
        }
        statement = insert(_Token).values(
            id=uuid4(),
            organization_id=organization_id,
            team_id=token.team_id,
            generation=0,
            saved_at=func.now(),
            created_at=func.now(),
            updated_at=func.now(),
            **values,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_slack_configuration_token_org_team",
            set_={
                **{key: statement.excluded[key] for key in values},
                # A workspace Slack could not name this time keeps the name it had.
                "team_name": func.coalesce(
                    statement.excluded.team_name, _Token.team_name
                ),
                "slack_user_id": func.coalesce(
                    statement.excluded.slack_user_id, _Token.slack_user_id
                ),
                "generation": _Token.generation + 1,
                "rotation_claimed_until": None,
                "refused_at": None,
                "saved_at": func.now(),
                "updated_at": func.now(),
            },
        ).returning(_Token.id)
        row_id = (await self.session.execute(statement)).scalar_one()
        row = await self.session.get(_Token, row_id, populate_existing=True)
        assert row is not None
        return row

    async def claim_rotation(
        self, row_id: UUID, *, generation: int, lease: timedelta
    ) -> bool:
        """Take the one rotator's lease, if nobody holds a live one and the pair
        is still the one this caller read."""
        result = await self.session.execute(
            update(_Token)
            .where(
                _Token.id == row_id,
                _Token.generation == generation,
                _Token.refused_at.is_(None),
                (_Token.rotation_claimed_until.is_(None))
                # The wall clock, not now(): that is the transaction's start.
                | (_Token.rotation_claimed_until < func.clock_timestamp()),
            )
            .values(rotation_claimed_until=func.clock_timestamp() + lease)
            .returning(_Token.id)
        )
        return result.scalar_one_or_none() is not None

    async def store_rotation(
        self, row_id: UUID, *, generation: int, token: RotatedConfigurationToken
    ) -> bool:
        """Keep a rotation's pair, if nothing replaced the one it rotated from."""
        cipher = get_secret_cipher()
        result = await self.session.execute(
            update(_Token)
            .where(_Token.id == row_id, _Token.generation == generation)
            .values(
                access_token=cipher.encrypt_str(token.access_token.get_secret_value()),
                refresh_token=cipher.encrypt_str(
                    token.refresh_token.get_secret_value()
                ),
                access_expires_at=token.expires_at,
                generation=_Token.generation + 1,
                rotation_claimed_until=None,
                updated_at=func.now(),
            )
            .returning(_Token.id)
        )
        return result.scalar_one_or_none() is not None

    async def release_rotation(self, row_id: UUID, *, generation: int) -> None:
        """Give the lease back after a rotation that changed nothing at Slack."""
        await self.session.execute(
            update(_Token)
            .where(_Token.id == row_id, _Token.generation == generation)
            .values(rotation_claimed_until=None)
        )

    async def mark_refused(self, row_id: UUID, *, generation: int) -> None:
        """Slack refused the stored refresh token: nothing rotates it again
        until a person pastes a new one."""
        await self.session.execute(
            update(_Token)
            .where(_Token.id == row_id, _Token.generation == generation)
            .values(
                refused_at=func.now(),
                rotation_claimed_until=None,
                updated_at=func.now(),
            )
        )

    async def delete(self, organization_id: UUID, team_id: str) -> bool:
        result = await self.session.execute(
            delete(_Token).where(
                _Token.organization_id == organization_id, _Token.team_id == team_id
            )
        )
        return bool(result.rowcount)


def reveal_tokens(row: SlackConfigurationTokenModel) -> ConfigurationTokenPair:
    """The decrypted pair, or ``SlackAppCreationTokenExpiredError`` when it cannot
    be read: calling Slack with an empty token would only be refused there."""
    cipher = get_secret_cipher()
    try:
        access = cipher.decrypt_str(row.access_token)
        refresh = cipher.decrypt_str(row.refresh_token)
    except InvalidToken, RuntimeError, ValueError:
        logger.warning(
            "agent_surfaces.slack_app_creation.token_unreadable",
            organization_id=str(row.organization_id),
            team_id=row.team_id,
            exc_info=True,
        )
        raise SlackAppCreationTokenExpiredError(row.team_id, unreadable=True) from None
    if not access or not refresh:
        raise SlackAppCreationTokenExpiredError(row.team_id, unreadable=True)
    return ConfigurationTokenPair(
        access_token=SecretStr(access), refresh_token=SecretStr(refresh)
    )
