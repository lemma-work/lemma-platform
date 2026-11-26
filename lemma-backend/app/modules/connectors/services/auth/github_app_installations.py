"""The organization's GitHub App installations, as an APP pod connector sees them.

The bindings (``connector_installations``, ``github_bindings.py``) say which
installations an organization holds. What an APP connector also needs is which
GitHub account each one is on, and whether that account is an organization:
the bot acts only on installations on GitHub organizations
(``domain/app_installation.py``). That is GitHub's to say, asked as the App
itself (``GET /app/installations/{id}`` with the App's JWT), and kept in the
binding's ``details`` under ``account`` once read, refreshed when a day old.

Asking GitHub never happens with a connection held. The reads and the write
here each take a session; the network half takes none, and the two halves are
put together by a caller that opens its own short scopes
(``refresh_app_installations``) or holds no session at all (an operation's
execute phase, ``checked_app_installations``).

Nobody waits long on it. Every installation due is asked at once (a few at a
time), and the whole ask has one short budget (``ASK_BUDGET``): what GitHub
has not answered by then stays as it was -- a day-old answer is still
shown, an installation never checked stays unchecked -- and is asked again on
the next read. Asked one after another with a 15-second timeout each, fifty
installations could hold the Connections page for minutes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.connectors.domain.app_installation import AppInstallation
from app.modules.connectors.infrastructure.models.connector_installation import (
    ConnectorInstallation,
)
from app.modules.connectors.services.auth.github_app import (
    GitHubAppUnavailable,
    app_jwt,
)
from app.modules.connectors.services.auth.github_bindings import GITHUB

logger = get_logger(__name__)

_GITHUB_API = "https://api.github.com"
#: One question to GitHub about one installation. A small read: GitHub
#: answers it in well under a second when it is well.
_TIMEOUT_SECONDS = 4.0
#: How long one read waits for GitHub across every installation it asks
#: about. A page waits at most this long; an operation, its own budget.
ASK_BUDGET = timedelta(seconds=3)
#: How many installations are asked about at once.
_ASK_AT_ONCE = 8
#: Installations one organization is read for: the same bound as
#: ``github_bindings.bound_installations``.
_MAX_INSTALLATIONS = 50
#: How old GitHub's answer about an installation may be before it is asked
#: again. A login renamed or a repository selection changed shows within a day.
STALE_AFTER = timedelta(days=1)
#: Where in a binding's ``details`` the installation's GitHub account is kept.
_ACCOUNT_KEY = "account"

#: Asks GitHub about one installation; ``None`` when it would not say.
InstallationFetcher = Callable[[str], Awaitable[AppInstallation | None]]


def _from_details(installation_id: str, details: object) -> AppInstallation:
    account = details.get(_ACCOUNT_KEY) if isinstance(details, dict) else None
    if not isinstance(account, dict):
        return AppInstallation(installation_id=installation_id)

    def text(key: str) -> str | None:
        value = account.get(key)
        return str(value) if value else None

    return AppInstallation(
        installation_id=installation_id,
        account_login=text("login"),
        account_type=text("type"),
        repository_selection=text("repository_selection"),
        checked=bool(account.get("checked_at")),
    )


def _checked_at(details: object) -> datetime | None:
    account = details.get(_ACCOUNT_KEY) if isinstance(details, dict) else None
    raw = account.get("checked_at") if isinstance(account, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


async def bound_app_installations(
    session: AsyncSession, organization_id: UUID
) -> tuple[AppInstallation, ...]:
    """The organization's proven bindings, with what GitHub said of each.

    Unproven bindings are left out: a binding nobody proved may be one that
    was squatted on before bindings were checked, and the bot acting on it
    would act on somebody else's repositories.
    """
    rows = await session.execute(
        select(ConnectorInstallation.external_id, ConnectorInstallation.details)
        .where(
            ConnectorInstallation.connector_id == GITHUB,
            ConnectorInstallation.organization_id == organization_id,
            ConnectorInstallation.proven_at.is_not(None),
        )
        .order_by(ConnectorInstallation.created_at, ConnectorInstallation.id)
        .limit(_MAX_INSTALLATIONS)
    )
    return tuple(
        _from_details(str(external_id), details) for external_id, details in rows.all()
    )


async def installations_to_check(
    session: AsyncSession, organization_id: UUID, *, now: datetime | None = None
) -> tuple[str, ...]:
    """Proven bindings GitHub has not been asked about, or not for a day."""
    cutoff = (now or datetime.now(UTC)) - STALE_AFTER
    rows = await session.execute(
        select(ConnectorInstallation.external_id, ConnectorInstallation.details)
        .where(
            ConnectorInstallation.connector_id == GITHUB,
            ConnectorInstallation.organization_id == organization_id,
            ConnectorInstallation.proven_at.is_not(None),
        )
        .order_by(ConnectorInstallation.created_at, ConnectorInstallation.id)
        .limit(_MAX_INSTALLATIONS)
    )
    due: list[str] = []
    for external_id, details in rows.all():
        checked_at = _checked_at(details)
        if checked_at is None or checked_at < cutoff:
            due.append(str(external_id))
    return tuple(due)


async def record_app_installations(
    session: AsyncSession, found: Sequence[AppInstallation]
) -> None:
    """Keep what GitHub said of each installation on its binding."""
    stamp = datetime.now(UTC).isoformat()
    for installation in found:
        current = await session.scalar(
            select(ConnectorInstallation.details).where(
                ConnectorInstallation.connector_id == GITHUB,
                ConnectorInstallation.external_id == installation.installation_id,
            )
        )
        details = dict(current) if isinstance(current, dict) else {}
        details[_ACCOUNT_KEY] = {
            "login": installation.account_login,
            "type": installation.account_type,
            "repository_selection": installation.repository_selection,
            "checked_at": stamp,
        }
        await session.execute(
            update(ConnectorInstallation)
            .where(
                ConnectorInstallation.connector_id == GITHUB,
                ConnectorInstallation.external_id == installation.installation_id,
            )
            .values(details=details)
        )


async def fetch_app_installation(
    installation_id: str, *, client: httpx.AsyncClient | None = None
) -> AppInstallation | None:
    """GitHub's own account of one installation, asked as the App.

    ``None`` when it would not say -- no App key configured, GitHub refused,
    the installation is gone. The caller then knows no more than before, which
    is the safe direction: an installation not known to be on an organization
    is not one the bot acts on where it has to decide without asking.
    """
    try:
        token = app_jwt()
    except GitHubAppUnavailable:
        return None
    try:
        if client is not None:
            response = await _ask(client, installation_id, token)
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as own:
                response = await _ask(own, installation_id, token)
        payload = response.json() if response.status_code == 200 else None
    except httpx.HTTPError, ValueError:
        logger.warning(
            "connectors.github_app_installations.lookup_failed.degraded",
            exc_info=True,
        )
        return None
    if not isinstance(payload, dict):
        logger.warning(
            "connectors.github_app_installations.lookup_refused.degraded",
            upstream_status=response.status_code,
        )
        return None
    return _from_payload(str(installation_id), payload)


async def _ask(
    client: httpx.AsyncClient, installation_id: str, token: str
) -> httpx.Response:
    return await client.get(
        f"{_GITHUB_API}/app/installations/{installation_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _from_payload(installation_id: str, payload: dict[str, object]) -> AppInstallation:
    """GitHub's installation JSON, narrowed to the three fields kept."""
    account = payload.get("account")
    account = account if isinstance(account, dict) else {}
    login, kind = account.get("login"), account.get("type")
    selection = payload.get("repository_selection")
    return AppInstallation(
        installation_id=installation_id,
        account_login=str(login) if login else None,
        account_type=str(kind) if kind else None,
        repository_selection=str(selection) if selection else None,
        checked=True,
    )


