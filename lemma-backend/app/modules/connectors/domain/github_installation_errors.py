"""The refusals a GitHub installation gives.

To an organization it does not belong to, and to a repository it does not
cover -- the second asked whenever a person names one to work in.
"""

from __future__ import annotations

from app.modules.connectors.domain.errors import (
    ConnectorConflictError,
    ConnectorDomainError,
)


class GithubInstallationBoundElsewhereError(ConnectorConflictError):
    """Another Lemma organization already holds this installation (audit H4).

    Deliberately says nothing about which one: the installation id is a bare
    number, and confirming whose it is would answer a question the caller has
    no business asking.
    """

    def __init__(self) -> None:
        super().__init__(
            "This GitHub App installation is connected to a different Lemma "
            "organization. Install the Lemma app on a GitHub account or "
            "organization for this one, then connect again."
        )
        self.code = "GITHUB_INSTALLATION_BOUND_ELSEWHERE"


class GithubInstallationNotAdministeredError(ConnectorDomainError):
    """Nobody has bound this installation yet, and the claimant cannot.

    Binding hands the claimant's organization the App's token on every
    repository in the installation, so only its owner on GitHub's side may do
    it: the GitHub user it is installed on, or an admin of the GitHub
    organization it is installed on (audit H4). Seeing a repository in it is
    not enough.
    """

    def __init__(self) -> None:
        super().__init__(
            message=(
                "Only the GitHub account this app is installed on, or an admin "
                "of that GitHub organization, can connect this installation to "
                "Lemma. Ask them to connect it first; after that, anyone in "
                "this organization can use it."
            ),
            code="GITHUB_INSTALLATION_NOT_ADMINISTERED",
            status_code=403,
        )


class InstallationClaimNotPermittedError(GithubInstallationNotAdministeredError):
    """The claimant controls the installation on GitHub, but not in Lemma.

    Binding an installation is installing an app for the whole organization,
    so it takes ``org.installation.manage`` as well -- the same rule as
    binding Lemma's Slack app to a workspace. A subclass, so every caller that
    leaves an unproven installation for an admin does the same with this one.
    """

    def __init__(self) -> None:
        ConnectorDomainError.__init__(
            self,
            message=(
                "Connecting this GitHub installation installs the app for your "
                "whole Lemma organization, which only someone who manages its "
                "connectors can do. Ask one of them to connect it first; after "
                "that, anyone in this organization can use it."
            ),
            code="INSTALLATION_CLAIM_NOT_PERMITTED",
            status_code=403,
        )


class GithubInstallationRequiredError(ConnectorDomainError):
    """The account is connected, and no installation is bound to it yet.

    A GitHub App's user token reaches only what the App is installed on, so
    until one is bound there is no repository to name -- the connect flow's
    ``INSTALL_REQUIRED``, met somewhere other than the connectors page.
    """

    def __init__(self) -> None:
        super().__init__(
            message=(
                "Your GitHub account is connected, but the Lemma app is not "
                "installed on anything it can reach yet. Install it from the "
                "connectors page, then try again."
            ),
            code="GITHUB_INSTALLATION_REQUIRED",
            status_code=409,
        )


class GithubInstallationUnreachableError(ConnectorDomainError):
    """The bound installation no longer answers to this account's token.

    The app was uninstalled, or the person lost access to the account it is
    installed on, or GitHub stopped honouring the token. Each is fixed on the
    GitHub side or by reconnecting, never by choosing another repository.
    """

    def __init__(self) -> None:
        super().__init__(
            message=(
                "Your GitHub account can no longer reach this organization's "
                "GitHub installation. Reconnect GitHub, or ask whoever manages "
                "the installation to reinstall the Lemma app."
            ),
            code="GITHUB_INSTALLATION_UNREACHABLE",
            status_code=409,
        )


class GithubRepositoryNotFoundError(ConnectorDomainError):
    """GitHub has no such repository that this account can see.

    Said as one answer on purpose: GitHub itself answers 404 both for a
    repository that does not exist and for a private one the token cannot
    see, so telling them apart here would be a guess.
    """

    def __init__(self, full_name: str) -> None:
        super().__init__(
            message=(
                f"GitHub has no repository '{full_name}' that your GitHub "
                "account can reach. Check the name, and that you have access "
                "to it."
            ),
            code="GITHUB_REPOSITORY_NOT_FOUND",
            status_code=422,
            details={"repository": full_name},
        )


class GithubRepositoryNotInstalledError(ConnectorDomainError):
    """The repository exists, and the organization's installation does not cover it.

    Two shapes, one remedy each: the installation is on another GitHub account
    altogether, or it is on the right one and this repository is not among
    those it was given. ``manage_url`` is where the second is fixed -- there is
    no API to add a repository to an installation with.
    """

    def __init__(
        self, full_name: str, *, installed_on: str | None, manage_url: str
    ) -> None:
        owner = full_name.partition("/")[0]
        if installed_on and installed_on.lower() != owner.lower():
            message = (
                "This organization's GitHub installation is on "
                f"'{installed_on}', so it does not cover '{full_name}'."
            )
        else:
            message = (
                f"'{full_name}' is not one of the repositories this "
                "organization's GitHub installation covers. Add it to the "
                f"installation at {manage_url}"
            )
        super().__init__(
            message=message,
            code="GITHUB_REPOSITORY_NOT_INSTALLED",
            status_code=422,
            details={
                "repository": full_name,
                "installed_on": installed_on,
                "manage_url": manage_url,
            },
        )


class GithubRepositoryUncheckedError(ConnectorDomainError):
    """A public repository not found before an installation's selection ran out.

    Not "not installed": the walk stopped before GitHub's list did, so the
    answer is unknown, and adding the repository to the installation -- the
    remedy that refusal names -- may already be done. An installation on every
    repository of the account is decided without the walk.
    """

    def __init__(self, full_name: str, *, installed_on: str | None) -> None:
        account = installed_on or full_name.partition("/")[0]
        super().__init__(
            message=(
                f"'{full_name}' could not be confirmed: this organization's "
                "GitHub installation is on more selected repositories than one "
                "check reads. Installing the app on every repository of "
                f"'{account}' lets it be checked."
            ),
            code="GITHUB_REPOSITORY_UNCHECKED",
            status_code=422,
            details={"repository": full_name, "installed_on": installed_on},
        )


class GithubUnavailableError(ConnectorDomainError):
    """GitHub did not give an answer, so nothing was decided.

    Refused rather than waved through: the check exists so that what a person
    picks is something the sandbox can actually clone.
    """

    def __init__(self) -> None:
        super().__init__(
            message=(
                "GitHub did not answer, so the repository could not be checked. "
                "Try again in a moment."
            ),
            code="GITHUB_UNAVAILABLE",
            status_code=503,
        )
