"""What the decisions tools need from the rest of the system.

Two collaborators, each a port the unit tests fake:

* **The decisions module** (`DecisionAsking`, `DeciderBook`), through its
  contract. Every call there opens its own short units of work, which is what
  lets a tool ask an engine without holding a database session.
* **The pod, under this call's authority** (`PodAccess`): the permission
  check, reading rows from a file or a table, and landing a results file. One
  `decision_pod_scope` is one short unit of work, built by `tool_authorization_context`.

The decisions contract's types appear here for annotations only. That module
builds its services when it is imported, and the build reaches this toolset's
registry through the agent's model-runtime contract -- so importing it at
runtime from here would, whenever decisions happened to be imported first, ask
a half-imported module for names it has not defined yet. `contract.py` imports
what it calls where it calls it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from pydantic import JsonValue

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.decisions.models import TableRows

if TYPE_CHECKING:
    from app.modules.decisions.contracts.decide import (
        DeciderDefinition,
        DecisionEntity,
        Option,
        RowsResult,
    )
    from app.modules.decisions.contracts.deciders import (
        DeciderEntity,
        SampleResult,
    )

#: An answer a question can have: an option key, a list of them, a boolean, or
#: a scale level's index. The decisions module's own `AnswerValue`, restated so
#: this module can use it at runtime.
type AnswerValue = str | list[str] | bool | int

#: Per question, the options a caller adds for one call.
type CallOptions = Mapping[str, dict[str, Option]]


@dataclass(frozen=True, slots=True)
class Caller:
    """Who is asking: the person this call works for, in its pod.

    Decisions the tools record are that person's, `PERSONAL` as the API's are
    by default: the state an agent decides on is whatever it read for them --
    their mail, their rows -- which the rest of the pod may not be allowed to
    see, and a decision keeps its state as evidence.
    """

    user_id: UUID
    pod_id: UUID
    organization_id: UUID | None


@dataclass(frozen=True, slots=True)
class Sample:
    """A row to try a decider on, and the answers a person gave for it, if any."""

    state: JsonValue
    expected: dict[str, AnswerValue] | None


@dataclass(frozen=True, slots=True)
class TableBatch:
    rows: list[JsonValue]
    #: Rows matching the filters, whatever the batch: how the caller knows
    #: whether there are more than it read.
    total: int
    primary_key: str | None


@dataclass(frozen=True, slots=True)
class SavedFile:
    #: Where it landed, as the caller would write it (`/me/...`).
    pod_path: str
    size_bytes: int


class DecisionAsking(Protocol):
    """Asking decisions, and answering them, through the decisions contract."""

    def max_rows(self) -> int: ...

    def system_decider_names(self) -> list[str]: ...

    async def decide(
        self,
        *,
        caller: Caller,
        state: JsonValue,
        decider: str | None,
        definition: DeciderDefinition | None,
        subject: str | None,
        options: CallOptions,
    ) -> DecisionEntity: ...

    async def decide_rows(
        self,
        *,
        caller: Caller,
        rows: Sequence[JsonValue],
        decider: str | None,
        definition: DeciderDefinition | None,
        options: CallOptions,
        key: str | None,
    ) -> RowsResult: ...

    async def get_decision(
        self, *, caller: Caller, decision_id: UUID
    ) -> DecisionEntity: ...

    async def answer_as_agent(
        self,
        *,
        caller: Caller,
        decision_id: UUID,
        answers: Mapping[str, AnswerValue],
    ) -> DecisionEntity:
        """Record the answers as the agent's. There is no way to say "a person".

        An agent that relays what somebody said is still an agent, and text an
        agent read could otherwise have it "relay" a correction no person made
        and teach the decider with it.
        """
        ...


class DeciderBook(Protocol):
    """The pod's saved deciders."""

    async def get(self, *, pod_id: UUID, name: str) -> DeciderEntity | None: ...

    async def names(self, *, pod_id: UUID) -> list[str]: ...

    async def define(
        self, *, caller: Caller, name: str, definition: DeciderDefinition
    ) -> tuple[DeciderEntity, list[str]]: ...

    async def trial(
        self,
        *,
        caller: Caller,
        samples: Sequence[Sample],
        decider: str | None,
        definition: DeciderDefinition | None,
    ) -> SampleResult: ...


class PodSession(Protocol):
    """One short unit of work in the pod, with this tool call's authority."""

    @property
    def caller(self) -> Caller: ...

    async def require(self, permission: str, decider_id: UUID | None) -> None:
        """Refuse unless the action is allowed on this decider, or on the pod."""
        ...

    async def read_pod_file(self, path: str) -> bytes: ...

    async def read_table(self, source: TableRows, *, limit: int) -> TableBatch: ...

    async def write_pod_file(
        self, *, directory: str, name: str, content: bytes
    ) -> SavedFile: ...


class PodAccess(Protocol):
    def decision_pod_scope(
        self, deps: BaseAgentContext
    ) -> AbstractAsyncContextManager[PodSession]: ...
