"""One test double for the auth port, declared as a subclass rather than
monkeypatched onto a concrete class.

The reason is a real incident. Adding ``code_verifier`` to
``get_authorization_url`` left four doubles behind on the old signature, each
installed with ``monkeypatch.setattr(LemmaAuthProvider, ...)``. A plain
function replacing a method matches no interface, so nothing objected: the unit
suite stayed green and only an e2e run found it.

Subclassing alone does not fix that here -- ``pyproject.toml`` excludes
``app/**/tests/**`` and ``app/modules/test_support/**`` from basedpyright, so a
type error in a test file is never read by anything. What makes this safe is
that the double is *importable*, which lets ``test_auth_provider_conformance``
check its signatures against the port at run time, in the fast suite. Declaring
it as a subclass is what makes that check meaningful, and buys the editor
squiggle for whoever changes the port next.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple
from uuid import UUID

from app.modules.connectors.domain.account import CredentialTypes, OAuthCredentials
from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.services.auth.auth_provider import AuthProviderInterface

AuthorizeHook = Callable[[ResolvedAuthInstall, str | None], None]
ExchangeHook = Callable[[ResolvedAuthInstall, str, str | None], OAuthCredentials]


class FakeAuthProvider(AuthProviderInterface):
    """An OAuth provider that answers without a network.

    Tests vary behaviour through the two hooks rather than by redefining
    methods, so the signatures stay in one place and cannot drift per test.
    """

    def __init__(
        self,
        *,
        credentials: OAuthCredentials | None = None,
        authorization_url: str = "https://mock.example.com/authorize",
        provider_state: str = "provider_state",
        on_authorize: AuthorizeHook | None = None,
        on_exchange: ExchangeHook | None = None,
    ) -> None:
        self._credentials = credentials or OAuthCredentials(
            access_token="access-token",
            refresh_token="refresh-token",
            token_type="Bearer",
        )
        self._authorization_url = authorization_url
        self._provider_state = provider_state
        self._on_authorize = on_authorize
        self._on_exchange = on_exchange
        # What the last call was given, for tests that assert on it.
        self.last_code_verifier: str | None = None
        self.revoked: list[UUID] = []

    async def connect_with_credentials(
        self,
        install: ResolvedAuthInstall,
        user_id: UUID,
        credentials: dict,
    ) -> CredentialTypes | dict:
        return credentials

    async def get_authorization_url(
        self,
        install: ResolvedAuthInstall,
        user_id: UUID,
        state: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> Tuple[str, str]:
        self.last_code_verifier = code_verifier
        if self._on_authorize is not None:
            self._on_authorize(install, code_verifier)
        return self._authorization_url, self._provider_state

    async def exchange_code_for_credentials(
        self,
        install: ResolvedAuthInstall,
        redirect_uri: str,
        user_id: UUID,
        state: Optional[str] = None,
        code_verifier: str | None = None,
    ) -> OAuthCredentials:
        self.last_code_verifier = code_verifier
        if self._on_exchange is not None:
            return self._on_exchange(install, redirect_uri, code_verifier)
        return self._credentials

    async def refresh_credentials(
        self,
        install: ResolvedAuthInstall,
        credentials: OAuthCredentials,
        user_id: UUID,
    ) -> OAuthCredentials:
        return self._credentials

    async def revoke_connection(
        self,
        install: ResolvedAuthInstall,
        credentials: OAuthCredentials,
        user_id: UUID,
    ) -> None:
        self.revoked.append(user_id)
