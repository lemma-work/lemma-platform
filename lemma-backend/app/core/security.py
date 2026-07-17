from uuid import UUID
from fastapi import HTTPException
from fastapi.security import HTTPBearer
from starlette.requests import HTTPConnection
from supertokens_python.recipe.session.asyncio import get_session
from supertokens_python.recipe.session.exceptions import TryRefreshTokenError
from app.core.authorization.current import set_current_context
from app.core.config import settings
from app.core.authorization.delegation import (
    CLAIM_ACTOR_ID,
    DelegationClaimsError,
    parse_delegation_claims,
)
from app.core.authorization.delegation_revocation import is_delegation_revoked
from app.core.log.log import get_logger
from app.modules.identity.domain.user_entities import AuthUserEntity

logger = get_logger(__name__)

# Define the security scheme for OpenAPI
# auto_error=False allows us to handle the error manually and support exclusions
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="HTTPBearer")

# Paths that should differ from the global auth requirement
# Note: These are prefix matches provided to startswith()
EXCLUDED_PATHS = (
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
    "/public/icons",
    "/public/apps",
    "/public/sdk",  # browser SDK bundle for no-build apps
    "/widgets/serve",  # widget HTML; handler self-validates session-or-signed-token
    "/public/datastore",  # signed-token file serving validates its own token
    "/s/",  # short signed-URL file serving validates its own Redis-backed code
    "/scalar",
    "/st",  # SuperTokens auth endpoints
    "/auth/cli/info",
    "/auth/cli/refresh",
    "/workspace/browser/user",
    "/billing/payment",  # payment result pages (success/cancel) — no session needed post-redirect
    "/billing/webhooks",  # payment-provider webhooks (Dodo) — handler verifies the HMAC signature itself; delivered server-to-server with no session
    # NB: /connectors/connect-requests/oauth/callback is NOT excluded — the
    # handler now requires a session and binds the callback to the user who
    # initiated the connect request, so the global auth gate must run.
    "/surfaces/teams/admin-consent/callback",  # surface consent callback
    "/surfaces/webhooks",  # surface webhook endpoints
    "/webhooks",
    "/agent-runtime/runs/",  # run-scoped MCP routes validate their own token
    "/agent-runtime/conversations/",  # conversation-scoped MCP routes validate their own token
)


def _is_surface_webhook_path(path: str) -> bool:
    parts = path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "surfaces" or parts[2] != "webhook":
        return False
    try:
        UUID(parts[1])
    except ValueError:
        return False
    return True


def _is_datastore_changes_ws_path(path: str) -> bool:
    """Match ``/pods/{pod_id}/datastore/changes`` (the changes websocket).

    The handler authenticates the session itself (cookie or bearer), so the
    global HTTP auth dependency must let the handshake through.
    """
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "pods" or parts[2:4] != ["datastore", "changes"]:
        return False
    try:
        UUID(parts[1])
    except ValueError:
        return False
    return True


async def verify_auth(connection: HTTPConnection):
    """
    Global dependency to enforce authentication on all routes except excluded ones.
    Populates request.state.user and request.state.session if authenticated.
    """
    set_current_context(None)

    if connection.url.path.startswith(EXCLUDED_PATHS) or _is_surface_webhook_path(
        connection.url.path
    ):
        return

    if connection.scope["type"] != "http" and (
        connection.url.path.startswith("/workspace/browser")
        or connection.url.path == "/me/agent-runtime/daemon/ws"
        or _is_datastore_changes_ws_path(connection.url.path)
    ):
        return

    if connection.scope["type"] != "http":
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        # Session verification
        # session_required=True ensures 401 if no valid session is found
        # We rely on SuperTokens to parse the Bearer token from the header
        session = await get_session(connection, session_required=True)  # type: ignore[arg-type]

        if session:
            user_id = session.get_user_id()
            parsed_user_id = UUID(user_id)
            payload = session.get_access_token_payload() or {}

            connection.state.user = AuthUserEntity(id=parsed_user_id)
            connection.state.session = session
            connection.state.auth_claims = payload
            connection.state.delegation_claims = None
            if settings.authz_delegated_tokens_enabled:
                try:
                    delegation_claims = parse_delegation_claims(payload)
                except DelegationClaimsError as exc:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "INVALID_DELEGATION_CLAIMS",
                            "message": str(exc),
                        },
                    ) from exc

                if delegation_claims is not None and await is_delegation_revoked(
                    actor_id=delegation_claims.actor_id
                ):
                    # The workload this token delegates for lost its authority
                    # (e.g. the agent/function was deleted). Reject before it
                    # expires on its own.
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "DELEGATION_REVOKED",
                            "message": "Delegated workload has been revoked.",
                        },
                    )

                connection.state.delegation_claims = delegation_claims
            elif payload.get("isImpersonation") is True or payload.get(CLAIM_ACTOR_ID):
                # Delegation is disabled, but a delegated/impersonation token
                # (minted with isImpersonation + delegation claims) carries the
                # workload's authority. Without the delegation layer to clamp it,
                # honoring it would silently promote it to a full user session, so
                # reject it — disabling the flag must fail safe, not escalate.
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "IMPERSONATION_NOT_ALLOWED",
                        "message": "Delegated tokens are disabled.",
                    },
                )

    except TryRefreshTokenError:
        # This exception is raised when the access token has expired.
        # SuperTokens frontend SDKs handle the refresh flow, but for an API client,
        # we return 401 so they know to refresh.
        raise HTTPException(
            status_code=401,
            detail="Access token has expired. Please refresh your session.",
        )
    except Exception as e:
        # Catch-all for other auth errors (e.g. malformed token) provided by SuperTokens or logic
        # If get_session raises a 401-like exception (Unauthorised), FastAPI handles it.
        # But if it's a generic error, we log and deny.
        # Note: get_session(session_required=True) raises supertokens_python.exceptions.SuperTokensError mostly
        # creating a proper 401 response.

        # We check if it's already an HTTPException (like 401 from SuperTokens machinery if any)
        if isinstance(e, HTTPException):
            raise e

        logger.debug(f"Auth dependency error: {e}")
        # If we reached here, and authentication failed but didn't raise, we force 401.
        # However, get_session(session_required=True) *should* send a response or raise.
        # SuperTokens often sends a response directly which stops execution?
        # In FastAPI integration, it usually raises an exception that is handled by exception handlers.

        # Re-raising generic exception as 401 for safety if not handled
        raise HTTPException(status_code=401, detail="Unauthorized")
