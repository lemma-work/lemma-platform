import base64
import binascii
import json
import time
from uuid import UUID
from fastapi import HTTPException
from fastapi.security import HTTPBearer
from starlette.requests import HTTPConnection
from supertokens_python.recipe.session.asyncio import get_session
from supertokens_python.exceptions import SuperTokensError
from supertokens_python.recipe.session.exceptions import (
    InvalidClaimsError,
    TryRefreshTokenError,
)
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
from app.core.auth_state_cache import (
    AccountStanding,
    get_account_standing,
    set_account_standing,
)
from app.core.analytics.app_session import maybe_record_app_session
from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.infrastructure.models.user_models import User
from sqlalchemy import select

logger = get_logger(__name__)


# How far past its expiry an access token has to be before the expiry itself is
# the suspicious part.
#
# Minutes are ordinary: a token expires, the client refreshes, and a request in
# flight over the boundary answers 401 once. Hours are not. A token that was
# minted seconds ago and is already hours expired means the clock that signed it
# and the clock reading it disagree — which no amount of refreshing fixes,
# because every replacement token comes from the same wrong clock. That state
# used to be completely silent: the loop it produces logs a wall of 401s and not
# one line saying why.
CLOCK_SKEW_SUSPECT_SECONDS = 300

# How often that observation is worth repeating.
#
# The state it reports is a loop: every request in it carries the same expired
# token, so an un-throttled warning is hundreds of identical lines a minute --
# which is the log flood this whole branch exists to stop, arriving from the
# other side. One line a minute is enough to see it and enough to date it.
CLOCK_SKEW_REPORT_INTERVAL_SECONDS = 60


class _ReportThrottle:
    """Lets one observation through per interval, and swallows the rest.

    An object rather than a module-level counter and a `global`: the decision is
    then something a test can drive directly, and the state has an owner.
    """

    __slots__ = ("_interval_seconds", "_last_at")

    def __init__(self, interval_seconds: float) -> None:
        self._interval_seconds = interval_seconds
        self._last_at = -interval_seconds

    def should_report(self, now: float) -> bool:
        """`now` is monotonic, so a corrected wall clock cannot push the next
        report into the far future."""
        if now - self._last_at < self._interval_seconds:
            return False
        self._last_at = now
        return True

    def reset(self) -> None:
        self._last_at = -self._interval_seconds


# Process-local by design: this describes the machine, not a request.
_skew_reports = _ReportThrottle(CLOCK_SKEW_REPORT_INTERVAL_SECONDS)


def _unverified_token_expiry(connection: HTTPConnection) -> float | None:
    """The `exp` the presented access token claims, without verifying it.

    Only ever read after verification has already failed, and only to describe
    the failure. Nothing is authorized on the strength of it.
    """
    token = connection.cookies.get("sAccessToken")
    if not token:
        header = connection.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except binascii.Error, ValueError, UnicodeDecodeError:
        return None
    expiry = claims.get("exp") if isinstance(claims, dict) else None
    return float(expiry) if isinstance(expiry, (int, float)) else None


def _report_expired_access_token(connection: HTTPConnection) -> None:
    """Say so when a token is expired by more than an expiry explains."""
    expiry = _unverified_token_expiry(connection)
    if expiry is None:
        return
    expired_by_seconds = int(time.time() - expiry)
    if expired_by_seconds < CLOCK_SKEW_SUSPECT_SECONDS:
        return
    if not _skew_reports.should_report(time.monotonic()):
        return
    logger.warning(
        "identity.session.access_token_expiry_implausible.degraded",
        expired_by_seconds=expired_by_seconds,
    )


async def _get_local_auth_state(user_id: UUID) -> AccountStanding | None:
    """The caller's account standing, from cache when it is there.

    This runs on every authenticated request. Reading it from the database each
    time cost a query *and* a connection checkout of its own — measured at a
    meaningful share of the latency of every authenticated endpoint — for three
    booleans that change rarely and are invalidated when they do.
    """
    cached = await get_account_standing(user_id)
    if cached is not None:
        return cached

    async with async_session_maker() as db_session:
        row = (
            await db_session.execute(
                select(User.is_active, User.is_verified, User.is_deleted).where(
                    User.id == user_id
                )
            )
        ).first()
    if row is None:
        # Not cached: an absent user is either a race with signup or a token for
        # a row that is gone, and neither should be remembered as a fact.
        return None

    standing = AccountStanding(
        is_active=bool(row.is_active),
        is_verified=bool(row.is_verified),
        is_deleted=bool(row.is_deleted),
    )
    await set_account_standing(user_id, standing)
    return standing


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
    "/livez",
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
    "/connectors/connect-requests/oauth/callback",  # OAuth callback - secured by state parameter
    "/surfaces/teams/admin-consent/callback",  # surface consent callback
    "/surfaces/webhooks",  # surface webhook endpoints
    "/webhooks",
    "/agent-runtime/runs/",  # run-scoped MCP routes validate their own token
    "/agent-runtime/conversations/",  # conversation-scoped MCP routes validate their own token
    # A paired computer has no user session and never will: it authenticates
    # with its own host secret, which `_authenticated_host` checks on every one
    # of these routes, and `pairings:complete` is authenticated by the one-time
    # pairing code it consumes. Requiring a session here 401s the only caller
    # these routes have. The user-facing host routes are under `/me/runtime/...`
    # and stay session-protected.
    "/agent-host/",
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


