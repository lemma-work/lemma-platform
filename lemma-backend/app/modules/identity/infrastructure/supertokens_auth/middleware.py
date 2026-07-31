from uuid import UUID
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from supertokens_python.recipe.session.asyncio import get_session
from supertokens_python.recipe.session.exceptions import TryRefreshTokenError

from app.modules.identity.domain.user_entities import AuthUserEntity
from app.core.log.log import get_logger

logger = get_logger(__name__)


class CustomAuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth for specific paths
        if request.url.path.startswith(
            ("/st", "/docs", "/openapi.json", "/health", "/livez", "/schema")
        ):
            return await call_next(request)

        try:
            # Manually verify session
            session = await get_session(request, session_required=False)

            if session:
                user_id = session.get_user_id()
                request.state.user = AuthUserEntity(id=UUID(user_id))
                request.state.session = session
        except TryRefreshTokenError:
            # Let the route handler decide or client handle refresh
            pass
        except Exception:
            # Log but allow request to proceed (maybe public route)
            # Controllers enforcing auth will fail if user is missing
            logger.debug("identity.middleware.auth_middleware.observed")

        response = await call_next(request)
        return response
