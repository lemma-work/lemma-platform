from app.modules.connectors.domain.account import CredentialTypes, OAuthCredentials
from abc import ABC, abstractmethod
from typing import Tuple, Optional
from uuid import UUID
from app.modules.connectors.domain.connector import ConnectorEntity


class AuthProviderInterface(ABC):
    """Abstract interface for authentication providers."""

    @abstractmethod
    async def connect_with_credentials(
        self,
        connector: ConnectorEntity,
        user_id: UUID,
        credentials: dict,
    ) -> CredentialTypes:
        """
        Connect an account directly from user-supplied credentials (non-OAuth).

        Used for credential-managed schemes (API key, etc.) where there is no
        redirect/callback flow.

        Args:
            connector: The connector (effective connector with provider config)
            user_id: The user ID
            credentials: Raw credential fields submitted by the user

        Returns:
            The credentials to persist on the account.
        """
        pass

    @abstractmethod
    async def get_authorization_url(
        self,
        connector: ConnectorEntity,
        user_id: UUID,
        state: str,
        redirect_uri: str,
    ) -> Tuple[str, str]:
        """
        Get authorization URL for OAuth flow.

        Args:
            connector: The connector to connect
            user_id: The user ID
            state: OAuth state parameter
            redirect_uri: OAuth redirect URI

        Returns:
            Tuple of (authorization_url, state)
        """
        pass

    @abstractmethod
    async def exchange_code_for_credentials(
        self,
        connector: ConnectorEntity,
        redirect_uri: str,
        user_id: UUID,
        state: Optional[str] = None,
    ) -> OAuthCredentials:
        """
        Exchange authorization code for OAuth credentials.

        Args:
            connector: The connector
            redirect_uri: The redirect URI with authorization code
            user_id: The user ID
            state: Optional OAuth state parameter

        Returns:
            OAuthCredentials object
        """
        pass

    @abstractmethod
    async def refresh_credentials(
        self,
        connector: ConnectorEntity,
        credentials: OAuthCredentials,
        user_id: UUID,
    ) -> OAuthCredentials:
        """
        Refresh OAuth credentials.

        Args:
            connector: The connector
            credentials: Current credentials
            user_id: The user ID

        Returns:
            Updated OAuthCredentials object
        """
        pass

    @abstractmethod
    async def revoke_connection(
        self,
        connector: ConnectorEntity,
        credentials: OAuthCredentials,
        user_id: UUID,
    ) -> None:
        """
        Revoke/delete the connection.

        Args:
            connector: The connector
            credentials: The credentials to revoke
            user_id: The user ID
        """
        pass
