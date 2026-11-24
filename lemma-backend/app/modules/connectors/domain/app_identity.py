"""When a function may present as the connector's app, and why it is refused.

A function's connector calls act as the person it runs for, so what it posts
carries their name. Some work is the Lemma's, not the person's: a check run
(GitHub takes those only from an App), a review a team wants from its bot, an
approval withdrawn on a new push by nobody in particular. An agent already
acts as the app on every call that allows it; a function asks for it by name
(``act_as="app"``) on the pod route.

Asking is not the same as being an agent, so two things differ:

- **Only a function run may ask.** A person in the browser, an app's page, or
  the CLI acting as someone is refused. An app's script is its author's code,
  and the person pressing the button should post as themselves.
- **It is the app or nothing** (``app_only``). An agent's call falls back to
  the person when the app cannot speak, so a schedule keeps working; a
  function that asked for the bot and got the person would post under a name
  nobody chose. So the call is refused instead, before anything is sent.
"""

from __future__ import annotations

from app.core.authorization.actor import WorkloadKind
from app.core.authorization.context import Context
from app.modules.connectors.domain.errors import ConnectorDomainError


class AppIdentityRefusedError(ConnectorDomainError):
    """The caller may not present as the app."""

    def __init__(self) -> None:
        super().__init__(
            message=(
                "Only a function run may act as the connector's app. "
                "Call without act_as to act as yourself."
            ),
            code="APP_IDENTITY_FUNCTIONS_ONLY",
            status_code=403,
        )


class AppIdentityUnavailableError(ConnectorDomainError):
    """The app cannot make this call, and acting as the person was not asked."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            message=f"This call cannot act as the connector's app: {reason}",
            code="APP_IDENTITY_UNAVAILABLE",
            status_code=409,
        )


def require_function_run(ctx: Context) -> None:
    """Refuse ``act_as="app"`` from anyone but a function run."""
    workload = ctx.workload
    if workload is None or workload.kind is not WorkloadKind.FUNCTION:
        raise AppIdentityRefusedError()


__all__ = [
    "AppIdentityRefusedError",
    "AppIdentityUnavailableError",
    "require_function_run",
]