def _is_public_desktop_auth_path(path: str, method: str) -> bool:
    """Only request creation and verifier exchange are unauthenticated.

    The similarly-named ``.../{request_id}/complete`` route deliberately does
    not match: it must receive the authenticated browser session.
    """
    normalized_method = method.upper()
    return normalized_method == "POST" and path in {
        "/auth/desktop/requests",
        "/auth/desktop/session",
    }


def _is_public_identity_auth_path(path: str, method: str) -> bool:
    normalized_method = method.upper()
    return (
        normalized_method == "GET"
        and path
        in {
            "/auth/altcha/config",
            "/auth/altcha/challenge",
            "/auth/telegram/config",
            "/auth/telegram/start",
            "/auth/telegram/callback",
        }
    ) or (
        normalized_method == "POST"
        and path in {"/auth/email/bounces", "/auth/email/bounces/resend"}
    )


async def verify_auth(connection: HTTPConnection):
    """
    Global dependency to enforce authentication on all routes except excluded ones.
    Populates request.state.user and request.state.session if authenticated.
    """
    set_current_context(None)

    if (
        connection.url.path.startswith(EXCLUDED_PATHS)
        or _is_surface_webhook_path(connection.url.path)
        or _is_public_desktop_auth_path(
            connection.url.path,
            str(connection.scope.get("method", "GET")),
        )
        or _is_public_identity_auth_path(
            connection.url.path,
            str(connection.scope.get("method", "GET")),
        )
    ):
        return

    if connection.scope["type"] != "http":
        # WebSockets: two of them carry their own authentication, and nothing
        # else on a non-HTTP scope is authenticated here at all.
        if connection.url.path.startswith(
            "/workspace/browser"
        ) or _is_datastore_changes_ws_path(connection.url.path):
            return
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

            local_state = await _get_local_auth_state(parsed_user_id)
            if (
                local_state is None
                or not local_state.is_active
                or local_state.is_deleted
            ):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "ACCOUNT_INACTIVE",
                        "message": "This account is inactive.",
                    },
                )
            if (
                settings.auth_email_verification_required
                and not local_state.is_verified
            ):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "EMAIL_VERIFICATION_REQUIRED",
                        "message": "Verify your email before continuing.",
                    },
                )

            connection.state.user = AuthUserEntity(id=parsed_user_id)
            connection.state.session = session
            # Cheap for every request that is not from a published app: the
            # header check short-circuits before anything else runs.
            await maybe_record_app_session(connection, session, parsed_user_id)
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

    except InvalidClaimsError:
        # Keep SuperTokens' invalid-claim contract intact. Its middleware turns
        # this into a 403 response with ``claimValidationErrors``. Rewriting it
        # to 401 makes the frontend treat an intentionally unverified session as
        # expired and repeatedly refresh/retry the same protected request.
        raise
    except TryRefreshTokenError:
        # This exception is raised when the access token has expired.
        # SuperTokens frontend SDKs handle the refresh flow, but for an API client,
        # we return 401 so they know to refresh.
        _report_expired_access_token(connection)
        raise HTTPException(
            status_code=401,
            detail="Access token has expired. Please refresh your session.",
        )
    except HTTPException:
        # Already classified above (403 for an inactive account, an unverified
        # email, revoked delegation) or by SuperTokens' own machinery.
        raise
    except SuperTokensError as e:
        # The ordinary answer to a request without a valid session: no token, a
        # malformed one, a revoked one. Same 401 as before and deliberately no
        # record -- one per unauthenticated request is the volume that would
        # bury the arm below, which is the one worth reading.
        raise HTTPException(status_code=401, detail="Unauthorized") from e
    except Exception as e:
        # Everything else is this deployment failing, not the caller: the
        # SuperTokens core unreachable, a JWKS fetch that did not come back, a
        # key that does not match. It still answers 401, because a request
        # whose identity could not be established must not proceed -- but the
        # client's reaction to a 401 is to discard a working session and ask
        # for a new one, so the server has to be the one that knows better.
        #
        # This was `logger.debug` with no exception, on the hottest path in the
        # service. At LOG_LEVEL=INFO the record was dropped before formatting,
        # so a core outage looked exactly like a wave of bad tokens and there
        # was nothing server-side to say otherwise.
        logger.warning(
            "security.auth_dependency.unexpected_failure.degraded",
            error_type=type(e).__name__,
            exc_info=True,
        )
        # `from e` so the traceback keeps the frame that actually failed.
        raise HTTPException(status_code=401, detail="Unauthorized") from e


async def supertokens_core_reachable() -> bool:
    """Can this process reach the SuperTokens core it verifies sessions against?

    ``initialize_supertokens`` is configuration only -- it makes no network call
    -- while ``verify_auth`` above calls the core on *every* authenticated
    request. So a core that is down leaves readiness at 200 and every API call
    failing, which is the state PS-OPS-030 says a process must not report itself
    healthy in.

    ``/hello`` is the core's own liveness route and needs no API key. Never
    raises: readiness treats "did not answer" and "answered badly" alike, and it
    is the caller that owns the deadline.
    """
    import httpx

    base = settings.supertokens_core_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            response = await client.get(f"{base}/hello")
        return response.status_code == 200
    except httpx.HTTPError:
        return False
