"""Proving which Lemma person a Slack user is, across a workspace Lemma manages.

Two proofs count, and both are Slack's own word -- never a profile email, which
a workspace admin can edit:

* **The install** (``INSTALL``): the person whose grant installed Lemma's app
  is the Slack user that grant names (``authed_user``), when the browser that
  came back from Slack carried their own Lemma session.
* **Sign in with Slack** (``OIDC``): from the link their App Home shows, a
  signed-in person signs in with Slack on Lemma's own app. The callback must
  see the same Lemma session that started it, and Slack must name the very
  Slack user and workspace the link was for. So a link somebody else sent
  proves nothing: whoever opens it signs in to Slack as themselves, and is
  refused.

A proof is written through to the identity cache
(``agent_surface_external_users``), so identity resolution, the group log and
rosters see the person at once; any other Slack user in that workspace cached
as the same person is forgotten.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlencode
from uuid import UUID

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.errors import SlackSignInError
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (  # noqa: E501
    PROVED_BY_OIDC,
    SlackManagerRepository,
)
from app.modules.connectors.contracts.own_apps import LemmaAppClient, lemma_app_client
from app.modules.identity.contracts.organizations import organization_member_role

logger = get_logger(__name__)

SLACK_OIDC_AUTHORIZE_URL = "https://slack.com/openid/connect/authorize"
SLACK_OIDC_TOKEN_URL = "https://slack.com/api/openid.connect.token"
SLACK_OIDC_JWKS_URL = "https://slack.com/openid/connect/keys"
SLACK_OIDC_ISSUER = "https://slack.com"
_TEAM_CLAIM = "https://slack.com/team_id"
_USER_CLAIM = "https://slack.com/user_id"
#: Long enough to pass Slack's consent page; a started sign-in is single use.
SIGN_IN_TTL_SECONDS = 10 * 60
#: Clock skew tolerated on the ID token's ``iat`` and ``exp``.
ID_TOKEN_LEEWAY_SECONDS = 30
#: The route Slack sends the browser back to, registered on Lemma's app.
CALLBACK_PATH = "/surface-setup/slack/people/sign-in/callback"


def callback_url() -> str:
    return f"{settings.api_url.rstrip('/')}{CALLBACK_PATH}"


@dataclass(frozen=True, slots=True)
class SlackIdentity:
    """Who Slack's ID token says signed in."""

    team_id: str
    slack_user_id: str


@dataclass(frozen=True)
class SlackOidcCalls:
    """Slack's side of signing in. A seam, so a test can stand in for Slack."""

    exchange: Callable[[LemmaAppClient, str, str], Awaitable[str]]
    signing_keys: Callable[[], Awaitable[dict[str, Any]]]


