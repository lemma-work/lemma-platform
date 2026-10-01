"""Decisions toolset: ask closed-set questions, save them as deciders, try them,
and record answers.

Every tool runs the same way. It checks `decider.*` on the named decider, or
on the pod for a system or inline one, in one short unit of work under the
call's authority (`tool_authorization_context`, through `pod_services`),
reading any rows in the same unit; closes it; and only then asks the decisions
contract, which opens its own. A missing grant comes back as `needs_approval`,
exactly as a pod tool's does, so the agent can re-issue the call through
`request_approval`.

`pod_id` always comes from the run context, never from an argument.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID

from pydantic import BaseModel, JsonValue
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.decisions.contract import (
    ContractDecisions,
    ContractDeciders,
)
from app.modules.agent.tools.decisions.inputs import (
    Judgement,
    answers_of,
    call_options,
    decision_id_of,
    definition_of,
    expected_fields,
    judgement,
    question_types,
)
from app.modules.agent.tools.decisions.models import (
    AnswerDecisionRequest,
    DecideRequest,
    DeciderTestRequest,
    DefineDeciderRequest,
    InputRefused,
)
from app.modules.agent.tools.decisions.pod_access import DatastorePodAccess
from app.modules.agent.tools.decisions.results import (
    INLINE_RESULT_ROWS,
    answered_view,
    decision_view,
    defined_view,
    inline_results,
    rows_view,
    trial_view,
)
from app.modules.agent.tools.decisions.rows import (
    ResultsPlace,
    hold_out,
    results_csv,
    results_place,
    row_label,
)
from app.modules.agent.tools.decisions.seams import (
    Caller,
    DecisionAsking,
    DeciderBook,
    PodAccess,
    PodSession,
    Sample,
    SavedFile,
)
from app.modules.agent.tools.decisions.sources import (
    Read,
    chosen_source,
    read_rows,
    rows_of,
)
from app.modules.agent.tools.tool_errors import APPROVAL_CODES, approval_error_result

if TYPE_CHECKING:
    from app.modules.decisions.contracts.decide import DeciderDefinition, RowsResult

logger = get_logger(__name__)

#: Saved deciders named when a call names one that does not exist.
_LISTED_DECIDERS = 20


@dataclass(frozen=True, slots=True)
class _Target:
    """What an action is authorized on, and the saved definition, when known."""

    #: The pod decider's id; None authorizes on the pod.
    decider_id: UUID | None
    definition: DeciderDefinition | None


def _refused(exc: DomainError) -> bool:
    """A write the caller may not make, as opposed to one that failed."""
    return exc.status_code in (401, 403) or exc.code in APPROVAL_CODES


async def require_asking(pod: PodSession, decider_id: UUID | None) -> None:
    """A named pod decider is asked under its own grant; nothing else needs one.

    Inline questions and `system:` deciders read only the state the agent passes
    in -- whatever rows it reads for them are authorized where they are read --
    so declaring the toolset is enough, as it is for web search. A pod decider
    carries the pod's rubric and examples, and is granted like a function.
    """
    if decider_id is not None:
        await pod.require(Permissions.DECIDER_EXECUTE, decider_id)


class DecisionTools:
    """The four tools, over the ports they need. The docstrings are model-facing."""

    def __init__(
        self, *, decisions: DecisionAsking, deciders: DeciderBook, pod: PodAccess
    ) -> None:
        self._decisions = decisions
        self._deciders = deciders
        self._pod = pod

    async def decide(
        self, ctx: RunContext[BaseAgentContext], request: DecideRequest
    ) -> JsonObject:
        """Ask a closed-set question -- which of these, yes or no, how much -- about one thing or many rows.

        Use it when the same judgement has to be made consistently over many
        things (sort 300 tickets by urgency, flag which leads are a fit), or
        when a judgement should be recorded so the pod can act on it and a
        person can correct it. Rules answer first, then a fast classifier, then
        a language model, and every answer says which one gave it.

        Do not use it for anything that needs investigating, other tools or
        written text: reading more, extracting fields, summarising and drafting
        are your own work. It never writes text; it only picks from the options
        it was given.

        Name the judgement one way: `decider` (a saved one, or
        `system:<name>`), `questions` (a one-off) or `definition` (an unsaved
        draft). Then give it `state` for one thing, or rows from exactly one of
        `items`, `file` (CSV or JSONL) or `table`.

        One state returns its answers, who gave each, and a `decision_id`. Rows
        return counts per answer, the rows left open, the rows that failed, and
        the results: inline when there are few, otherwise a CSV next to the
        input file (or under /me/decisions/) with each row's columns plus one
        column per question. One call takes a few hundred rows at most; more is
        refused with the limit, so batch a table with `limit` and `offset`.

        A question nothing was sure of is left `open` (a choice with a
        `fallback` takes it, marked as such). If it matters, ask the person with
        ask_user and record their answer with answer_decision.
        """
        return await self._guarded(ctx.deps, "decide", request, self._decide)

    async def define_decider(
        self, ctx: RunContext[BaseAgentContext], request: DefineDeciderRequest
    ) -> JsonObject:
        """Save a decider: a named, versioned judgement the pod can ask again and again.

        Use it when the same question will be asked repeatedly -- by you later,
        by a schedule, a workflow or an app -- so it keeps one meaning and
        learns from the people who correct it. For a one-off, pass `questions`
        to decide instead. Saving an existing name makes a new version;
        decisions already made keep the version that made them.

        Try a definition with test_decider first, above all a change to one in
        use: rows with answers people gave show what the change gets right and
        what it breaks. Returns the saved definition, normalized, and warnings
        for what it allows but is usually a mistake, such as a choice with no
        fallback or an input view that sends every field to the engines.
        """
        return await self._guarded(ctx.deps, "define_decider", request, self._define)

    async def test_decider(
        self, ctx: RunContext[BaseAgentContext], request: DeciderTestRequest
    ) -> JsonObject:
        """Try a saved decider or a draft definition on sample rows, recording nothing.

        Use it before saving a definition, and before replacing one people rely
        on. Give rows as `items`, a `file` or a `table`, and name in `expected`
        the fields that hold answers people gave: those fields are hidden from
        the decider, and the result reports agreement per question and lists
        each disagreement. Without `expected` it previews how the rows would be
        sorted. Every row is really asked, so it costs what deciding them does;
        a few dozen well-chosen rows say as much as hundreds.
        """
        return await self._guarded(ctx.deps, "test_decider", request, self._test)

    async def answer_decision(
        self, ctx: RunContext[BaseAgentContext], request: AnswerDecisionRequest
    ) -> JsonObject:
        """Record an answer to a decision: one of its open questions, or a correction.

        Use it when you worked out an open question yourself, or to write down
        what the person told you after you asked them with ask_user. The answer
        is recorded as yours, the agent's, and it never becomes an example the
        decider learns from -- even when you are passing on what a person said.
        That is deliberate: nothing an agent reads may teach a decider. If the
        person wants to teach it, they can correct the decision themselves in
        Lemma; their correction is what the decider learns from.
        """
        return await self._guarded(ctx.deps, "answer_decision", request, self._answer)

    async def _guarded[R: BaseModel](
        self,
        deps: BaseAgentContext,
        tool_name: str,
        request: R,
        work: Callable[[BaseAgentContext, R], Awaitable[JsonObject]],
    ) -> JsonObject:
        """Run a tool, returning refused input or a missing grant as its result."""
        try:
            return await work(deps, request)
        except InputRefused as exc:
            return {"success": False, "error": str(exc)}
        except DomainError as exc:
            return approval_error_result(
                exc, tool_name=tool_name, args=request.model_dump(mode="json")
            )

    # --- who is asked, and what it is authorized on -------------------------

    async def _target(self, pod_id: UUID, decider: str | None) -> _Target:
        """A pod decider is authorized on itself; system and inline on the pod."""
        if decider is None or decider in self._decisions.system_decider_names():
            return _Target(None, None)
        found = await self._deciders.get(pod_id=pod_id, name=decider)
        if found is None:
            raise InputRefused(await self._unknown(pod_id, decider))
        return _Target(found.id, found.definition)

    async def _unknown(self, pod_id: UUID, name: str) -> str:
        saved = await self._deciders.names(pod_id=pod_id)
        shipped = self._decisions.system_decider_names()
        parts = [f"No decider named {name!r} in this pod."]
        if saved:
            parts.append(f"Saved here: {', '.join(saved[:_LISTED_DECIDERS])}.")
        if shipped:
            parts.append(f"Shipped with Lemma: {', '.join(shipped)}.")
        parts.append(
            "Or pass `questions` for a one-off, or save one with define_decider."
        )
        return " ".join(parts)

    # --- decide ------------------------------------------------------------

    async def _decide(
        self, deps: BaseAgentContext, request: DecideRequest
    ) -> JsonObject:
        asked = judgement(
            decider=request.decider,
            questions=request.questions,
            definition=request.definition,
            guidance=request.guidance,
        )
        options = call_options(request.options)
        source = chosen_source(request)
        if (source is None) == (request.state is None):
            raise InputRefused(
                "Give `state` for one thing, or rows as `items`, `file` or "
                "`table` -- one of them."
            )
        if source is not None and request.subject is not None:
            raise InputRefused(
                "`subject` names a single `state`; rows are told apart by `key`."
            )
        target = await self._target(deps.pod_id, asked.decider)
        cap = self._decisions.max_rows()
        async with self._pod.decision_pod_scope(deps) as pod:
            await require_asking(pod, target.decider_id)
            read = None if source is None else await read_rows(pod, deps, request, cap)
            caller = pod.caller
        if read is None:
            decision = await self._decisions.decide(
                caller=caller,
                state=cast(JsonValue, request.state),
                decider=asked.decider,
                definition=asked.definition,
                subject=request.subject,
                options=options,
            )
            return decision_view(decision, decider=asked.label)
        rows = await rows_of(read, cap)
        if not rows:
            return {
                "success": True,
                "decider": asked.label,
                "decided": 0,
                "note": f"`{read.source}` has no rows to decide.",
            }
        result = await self._decisions.decide_rows(
            caller=caller,
            rows=rows,
            decider=asked.decider,
            definition=asked.definition,
            options=options,
            key=read.key,
        )
        view = rows_view(result, decider=asked.label)
        if read.remaining:
            view["remaining"] = read.remaining
            view["next_offset"] = read.next_offset
        if len(rows) <= INLINE_RESULT_ROWS:
            view["results"] = inline_results(result)
            return view
        return await self._with_results_file(deps, view, rows, result, read, asked)

    async def _with_results_file(
        self,
        deps: BaseAgentContext,
        view: JsonObject,
        rows: list[JsonValue],
        result: RowsResult,
        read: Read,
        asked: Judgement,
    ) -> JsonObject:
        content, columns = results_csv(rows, result.rows)
        place = results_place(
            input_path=read.input_path,
            source=read.source,
            decider=asked.label,
            now=datetime.now(UTC),
        )
        try:
            saved, moved = await self._save(deps, place, content)
        except DomainError as exc:
            logger.warning(
                "agent.decision_tools.results_not_saved.degraded",
                rows=len(rows),
                exc_info=True,
            )
            view["success"] = False
            view["error"] = (
                "The rows were decided and the decisions recorded, but the "
                f"results file could not be saved ({exc.message.rstrip('.')}). "
                "Deciding the rows again would ask every one of them again."
            )
            return view
        view["results_file"] = {
            "type": "pod_file",
            "pod_path": saved.pod_path,
            "size_bytes": saved.size_bytes,
            "media_type": "text/csv",
            "columns": list(columns),
        }
        if moved:
            view["note"] = (
                "The input's folder is not yours to write in, so the results "
                f"are in {place.fallback} instead."
            )
        return view

    async def _save(
        self, deps: BaseAgentContext, place: ResultsPlace, content: bytes
    ) -> tuple[SavedFile, bool]:
        """Land the file beside its input, or in the person's own space if refused."""
        try:
            async with self._pod.decision_pod_scope(deps) as pod:
                saved = await pod.write_pod_file(
                    directory=place.directory, name=place.name, content=content
                )
            return saved, False
        except DomainError as exc:
            if place.fallback is None or not _refused(exc):
                raise
        # A fresh unit of work: the refused one was rolled back, not reused.
        async with self._pod.decision_pod_scope(deps) as pod:
            saved = await pod.write_pod_file(
                directory=place.fallback, name=place.name, content=content
            )
        return saved, True

    # --- define_decider ----------------------------------------------------

    async def _define(
        self, deps: BaseAgentContext, request: DefineDeciderRequest
    ) -> JsonObject:
        definition = definition_of(cast(JsonObject, request.definition))
        name = request.name.strip()
        existing = await self._deciders.get(pod_id=deps.pod_id, name=name)
        async with self._pod.decision_pod_scope(deps) as pod:
            if existing is None:
                await pod.require(Permissions.DECIDER_CREATE, None)
            else:
                await pod.require(Permissions.DECIDER_UPDATE, existing.id)
            caller = pod.caller
        saved, warnings = await self._deciders.define(
            caller=caller, name=name, definition=definition
        )
        return defined_view(saved, warnings, created=existing is None)

    # --- test_decider ------------------------------------------------------

    async def _test(
        self, deps: BaseAgentContext, request: DeciderTestRequest
    ) -> JsonObject:
        asked = judgement(
            decider=request.decider, questions=None, definition=request.definition
        )
        if chosen_source(request) is None:
            raise InputRefused("Give sample rows as `items`, a `file` or a `table`.")
        target = await self._target(deps.pod_id, asked.decider)
        known = asked.definition or target.definition
        fields = expected_fields(request.expected, known)
        cap = self._decisions.max_rows()
        async with self._pod.decision_pod_scope(deps) as pod:
            await require_asking(pod, target.decider_id)
            read = await read_rows(pod, deps, request, cap)
            caller = pod.caller
        rows = await rows_of(read, cap)
        if not rows:
            raise InputRefused(f"`{read.source}` has no rows to try the decider on.")
        types = question_types(known)
        held = [hold_out(row, fields, types) for row in rows]
        result = await self._deciders.trial(
            caller=caller,
            samples=[Sample(state=row.state, expected=row.expected) for row in held],
            decider=asked.decider,
            definition=asked.definition,
        )
        labels = [row_label(row, read.key, index) for index, row in enumerate(rows)]
        return trial_view(result, decider=asked.label, labels=labels)

    # --- answer_decision ---------------------------------------------------

    async def _answer(
        self, deps: BaseAgentContext, request: AnswerDecisionRequest
    ) -> JsonObject:
        decision_id = decision_id_of(request.decision_id)
        answers = answers_of(request.answers)
        viewer = Caller(
            user_id=deps.user_id, pod_id=deps.pod_id, organization_id=deps.org_id
        )
        decision = await self._decisions.get_decision(
            caller=viewer, decision_id=decision_id
        )
        decider_id = await self._decider_of(deps.pod_id, decision.decider_name)
        async with self._pod.decision_pod_scope(deps) as pod:
            await require_asking(pod, decider_id)
            caller = pod.caller
        updated = await self._decisions.answer_as_agent(
            caller=caller, decision_id=decision_id, answers=answers
        )
        return answered_view(updated)

    async def _decider_of(self, pod_id: UUID, name: str | None) -> UUID | None:
        """The pod decider a decision was asked of, if it still exists.

        A system or inline decision, and one whose decider has since been
        deleted, is authorized on the pod.
        """
        if name is None or name in self._decisions.system_decider_names():
            return None
        found = await self._deciders.get(pod_id=pod_id, name=name)
        return None if found is None else found.id


def build_decisions_toolset(tools: DecisionTools) -> FunctionToolset[BaseAgentContext]:
    return FunctionToolset[BaseAgentContext](
        tools=[
            tools.decide,
            tools.define_decider,
            tools.test_decider,
            tools.answer_decision,
        ]
    )


decisions_toolset = build_decisions_toolset(
    DecisionTools(
        decisions=ContractDecisions(),
        deciders=ContractDeciders(),
        pod=DatastorePodAccess(),
    )
)
