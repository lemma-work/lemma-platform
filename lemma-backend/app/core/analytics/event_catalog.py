"""The exact product-analytics event contract.

Sibling to :mod:`app.core.log.event_catalog`, and hand-written where that one
is generated — these events are a product decision, not a by-product of the
code. Emitting a name absent from this catalog raises in dev and CI and no-ops
in production, the same posture the logging contract already takes.

Naming is ``noun.verb_past``, and the noun is the product's noun: pod, table,
document, agent, function, workflow, schedule, connector, surface, app, bundle,
conversation. One noun per concept.

Two rules keep the contract honest:

* Adding an event is a PR that edits this file.
* Events are append-only. Never redefine a name — add a new one if the meaning
  changes, because renaming silently splits every historical funnel.

Every property listed here must be an id, a bounded enum, or a bucket. Never a
name, an email, a path, a URL, free text, or model input/output. The emitter
enforces this by dropping anything unlisted (:mod:`app.core.analytics.emitter`),
and ``test_analytics_safety.py`` proves it with adversarial input.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, FrozenSet

from app.core.origin import OriginKind


class PersonProfile(str, Enum):
    """Whether an event should create or update a person in the analytics store.

    Identified events cost more and are the only ones that carry group
    membership -- PostHog drops ``$groups`` on an anonymous event
    (https://posthog.com/docs/product-analytics/group-analytics). That single
    fact decides the policy: pod- and org-level retention is the measurement
    this design exists for, so anything declaring a group has to stay
    identified, whoever or whatever acted.

    ``ANONYMOUS`` is therefore reserved for events with no group and no person
    behind them, where a profile would be pure cost and pure noise.
    """

    IDENTIFIED = "identified"
    ANONYMOUS = "anonymous"


#: Attached to every event by the emitter, so catalog entries below list only
#: what is specific to the event. These are the four questions of §2 of
#: docs/design/product-analytics.md: who acted, how the work arrived, and in
#: which deployment.
SPINE_PROPERTIES: Final[FrozenSet[str]] = frozenset(
    {
        "actor_type",
        "on_behalf_of_user",
        "origin",
        "origin_platform",
        "deployment",
        # Reserved so no call site can flip person processing and silently take
        # an event's group membership with it.
        "$process_person_profile",
    }
)

#: Group types. ``organization`` is the account — the unit of retention and
#: expansion. ``pod`` is the product unit, and pod-level retention matters more
#: than user retention: a pod running on a schedule with nobody watching is
#: delivering value, and a user-centric DAU chart scores that as churn.
GROUP_TYPES: Final[FrozenSet[str]] = frozenset({"organization", "pod"})


@dataclass(frozen=True, slots=True)
class AnalyticEvent:
    properties: FrozenSet[str] = frozenset()
    groups: FrozenSet[str] = frozenset({"organization", "pod"})
    origins: FrozenSet[OriginKind] | None = None
    """Origins allowed to raise this event, or ``None`` for any. A narrow set
    is a claim worth making: ``surface.message_answered`` can only come from a
    surface, so an event bearing any other origin is a bug, not data."""

    person_profile: PersonProfile = PersonProfile.IDENTIFIED
    """Defaults to ``IDENTIFIED`` deliberately: the default for a *new* catalog
    entry must be the one that cannot silently lose its groups. Setting this to
    ``ANONYMOUS`` on an event that declares a group is a contract violation and
    ``test_analytics_safety.py`` fails on it."""


_ORG_ONLY: Final[FrozenSet[str]] = frozenset({"organization"})


ANALYTICS_CATALOG: Final[dict[str, AnalyticEvent]] = {
    # -- Account ---------------------------------------------------------
    "auth.signed_up": AnalyticEvent(
        properties=frozenset({"method"}),
        groups=frozenset(),
    ),
    "organization.created": AnalyticEvent(groups=_ORG_ONLY),
    "organization.member_joined": AnalyticEvent(
        properties=frozenset({"member_count_bucket"}),
        groups=_ORG_ONLY,
    ),
    # -- Pod lifecycle ---------------------------------------------------
    "pod.created": AnalyticEvent(
        properties=frozenset({"pod_id", "source", "template_id", "recipe_id"}),
    ),
    "pod.member_joined": AnalyticEvent(
        properties=frozenset({"pod_id", "member_count_bucket"}),
    ),
    "pod.deleted": AnalyticEvent(properties=frozenset({"pod_id", "age_days_bucket"})),
    # Activation. Derived rather than raw: the first time a pod produced an
    # outcome for someone other than the person building it, through any
    # origin. Deliberately not tied to surfaces -- a dashboard pod, a scheduled
    # report and a connector-driven desk all activate, and none of them answers
    # a chat message. ``via`` records which kind of outcome got there first so
    # the mix stays visible.
    "pod.delivered": AnalyticEvent(
        properties=frozenset(
            {"pod_id", "via", "days_since_created_bucket", "resource_count_bucket"}
        ),
    ),
    # -- Building --------------------------------------------------------
    "table.created": AnalyticEvent(properties=frozenset({"pod_id", "table_id"})),
    "document.added": AnalyticEvent(
        properties=frozenset({"pod_id", "document_id", "kind", "size_bucket"}),
    ),
    "function.created": AnalyticEvent(properties=frozenset({"pod_id", "function_id"})),
    "agent.created": AnalyticEvent(
        properties=frozenset({"pod_id", "agent_id", "tool_count_bucket"}),
    ),
    "workflow.created": AnalyticEvent(
        properties=frozenset({"pod_id", "workflow_id", "node_count_bucket"}),
    ),
    "schedule.created": AnalyticEvent(
        properties=frozenset({"pod_id", "schedule_id", "trigger_kind"}),
    ),
    "app.created": AnalyticEvent(properties=frozenset({"pod_id", "app_id"})),
    "app.published": AnalyticEvent(properties=frozenset({"pod_id", "app_id"})),
    # -- Work ------------------------------------------------------------
    "conversation.started": AnalyticEvent(
        properties=frozenset({"pod_id", "conversation_id", "agent_id", "is_assistant"}),
    ),
    "agent_run.completed": AnalyticEvent(
        properties=frozenset(
            {
                "pod_id",
                "agent_id",
                "conversation_id",
                "status",
                "duration_bucket",
                "tool_call_count_bucket",
                "token_count_bucket",
            }
        ),
    ),
    "workflow_run.completed": AnalyticEvent(
        properties=frozenset(
            {"pod_id", "workflow_id", "status", "duration_bucket", "waited_on_form"}
        ),
    ),
    "schedule_run.completed": AnalyticEvent(
        properties=frozenset({"pod_id", "schedule_id", "status"}),
        origins=frozenset({OriginKind.SCHEDULE, OriginKind.DATA_TRIGGER}),
    ),
    "app.session_started": AnalyticEvent(
        properties=frozenset({"pod_id", "app_id"}),
        origins=frozenset({OriginKind.APP}),
    ),
    # -- Reach -----------------------------------------------------------
    # Inside reach: surfaces and apps, bounded by pod membership (REACH_RULE).
    "surface.connected": AnalyticEvent(properties=frozenset({"pod_id", "surface_id"})),
    "surface.message_answered": AnalyticEvent(
        properties=frozenset({"pod_id", "surface_id", "agent_id"}),
        origins=frozenset({OriginKind.SURFACE}),
    ),
    # Outside reach: the only path by which a non-member reaches a pod. Never
    # sum these with the two above -- they measure different products.
    # Org-scoped, not pod-scoped, because a connector account genuinely has no
    # pod: `app/modules/connectors` carries `organization_id` and `user_id` and
    # nothing else. Declaring a `pod_id` that is always absent would be worse
    # than not declaring one -- it reads as a gap in the data rather than a
    # property of the model. The consequence is that "outside reach *per pod*"
    # is not computable today, and §2 of the design doc says so.
    "connector.connected": AnalyticEvent(
        properties=frozenset({"connector_id"}),
        groups=_ORG_ONLY,
    ),
    "connector.operation_executed": AnalyticEvent(
        properties=frozenset({"connector_id", "provider", "direction", "status"}),
        groups=_ORG_ONLY,
    ),
    # -- The loop --------------------------------------------------------
    "bundle.exported": AnalyticEvent(
        properties=frozenset({"pod_id", "bundle_id", "resource_count_bucket"}),
    ),
    # The only anonymous event in the catalog: no group, and the viewer is
    # usually not a person we know -- often not a person at all, since a shared
    # link is exactly what crawlers follow. Minting a profile for each would be
    # cost and noise with nothing on the other side of it.
    "share_link.viewed": AnalyticEvent(
        properties=frozenset({"bundle_id", "kind", "viewer_is_member"}),
        groups=frozenset(),
        person_profile=PersonProfile.ANONYMOUS,
    ),
    # ``is_remix`` belongs at *start*, not only on completion: the abandonment
    # between the two is the interesting number, and it is only countable if
    # both ends carry the same dimension.
    "import.started": AnalyticEvent(
        properties=frozenset({"bundle_id", "is_remix"}),
        groups=_ORG_ONLY,
    ),
    "import.completed": AnalyticEvent(
        properties=frozenset(
            {"pod_id", "bundle_id", "resource_count_bucket", "is_remix"}
        ),
    ),
}


class UnknownAnalyticEventError(LookupError):
    """Raised in dev and CI when a name is not in the catalog."""


def spec_for(name: str) -> AnalyticEvent | None:
    return ANALYTICS_CATALOG.get(name)
