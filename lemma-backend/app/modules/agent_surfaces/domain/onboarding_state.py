from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID
from pydantic import BaseModel, ConfigDict, JsonValue

from app.modules.agent_surfaces.domain.ingress_context import AgentSurfaceContext


@dataclass(frozen=True, slots=True)
class OnboardingIngressResult:
    """Whether onboarding answered a delivery, and what it left to run.

    Lives here rather than beside the coordinator because the worker's webhook
    subscriber names it in its own signature, and FastStream resolves those
    annotations at runtime -- a forward reference to a module the worker
    deliberately does not import is not a reference it can resolve.
    """

    handled: bool
    context: AgentSurfaceContext | None = None


class OnboardingStep(StrEnum):
    """Where a pending signup has got to.

    Spelled once because five modules read and write it and the column is a
    plain string: a typo in any of them is not an error, it is a state the
    dispatcher silently has no branch for. Stored as the member's value, so an
    older process reading a newer row still sees the string it always did.
    """

    #: A private destination has not been opened yet. Set again when opening
    #: one fails, so the next message retries it rather than accepting input.
    HANDOFF = "handoff"
    AWAITING_PHONE = "awaiting_phone"
    AWAITING_EMAIL = "awaiting_email"
    AWAITING_CODE = "awaiting_code"
    #: The mailbox (or the shared contact) is proven and an account is resolved.
    VERIFIED = "verified"
    #: Recognised, but with nowhere to talk: the person already has an account
    #: and no personal route, so they have been offered their pods to attach
    #: this conversation to, or the chance to name a new one.
    AWAITING_POD = "awaiting_pod"
    #: Verified, but the installation's organization has not admitted them.
    ORGANIZATION_ACCESS_REQUIRED = "organization_access_required"
    #: A workspace exists and the original request is ready to be replayed.
    READY = "ready"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PendingState(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    binding_key: str
    platform: str
    step: str
    challenge_id: UUID | None
    user_id: UUID | None
    installation_surface_id: UUID | None
    verified_phone: str | None
    destination: dict[str, JsonValue]
    original_event: dict[str, JsonValue] | None
    #: The pods offered in AWAITING_POD, in the order they were listed. Stored
    #: rather than re-derived when the reply lands, because "3" has to mean the
    #: third pod *they were shown*: re-running the query answers a list that a
    #: pod created or deleted in between has already shifted, and the cost of
    #: being wrong is a conversation wired to somebody else's pod.
    offered_pods: list[dict[str, JsonValue]] | None
    expires_at: datetime
    ready_at: datetime | None
    handed_off_at: datetime | None
