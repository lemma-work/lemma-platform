"""GitHub-specific values other modules legitimately need.

A submodule rather than names on `contracts/__init__`: `install_url` reaches the
App auth service, and every importer of any other contract would pay for that
import. The budget gate says so out loud.

`installation_repository` and `installation_repositories` answer which
repositories an organization's installations cover for one person, asked with
that person's token; they raise the refusals exported beside them.
`bound_installations` names those installations.
"""

from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.github_installation_errors import (
    GithubInstallationRequiredError,
    GithubInstallationUnreachableError,
    GithubRepositoryNotFoundError,
    GithubRepositoryNotInstalledError,
    GithubRepositoryUncheckedError,
    GithubUnavailableError,
)
from app.modules.connectors.services.auth.github_bindings import bound_installations
from app.modules.connectors.services.auth.github_installation import (
    InstallState,
    install_url as github_install_url,
    installation_still_needed,
    manage_installation_url,
)
from app.modules.connectors.services.auth.github_repositories import (
    InstallationRepository,
    RepositoryListing,
    installation_repositories,
    installation_repository,
)


def install_state_for(connector_id: str, external_ref: str | None) -> InstallState:
    """How far an account is from reaching anything, without asking GitHub.

    The cheap half of the question, decided from the column alone, so listing
    accounts costs no network. It cannot tell `INSTALL_REQUIRED` apart from "you
    have several installations, pick one" -- that needs a call with the
    account's own token, which is what the installations endpoint is for.
    """
    return (
        InstallState.INSTALL_REQUIRED
        if installation_still_needed(connector_id, external_ref)
        else InstallState.READY
    )


__all__ = [
    "GithubInstallationRequiredError",
    "GithubInstallationUnreachableError",
    "GithubRepositoryNotFoundError",
    "GithubRepositoryNotInstalledError",
    "GithubRepositoryUncheckedError",
    "GithubUnavailableError",
    "InstallState",
    "InstallationRepository",
    "RepositoryListing",
    "bound_installations",
    "connector_settings",
    "github_install_url",
    "install_state_for",
    "installation_repositories",
    "installation_repository",
    "installation_still_needed",
    "manage_installation_url",
]
