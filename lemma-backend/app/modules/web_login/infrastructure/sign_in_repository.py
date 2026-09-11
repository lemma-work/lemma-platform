"""Reading and writing the requests that ask a person to sign in."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.web_login.domain.entities import SignInRequest, SignInRequestStatus
from app.modules.web_login.infrastructure.models import SignInRequestModel


class SignInRequestNotFound(Exception):
    """No such request, or it is not this person's.

    One exception for both, because a caller must not be able to tell them
    apart: "that request exists but is not yours" tells somebody holding a
    guessed id that they guessed right.
    """


class SignInRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: UUID,
        origin: str,
        reason: str,
        conversation_id: UUID | None,
        tool_call_id: str | None,
    ) -> SignInRequest:
        row = SignInRequestModel(
            user_id=user_id,
            origin=origin,
            reason=reason[:500],
            status=SignInRequestStatus.PENDING.value,
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_entity(row)

    async def get_for_user(
        self, request_id: UUID, user_id: UUID
    ) -> SignInRequest | None:
        """One request, if it belongs to this person.

        The user filter is in the query rather than checked afterwards, so
        there is no path that reads a row first and decides later.
        """
        row = await self._session.scalar(
            select(SignInRequestModel).where(
                SignInRequestModel.id == request_id,
                SignInRequestModel.user_id == user_id,
            )
        )
        return _to_entity(row) if row is not None else None

    async def resolve(
        self,
        request_id: UUID,
        user_id: UUID,
        *,
        status: SignInRequestStatus,
        saved: bool = False,
        saved_detail: str | None = None,
    ) -> SignInRequest:
        row = await self._session.scalar(
            select(SignInRequestModel).where(
                SignInRequestModel.id == request_id,
                SignInRequestModel.user_id == user_id,
            )
        )
        if row is None:
            raise SignInRequestNotFound(str(request_id))
        row.status = status.value
        row.saved = saved
        row.saved_detail = saved_detail[:500] if saved_detail else None
        row.resolved_at = datetime.now(timezone.utc)
        await self._session.flush()
        return _to_entity(row)

    async def open_for_user(
        self, user_id: UUID, limit: int = 20
    ) -> list[SignInRequest]:
        rows = await self._session.scalars(
            select(SignInRequestModel)
            .where(
                SignInRequestModel.user_id == user_id,
                SignInRequestModel.status == SignInRequestStatus.PENDING.value,
            )
            .order_by(SignInRequestModel.created_at.desc())
            .limit(limit)
        )
        return [_to_entity(row) for row in rows]


def _to_entity(row: SignInRequestModel) -> SignInRequest:
    return SignInRequest(
        id=row.id,
        user_id=row.user_id,
        origin=row.origin,
        reason=row.reason,
        status=SignInRequestStatus(row.status),
        created_at=row.created_at,
        conversation_id=row.conversation_id,
        tool_call_id=row.tool_call_id,
        resolved_at=row.resolved_at,
        saved=bool(row.saved),
        saved_detail=row.saved_detail,
    )


__all__ = ["SignInRequestNotFound", "SignInRequestRepository"]
