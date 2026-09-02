"""Deciding which credential an operation is actually run with.

Resolving a credential and *presenting* one are different questions, and until
now only the first had a home. The resolve phase answers "whose account is
this", reads the row and hands over what it stored. Presentation answers "and
which of that account's identities should this particular call use" -- which for
most connectors is a question with one answer, and for a GitHub App is not.

It runs at the dispatcher, immediately before the executor, for two reasons.
It is the one place every kind passes through, so a new kind cannot forget it.
And it is inside the execute phase, which holds no pooled database connection --
minting a GitHub installation token is a call to GitHub, and doing it during
resolve would hold a connection across that call.

Identity is the default and stays the default: a connector with nothing to
decide has no presenter and its credentials pass through untouched.
"""

from __future__ import annotations

from typing import Protocol

from app.core.log.log import get_logger
from app.modules.connectors.domain.kinds import ExecutionRequest

logger = get_logger(__name__)


class CredentialPresenter(Protocol):
    """Turns the stored credential into the one this call should carry."""

    async def present(self, request: ExecutionRequest) -> dict[str, object]: ...


class PassThroughPresenter:
    """What every connector gets unless it says otherwise."""

    async def present(self, request: ExecutionRequest) -> dict[str, object]:
        return request.credentials


class PresenterRegistry:
    """Presenters by connector id, with pass-through as the floor."""

    def __init__(self, presenters: dict[str, CredentialPresenter] | None = None):
        self._presenters = presenters or {}
        self._default = PassThroughPresenter()

    def for_connector(self, connector_id: str) -> CredentialPresenter:
        return self._presenters.get(connector_id, self._default)
