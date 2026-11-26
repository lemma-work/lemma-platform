"""The catalog's one deliberate off-bus analytics emit.

Every other product-analytics event is projected from a domain event on the
transactional outbox, so an event that fires is an event that committed. This
one cannot be, and the reason is `ConnectorOperationService.execute_resolved`'s
own docstring: that method holds **no** DB connection on purpose, because the
external call it wraps can run for tens of seconds. Staging a domain event there
would put a pooled checkout back around exactly that call -- the hazard
``docs/design/db-connection-scope.md`` and the connection-scope work exist to
eliminate.

An in-process ``emit`` needs no transaction: it appends to a bounded buffer and
returns. The trade is that a connector call which succeeds and then crashes the
process before the flush is unreported. For a volume metric that is the right
trade; for anything billed it would not be, which is why nothing here is.

``identity`` is what the call actually went out as -- the App, or an account's
own credential -- as the credential presenter noted it while presenting
(``app_identity.note_presented``), not what the caller asked for. The actor
stays the person who asked, whichever it was.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from app.core.analytics import AnalyticsActor, emit
from app.core.authorization.context import ActorType
from app.modules.connectors.domain.app_identity import (
    PresentedIdentity,
    presented_identity_observed,
)
from app.modules.connectors.domain.execution_plan import ResolvedConnectorExecution


def _identity(
    resolved: ResolvedConnectorExecution, presented: list[PresentedIdentity]
) -> PresentedIdentity:
    if presented:
        return presented[-1]
    # Nothing was presented: refused, or failed, before the credential was.
    # An APP connector has nothing but the App to be.
    return "app" if resolved.acts_as_app_connector else "user"


@contextmanager
def operation_execution_recorded(
    resolved: ResolvedConnectorExecution,
) -> Iterator[None]:
    """Record one connector operation, whichever way it ends."""
    status = "succeeded"
    presented: list[PresentedIdentity] = []
    try:
        with presented_identity_observed() as presented:
            yield
    except Exception:
        status = "failed"
        raise
    finally:
        identity = _identity(resolved, presented)
        emit(
            "connector.operation_executed",
            actor=(
                AnalyticsActor.user(resolved.acting_user_id)
                if resolved.acting_user_id
                # Only when the plan genuinely has no person behind it. Every
                # request-driven execution does, and attributing those to the
                # machine actor made "who is using connectors?" unanswerable.
                else AnalyticsActor.autonomous(ActorType.SYSTEM)
            ),
            organization_id=resolved.organization_id,
            properties={
                "connector_id": resolved.connector_id,
                "provider": resolved.provider,
                "direction": "outbound",
                "status": status,
                "identity": identity,
                "pod_connector": resolved.pod_connector,
                "installation_id": (
                    resolved.account_external_ref if identity == "app" else None
                ),
            },
        )