async def ask_github(
    installation_ids: Sequence[str],
    *,
    fetch: InstallationFetcher = fetch_app_installation,
    budget: timedelta = ASK_BUDGET,
) -> dict[str, AppInstallation]:
    """What GitHub said of each installation within ``budget``, by id.

    Asked together, ``_ASK_AT_ONCE`` at a time. One that fails, or has not
    answered when the budget runs out, is simply absent: the caller keeps
    what it knew.
    """
    if not installation_ids:
        return {}
    gate = asyncio.Semaphore(_ASK_AT_ONCE)
    answers: dict[str, AppInstallation] = {}

    async def one(installation_id: str) -> None:
        async with gate:
            found = await fetch(installation_id)
        if found is not None:
            answers[installation_id] = found

    tasks = [asyncio.create_task(one(i)) for i in dict.fromkeys(installation_ids)]
    _done, late = await asyncio.wait(tasks, timeout=budget.total_seconds())
    for task in late:
        task.cancel()
    if late:
        logger.info(
            "connectors.github_app_installations.ask_over_budget.degraded",
            unanswered=len(late),
            asked=len(tasks),
        )
    # Collected so a failure inside one is never "never retrieved".
    await asyncio.gather(*tasks, return_exceptions=True)
    return answers


async def checked_app_installations(
    candidates: tuple[AppInstallation, ...],
    *,
    fetch: InstallationFetcher = fetch_app_installation,
    budget: timedelta = ASK_BUDGET,
) -> tuple[AppInstallation, ...]:
    """``candidates`` with every unchecked one asked about now, no session held.

    For an operation's execute phase. What it learns is not written back --
    that phase holds no connection -- so an installation stays unchecked on its
    binding until the Connections page refreshes it.
    """
    answers = await ask_github(
        [one.installation_id for one in candidates if not one.checked],
        fetch=fetch,
        budget=budget,
    )
    return tuple(answers.get(one.installation_id, one) for one in candidates)


async def refresh_app_installations(
    uow_factory: UnitOfWorkFactory,
    organization_id: UUID,
    *,
    fetch: InstallationFetcher = fetch_app_installation,
) -> None:
    """Ask GitHub about the organization's unchecked or day-old bindings.

    Three steps, so no connection is held while GitHub answers: read what is
    due, ask (all at once, within ``ASK_BUDGET``), write what came back.
    """
    async with uow_factory() as uow:
        due = await installations_to_check(uow.session, organization_id)
    if not due:
        return
    found = list((await ask_github(due, fetch=fetch)).values())
    if not found:
        return
    async with uow_factory() as uow:
        await record_app_installations(uow.session, found)


__all__ = [
    "ASK_BUDGET",
    "InstallationFetcher",
    "ask_github",
    "STALE_AFTER",
    "bound_app_installations",
    "checked_app_installations",
    "fetch_app_installation",
    "installations_to_check",
    "record_app_installations",
    "refresh_app_installations",
]
