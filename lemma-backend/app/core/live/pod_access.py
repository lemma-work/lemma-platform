"""Whether a socket's user may still see its pod.

Checked on connect, again each time the socket refreshes its slot, and on a
call that answers about the pod as a whole (``presence``): a user taken out of
the pod loses the socket -- closed ``4403`` -- and with it their slot and
their presence, rather than keep both until they disconnect.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import status
from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.context import ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.connection import LiveConnection
from app.core.live.protocol import (
    CLOSE_FORBIDDEN,
    CLOSE_NOT_FOUND,
    LiveAccessDenied,
    LiveErrorCode,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


async def require_pod_access(pod_id: UUID, user_id: UUID) -> None:
    """Raise the ``DomainError`` a REST read of the pod would."""
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        ctx = await AuthorizationDataService(uow.session).build_user_context(
            user_id=user_id, pod_id=pod_id
        )
        await ctx.require(Permissions.POD_READ, ResourceRef.pod(pod_id))


def close_code_for(status_code: int) -> int:
    """Map a domain HTTP status to a websocket close code."""
    if status_code == 404:
        return CLOSE_NOT_FOUND
    if status_code == 403:
        return CLOSE_FORBIDDEN
    return status.WS_1008_POLICY_VIOLATION


async def keep_pod_access(connection: LiveConnection) -> bool:
    """Whether the socket's user may still see its pod; if not, the socket is
    closed with the code a connect would have been refused with."""
    try:
        await require_pod_access(connection.pod_id, connection.user_id)
    except DomainError as lost:
        connection.close_with(close_code_for(lost.status_code), lost.message)
        return False
    except (SQLAlchemyError, OSError) as exc:
        # A check that cannot be made keeps the socket: it is made again on
        # the next refresh, rather than every socket close on a blip.
        logger.warning(
            "live.pod_access_check_unavailable.degraded",
            pod_id=str(connection.pod_id),
            error_type=type(exc).__name__,
            exc_info=True,
        )
    return True


async def require_kept_pod_access(connection: LiveConnection) -> None:
    """``keep_pod_access`` for a call: refused, and the socket closed."""
    if not await keep_pod_access(connection):
        raise LiveAccessDenied(
            LiveErrorCode.FORBIDDEN, "You can no longer see this pod"
        )


__all__ = [
    "close_code_for",
    "keep_pod_access",
    "require_kept_pod_access",
    "require_pod_access",
]
