"""What a run records about itself beyond its status: why it started, whom it
answers for, what it continues, and how it ended.

Each was a key in the run's metadata JSON, read at every site as
``metadata.get(...)`` and re-decided there. They are columns of ``agent_runs``
now; this is their typed form on the entity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent.domain.state_kinds import RunTrigger, WrittenIn

JsonObject = dict[str, object]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class ScheduleFiring:
    """The facts about the firing that started a schedule's run."""

    schedule_id: str | None = None
    schedule_name: str | None = None
    #: ``TIME``, ``WEBHOOK`` or ``DATASTORE``.
    trigger_type: str | None = None
    #: ISO timestamps, as the schedule module renders them.
    fired_at: str | None = None
    scheduled_for: str | None = None
    timezone: str | None = None

    def to_json(self) -> JsonObject:
        return {
            key: value
            for key, value in (
                ("schedule_id", self.schedule_id),
                ("schedule_name", self.schedule_name),
                ("trigger_type", self.trigger_type),
                ("fired_at", self.fired_at),
                ("scheduled_for", self.scheduled_for),
                ("timezone", self.timezone),
            )
            if value is not None
        }

    @classmethod
    def from_json(cls, raw: object) -> ScheduleFiring | None:
        if not isinstance(raw, Mapping):
            return None
        firing = cls(
            schedule_id=_text(raw.get("schedule_id")),
            schedule_name=_text(raw.get("schedule_name")),
            trigger_type=_text(raw.get("trigger_type")),
            fired_at=_text(raw.get("fired_at")),
            scheduled_for=_text(raw.get("scheduled_for")),
            timezone=_text(raw.get("timezone")),
        )
        return firing if firing.to_json() else None


@dataclass(frozen=True, slots=True)
class RunFailure:
    """Why a FAILED run failed, in the codes a client branches on."""

    code: str
    reason: str | None = None

    def to_json(self) -> JsonObject:
        return {"code": self.code, "reason": self.reason}

    @classmethod
    def from_json(cls, raw: object) -> RunFailure | None:
        if not isinstance(raw, Mapping):
            return None
        code = _text(raw.get("code"))
        if code is None:
            return None
        return cls(code=code, reason=_text(raw.get("reason")))


@dataclass(frozen=True, slots=True)
class ParkedSuspension:
    """The pause a run's turn was ended for, once it had waited too long.

    Its presence is what makes the host's "cancelled" read as WAITING, and
    what tells a later decision that it has no live turn to land in and must
    resume a new run instead.
    """

    tool_call_id: str
    tool_name: str

    def to_json(self) -> JsonObject:
        return {"tool_call_id": self.tool_call_id, "tool_name": self.tool_name}

    @classmethod
    def from_json(cls, raw: object) -> ParkedSuspension | None:
        if not isinstance(raw, Mapping):
            return None
        tool_call_id, tool_name = raw.get("tool_call_id"), raw.get("tool_name")
        if not (isinstance(tool_call_id, str) and isinstance(tool_name, str)):
            return None
        return cls(tool_call_id=tool_call_id, tool_name=tool_name)


class RunPrivacy(BaseModel):
    """Where a run answers, carried on to every run that continues it.

    A resume, a retry, a follow-up: each continues answering what the earlier
    run answered, so each answers where it would have -- a note's in Lemma --
    and for whom it would have (``domain/private_notes``).
    """

    model_config = ConfigDict(frozen=True)

    #: A private note started it: the answer stays in Lemma.
    private_note: bool = False
    #: A line in one of Lemma's own channels started it: answered in Lemma.
    asked_in_lemma: bool = False
    #: A message typed in Lemma into a platform's conversation started it.
    written_in: WrittenIn | None = None


class RunState(RunPrivacy):
    """The typed facts of one run, as its row stores them."""

    trigger: RunTrigger = RunTrigger.MESSAGE
    #: The pausing call a resumed run picks up from.
    resumed_tool_call_id: str | None = None
    #: The failed run a retry replaces.
    retried_run_id: UUID | None = None
    #: The finished run whose queue a follow-up answers.
    queued_behind_run_id: UUID | None = None
    #: On a follow-up: finished runs still holding messages of the kind it did
    #: not answer, for the follow-up after it to find.
    queued_leftovers_behind: list[UUID] = Field(default_factory=list)
    #: The newest message sequence when Stop was pressed. Only what was said
    #: after it is owed a turn once the run has stopped.
    stop_requested_after_sequence: int | None = None
    #: When a sub-agent's result reached its parent -- by delivery, an await,
    #: a status check or a wait -- so it is handed over once.
    delivered_to_parent_at: datetime | None = None
    #: Lemma ended this run's turn because nobody answered what it was parked
    #: on within the parked window.
    suspended_while_parked: ParkedSuspension | None = None
    schedule_firing: ScheduleFiring | None = None
    failure: RunFailure | None = None

    @property
    def privacy(self) -> RunPrivacy:
        return RunPrivacy(
            private_note=self.private_note,
            asked_in_lemma=self.asked_in_lemma,
            written_in=self.written_in,
        )

    @property
    def replays_a_retried_turn(self) -> bool:
        """Started by a retry: replays a person's turn that belongs to the run
        it retried rather than to itself."""
        return self.trigger is RunTrigger.RETRY
