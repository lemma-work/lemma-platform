"""``/oauth/revoke`` (RFC 7009), for every kind of client this server has.

The SDK's handler validates the form against a model that requires
``client_secret`` -- a key a public client (Claude, Claude Code) and a
``private_key_jwt`` client (ChatGPT) never send. Every disconnect from either
was a 400, and the grant stayed live until the person ended it here. The
client is authenticated exactly as at the token endpoint; the form only has to
name the token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mcp.server.auth.middleware.client_auth import (
    AuthenticationError,
    ClientAuthenticator,
)
from mcp.server.auth.provider import AccessToken, RefreshToken
from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.modules.mcp_access.services.authorization_server import (
    LemmaAuthorizationServer,
)


class _RevocationForm(BaseModel):
    token: str
    token_type_hint: Literal["access_token", "refresh_token"] | None = None


@dataclass
class RevocationHandler:
    provider: LemmaAuthorizationServer
    client_authenticator: ClientAuthenticator

    async def handle(self, request: Request) -> Response:
        try:
            client = await self.client_authenticator.authenticate_request(request)
        except AuthenticationError as exc:
            return JSONResponse(
                {"error": "invalid_client", "error_description": exc.message},
                status_code=401,
            )
        try:
            form = _RevocationForm.model_validate(dict(await request.form()))
        except ValidationError:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "token is required"},
                status_code=400,
            )

        loaders = [
            self.provider.load_access_token,
            lambda token: self.provider.load_refresh_token(client, token),
        ]
        if form.token_type_hint == "refresh_token":
            loaders.reverse()
        found: AccessToken | RefreshToken | None = None
        for load in loaders:
            found = await load(form.token)
            if found is not None:
                break
        # RFC 7009 §2.2: an unknown token, or one that is not this client's,
        # is answered 200 all the same.
        if found is not None and found.client_id == client.client_id:
            await self.provider.revoke_token(found)
        return Response(
            status_code=200, headers={"Cache-Control": "no-store", "Pragma": "no-cache"}
        )
