"""Triage: what happens to each event a schedule receives, by a decider's answer.

A schedule's `triage` names a pod decider and maps the options of one of its
choice questions to an outcome:

- **act** wakes the target now, as any fire does.
- **digest** holds the event; held events go out together on the digest's
  cadence, one run for many.
- **ask** holds the event and puts it in front of the person it belongs to,
  with the options. Their answer routes it, and teaches the decider.
- **ignore** records the event as skipped.

It replaces a schedule's `filter_instruction` rather than sitting beside it: a
filter is the two-outcome case of the same idea, act or ignore.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.modules.schedule.domain.cron import CronSchedule


class TriageRoute(StrEnum):
    ACT = "act"
    DIGEST = "digest"
    ASK = "ask"
    IGNORE = "ignore"


#: The outcomes that hold an event instead of settling it.
HELD_ROUTES = frozenset({TriageRoute.DIGEST, TriageRoute.ASK})

#: How a digest run's `source_event_id` begins. One digest is one run, filed
#: under the occurrence it went out at and the person its events belong to.
DIGEST_EVENT_PREFIX = "digest:"

#: Said by the create schema and by the service alike, as the filter's refusal is.
TIME_SCHEDULE_TRIAGE_REFUSED = (
    "triage acts on the event that fired a WEBHOOK or DATASTORE schedule. A TIME "
    "schedule fires on the clock, with no event to sort, so it cannot carry one."
)
TRIAGE_REPLACES_FILTER = (
    "A schedule carries `triage` instead of `filter_instruction` and "
    "`filter_output_schema`, not alongside them: a filter is the act-or-ignore "
    "case of a triage. Clear the filter, or route a decider's options instead."
)


class TriageDigest(BaseModel):
    """When held events go out together."""

    model_config = ConfigDict(extra="forbid")

    cron: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Five-field cron for the digest, e.g. '0 9 * * 1-5'. No more often "
            "than a TIME schedule may fire."
        ),
    )
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description="IANA zone the cron is read in, e.g. 'Europe/Berlin'. Omitted means UTC.",
    )


class TriageConfig(BaseModel):
    """A decider, and what each of its answers does with the event."""

    model_config = ConfigDict(extra="forbid")

    decider: str = Field(
        min_length=1,
        max_length=64,
        description="The pod decider asked about each event.",
    )
    question: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "The decider's choice question to route on. Omitted means its only "
            "question; saved as the one it resolved to."
        ),
    )
    routes: dict[str, TriageRoute] = Field(
        min_length=1,
        max_length=255,
        description=(
            "Every declared option of the question, mapped to act, digest, ask or "
            "ignore."
        ),
    )
    digest: TriageDigest | None = Field(
        default=None,
        description="Required when any option routes to digest.",
    )
    act_per_hour: int | None = Field(
        default=None,
        ge=1,
        le=10_000,
        description=(
            "At most this many act runs an hour. Past it, act becomes digest when "
            "the schedule has one, and ask when it does not."
        ),
    )

    @model_validator(mode="after")
    def _a_digest_route_has_a_cadence(self) -> TriageConfig:
        if self.digest is None and TriageRoute.DIGEST in self.routes.values():
            raise ValueError(
                "An option routes to digest, so triage needs a `digest`: "
                "{cron, timezone}."
            )
        return self

    @property
    def over_ceiling(self) -> TriageRoute:
        """What an act becomes once the hour's `act_per_hour` is spent."""
        return TriageRoute.DIGEST if self.digest is not None else TriageRoute.ASK

    def to_json(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_none=True)


def triage_json(triage: TriageConfig | None) -> dict[str, JsonValue] | None:
    return None if triage is None else triage.to_json()


def next_digest_at(triage: TriageConfig | None, *, after: datetime) -> datetime | None:
    """When a schedule's held events next go out, or None when it has no digest.

    An expression that no longer parses -- a zone this host cannot resolve --
    has no next digest rather than raising: its held events wait, visibly HELD,
    until the triage is saved again.
    """
    if triage is None or triage.digest is None:
        return None
    try:
        cron = CronSchedule.parse(triage.digest.cron, zone=triage.digest.timezone)
    except ValueError:
        return None
    return cron.next_fire_time(after)


def rearmed_digest_at(
    before: TriageConfig | None, after: TriageConfig | None, *, now: datetime
) -> datetime | None:
    """The digest cursor once a schedule's triage changes from `before` to `after`.

    A digest that is removed sends what it held once more, now, rather than
    leaving those events held with nothing left to send them.
    """
    upcoming = next_digest_at(after, after=now)
    if upcoming is None and before is not None and before.digest is not None:
        return now
    return upcoming


class OfferedOption(BaseModel):
    """One answer a person is offered about a held event, and what it does."""

    model_config = ConfigDict(extra="ignore")

    key: str
    label: str
    route: TriageRoute


class TriageAsk(BaseModel):
    """What a held event's question asked, kept on the question itself.

    Written as the notification's action and read back from it when it closes,
    so an answer is routed by the options the person was shown -- whatever the
    schedule's routes have become since.
    """

    model_config = ConfigDict(extra="ignore")

    schedule_id: UUID
    run_id: UUID
    decision_id: UUID
    question: str
    options: list[OfferedOption] = Field(min_length=1)

    def route_for(self, answer: str) -> TriageRoute | None:
        return next(
            (option.route for option in self.options if option.key == answer), None
        )


def offered_options(
    routes: Mapping[str, TriageRoute], labels: Mapping[str, str]
) -> list[OfferedOption]:
    """The answers a person is offered: every option that settles the event.

    An option routed to ask is left out -- choosing it would ask again. They
    are listed by what they do, acting first, because the routes come back from
    JSONB, which keeps no order of its own.
    """
    offered = [
        OfferedOption(key=key, label=labels.get(key) or key, route=route)
        for key, route in routes.items()
        if route is not TriageRoute.ASK
    ]
    return sorted(offered, key=lambda option: (_OFFER_ORDER[option.route], option.key))


_OFFER_ORDER = {TriageRoute.ACT: 0, TriageRoute.DIGEST: 1, TriageRoute.IGNORE: 2}


@dataclass(frozen=True, slots=True)
class TriageVerdict:
    """What a schedule's triage made of one event.

    `output` is the event's `llm_output`: the decision, the question and its
    answer, and the route -- with `routed_from` when the act ceiling sent it
    elsewhere, and `fallback` when the question was left open and its fallback
    option answered it. `evidence` is what the decision judged, kept for the
    question put to a person.
    """

    route: TriageRoute
    decision_id: UUID
    question: str
    answer: str
    evidence: str | None
    output: dict[str, JsonValue]

    @property
    def holds(self) -> bool:
        return self.route in HELD_ROUTES


def verdict_output(
    *,
    decision_id: UUID,
    question: str,
    answer: str,
    route: TriageRoute,
    routed_from: TriageRoute | None = None,
    fallback: bool = False,
) -> dict[str, JsonValue]:
    output: dict[str, JsonValue] = {
        "decision_id": str(decision_id),
        "question": question,
        "answer": answer,
        "route": route.value,
    }
    if routed_from is not None:
        output["routed_from"] = routed_from.value
    if fallback:
        output["fallback"] = True
    return output
