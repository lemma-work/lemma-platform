from datetime import datetime
from typing import Optional

from sqlalchemy import select, update

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.repository import SqlAlchemyRepository
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.connect_request import (
    ConnectRequestEntity,
    ConnectRequestStatus,
)
from app.modules.connectors.domain.ports import ConnectRequestRepositoryPort
from app.modules.connectors.infrastructure.models import ConnectRequest


class ConnectRequestRepository(
    SqlAlchemyRepository[ConnectRequest, ConnectRequestEntity],
    ConnectRequestRepositoryPort,
):
    """Repository for ConnectRequest operations."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        super().__init__(uow, ConnectRequest, ConnectRequestEntity)
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    async def get_by_state(self, state: str) -> Optional[ConnectRequestEntity]:
        """Get connect request by state attribute."""
        stmt = select(ConnectRequest).where(_state_matches(state))
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def claim_pending_by_state(
        self, state: str, *, not_before: datetime
    ) -> Optional[ConnectRequestEntity]:
        """Take a pending request out of contention, atomically.

        Checking the status in Python and writing the result later is not
        single use: `handle_oauth_callback` read the row, decided it was
        PENDING, then spent seconds on the provider exchange before writing
        SUCCESS. Two callbacks arriving with the same `state` inside that window
        both passed the check and both completed.

        One UPDATE with the status and the age in its WHERE clause decides it
        instead. Postgres serialises the two writers on the row, so exactly one
        sees a rowcount of 1 and the other gets nothing back and is refused --
        the same answer a spent or expired request gets, which is also all a
        caller holding a `state` should be able to learn.
        """
        stmt = (
            update(ConnectRequest)
            .where(
                _state_matches(state),
                ConnectRequest.status == ConnectRequestStatus.PENDING.value,
                ConnectRequest.created_at >= not_before,
            )
            .values(status=ConnectRequestStatus.EXCHANGING.value)
            .returning(ConnectRequest)
        )
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None


def _state_matches(state: str):
    """Match the `state` inside the attributes JSONB.

    `.astext` renders as `#>>`, which compares the extracted text directly.
    The previous form cast the JSON value to a string and compared it to a
    hand-quoted `f'"{state}"'` -- correct only while `token_urlsafe` never
    emits a quote or a backslash, an invariant nothing asserted and nothing
    would notice breaking.
    """
    return ConnectRequest.attributes["state"].astext == state
