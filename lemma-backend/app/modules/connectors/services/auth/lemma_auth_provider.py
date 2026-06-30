import asyncio
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from authlib.integrations.requests_client import OAuth2Session

from app.modules.connectors.domain.account import OAuthCredentials
from app.modules.connectors.domain.connector import ConnectorEntity
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.services.auth.auth_provider import AuthProviderInterface
from app.modules.connectors.services.helpers.helpers import get_atlassian_cloud_id
from app.core.log.log import get_logger

logger = get_logger(__name__)

CloudIdResolver = Callable[[str], Awaitable[str]]


class LemmaAuthProvider(AuthProviderInterface):
    """Lemma OAuth2 authentication provider."""

    def __init__(
        self,
        oauth_session_factory: type[OAuth2Session] = OAuth2Session,
        cloud_id_resolver: CloudIdResolver = get_atlassian_cloud_id,
    ):
        self._oauth_session_factory = oauth_session_factory
        self._cloud_id_resolver = cloud_id_resolver

    async def connect_with_credentials(
        self,
        connector: ConnectorEntity,
        user_id: UUID,
        credentials: dict,
    ) -> dict:
        # Native credential-managed accounts store the submitted credentials
        # verbatim — there is no provider-side connection to establish.
        return credentials

    async def get_authorization_url(
        self,
        connector: ConnectorEntity,
        user_id: UUID,
        state: str,
        redirect_uri: str,
    ) -> Tuple[str, str]:
        if not connector.oauth2_config:
            raise ConnectorValidationError(
                "OAuth2 configuration not found for connector"
            )

        oauth_config = connector.oauth2_config

        oauth = self._oauth_session_factory(
            client_id=oauth_config.client_id,
            client_secret=oauth_config.client_secret,
            redirect_uri=redirect_uri,
            scope=oauth_config.default_scopes,
        )

        authorization_url, provider_state = await asyncio.to_thread(
            oauth.create_authorization_url,
            url=oauth_config.authorization_url,
            state=state,
            **(oauth_config.extra_params or {})
        )

        return authorization_url, provider_state

    async def exchange_code_for_credentials(
        self,
        connector: ConnectorEntity,
        redirect_uri: str,
        user_id: UUID,
        state: Optional[str] = None,
    ) -> OAuthCredentials:
        if not connector.oauth2_config:
            raise ConnectorValidationError(
                "OAuth2 configuration not found for connector"
            )

        oauth_config = connector.oauth2_config
        authorization_response = redirect_uri
        normalized_redirect_uri = self._normalize_redirect_uri(authorization_response)

        oauth = self._oauth_session_factory(
            client_id=oauth_config.client_id,
            client_secret=oauth_config.client_secret,
            redirect_uri=normalized_redirect_uri,
            scope=oauth_config.default_scopes,
        )

        token_data = await asyncio.to_thread(
            oauth.fetch_token,
            url=oauth_config.token_url,
            authorization_response=authorization_response,
        )

        return await self._create_oauth_credentials(token_data, connector)

    def _normalize_redirect_uri(self, callback_url: str) -> str:
        parsed = urlsplit(callback_url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    async def refresh_credentials(
        self,
        connector: ConnectorEntity,
        credentials: OAuthCredentials,
        user_id: UUID,
    ) -> OAuthCredentials:
        if not connector.oauth2_config:
            raise ConnectorValidationError(
                "OAuth2 configuration not found for connector"
            )

        if not credentials.refresh_token:
            raise ConnectorValidationError(
                "Cannot refresh token: missing refresh token. "
                "This connector might not support refresh tokens."
            )

        oauth_config = connector.oauth2_config

        oauth = self._oauth_session_factory(
            client_id=oauth_config.client_id,
            client_secret=oauth_config.client_secret,
            token=credentials.raw_response,
        )

        token_data = await asyncio.to_thread(
            oauth.refresh_token,
            url=oauth_config.token_url,
            refresh_token=credentials.refresh_token,
        )

        return await self._create_oauth_credentials(token_data, connector)

    async def revoke_connection(
        self,
        connector: ConnectorEntity,
        credentials: OAuthCredentials,
        user_id: UUID,
    ) -> None:
        return None

    async def _create_oauth_credentials(
        self, token_data: dict, connector: ConnectorEntity
    ) -> OAuthCredentials:
        if not isinstance(token_data, dict):
            token_data = dict(token_data) if token_data else {}

        oauth_config = connector.oauth2_config
        access_token_path = oauth_config.access_token_path if oauth_config else None
        access_token = self._extract_token_field(
            token_data, access_token_path or "access_token", fallback_key="access_token"
        )
        if not access_token:
            logger.warning(
                "Access token not found at %s for connector %s",
                access_token_path,
                connector.id,
            )

        refresh_token_path = oauth_config.refresh_token_path if oauth_config else None
        refresh_token = self._extract_token_field(
            token_data, refresh_token_path or "refresh_token", fallback_key="refresh_token"
        )
        if not refresh_token:
            logger.warning(
                "Refresh token not found at %s for connector %s",
                refresh_token_path,
                connector.id,
            )

        expires_at = None
        if "expires_at" in token_data:
            expires_at = datetime.fromtimestamp(token_data["expires_at"])
        elif "expires_in" in token_data:
            expires_at = datetime.now().replace(microsecond=0) + timedelta(
                seconds=token_data["expires_in"]
            )
        if connector.id == "teams":
            import base64
            import json as _json

            tid: str | None = None
            oid: str | None = None
            for jwt_candidate in [
                token_data.get("access_token", ""),
                token_data.get("id_token", ""),
            ]:
                try:
                    parts = str(jwt_candidate).split(".")
                    if len(parts) >= 2:
                        padding = "=" * (4 - len(parts[1]) % 4)
                        claims = _json.loads(
                            base64.urlsafe_b64decode(parts[1] + padding)
                        )
                        tid = tid or claims.get("tid")
                        oid = oid or claims.get("oid")
                except Exception:
                    pass
                if tid:
                    break
            user_data = {"tid": tid, "tenant_id": tid, "oid": oid} if tid else None

        elif connector.id in ["jira", "confluence"]:
            logger.info("Getting Atlassian cloud id for connector: %s", connector.id)
            cloud_id = await self._cloud_id_resolver(access_token)
            if "jira" in connector.id:
                server_url = f"https://api.atlassian.com/ex/jira/{cloud_id}"
            else:
                server_url = (
                    f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2"
                )
            user_data = {
                "base_url": server_url,
                "cloud_id": cloud_id,
            }
        else:
            user_data = None

        token_type = token_data.get("token_type", "Bearer")
        if connector.id == "slack" and token_type.lower() in {"bot", "user"}:
            token_type = "Bearer"

        return OAuthCredentials(
            access_token=access_token or "",
            refresh_token=refresh_token,
            token_type=token_type,
            expires_at=expires_at,
            raw_response=token_data,
            user_data=user_data,
        )

    def _extract_token_field(
        self, token_data: dict, path: str, *, fallback_key: str
    ) -> str | None:
        import jsonpath_ng

        try:
            expr = jsonpath_ng.parse(path)
            matches = expr.find(token_data)
            if matches:
                value = matches[0].value
                if value is not None:
                    return str(value)
        except Exception:
            pass
        value = token_data.get(fallback_key)
        return str(value) if value is not None else None
