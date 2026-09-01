"""What an authentication scheme has to provide.

Every method takes the *install* rather than the connector. The connector is
shared catalog data; which client secret to present, which tenant an
installation is bound to, and whether the organization brought its own
credentials are all properties of one organization's install, and none of them
have anywhere to live on a catalog entry.
"""

from abc import ABC, abstractmethod
from typing import Optional, Tuple
from uuid import UUID

from app.modules.connectors.domain.account import CredentialTypes, OAuthCredentials
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall


class AuthProviderInterface(ABC):
    """Abstract interface for authentication providers."""

    @abstractmethod
    async def connect_with_credentials(
        self,
        install: ResolvedAuthInstall,
        user_id: UUID,
        credentials: dict,
    ) -> CredentialTypes:
        """Connect an account directly from user-supplied credentials (non-OAuth).

        Used for credential-managed schemes (API key, etc.) where there is no
        redirect/callback flow. Returns the credentials to persist.
        """

    @abstractmethod
    async def get_authorization_url(
        self,
        install: ResolvedAuthInstall,
        user_id: UUID,
        state: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> Tuple[str, str]:
        """Return ``(authorization_url, state)`` to send the person to.

        ``code_verifier`` is supplied by the caller when the install's client
        has no secret, because it must survive until the callback.
        """

    @abstractmethod
    async def exchange_code_for_credentials(
        self,
        install: ResolvedAuthInstall,
        redirect_uri: str,
        user_id: UUID,
        state: Optional[str] = None,
        code_verifier: str | None = None,
    ) -> OAuthCredentials:
        """Turn the provider's callback into credentials worth storing.

        ``redirect_uri`` is the full callback URL, code and all.
        """

    @abstractmethod
    async def refresh_credentials(
        self,
        install: ResolvedAuthInstall,
        credentials: OAuthCredentials,
        user_id: UUID,
    ) -> OAuthCredentials:
        """Exchange an expiring credential for a fresh one."""

    @abstractmethod
    async def revoke_connection(
        self,
        install: ResolvedAuthInstall,
        credentials: OAuthCredentials,
        user_id: UUID,
    ) -> None:
        """Give the credential up at the provider, if it can be given up."""
