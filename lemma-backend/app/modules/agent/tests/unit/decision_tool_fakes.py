"""Stand-ins for what the decisions tools talk to: the decisions contract and the pod.

Injected through `DecisionTools`' constructor, so nothing inside the tools is
patched. The decisions fake answers by a rule the test gives it and keeps what
it was asked; the pod fake keeps files and tables in memory, refuses the
permissions a test names, and counts open sessions -- so a test can see that no
unit of work was held while a decision was asked.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from pydantic import JsonValue

from app.core.domain.errors import DomainError
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.decisions.models import TableRows
from app.modules.agent.tools.decisions.seams import (
    AnswerValue,
    CallOptions,
    Caller,
    Sample,
    SavedFile,
    TableBatch,
)
from app.modules.decisions.contracts.decide import (
    Answer,
    DeciderDefinition,
    DecisionEntity,
    DecisionStatus,
    RowResult,
    RowsResult,
    Rung,
)
from app.modules.decisions.contracts.deciders import (
    DeciderEntity,
    SampleDisagreement,
    SampleResult,
)

ORGANIZATION_ID = uuid4()

#: What a decider answers for one state; None leaves the question open.
type Answering = Callable[[JsonValue], Mapping[str, AnswerValue | None]]


def _urgent_when_it_says_so(state: JsonValue) -> Mapping[str, AnswerValue | None]:
    return {"urgent": "urgent" in str(state).lower()}


def _scope(decider: str | None) -> str:
    if decider is None:
        return "inline"
    return "system" if decider.startswith("system:") else "pod"


class FakeDecisions:
    """`DecisionAsking`, answering by rule and remembering everything."""

    def __init__(
        self,
        *,
        answering: Answering = _urgent_when_it_says_so,
        cap: int = 500,
        system: Sequence[str] = ("system:reply_to_question",),
        open_sessions: Callable[[], int] = lambda: 0,
    ) -> None:
        self._answering = answering
        self._cap = cap
        self._system = list(system)
        self._open_sessions = open_sessions
        self.asked: list[JsonValue] = []
        self.options_seen: list[CallOptions] = []
        self.sessions_open_while_asking: list[int] = []
        self.decisions: dict[UUID, DecisionEntity] = {}
        self.agent_answers: list[tuple[UUID, dict[str, AnswerValue]]] = []

    def max_rows(self) -> int:
        return self._cap

    def system_decider_names(self) -> list[str]:
        return list(self._system)

    def _record(
        self, caller: Caller, state: JsonValue, decider: str | None
    ) -> DecisionEntity:
        self.asked.append(state)
        self.sessions_open_while_asking.append(self._open_sessions())
        given = self._answering(state)
        open_keys = [key for key, value in given.items() if value is None]
        decision = DecisionEntity(
            pod_id=caller.pod_id,
            organization_id=caller.organization_id,
            user_id=caller.user_id,
            decider_scope=_scope(decider),
            decider_key=decider or "inline:fake",
            decider_name=decider,
            answers={
                key: Answer(value=value, by=Rung.RULES)
                for key, value in given.items()
                if value is not None
            },
            open=open_keys,
            status=DecisionStatus.ABSTAINED if open_keys else DecisionStatus.ANSWERED,
        )
        self.decisions[decision.id] = decision
        return decision

    async def decide(
        self,
        *,
        caller: Caller,
        state: JsonValue,
        decider: str | None,
        definition: DeciderDefinition | None,
        subject: str | None,
        options: CallOptions,
    ) -> DecisionEntity:
        self.options_seen.append(options)
        return self._record(caller, state, decider)

    async def decide_rows(
        self,
        *,
        caller: Caller,
        rows: Sequence[JsonValue],
        decider: str | None,
        definition: DeciderDefinition | None,
        options: CallOptions,
        key: str | None,
    ) -> RowsResult:
        results = []
        for index, row in enumerate(rows):
            decision = self._record(caller, row, decider)
            row_id = str(index + 1)
            if key is not None and isinstance(row, dict):
                row_id = str(row.get(key))
            results.append(
                RowResult(index, row_id, decision.answers, decision.open, decision.id)
            )
        counts: dict[str, Counter[str]] = {}
        for result in results:
            for question, answer in result.answers.items():
                counts.setdefault(question, Counter())[str(answer.value)] += 1
        return RowsResult(
            decider_key=decider or "inline:fake",
            rows=results,
            counts={question: dict(tally) for question, tally in counts.items()},
        )

    async def get_decision(
        self, *, caller: Caller, decision_id: UUID
    ) -> DecisionEntity:
        found = self.decisions.get(decision_id)
        if found is None:
            raise DomainError(
                "No such decision.", code="DECISION_NOT_FOUND", status_code=404
            )
        return found

    async def answer_as_agent(
        self,
        *,
        caller: Caller,
        decision_id: UUID,
        answers: Mapping[str, AnswerValue],
    ) -> DecisionEntity:
        self.agent_answers.append((decision_id, dict(answers)))
        decision = self.decisions[decision_id]
        updated = decision.model_copy(
            update={
                "answers": {
                    **decision.answers,
                    **{
                        key: Answer(value=value, by=Rung.AGENT)
                        for key, value in answers.items()
                    },
                },
                "open": [key for key in decision.open if key not in answers],
                "status": DecisionStatus.RESOLVED,
            }
        )
        self.decisions[decision_id] = updated
        return updated


class FakeDeciders:
    """`DeciderBook` over a dict, trying samples with the same kind of rule."""

    def __init__(
        self,
        saved: Sequence[DeciderEntity] = (),
        *,
        answering: Answering = _urgent_when_it_says_so,
        warnings: Sequence[str] = (),
    ) -> None:
        self.saved = {decider.name: decider for decider in saved}
        self._answering = answering
        self._warnings = list(warnings)
        self.trials: list[list[Sample]] = []

    async def get(self, *, pod_id: UUID, name: str) -> DeciderEntity | None:
        return self.saved.get(name)

    async def names(self, *, pod_id: UUID) -> list[str]:
        return sorted(self.saved)

    async def define(
        self, *, caller: Caller, name: str, definition: DeciderDefinition
    ) -> tuple[DeciderEntity, list[str]]:
        existing = self.saved.get(name)
        saved = DeciderEntity(
            id=existing.id if existing else uuid4(),
            pod_id=caller.pod_id,
            user_id=caller.user_id,
            name=name,
            version=existing.version + 1 if existing else 1,
            definition=definition,
        )
        self.saved[name] = saved
        return saved, list(self._warnings)

    async def trial(
        self,
        *,
        caller: Caller,
        samples: Sequence[Sample],
        decider: str | None,
        definition: DeciderDefinition | None,
    ) -> SampleResult:
        self.trials.append(list(samples))
        answers: list[dict[str, Answer]] = []
        agreement: dict[str, tuple[int, int]] = {}
        disagreements: list[SampleDisagreement] = []
        for index, sample in enumerate(samples):
            given = {
                key: Answer(value=value, by=Rung.MODEL)
                for key, value in self._answering(sample.state).items()
                if value is not None
            }
            answers.append(given)
            for key, expected in (sample.expected or {}).items():
                answer = given.get(key)
                agreed = answer is not None and answer.value == expected
                before = agreement.get(key, (0, 0))
                agreement[key] = (before[0] + int(agreed), before[1] + 1)
                if not agreed:
                    disagreements.append(
                        SampleDisagreement(index, key, expected, answer)
                    )
        return SampleResult(
            answers=answers,
            open=[[] for _ in samples],
            agreement=agreement,
            disagreements=disagreements,
        )


def refused(permission: str) -> DomainError:
    return DomainError(
        f"Missing permission {permission}.",
        code="MISSING_WORKLOAD_RESOURCE_GRANT",
        status_code=403,
        details={"permission_ids": [permission]},
    )


class FakePod:
    """`PodAccess` in memory: files by path, tables by name, refusals by permission."""

    def __init__(
        self,
        *,
        files: Mapping[str, bytes] | None = None,
        tables: Mapping[str, list[JsonValue]] | None = None,
        refuse: Sequence[str] = (),
        unwritable: Sequence[str] = (),
    ) -> None:
        self.files = dict(files or {})
        self.tables = dict(tables or {})
        self.refuse = set(refuse)
        self.unwritable = set(unwritable)
        self.checks: list[tuple[str, UUID | None]] = []
        self.written: dict[str, bytes] = {}
        self.open_sessions = 0

    @asynccontextmanager
    async def decision_pod_scope(
        self, deps: BaseAgentContext
    ) -> AsyncIterator[_FakeSession]:
        self.open_sessions += 1
        try:
            yield _FakeSession(self, deps)
        finally:
            self.open_sessions -= 1


class _FakeSession:
    def __init__(self, pod: FakePod, deps: BaseAgentContext) -> None:
        self._pod = pod
        self._deps = deps

    @property
    def caller(self) -> Caller:
        return Caller(
            user_id=self._deps.user_id,
            pod_id=self._deps.pod_id,
            organization_id=ORGANIZATION_ID,
        )

    async def require(self, permission: str, decider_id: UUID | None) -> None:
        self._pod.checks.append((permission, decider_id))
        if permission in self._pod.refuse:
            raise refused(permission)

    async def read_pod_file(self, path: str) -> bytes:
        if path not in self._pod.files:
            raise DomainError(f"No file at {path}.", code="NOT_FOUND", status_code=404)
        return self._pod.files[path]

    async def read_table(self, source: TableRows, *, limit: int) -> TableBatch:
        rows = self._pod.tables[source.table_name]
        return TableBatch(
            rows=rows[source.offset : source.offset + limit],
            total=len(rows),
            primary_key="id",
        )

    async def write_pod_file(
        self, *, directory: str, name: str, content: bytes
    ) -> SavedFile:
        if directory in self._pod.unwritable:
            raise refused("folder.write")
        path = f"{directory.rstrip('/')}/{name}"
        self._pod.written[path] = content
        return SavedFile(pod_path=path, size_bytes=len(content))
