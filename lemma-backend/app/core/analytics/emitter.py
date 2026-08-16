"""The export boundary for product analytics: default-deny, like the span
sanitizer it is modelled on.

Call sites hand over an event name, an actor, and properties. This module
decides what is allowed to leave the process:

* a name absent from the catalog is not an event;
* a property absent from that event's allowlist is dropped, not forwarded;
* a value that is not an id, a bounded enum, or a bucket is dropped even if its
  key is allowed, so a pod name landing in ``template_id`` cannot escape;
* an origin the event cannot legitimately come from disqualifies the event.

The rule is the observability plane's rule, for the same reason: a denylist
fails the first time somebody adds a field. See
``app/core/tests/unit/test_analytics_safety.py``, which feeds adversarial
content through this boundary and asserts none of it survives.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping
from uuid import UUID

from app.core.analytics.event_catalog import (
    ANALYTICS_CATALOG,
    GROUP_TYPES,
    SPINE_PROPERTIES,
    PersonProfile,
    UnknownAnalyticEventError,
)
from app.core.analytics.sink import AnalyticsSink, CapturedEvent, NullSink
from app.core.authorization.context import ActorType
from app.core.log.log import get_logger
from app.core.origin import Origin, current_origin

logger = get_logger(__name__)


#: A property value that is a string must look like an identifier or a bounded
#: enum. Emails carry ``@``, paths carry ``/``, prompts and names carry spaces
#: -- none of them match, so none of them cross.
_BOUNDED_VALUE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_sink: AnalyticsSink = NullSink()
_deployment: str = "unknown"
_strict: bool = False


def configure(
    sink: AnalyticsSink | None = None,
    *,
    deployment: str = "unknown",
    strict: bool = False,
) -> None:
    """Install the process-wide sink.

    Called once at startup. Absent an ``ANALYTICS_WRITE_KEY`` the caller passes
    nothing and the sink stays ``NullSink`` -- a null object rather than a
    disabled client, so no runtime flag can turn a self-hosted or Desktop-local
    deployment into one that sends pod content.

    ``strict`` raises on contract violations instead of dropping them. On in
    dev and CI, off in production, matching the logging contract's posture.
    """
    global _sink, _deployment, _strict
    _sink = sink if sink is not None else NullSink()
    _deployment = deployment
    _strict = strict


def current_sink() -> AnalyticsSink:
    return _sink


@dataclass(frozen=True, slots=True)
class AnalyticsActor:
    """Who acted, and for whom.

    ``DELEGATED_USER_WORKLOAD`` -- an agent acting *as* a person -- is the case
    that makes this more than a single id. The work belongs on the human's
    timeline, so they are the ``distinct_id``; the fact that an agent did it is
    ``actor_type``. Recording only one of the two makes it permanently
    impossible to separate what people did from what their agents did for them.
    """

    actor_type: ActorType
    user_id: str | None = None
    on_behalf_of_user: str | None = None
    anonymous_id: str | None = None

    @classmethod
    def user(cls, user_id: UUID | str) -> "AnalyticsActor":
        return cls(ActorType.USER, user_id=str(user_id))

    @classmethod
    def delegated(cls, *, delegated_by_user_id: UUID | str) -> "AnalyticsActor":
        """An agent acting as a person. *Which* agent travels as the event's
        own ``agent_id`` property, where it is scoped to events that have one,
        rather than as a spine dimension on everything."""
        return cls(
            ActorType.DELEGATED_USER_WORKLOAD,
            user_id=str(delegated_by_user_id),
            on_behalf_of_user=str(delegated_by_user_id),
        )

    @classmethod
    def autonomous(cls, actor_type: ActorType = ActorType.SYSTEM) -> "AnalyticsActor":
        return cls(actor_type)

    @classmethod
    def anonymous(cls, anonymous_id: str | None = None) -> "AnalyticsActor":
        return cls(ActorType.ANONYMOUS, anonymous_id=anonymous_id)


#: One machine actor for all autonomous work, product-wide.
#:
#: The alternative -- a distinct id per pod -- reads as the honest answer and is
#: not: an analytics store has no concept of a non-human distinct id, so every
#: pod becomes a *person*. Person count then scales with the pod count, and the
#: people-shaped metrics this plane exists to produce (DAU, retention, "how much
#: of this is human?") are polluted by machines, permanently and irreversibly --
#: PostHog's identified-stickiness is per distinct id.
#:
#: The pod is not lost by collapsing them. It rides on the event as ``pod_id``
#: *and* as a group, and pod-level retention is computed on the group, which is
#: what group analytics is for.
AUTONOMOUS_DISTINCT_ID: str = "lemma:autonomous"


def _resolve_distinct_id(actor: AnalyticsActor, *, event: str) -> str:
    """Whose timeline this event belongs on.

    The machine actor is a last resort, not a default. Almost nothing the backend
    does is unattributed: requests are authenticated, and unattended work --
    a schedule firing, a trigger on an RLS row -- is still done *for* somebody
    and belongs on their timeline as ``DELEGATED_USER_WORKLOAD``. Genuinely
    anonymous traffic is a browser thing.

    So falling through to the machine actor is reported. It shipped silently
    once and put connector executions, scheduled runs, function creations, pod
    deletions and surface connections on a fake person instead of the people who
    caused them -- and nothing said so, because the fallback looked like a
    design choice rather than a gap.
    """
    if actor.user_id:
        return actor.user_id
    if actor.anonymous_id:
        return actor.anonymous_id
    logger.warning(
        "analytics.actor.unattributed",
        analytic_event=event,
        actor_type=actor.actor_type.value,
    )
    return AUTONOMOUS_DISTINCT_ID


def _coerce(value: Any) -> str | int | float | bool | None:
    """Return ``value`` if it may cross the boundary, else ``None``."""
    if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        return value if _BOUNDED_VALUE_RE.match(value) else None
    return None


def _violation(reason: str, *, event: str, origin: str | None = None) -> None:
    """Drop the event and say why.

    ``reason`` is one of a small fixed set of strings, never interpolated
    content -- the logging contract's field allowlist is enforced statically
    (``test_logging_contract_static.py``) and this event is registered in
    ``app/core/log/event_catalog.py`` like every other.
    """
    if _strict:
        raise UnknownAnalyticEventError(f"{reason}: {event}")
    # Not ``event=``: structlog owns that name for the log event itself.
    logger.warning(
        "analytics.contract.violation",
        reason=reason,
        analytic_event=event,
        origin=origin,
    )


def emit(
    name: str,
    *,
    actor: AnalyticsActor,
    origin: Origin | None = None,
    organization_id: UUID | str | None = None,
    pod_id: UUID | str | None = None,
    properties: Mapping[str, Any] | None = None,
) -> None:
    """Capture one product-analytics event, or drop it and say why."""
    spec = ANALYTICS_CATALOG.get(name)
    if spec is None:
        _violation("unknown_event", event=name)
        return

    resolved_origin = origin or current_origin()
    if spec.origins is not None:
        if resolved_origin is None or resolved_origin.kind not in spec.origins:
            _violation(
                "origin_not_permitted",
                event=name,
                origin=resolved_origin.kind.value if resolved_origin else None,
            )
            return

    org = str(organization_id) if organization_id else None
    pod = str(pod_id) if pod_id else None

    distinct_id = _resolve_distinct_id(actor, event=name)

    allowed = spec.properties
    payload: dict[str, str | int | float | bool] = {}
    for key, raw in (properties or {}).items():
        if key in SPINE_PROPERTIES:
            # The spine is assembled here, never supplied by a call site --
            # otherwise a caller could relabel who acted.
            continue
        if key not in allowed:
            continue
        coerced = _coerce(raw)
        if coerced is not None:
            payload[key] = coerced

    payload["actor_type"] = actor.actor_type.value
    payload["deployment"] = _deployment
    if actor.on_behalf_of_user:
        payload["on_behalf_of_user"] = actor.on_behalf_of_user
    if resolved_origin is not None:
        payload.update(resolved_origin.as_properties())
    if spec.person_profile is PersonProfile.ANONYMOUS:
        # Only in the anonymous case: sending the flag as True would put a
        # property on every event to say nothing. An anonymous event carries no
        # group, which the catalog guarantees.
        payload["$process_person_profile"] = False

    groups: dict[str, str] = {}
    for group_type in spec.groups & GROUP_TYPES:
        value = org if group_type == "organization" else pod
        if value:
            groups[group_type] = value

    _sink.capture(
        CapturedEvent(
            name=name,
            distinct_id=distinct_id,
            properties=payload,
            groups=groups,
        )
    )
