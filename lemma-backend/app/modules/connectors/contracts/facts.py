"""What the connectors module announces to other modules.

``ConnectorAccountConnected`` is emitted in the transaction that stores an
OAuth grant: a new account, or an existing one granted again (``renewed``). A
module that finishes something a connect request started -- a teammate's Slack
app waiting for its Allow -- subscribes a work kind to it rather than reading
the analytics event, which is a projection and not a contract.

A submodule rather than ``contracts/__init__``: it pulls in the work runtime.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import Fact


class ConnectorAccountConnected(Fact):
    account_id: UUID
    auth_config_id: UUID
    connector_id: str
    organization_id: UUID
    #: Whose account it is: the person who started the connect request.
    user_id: UUID
    #: What the connect request was started for, when its starter said
    #: (``start_authorization(purpose=...)``). ``None`` for an ordinary connect.
    purpose: str | None = None
    #: The browser that came back from the provider carried the session of the
    #: person who started the request. Without it the grant may be somebody
    #: else's consent completed on a link they were sent, so anything that
    #: treats the grant as proof of who the provider's user is requires it.
    browser_bound: bool = False
    #: An account that already existed was granted again.
    renewed: bool = False

    def work_namespace(self) -> str:
        return f"org:{self.organization_id}"


__all__ = ["ConnectorAccountConnected"]
