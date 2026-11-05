"""The consent screen's two calls: what is being asked, and the answer.

The person reaches the screen from the client, signed in to Lemma as usual.
Nothing about the request is trusted to the browser: it carries only the
request id, and the id is single-use -- answering it deletes it, so one "Allow"
produces one code.

The screen shows the redirect host as well as the client's name. Anybody can
register a client called "Claude"; what they cannot do is receive a code at
claude.ai. The host is the part of the request a person can actually check.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from mcp.server.auth.provider import construct_redirect_uri

from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.mcp_access.domain.entities import Scope, parse_scopes
from app.modules.mcp_access.domain.names import display_name
from app.modules.mcp_access.domain.redirects import redirect_allowed
from app.modules.mcp_access.domain.resources import pod_resource_url
from app.modules.mcp_access.infrastructure.ephemeral import (
    CODE_TTL_SECONDS,
    EphemeralStore,
    IssuedCode,
    PendingAuthorization,
)
from app.modules.mcp_access.infrastructure.repositories import McpAccessRepository
from app.modules.mcp_access.services.clients import ClientDirectory
from app.modules.pod.contracts.members import pod_name


logger = get_logger(__name__)


class ConsentRequestGone(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "This sign-in request has expired or was already answered. "
            "Start again from the app you were connecting.",
            code="MCP_CONSENT_REQUEST_GONE",
            status_code=404,
        )


class PodNotAvailable(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "You do not have access to this pod, or it no longer exists.",
            code="MCP_CONSENT_POD_UNAVAILABLE",
            status_code=403,
        )


@dataclass(frozen=True, slots=True)
class ConsentRequest:
    client_id: str
    client_name: str
    """What the client calls itself. Never verified."""
    verified_host: str | None
    """The host serving the client's metadata document -- the one checked
    fact about who is asking. ``None`` for a dynamically registered client."""
    client_uri: str | None
    redirect_host: str
    """For a web redirect, the host. For an app's own scheme, the scheme and a
    colon -- ``cursor:`` -- because the rest of such a URI is whatever the app
    wrote: ``evilapp://claude.ai/cb`` is not going to claude.ai."""
    redirect_to_app: bool
    pod_id: UUID
    pod_name: str
    scopes: frozenset[Scope]


def _host(url: str) -> str:
    parts = urlsplit(url)
    return parts.netloc or url


def _is_web(url: str) -> bool:
    return urlsplit(url).scheme.lower() in {"http", "https"}


def _redirect_target(url: str) -> str:
    return _host(url) if _is_web(url) else urlsplit(url).scheme.lower() + ":"


