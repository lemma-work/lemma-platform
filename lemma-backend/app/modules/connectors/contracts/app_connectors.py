"""APP pod connectors, for the one other module that can act as the App.

An APP connector runs as the organization's GitHub App and has no account
behind it, so ``resolve_pod_connector`` and ``resolve_standing_pod_connector``
refuse it (``APP_CONNECTOR_UNSUPPORTED``). Schedules can listen as the App --
GitHub routes the App's webhooks by installation, with nobody needed -- and
ask here instead: the same resolution, handing back a ``PodConnectorApp`` for
an APP connector and the usual ``PodConnectorAccount`` for any other.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.contracts.pod_connectors import (
    PodConnectorAccount,
    _present,
    _resolver,
)
from app.modules.connectors.domain.app_installation import (
    AppInstallation,
    pick_installation,
    requested_owner,
)
from app.modules.connectors.domain.pod_connector import PodConnectorMode
from app.modules.connectors.domain.pod_connector_errors import (
    AppConnectorUnsupportedError,
    AppInstallationAmbiguousError,
    AppNotInstalledError,
)
from app.modules.connectors.services.pod_connector_resolution import (
    ResolvedAppConnector,
    ResolvedPodConnector,
)


@dataclass(frozen=True, slots=True)
class PodConnectorApp:
    """An APP pod connector, resolved: it acts as the organization's GitHub App.

    No account and no owner. ``installations`` are the organization's proven
    App installations; which one a binding acts on is picked by the GitHub
    account it is about (``pick_app_installation``).
    """

    pod_id: UUID
    slug: str
    auth_config_id: UUID
    connector_id: str
    organization_id: UUID
    installations: tuple[AppInstallation, ...]

    @property
    def mode(self) -> PodConnectorMode:
        return PodConnectorMode.APP

    @property
    def pinned_pod_account_id(self) -> UUID | None:
        """Nothing to pin: there is no account."""
        return None


def pick_app_installation(app: PodConnectorApp, *, owner: str | None) -> str:
    """The installation id a binding about ``owner`` acts on.

    Only installations GitHub has confirmed are on a GitHub organization: a
    binding that routes deliveries decides without asking GitHub, and must
    not settle on one that may be a person's own. Refuses with
    ``APP_NOT_INSTALLED`` or ``APP_INSTALLATION_AMBIGUOUS``.
    """
    from app.modules.connectors.services.auth.github_installation import (
        install_url,
    )

    return pick_installation(
        app.installations, owner=owner, slug=app.slug, install_url=install_url()
    ).installation_id


def _present_app(resolved: ResolvedAppConnector) -> PodConnectorApp:
    return PodConnectorApp(
        pod_id=resolved.pod_connector.pod_id,
        slug=resolved.pod_connector.slug,
        auth_config_id=resolved.auth_config.id,
        connector_id=resolved.auth_config.connector_id,
        organization_id=resolved.auth_config.organization_id,
        installations=resolved.app_installations,
    )


def _present_any(
    resolved: ResolvedPodConnector | ResolvedAppConnector,
) -> PodConnectorAccount | PodConnectorApp:
    if isinstance(resolved, ResolvedAppConnector):
        return _present_app(resolved)
    return _present(resolved)


async def resolve_pod_connector_or_app(
    uow: SqlAlchemyUnitOfWork,
    ctx: Context,
    *,
    pod_id: UUID,
    slug: str,
    pod_account_id: UUID | None = None,
) -> PodConnectorAccount | PodConnectorApp:
    """``resolve_pod_connector``, or the App for an APP connector.

    For a caller that can act as the App (a schedule): ``connector.use`` on the
    connector is asked either way; an APP connector asks nothing more.
    """
    resolver = _resolver(uow)
    connector = await resolver.connector(pod_id, slug)
    return _present_any(
        await resolver.resolve_any(ctx, connector, pod_account_id=pod_account_id)
    )


async def resolve_standing_pod_connector_or_app(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    slug: str,
    on_behalf_of: UUID | None = None,
    pod_account_id: UUID | None = None,
    record_use: bool = True,
) -> PodConnectorAccount | PodConnectorApp:
    """``resolve_standing_pod_connector``, or the App for an APP connector,
    which needs nobody behind it: a schedule on one outlives its owner."""
    resolver = _resolver(uow)
    connector = await resolver.connector(pod_id, slug)
    return _present_any(
        await resolver.resolve_standing_any(
            connector,
            on_behalf_of=on_behalf_of,
            pod_account_id=pod_account_id,
            record_use=record_use,
        )
    )


__all__ = [
    "AppConnectorUnsupportedError",
    "AppInstallationAmbiguousError",
    "AppNotInstalledError",
    "PodConnectorApp",
    "pick_app_installation",
    "resolve_pod_connector_or_app",
    "requested_owner",
    "resolve_standing_pod_connector_or_app",
]
