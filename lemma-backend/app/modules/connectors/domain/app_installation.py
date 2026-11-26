"""Which of the organization's GitHub App installations an APP call acts on.

An APP pod connector stores no installation. One organization can have the App
installed on several GitHub accounts -- its own GitHub organization and a
client's -- and a GitHub App finds the installation for a call from the
repository owner the call names, so the bot does the same, per call.

Only some bindings count. A binding to the pod's organization that nobody
proved (``proven_at`` NULL) may be one somebody squatted on before bindings
were checked; and an installation on a person's own GitHub user would hand
their private repositories to every editor of a shared Lemma. So a candidate is
proven, and on a GitHub **Organization**.

Whether it is on an organization is GitHub's to say (``GET
/app/installations/{id}``). It is kept on the binding once read; until it has
been read (``checked`` false) a binding can still be tried by a call, which
asks GitHub first, but nothing that must decide without asking -- a schedule's
routing key -- relies on it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.modules.connectors.domain.pod_connector_errors import (
    AppInstallationAmbiguousError,
    AppNotInstalledError,
)

#: GitHub's word for an installation on an organization rather than a user.
ORGANIZATION = "Organization"


@dataclass(frozen=True, slots=True)
class AppInstallation:
    """One installation of the GitHub App bound to the organization."""

    installation_id: str
    account_login: str | None = None
    account_type: str | None = None
    repository_selection: str | None = None
    #: GitHub was asked about it, and the three fields above are its answer.
    checked: bool = False

    @property
    def counts(self) -> bool:
        """Known to be on a GitHub organization: the bot may act on it."""
        return self.checked and self.account_type == ORGANIZATION

    @property
    def may_count(self) -> bool:
        """Counts, or has not been checked yet and so might."""
        return self.counts or not self.checked


def app_is_installed(installations: tuple[AppInstallation, ...]) -> bool:
    """Whether an APP connector over these can run: one is known to be on a
    GitHub organization. The same rule a call picks by, so a connector never
    reads "Working" while its calls refuse. Callers read the installations
    through ``checked_bound_app_installations``, so one GitHub was never asked
    about is asked first rather than guessed at."""
    return any(one.counts for one in installations)


#: Payload keys a GitHub operation names its repository owner by, in the
#: order they are believed: an explicit owner or organization first, then the
#: owner half of an ``owner/name`` repository.
_OWNER_KEYS = ("owner", "org")
_REPOSITORY_KEYS = ("repository", "full_name", "repo_full_name")


def requested_owner(payload: Mapping[str, object] | None) -> str | None:
    """The GitHub account a call is about, as its payload names it."""
    if not payload:
        return None
    for key in _OWNER_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in _REPOSITORY_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and "/" in value:
            owner = value.strip().split("/", 1)[0]
            if owner:
                return owner
    return None


def pick_installation(
    candidates: tuple[AppInstallation, ...],
    *,
    owner: str | None,
    slug: str,
    install_url: str | None,
) -> AppInstallation:
    """The installation a call about ``owner`` acts on, among those that count.

    No owner named: the only one there is, or a refusal listing the logins
    (``APP_INSTALLATION_AMBIGUOUS``) -- picking whichever came first would act
    on one organization's repositories for a call meant for another's. An
    owner no installation is on: ``APP_NOT_INSTALLED``, with where to install.
    """
    counting = tuple(one for one in candidates if one.counts)
    if owner is not None:
        wanted = owner.casefold()
        for one in counting:
            if (one.account_login or "").casefold() == wanted:
                return one
        raise AppNotInstalledError(slug=slug, owner=owner, install_url=install_url)
    if len(counting) == 1:
        return counting[0]
    if not counting:
        raise AppNotInstalledError(slug=slug, owner=None, install_url=install_url)
    raise AppInstallationAmbiguousError(
        slug=slug,
        logins=sorted(one.account_login or one.installation_id for one in counting),
    )


__all__ = [
    "AppInstallation",
    "ORGANIZATION",
    "app_is_installed",
    "pick_installation",
    "requested_owner",
]
