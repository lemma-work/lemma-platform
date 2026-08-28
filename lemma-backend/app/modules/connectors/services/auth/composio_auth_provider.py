from contextlib import suppress
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Tuple
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import aiohttp

os.environ.setdefault("COMPOSIO_CACHE_DIR", "/tmp/composio")

from composio.types import auth_scheme as composio_auth_scheme

from app.modules.connectors.infrastructure.composio_client import get_composio_client

from app.modules.connectors.domain.account import ComposioCredentials, OAuthCredentials
from app.modules.connectors.domain.connector import AuthScheme, ConnectorEntity
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.domain.ports import ConnectorRepositoryPort
from app.modules.connectors.services.auth.auth_provider import AuthProviderInterface
from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger

logger = get_logger(__name__)

ComposioClientFactory = Callable[[], Any]
HttpSessionFactory = Callable[[], aiohttp.ClientSession]

# Mirrors the Composio SDK's terminal connection states (INACTIVE is excluded
# on purpose — it can recover to ACTIVE).
_TERMINAL_CONNECTION_STATES: frozenset[str] = frozenset(
    {"FAILED", "EXPIRED", "REVOKED"}
)


class ComposioAuthProvider(AuthProviderInterface):
    """Composio authentication provider."""

    def __init__(
        self,
        connector_repository: ConnectorRepositoryPort,
        composio_client_factory: ComposioClientFactory | None = None,
        http_session_factory: HttpSessionFactory = aiohttp.ClientSession,
    ):
        self._connector_repository = connector_repository
        # Shared: this provider is built per request, and the SDK client costs
        # 42-262ms to construct.
        self._composio_client_factory = composio_client_factory or get_composio_client
        self._http_session_factory = http_session_factory

    async def _get_google_token_expiration(
        self, access_token: str
    ) -> Optional[datetime]:
        try:
            async with self._http_session_factory() as session:
                url = f"https://www.googleapis.com/oauth2/v1/tokeninfo?access_token={access_token}"
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        expires_in = data.get("expires_in")
                        if expires_in:
                            return datetime.now(timezone.utc) + timedelta(
                                seconds=int(expires_in)
                            )
                    logger.debug(
                        "connectors.composio_auth_provider.fetch_token_info_google_api.diagnostic",
                        status=response.status,
                    )
                    return None
        except Exception:
            # "Expiry unknown" degrades refresh scheduling; the connection still
            # works, so this is not an error. The sibling debug above is a
            # non-200 from tokeninfo, which means the token is invalid — an
            # expected answer, not a swallowed failure.
            logger.warning(
                "connectors.composio_auth_provider.google_token_expiration_lookup.degraded",
                exc_info=True,
            )
            return None

    def _is_google_app(self, app: ConnectorEntity) -> bool:
        return app.id in ["google_calendar", "gmail", "google_workspace"]

    def _toolkit_slug(self, connector: ConnectorEntity) -> str:
        if connector.composio_toolkit_slug:
            return connector.composio_toolkit_slug
        try:
            capability = connector.capability_for("COMPOSIO")
        except ValueError as exc:
            raise ConnectorValidationError("Composio app name not configured") from exc
        toolkit_slug = getattr(capability, "toolkit_slug", None)
        if not toolkit_slug:
            raise ConnectorValidationError("Composio app name not configured")
        return toolkit_slug

    def _extract_expiration_from_connection(
        self, connection_account: Any
    ) -> Optional[datetime]:
        state = getattr(connection_account, "state", None)
        value = getattr(state, "val", None)
        if value is None:
            return None

        expires_at = getattr(value, "expires_at", None)
        if isinstance(expires_at, datetime):
            return expires_at
        if isinstance(expires_at, (int, float)):
            return datetime.fromtimestamp(expires_at, tz=timezone.utc)
        if isinstance(expires_at, str):
            normalized = expires_at.replace("Z", "+00:00")
            with suppress(ValueError):
                return datetime.fromisoformat(normalized)

        expires_in = getattr(value, "expires_in", None)
        if expires_in not in (None, ""):
            with suppress((TypeError, ValueError)):
                return datetime.now(timezone.utc) + timedelta(
                    seconds=int(float(expires_in))
                )

        return None

    async def _resolve_token_expiration(
        self, connector: ConnectorEntity, connection_account: Any
    ) -> datetime:
        token_expires_at = self._extract_expiration_from_connection(connection_account)
        if token_expires_at is not None:
            return token_expires_at

        state = getattr(connection_account, "state", None)
        value = getattr(state, "val", None)
        access_token = getattr(value, "access_token", None)
        if self._is_google_app(connector) and access_token:
            google_expiry = await self._get_google_token_expiration(access_token)
            if google_expiry is not None:
                return google_expiry

        return datetime.now(timezone.utc) + timedelta(minutes=5)

    def _serialize_raw_connection_state(
        self, connection_account: Any
    ) -> dict[str, Any] | None:
        state = getattr(connection_account, "state", None)
        value = getattr(state, "val", None)
        model_dump = getattr(value, "model_dump", None)
        data = (
            model_dump(mode="json", by_alias=True, exclude_none=True)
            if callable(model_dump)
            else {}
        )
        # word_id/alias live on the connected account itself, not state.val, and
        # are the only toolkit-agnostic identity signal Composio gives us: a
        # user-set alias, else a stable auto-generated label ("gmail_red-castle")
        # meant for exactly this — telling multiple accounts of the same app
        # apart when the toolkit's own fields (below) don't surface an email.
        alias = getattr(connection_account, "alias", None)
        word_id = getattr(connection_account, "word_id", None)
        if alias:
            data["alias"] = alias
        if word_id:
            data["word_id"] = word_id
        return data or None

    def _composio_auth_scheme(self, connector: ConnectorEntity) -> AuthScheme:
        try:
            capability = connector.capability_for("COMPOSIO")
        except ValueError:
            return AuthScheme.OAUTH2
        return getattr(capability, "auth_scheme", AuthScheme.OAUTH2)

    # Maps our auth scheme to the Composio custom-auth scheme string used when a
    # toolkit has no Composio-managed credentials (bring-your-own API key, etc.).
    _CUSTOM_AUTH_SCHEME = {
        AuthScheme.API_KEY: "API_KEY",
        AuthScheme.NOAUTH: "NO_AUTH",
    }

    async def _resolve_auth_config_id(
        self,
        connector: ConnectorEntity,
        composio: Any,
        *,
        custom_auth_scheme: str | None = None,
    ) -> str:
        """Create the Composio auth config this connect will run under.

        OAuth apps use Composio-managed credentials (``use_composio_managed_auth``).
        Credential-managed apps (API key / no-auth) have no managed credentials, so
        they need ``use_custom_auth`` with the explicit scheme; the per-account key
        is supplied at ``initiate`` time. Note ``use_custom_auth`` is about the
        *toolkit's* auth scheme, not about who owns the Composio account -- that is
        always Lemma.

        A ``connector.composio_auth_config_id`` reuse hook used to short-circuit
        this. It could never fire: the id had to arrive in an install's config,
        and a Composio install is always SYSTEM_DEFAULT, whose config is then
        validated against a closed empty schema that rejects the key.
        """
        if custom_auth_scheme is not None:
            options: dict[str, Any] = {
                "type": "use_custom_auth",
                "auth_scheme": custom_auth_scheme,
            }
        else:
            options = {"type": "use_composio_managed_auth"}
        auth_config = await run_blocking(
            lambda: composio.auth_configs.create(
                toolkit=self._toolkit_slug(connector),
                options=options,
            ),
            limiter="external_http",
        )
        return auth_config.id

    async def connect_with_credentials(
        self,
        connector: ConnectorEntity,
        user_id: UUID,
        credentials: dict,
    ) -> ComposioCredentials:
        if not credentials:
            raise ConnectorValidationError(
                "Credentials are required to connect this Composio app."
            )

        scheme = self._composio_auth_scheme(connector)
        if scheme == AuthScheme.OAUTH2:
            raise ConnectorValidationError(
                "OAuth2 Composio apps must be connected with a connect request, "
                "not direct credentials."
            )

        # Constructing the client is not free — it reads config, builds an
        # httpx client and imports the SDK's lazy namespaces on first use.
        # Only the SDK CALL below was offloaded, so the construction sat on
        # the event loop: measured at 76ms cold, 4ms warm, per call site.
        composio = await run_blocking(
            self._composio_client_factory, limiter="external_http"
        )
        auth_config_id = await self._resolve_auth_config_id(
            connector,
            composio,
            custom_auth_scheme=self._CUSTOM_AUTH_SCHEME.get(scheme, "API_KEY"),
        )

        if scheme == AuthScheme.NOAUTH:
            config = composio_auth_scheme.no_auth(credentials)
        else:
            config = composio_auth_scheme.api_key(credentials)

        connection_request = await run_blocking(
            lambda: composio.connected_accounts.initiate(
                user_id=str(user_id),
                auth_config_id=auth_config_id,
                config=config,
            ),
            limiter="external_http",
        )

        return ComposioCredentials(connection_id=connection_request.id)

    async def get_authorization_url(
        self,
        connector: ConnectorEntity,
        user_id: UUID,
        state: str,
        redirect_uri: str,
    ) -> Tuple[str, str]:
        composio = await run_blocking(
            self._composio_client_factory, limiter="external_http"
        )

        auth_config_id = await self._resolve_auth_config_id(connector, composio)

        redirect_url = f"{redirect_uri}?state={state}"

        connection_request = await run_blocking(
            lambda: composio.connected_accounts.initiate(
                user_id=str(user_id),
                auth_config_id=auth_config_id,
                callback_url=redirect_url,
            ),
            limiter="external_http",
        )

        if not connection_request.redirect_url:
            raise ConnectorValidationError("No redirect URL found for Composio app")

        return connection_request.redirect_url, connection_request.id

    async def exchange_code_for_credentials(
        self,
        connector: ConnectorEntity,
        redirect_uri: str,
        user_id: UUID,
        state: Optional[str] = None,
    ) -> OAuthCredentials:
        self._toolkit_slug(connector)

        parsed_url = urlparse(redirect_uri)
        query_params = parse_qs(parsed_url.query)
        connected_account_id_list = query_params.get("connectedAccountId")
        if not connected_account_id_list or not connected_account_id_list[0]:
            raise ConnectorValidationError(
                "connectedAccountId not found in callback URL"
            )

        connected_account_id = connected_account_id_list[0]

        composio = await run_blocking(
            self._composio_client_factory, limiter="external_http"
        )
        connection_account = await run_blocking(
            lambda: composio.connected_accounts.get(connected_account_id),
            limiter="external_http",
        )

        status = str(getattr(connection_account, "status", "") or "").upper()
        if status in _TERMINAL_CONNECTION_STATES:
            raise ConnectorValidationError(
                f"Composio connection {connected_account_id} is in terminal state "
                f"{status}; the account could not be connected."
            )

        state_value = connection_account.state.val
        access_token = getattr(state_value, "access_token", None)
        refresh_token = getattr(state_value, "refresh_token", None)
        token_expires_at = await self._resolve_token_expiration(
            connector, connection_account
        )

        logger.debug("connectors.composio_auth_provider.set_token_expiration.observed")

        return OAuthCredentials(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=getattr(state_value, "token_type", None) or "Bearer",
            expires_at=token_expires_at,
            raw_response=self._serialize_raw_connection_state(connection_account),
            connection_id=connection_account.id,
        )

    async def refresh_credentials(
        self,
        connector: ConnectorEntity,
        credentials: OAuthCredentials,
        user_id: UUID,
    ) -> OAuthCredentials:
        if not credentials.connection_id:
            raise ConnectorValidationError(
                "Connection ID required for Composio refresh"
            )

        composio = await run_blocking(
            self._composio_client_factory, limiter="external_http"
        )
        connection_account = await run_blocking(
            lambda: composio.connected_accounts.get(credentials.connection_id),
            limiter="external_http",
        )

        state_value = connection_account.state.val
        access_token = getattr(state_value, "access_token", None)
        refresh_token = getattr(state_value, "refresh_token", None)
        token_expires_at = await self._resolve_token_expiration(
            connector, connection_account
        )

        return OAuthCredentials(
            access_token=access_token,
            refresh_token=refresh_token or credentials.refresh_token,
            token_type=getattr(state_value, "token_type", None)
            or credentials.token_type
            or "Bearer",
            expires_at=token_expires_at,
            raw_response=self._serialize_raw_connection_state(connection_account),
            connection_id=connection_account.id,
            user_data=credentials.user_data,
        )

    async def revoke_connection(
        self,
        connector: ConnectorEntity,
        credentials: OAuthCredentials,
        user_id: UUID,
    ) -> None:
        if not credentials.connection_id:
            raise ConnectorValidationError(
                "Connection ID required for Composio revocation"
            )

        composio = await run_blocking(
            self._composio_client_factory, limiter="external_http"
        )
        await run_blocking(
            lambda: composio.connected_accounts.delete(credentials.connection_id),
            limiter="external_http",
        )
