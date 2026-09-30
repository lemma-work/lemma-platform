"""Client authentication at the token and revocation endpoints.

The SDK's authenticator compares the presented secret with a stored one in the
clear. Secrets here are stored only as digests, so this compares digests; the
rest -- which method the client registered, where each method carries the
secret -- follows RFC 6749 §2.3.1 exactly as the SDK does.
"""

from __future__ import annotations

import base64
import binascii
import hmac
from urllib.parse import unquote

from mcp.server.auth.middleware.client_auth import (
    AuthenticationError,
    ClientAuthenticator,
)
from starlette.requests import Request

from app.modules.mcp_access.domain.entities import ClientRegistration
from app.modules.mcp_access.domain.tokens import digest
from app.modules.mcp_access.services.clients import ClientDirectory, LemmaOAuthClient


def basic_credentials(header: str) -> tuple[str, str] | None:
    """(client id, secret) from an ``Authorization: Basic`` header, each
    form-decoded as RFC 6749 §2.3.1 has them sent; ``None`` for any other
    header, or one that does not decode. The one parser for it here."""
    if not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except binascii.Error, UnicodeDecodeError:
        return None
    client_id, _, secret = decoded.partition(":")
    return unquote(client_id), unquote(secret)


def _basic_secret(header: str, client_id: str) -> str:
    credentials = basic_credentials(header)
    if credentials is None:
        raise AuthenticationError("Missing or invalid Basic authentication")
    if credentials[0] != client_id:
        raise AuthenticationError("Client ID mismatch in Basic auth")
    return credentials[1]


JWT_BEARER_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def _client_id(from_form: object, header: str) -> str:
    """From the form, or from Basic credentials when the form has none."""
    client_id = from_form
    if not client_id and header.startswith("Basic "):
        credentials = basic_credentials(header)
        if credentials is None:
            raise AuthenticationError("Invalid Basic authentication header")
        client_id = credentials[0]
    if not isinstance(client_id, str) or not client_id:
        raise AuthenticationError("Missing client_id")
    return client_id


class DigestClientAuthenticator(ClientAuthenticator):
    def __init__(
        self, clients: ClientDirectory, *, assertion_audiences: tuple[str, ...]
    ) -> None:
        # The base class only keeps the provider to call `get_client` on, and
        # this override never reaches the base implementation.
        self._clients = clients
        self._assertion_audiences = assertion_audiences

    async def authenticate_request(self, request: Request) -> LemmaOAuthClient:
        form = await request.form()
        header = request.headers.get("Authorization", "")
        client_id = _client_id(form.get("client_id"), header)
        client = await self._clients.get(client_id)
        if client is None:
            raise AuthenticationError("Invalid client_id")

        method = client.token_endpoint_auth_method or "client_secret_post"
        if method == "none":
            return client
        if method == "private_key_jwt":
            await self._check_assertion(
                client, form.get("client_assertion_type"), form.get("client_assertion")
            )
            return client
        if client.client_secret_hash is None:
            raise AuthenticationError("Client has no stored secret")
        if method == "client_secret_basic":
            presented = _basic_secret(header, client_id)
        elif method == "client_secret_post":
            raw = form.get("client_secret")
            presented = raw if isinstance(raw, str) else ""
        else:
            raise AuthenticationError(f"Unsupported auth method: {method}")
        if not presented or not hmac.compare_digest(
            digest(presented).encode(), client.client_secret_hash.encode()
        ):
            raise AuthenticationError("Invalid client_secret")
        return client

    async def _check_assertion(
        self, client: LemmaOAuthClient, assertion_type: object, assertion: object
    ) -> None:
        """Only a metadata-document client can use ``private_key_jwt``: its
        keys are wherever its document says, and a dynamically registered
        client has no document to say it."""
        if client.registration is not ClientRegistration.METADATA_DOCUMENT:
            raise AuthenticationError(
                "private_key_jwt needs a client metadata document"
            )
        if assertion_type != JWT_BEARER_ASSERTION_TYPE:
            raise AuthenticationError("Invalid client_assertion_type")
        if not isinstance(assertion, str) or not assertion:
            raise AuthenticationError("Missing client_assertion")
        try:
            await self._clients.verify_assertion(
                client_id=client.client_id or "",
                assertion=assertion,
                audiences=self._assertion_audiences,
            )
        except ValueError as exc:
            raise AuthenticationError(f"Invalid client assertion: {exc}") from exc