async def _exchange_code(client: LemmaAppClient, code: str, redirect_uri: str) -> str:
    async with httpx.AsyncClient(timeout=10) as http:
        response = await http.post(
            SLACK_OIDC_TOKEN_URL,
            data={
                "client_id": client.client_id,
                "client_secret": client.client_secret.get_secret_value(),
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    body = response.json() if response.status_code == 200 else {}
    id_token = str(body.get("id_token") or "") if isinstance(body, dict) else ""
    if not id_token:
        raise SlackSignInError("unavailable", "Slack did not sign you in. Try again.")
    return id_token


async def _signing_keys() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as http:
        response = await http.get(SLACK_OIDC_JWKS_URL)
    if response.status_code != 200:
        raise SlackSignInError("unavailable", "Slack's keys are unavailable.")
    return cast(dict[str, Any], response.json())


SLACK_OIDC = SlackOidcCalls(exchange=_exchange_code, signing_keys=_signing_keys)


def identity_from_id_token(
    id_token: str, *, keys: dict[str, Any], client_id: str, nonce: str
) -> SlackIdentity:
    """Check Slack's signature, audience, issuer and nonce; who it names."""
    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") != "RS256":
            raise SlackSignInError("unavailable", "Slack signed in an unexpected way.")
        matching = next(
            key for key in keys.get("keys", []) if key.get("kid") == header.get("kid")
        )
        claims = jwt.decode(
            id_token,
            cast(Any, RSAAlgorithm.from_jwk(json.dumps(matching))),
            algorithms=["RS256"],
            audience=client_id,
            issuer=SLACK_OIDC_ISSUER,
            # The token is minted a moment before this check, so a clock a
            # second behind Slack's reads its ``iat`` as in the future.
            leeway=ID_TOKEN_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        )
    except StopIteration, jwt.PyJWTError:
        raise SlackSignInError(
            "unavailable", "Slack's answer could not be checked. Try again."
        ) from None
    if not secrets.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise SlackSignInError("expired", "That sign-in has expired. Try again.")
    team_id = str(claims.get(_TEAM_CLAIM) or "")
    slack_user_id = str(claims.get(_USER_CLAIM) or claims.get("sub") or "")
    if not team_id or not slack_user_id:
        raise SlackSignInError("unavailable", "Slack did not say who you are.")
    return SlackIdentity(team_id=team_id, slack_user_id=slack_user_id)


@dataclass(frozen=True, slots=True)
class _SignIn:
    """A started sign-in, kept under its ``state`` until Slack sends them back."""

    nonce: str
    user_id: str
    team_id: str
    slack_user_id: str


def _key(state: str) -> str:
    return f"surfaces:slack-sign-in:{hashlib.sha256(state.encode()).hexdigest()}"


async def _member_of_install_org(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID, team_id: str
) -> None:
    install = await SlackManagerRepository(uow.session).install_for_team(team_id)
    if install is None:
        raise SlackSignInError(
            "not_installed", "Lemma isn't installed in that Slack workspace."
        )
    role = await organization_member_role(
        uow, user_id=user_id, organization_id=install.organization_id
    )
    if role is None:
        raise SlackSignInError(
            "not_in_organization",
            "That Slack workspace belongs to a Lemma organization you're not in. "
            "Ask an owner to invite you, then connect again.",
        )


async def start_sign_in(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID, team_id: str, slack_user_id: str
) -> str:
    """Begin Sign in with Slack for the signed-in person; Slack's URL to open.

    Refused before Slack is involved when the workspace is not Lemma's here,
    or belongs to an organization the person is not in.
    """
    await _member_of_install_org(uow, user_id=user_id, team_id=team_id)
    client = await lemma_app_client(uow, "slack")
    if client is None:
        raise SlackSignInError(
            "unavailable", "Sign in with Slack isn't set up on this Lemma."
        )
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    started = _SignIn(
        nonce=nonce,
        user_id=str(user_id),
        team_id=team_id,
        slack_user_id=slack_user_id,
    )
    try:
        await get_redis().set(
            _key(state),
            _encoded(started),
            ex=SIGN_IN_TTL_SECONDS,
            nx=True,
        )
    except RedisError:
        logger.warning(
            "agent_surfaces.slack_person.sign_in_start.degraded", exc_info=True
        )
        raise SlackSignInError(
            "unavailable", "Sign in with Slack is unavailable. Try again shortly."
        ) from None
    query = urlencode(
        {
            "response_type": "code",
            "scope": "openid profile",
            "client_id": client.client_id,
            "redirect_uri": callback_url(),
            "state": state,
            "nonce": nonce,
            # Straight to this workspace: the proof is for it alone.
            "team": team_id,
        }
    )
    return f"{SLACK_OIDC_AUTHORIZE_URL}?{query}"


def _encoded(started: _SignIn) -> str:
    """Four short ids: bounded, so on the loop."""
    return json.dumps(
        {
            "nonce": started.nonce,
            "user_id": started.user_id,
            "team_id": started.team_id,
            "slack_user_id": started.slack_user_id,
        }
    )


def _decoded(raw: str | bytes) -> _SignIn:
    return _SignIn(**json.loads(raw))


async def _take(state: str) -> _SignIn:
    try:
        raw = await get_redis().getdel(_key(state))
    except RedisError:
        logger.warning(
            "agent_surfaces.slack_person.sign_in_finish.degraded", exc_info=True
        )
        raise SlackSignInError(
            "unavailable", "Sign in with Slack is unavailable. Try again shortly."
        ) from None
    if not raw:
        raise SlackSignInError("expired", "That sign-in has expired. Try again.")
    try:
        return _decoded(raw)
    except TypeError, ValueError:
        raise SlackSignInError("expired", "That sign-in has expired.") from None


async def finish_sign_in(
    uow: SqlAlchemyUnitOfWork,
    *,
    state: str,
    code: str | None,
    session_user_id: UUID | None,
    calls: SlackOidcCalls | None = None,
) -> SlackIdentity:
    """Check Slack's answer and record the proof; the Slack user proven.

    The state is spent first, so a replay is refused whatever happens next.
    """
    started = await _take(state)
    if session_user_id is None or str(session_user_id) != started.user_id:
        logger.warning(
            "agent_surfaces.slack_person.sign_in_refused.degraded",
            reason="session_changed",
            team_id=started.team_id,
        )
        raise SlackSignInError(
            "session_changed",
            "Finish signing in from the browser you started in, signed in as yourself.",
        )
    if not code:
        raise SlackSignInError("cancelled", "Sign in with Slack was cancelled.")
    client = await lemma_app_client(uow, "slack")
    if client is None:
        raise SlackSignInError(
            "unavailable", "Sign in with Slack isn't set up on this Lemma."
        )
    slack = calls or SLACK_OIDC
    try:
        id_token = await slack.exchange(client, code, callback_url())
        keys = await slack.signing_keys()
    except httpx.HTTPError, ValueError:
        logger.warning(
            "agent_surfaces.slack_person.sign_in_exchange.degraded", exc_info=True
        )
        raise SlackSignInError(
            "unavailable", "Slack did not answer. Try again shortly."
        ) from None
    identity = identity_from_id_token(
        id_token, keys=keys, client_id=client.client_id, nonce=started.nonce
    )
    if (identity.team_id, identity.slack_user_id) != (
        started.team_id,
        started.slack_user_id,
    ):
        logger.warning(
            "agent_surfaces.slack_person.sign_in_refused.degraded",
            reason="other_slack_user",
            team_id=started.team_id,
        )
        raise SlackSignInError(
            "other_slack_user",
            "You signed in to Slack as someone other than the person this link "
            "was for. Open Lemma's Home tab in Slack yourself and connect from there.",
        )
    user_id = UUID(started.user_id)
    await _member_of_install_org(uow, user_id=user_id, team_id=identity.team_id)
    await record_proof(
        uow,
        team_id=identity.team_id,
        slack_user_id=identity.slack_user_id,
        user_id=user_id,
        proved_by=PROVED_BY_OIDC,
    )
    await uow.commit()
    return identity


async def record_proof(
    uow: SqlAlchemyUnitOfWork,
    *,
    team_id: str,
    slack_user_id: str,
    user_id: UUID,
    proved_by: str,
) -> None:
    """Write the proof, and through to the identity cache. Not committed."""
    replaced = await SlackManagerRepository(uow.session).prove_person(
        team_id=team_id,
        slack_user_id=slack_user_id,
        user_id=user_id,
        proved_by=proved_by,
    )
    cache = ExternalSurfaceUserRepository(uow)
    await cache.upsert(
        platform=SurfacePlatform.SLACK.value,
        tenant_id=team_id,
        external_user_id=slack_user_id,
        email=None,
        phone=None,
        display_name=None,
        raw_profile=None,
        resolved_user_id=user_id,
    )
    forgotten = await cache.clear_others_resolved_to(
        platform=SurfacePlatform.SLACK.value,
        tenant_id=team_id,
        resolved_user_id=user_id,
        keep_external_user_id=slack_user_id,
    )
    logger.info(
        "agent_surfaces.slack_manager.person_linked",
        team_id=team_id,
        proved_by=proved_by,
        replaced=bool(replaced),
        forgotten=forgotten,
    )


__all__ = [
    "CALLBACK_PATH",
    "SLACK_OIDC",
    "SlackIdentity",
    "SlackOidcCalls",
    "finish_sign_in",
    "identity_from_id_token",
    "record_proof",
    "start_sign_in",
]