class ConsentService:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clients: ClientDirectory,
        ephemeral: EphemeralStore,
        issuer: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._uow_factory = uow_factory
        self._clients = clients
        self._ephemeral = ephemeral
        self._issuer = issuer
        self._now = clock

    async def describe(self, *, request_id: str, user_id: UUID) -> ConsentRequest:
        pending = await self._ephemeral.read_pending(request_id)
        if pending is None:
            raise ConsentRequestGone()
        name = await self._pod_name_if_allowed(
            user_id=user_id, pod_id=UUID(pending.pod_id)
        )
        client = await self._clients.get(pending.client_id)
        if client is None:
            raise ConsentRequestGone()
        return ConsentRequest(
            client_id=pending.client_id,
            client_name=display_name(
                client.client_name, _redirect_target(pending.redirect_uri)
            ),
            verified_host=client.verified_host,
            client_uri=str(client.client_uri) if client.client_uri else None,
            redirect_host=_redirect_target(pending.redirect_uri),
            redirect_to_app=not _is_web(pending.redirect_uri),
            pod_id=UUID(pending.pod_id),
            pod_name=name,
            scopes=parse_scopes(pending.scopes),
        )

    async def answer(
        self,
        *,
        request_id: str,
        user_id: UUID,
        allow: bool,
        read_only: bool = False,
        events: bool = False,
    ) -> str:
        """The URL to send the browser to: the client's redirect URI, with a
        code or with ``access_denied``, and always with ``state`` and ``iss``.

        ``read_only`` is the person answering "allow reading only": the
        connection reads and does nothing else, whatever the client asked
        for. ``events`` is the person agreeing, separately, to the app being
        told about new rows (`Scope.EVENTS`); it is granted only when the app
        asked for it and the person said yes, never by default. The person's
        standing in the pod is checked
        before the request is used up, so somebody who has learned a request id
        but cannot read the pod cannot spend it.
        """
        pending = await self._ephemeral.read_pending(request_id)
        if pending is None:
            raise ConsentRequestGone()
        # Before anything is granted: refused on the way out instead, the
        # grant and a code would already exist for a browser never sent back.
        if not redirect_allowed(pending.redirect_uri):
            raise ConsentRequestGone()
        pod_id = UUID(pending.pod_id)
        await self._pod_name_if_allowed(user_id=user_id, pod_id=pod_id)
        pending = await self._ephemeral.take_pending(request_id)
        if pending is None:
            raise ConsentRequestGone()
        if not allow:
            logger.info(
                "mcp_access.consent.denied",
                client_id=pending.client_id,
                pod_id=str(pod_id),
            )
            return self._redirect(pending, error="access_denied")
        client = await self._clients.get(pending.client_id)
        if client is None:
            raise ConsentRequestGone()
        await self._clients.persist(client)
        # Reading only is reading, whatever the client asked for: an app that
        # asked only to write, answered "read only", can read.
        asked = parse_scopes(pending.scopes)
        scopes = {Scope.READ} if read_only else set(asked - {Scope.EVENTS})
        if events and Scope.EVENTS in asked:
            scopes.add(Scope.EVENTS)
        granted = sorted(scope.value for scope in scopes)
        async with self._uow_factory() as uow:
            grant_id = await McpAccessRepository(uow).create_grant(
                user_id=user_id,
                client_id=pending.client_id,
                pod_id=pod_id,
                scopes=granted,
                # The canonical form, not what the client sent: authorize
                # accepts any spelling RFC 3986 calls the same (a trailing
                # slash, an upper-case host), and the verifier compares with
                # the canonical one.
                resource=pod_resource_url(self._issuer, pod_id),
            )
            await uow.commit()
        logger.info(
            "mcp_access.grant.created",
            grant_id=str(grant_id),
            client_id=pending.client_id,
            pod_id=str(pod_id),
            scopes=" ".join(granted),
        )
        code = await self._ephemeral.issue_code(
            IssuedCode(
                grant_id=str(grant_id),
                client_id=pending.client_id,
                scopes=granted,
                code_challenge=pending.code_challenge,
                redirect_uri=pending.redirect_uri,
                redirect_uri_provided_explicitly=pending.redirect_uri_provided_explicitly,
                resource=pod_resource_url(self._issuer, pod_id),
                expires_at=self._now() + CODE_TTL_SECONDS,
            )
        )
        return self._redirect(pending, code=code)

    def _redirect(
        self,
        pending: PendingAuthorization,
        *,
        code: str | None = None,
        error: str | None = None,
    ) -> str:
        # Checked again here, on the way out, although authorize checked it on
        # the way in: this string is what the browser is sent to, and a URI that
        # runs code would run it on the auth site with the person signed in.
        if not redirect_allowed(pending.redirect_uri):
            raise ConsentRequestGone()
        # `iss` is RFC 9207: it tells the client which server answered, which is
        # how a client talking to several servers notices a mix-up attack.
        return construct_redirect_uri(
            pending.redirect_uri,
            code=code,
            error=error,
            state=pending.state,
            iss=self._issuer,
        )

    async def _pod_name_if_allowed(self, *, user_id: UUID, pod_id: UUID) -> str:
        """The pod's name, if this person may read it -- the same standing any
        other pod read needs. A person cannot hand a client more than they
        have: every tool call is authorized as them, so this is a courtesy
        refusal at the door, not the enforcement."""
        async with self._uow_factory() as uow:
            ctx = await create_authorization_data_service(uow).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            if ctx.pod_is_deleted or not ctx.has_permission(Permissions.POD_READ):
                raise PodNotAvailable()
            name = await pod_name(uow.session, pod_id)
        if name is None:
            raise PodNotAvailable()
        return name
