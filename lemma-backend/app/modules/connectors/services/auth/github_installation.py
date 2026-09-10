"""Which installation a freshly connected GitHub account speaks for.

Installation is the unit of access in the GitHub App model. A user token reaches
only the repositories the App is *installed* on -- GitHub says so outright, and
there is no escape hatch: `GET /user/repos` is not available to App user tokens
at all, so the only enumeration that exists is `/user/installations` and the
repositories under one. An account with no installation is not a connection with
a caveat; it is a token that can read nothing.

The install redirect names the installation in the callback query, and that is
where it arrives first. It is *not* taken on trust: GitHub warns the parameter is
spoofable, and `external_ref` is the webhook routing key, so a bad bind delivers
one tenant's events to another. Everything stated is verified against the token
that just came back before it is believed.

The connect flow itself uses the ordinary user-authorization endpoint rather than
`/apps/{slug}/installations/new`, because the install page only redirects on a
*first* install: someone who already has the App is shown the configure page and
never round-trips. That is not an edge case -- it is every reconnect, and every
second person in an organization. Authorize always round-trips, and the
installation is resolved afterwards from the token itself.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from app.core.log.log import get_logger
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.install_binding import (
    CredentialBlob,
    bind_external_ref,
)

logger = get_logger(__name__)

_API_ROOT = "https://api.github.com"
_USER_INSTALLATIONS_URL = f"{_API_ROOT}/user/installations"
_TIMEOUT = 15.0

#: One entry of GitHub's `/user/installations` response. Third-party JSON: only
#: the handful of keys read below are ours, and the rest is GitHub's to change.
Installation = dict[str, Any]

#: GitHub's own word for an installation scoped to a chosen subset.
SELECTION_ALL = "all"

_ORGANIZATION = "Organization"
_SETUP_ACTION_REQUEST = "request"


class InstallState(str, enum.Enum):
    """How far a GitHub account is from being able to reach anything.

    Four states rather than a boolean because the three unfinished ones need
    different things from the person, and telling someone to "connect again"
    when they are waiting on an organization owner is advice that cannot
    succeed.
    """

    READY = "READY"
    INSTALL_REQUIRED = "INSTALL_REQUIRED"
    CHOOSE_INSTALL = "CHOOSE_INSTALL"
    PENDING_APPROVAL = "PENDING_APPROVAL"


@dataclass(frozen=True, slots=True)
class InstallationChoice:
    """One installation this person can see, in the shape a picker needs."""

    installation_id: str
    account_login: str | None = None
    account_type: str | None = None
    repository_selection: str | None = None

    @property
    def is_organization(self) -> bool:
        return (self.account_type or "") == _ORGANIZATION

    @property
    def covers_every_repository(self) -> bool:
        return (self.repository_selection or "") == SELECTION_ALL

    @property
    def manage_url(self) -> str:
        """Where this installation's repository access is actually changed.

        A deep link rather than a picker of our own, because there is no API to
        build one with: the endpoints that add or remove a repository from an
        installation take a classic personal access token and nothing else.
        """
        return manage_installation_url(
            self.installation_id,
            account_login=self.account_login,
            account_type=self.account_type,
        )


@dataclass(frozen=True, slots=True)
class InstallationOutcome:
    """What a connect attempt established, and what is still outstanding."""

    state: InstallState
    installation_id: str | None = None
    choices: tuple[InstallationChoice, ...] = ()

    @property
    def is_ready(self) -> bool:
        return self.state is InstallState.READY


def install_url(state: str | None = None) -> str | None:
    """Where to send someone who has authorized but installed nothing.

    The `state` is the whole reason the round trip completes. Because the App
    manifest sets `request_oauth_on_install`, installing redirects back to the
    OAuth callback carrying `code`, `installation_id` and `setup_action` -- and
    GitHub preserves whatever `state` this link carried. Without one the callback
    has no connect request to claim and rejects the only redirect that ever names
    the installation, which is exactly how this flow used to dead-end.
    """
    slug = connector_settings.connector_github_app_slug
    if not slug:
        return None
    url = f"https://github.com/apps/{quote(slug, safe='')}/installations/new"
    return f"{url}?{urlencode({'state': state})}" if state else url


def manage_installation_url(
    installation_id: str,
    *,
    account_login: str | None = None,
    account_type: str | None = None,
) -> str:
    """The settings page where a person changes an installation's repositories.

    Organizations keep theirs somewhere else entirely, and sending an
    organization member to the personal page shows them their own installations
    instead of the one they were looking for.
    """
    if account_type == _ORGANIZATION and account_login:
        owner = quote(account_login, safe="")
        return f"https://github.com/organizations/{owner}/settings/installations/{installation_id}"
    return f"https://github.com/settings/installations/{installation_id}"


def installation_still_needed(connector_id: str, external_ref: str | None) -> bool:
    """Whether a just-connected account can reach nothing until someone installs.

    A GitHub App's user token is scoped to the repositories the App is installed
    on, and authorizing is not installing. So a first connect ends with a valid
    token, the right person, and no installation, and until this the flow
    reported that as "GitHub is connected" and stopped. It was true and useless:
    every repository call came back empty, and the only thing that ever mentioned
    installing was a trigger refusing to be created, much later and somewhere
    else.
    """
    return (connector_id or "").strip().lower() == "github" and not external_ref


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _installations(access_token: str) -> list[Installation]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            _USER_INSTALLATIONS_URL, headers=_headers(access_token)
        )
    response.raise_for_status()
    payload = response.json()
    found = payload.get("installations") if isinstance(payload, dict) else None
    return [item for item in (found or []) if isinstance(item, dict)]


def _choice(item: Installation) -> InstallationChoice | None:
    identifier = item.get("id")
    if identifier is None:
        return None
    account = item.get("account")
    account = account if isinstance(account, dict) else {}
    login = account.get("login")
    kind = account.get("type")
    selection = item.get("repository_selection")
    return InstallationChoice(
        installation_id=str(identifier),
        account_login=str(login) if login else None,
        account_type=str(kind) if kind else None,
        repository_selection=str(selection) if selection else None,
    )


def _access_token(credentials: CredentialBlob) -> str | None:
    if isinstance(credentials, dict):
        token = credentials.get("access_token")
    else:
        token = getattr(credentials, "access_token", None)
    reveal = getattr(token, "get_secret_value", None)
    if callable(reveal):
        token = reveal()
    return str(token) if token else None


async def verify_installation(access_token: str, installation_id: str) -> bool:
    """Whether this token's owner can actually reach that installation.

    GitHub is explicit that the `installation_id` on the callback cannot be
    trusted -- anyone can hit the callback URL with one they do not own. The
    documented check is the only one that proves it: ask for the installation's
    repositories *as the user who just authorized*, and let GitHub answer. A 404
    means it is not theirs.
    """
    url = (
        f"{_USER_INSTALLATIONS_URL}/{quote(str(installation_id), safe='')}/repositories"
    )
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_headers(access_token))
    except httpx.HTTPError:
        logger.warning(
            "connectors.github_installation.verify_failed.degraded", exc_info=True
        )
        return False
    if response.status_code == 200:
        return True
    if response.status_code in {403, 404}:
        # Not a transport failure: GitHub is saying this person cannot see that
        # installation. Worth a distinct event -- it is the shape a substituted
        # `installation_id` would take.
        logger.warning(
            "connectors.github_installation.claim_rejected.denied",
            status_code=response.status_code,
        )
        return False
    logger.warning(
        "connectors.github_installation.verify_failed.degraded",
        status_code=response.status_code,
    )
    return False


async def resolve_outcome(
    access_token: str,
    *,
    stated: str | None = None,
    setup_action: str | None = None,
) -> InstallationOutcome:
    """Everything the connect flow knows about this account's installation.

    `stated` is what the callback claimed and is checked before it is believed.
    `setup_action` is GitHub's word for what the person just did on the install
    screen, and `request` is the one that changes the answer: an organization
    member without installation rights gets no `installation_id` at all, because
    nothing was installed -- an owner has to approve first.
    """
    if (setup_action or "").strip().lower() == _SETUP_ACTION_REQUEST:
        return InstallationOutcome(InstallState.PENDING_APPROVAL)

    if stated and await verify_installation(access_token, stated):
        return InstallationOutcome(InstallState.READY, installation_id=str(stated))

    try:
        installations = await _installations(access_token)
    except httpx.HTTPError, ValueError:
        # Unbound rather than guessed. A transient failure here shows the person
        # the install step they may not need; the reconciler corrects it on the
        # next read, and that is the safe direction to be wrong in.
        logger.warning(
            "connectors.github_installation.lookup_failed.degraded", exc_info=True
        )
        return InstallationOutcome(InstallState.INSTALL_REQUIRED)

    choices = tuple(
        choice for choice in (_choice(item) for item in installations) if choice
    )
    if len(choices) == 1:
        return InstallationOutcome(
            InstallState.READY,
            installation_id=choices[0].installation_id,
            choices=choices,
        )
    if not choices:
        return InstallationOutcome(InstallState.INSTALL_REQUIRED)
    # More than one, and picking whichever came back first would route another
    # organization's events at this account -- the precise failure the
    # per-account binding exists to prevent. The person chooses.
    logger.info(
        "connectors.github_installation.choice_required.diagnostic",
        count=len(choices),
    )
    return InstallationOutcome(InstallState.CHOOSE_INSTALL, choices=choices)


async def resolve_installation(access_token: str) -> str | None:
    """The single installation this token speaks for, if there is exactly one."""
    return (await resolve_outcome(access_token)).installation_id


async def bound_external_ref(
    connector_id: str,
    credentials: CredentialBlob,
    callback_url: str | None = None,
) -> str | None:
    """The tenant to bind an account to, asking GitHub when nothing else says.

    Every other connector is unchanged: this is `bind_external_ref` plus the
    GitHub-only step of proving the answer before storing it.
    """
    stated = bind_external_ref(connector_id, credentials, callback_url)
    if (connector_id or "").strip().lower() != "github":
        return stated
    token = _access_token(credentials)
    if not token:
        return None
    return (await resolve_outcome(token, stated=stated)).installation_id
