"""The typed answers a pod connector gives when it cannot run.

Every surface -- chat, an app, the CLI, an SDK -- gets the same code and the
same fields, so each can render the same remedy: a connect link, a reconnect
link, or "ask an editor to share an account". The bare 400
``ACCOUNT_RESOLUTION_ERROR`` these replace named none of that, so nothing
downstream could act on it.
"""

from __future__ import annotations

from app.core.domain.errors import DomainError
from app.modules.connectors.domain.errors import (
    ConnectorConflictError,
    ConnectorDomainError,
    ConnectorValidationError,
    _ConnectorNotFoundBase,
)


class PodConnectorNotFoundError(_ConnectorNotFoundBase):
    def __init__(self, slug: str):
        super().__init__(f"This pod has no connector named '{slug}'.")
        self.code = "POD_CONNECTOR_NOT_FOUND"


class PodConnectorConflictError(ConnectorConflictError):
    def __init__(self, slug: str):
        super().__init__(f"This pod already has a connector named '{slug}'.")
        self.code = "POD_CONNECTOR_CONFLICT"


class PodAccountNotFoundError(_ConnectorNotFoundBase):
    def __init__(self) -> None:
        super().__init__("That account is not in use in this pod.")
        self.code = "POD_ACCOUNT_NOT_FOUND"


class PodConnectorValidationError(ConnectorValidationError):
    def __init__(self, message: str, *, reason: str):
        super().__init__(message, details={"reason": reason})
        self.code = "POD_CONNECTOR_INVALID"


def _remedy(*, slug: str, connector_id: str | None, url: str | None) -> dict[str, str]:
    details = {"pod_connector": slug}
    if connector_id:
        details["connector_id"] = connector_id
    if url:
        details["connect_url"] = url
    return details


class ConnectionRequiredError(ConnectorDomainError):
    """The acting person has no account for a per-member connector."""

    def __init__(self, *, slug: str, connector_id: str, connect_url: str):
        super().__init__(
            message=(
                f"Connect your {connector_id} account to use '{slug}' in this pod."
            ),
            code="CONNECTION_REQUIRED",
            status_code=409,
            details=_remedy(slug=slug, connector_id=connector_id, url=connect_url),
        )


class ConnectionRequiresPersonError(ConnectorDomainError):
    """A per-member connector, asked by somebody who is not a member person.

    A contact, an anonymous visitor, another pod, or a run with nobody behind
    it has no account of its own to use -- only a SHARED connector serves them.
    """

    def __init__(self, *, slug: str):
        super().__init__(
            message=(
                f"'{slug}' uses each member's own account, and this call has no "
                "member behind it. Only a shared connector can serve it."
            ),
            code="CONNECTION_REQUIRES_PERSON",
            status_code=403,
            details={"pod_connector": slug},
        )


class ReauthRequiredError(ConnectorDomainError):
    """The account a connector resolved to needs its owner to sign in again."""

    def __init__(
        self,
        *,
        slug: str,
        connector_id: str,
        connect_url: str,
        acting_person_is_owner: bool,
    ):
        message = (
            f"Sign-in for your {connector_id} account has expired. Reconnect it "
            f"to keep using '{slug}'."
            if acting_person_is_owner
            else f"The shared {connector_id} account behind '{slug}' needs its "
            "owner to reconnect it."
        )
        super().__init__(
            message=message,
            code="REAUTH_REQUIRED",
            status_code=409,
            details=_remedy(
                slug=slug,
                connector_id=connector_id,
                # Only the owner can reconnect; a link is no use to anyone else.
                url=connect_url if acting_person_is_owner else None,
            ),
        )


class ConnectorNeedsAccountError(ConnectorDomainError):
    """A SHARED connector with no account its owner has shared."""

    def __init__(self, *, slug: str, connect_url: str):
        super().__init__(
            message=(
                f"'{slug}' runs as one shared account, and none is shared with "
                "this pod right now. An account's owner has to share it, and a "
                "pod editor point the connector at it."
            ),
            code="CONNECTOR_NEEDS_ACCOUNT",
            status_code=409,
            # Both halves of the fix happen on the pod's connections page, so
            # the link is of use to whoever hits this, not only an owner.
            details=_remedy(slug=slug, connector_id=None, url=connect_url),
        )


class ConnectorUseRefusedError(ConnectorDomainError):
    """The actor may not run operations through this pod connector.

    The authorization refusal, kept as it was -- its code, status and
    ``permission_ids``, which approvals key on -- with the connector it was
    about added, so a named agent's refusal can become a card offering it the
    grant: which connector (``pod_connector``, ``pod_connector_id``), what it
    is (``connector_id``) and who asked (``workload_id``, ``workload_kind``).
    """

    def __init__(
        self,
        refusal: DomainError,
        *,
        slug: str,
        pod_connector_id: str,
        connector_id: str | None,
        workload_id: str | None,
        workload_kind: str | None,
    ):
        details = dict(refusal.details) if isinstance(refusal.details, dict) else {}
        details.update(
            {
                key: value
                for key, value in (
                    ("pod_connector", slug),
                    ("pod_connector_id", pod_connector_id),
                    ("connector_id", connector_id),
                    ("workload_id", workload_id),
                    ("workload_kind", workload_kind),
                )
                if value is not None
            }
        )
        super().__init__(
            message=refusal.message,
            code=refusal.code or "CONNECTOR_ACCESS_DENIED",
            status_code=refusal.status_code,
            details=details,
        )


class ConnectorUnavailableError(ConnectorDomainError):
    """The installation behind a pod connector is disabled."""

    def __init__(self, *, slug: str):
        super().__init__(
            message=(
                f"The installation behind '{slug}' is disabled in this "
                "organization. An organization admin can re-enable it."
            ),
            code="CONNECTOR_UNAVAILABLE",
            status_code=409,
            details={"pod_connector": slug},
        )


__all__ = [
    "ConnectionRequiredError",
    "ConnectionRequiresPersonError",
    "ConnectorNeedsAccountError",
    "ConnectorUnavailableError",
    "ConnectorUseRefusedError",
    "PodAccountNotFoundError",
    "PodConnectorConflictError",
    "PodConnectorNotFoundError",
    "PodConnectorValidationError",
    "ReauthRequiredError",
]
