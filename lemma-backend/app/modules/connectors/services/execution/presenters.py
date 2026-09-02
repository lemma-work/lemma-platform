"""Which connectors have something to decide about their credential.

One place, so the answer to "does this connector present differently" is a
lookup rather than a grep. Everything absent from here passes its stored
credential through untouched, which is every connector but one.
"""

from __future__ import annotations

from app.modules.connectors.services.execution.credential_presenter import (
    PresenterRegistry,
)
from app.modules.connectors.services.execution.github_presenter import (
    GitHubCredentialPresenter,
)

GITHUB_CONNECTOR_ID = "github"


def default_presenters() -> PresenterRegistry:
    return PresenterRegistry({GITHUB_CONNECTOR_ID: GitHubCredentialPresenter()})
