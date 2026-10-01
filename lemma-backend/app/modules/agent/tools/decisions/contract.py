"""The decisions module, as these tools reach it: every runtime call into it.

Each name is imported inside the function that uses it, never at module
level. `decisions.contracts.decide` builds its services when it is imported,
and that build reaches `agent.tools.registry` -- and so this package --
through `agent.contracts.model_runtime`. Imported at the top here, any process
that imported decisions first would ask the half-imported contract for names
it has not reached yet. By the time a tool runs, both are whole.

The parsing helpers live here for the same reason: they are the decisions
module's own models, validating the agent's input so its errors come back as
the module words them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import JsonValue

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.tools.decisions.models import InputRefused
from app.modules.agent.tools.decisions.seams import (
    AnswerValue,
    CallOptions,
    Caller,
    Sample,
)

if TYPE_CHECKING:
    from app.modules.decisions.contracts.decide import (
        Asker,
        DeciderDefinition,
        DecisionEntity,
        Option,
        RowsResult,
    )
    from app.modules.decisions.contracts.deciders import (
        DeciderEntity,
        SampleResult,
    )

#: The description an inline `questions` call is filed under. Inline decisions
#: are keyed on a digest of the whole definition, so this must never vary.
INLINE_DESCRIPTION = "Questions an agent asked without a saved decider."

#: How many of a pod's deciders a lookup reads to name the ones it has.
_DECIDERS_READ = 50


def parse_definition(raw: JsonObject) -> DeciderDefinition:
    """The definition, validated; `pydantic.ValidationError` says what is wrong."""
    from app.modules.decisions.contracts.decide import DeciderDefinition

    return DeciderDefinition.model_validate(raw)


def inline_definition(questions: JsonObject, guidance: str | None) -> DeciderDefinition:
    """A definition around questions passed without one."""
    raw: JsonObject = {"description": INLINE_DESCRIPTION, "questions": questions}
    if guidance and guidance.strip():
        raw["guidance"] = guidance.strip()
    return parse_definition(raw)


def parse_options(raw: JsonObject) -> dict[str, dict[str, Option]]:
    """Per question, the options added for one call, `key: description` accepted."""
    from app.modules.decisions.contracts.decide import Option

    parsed: dict[str, dict[str, Option]] = {}
    for question, options in raw.items():
        if not isinstance(options, dict):
            raise InputRefused(
                f"`options.{question}` must be an object of option key -> description."
            )
        parsed[question] = {
            str(key): Option.model_validate(
                {"description": option} if isinstance(option, str) else option
            )
            for key, option in options.items()
        }
    return parsed


def _asker(caller: Caller) -> Asker:
    from app.modules.decisions.contracts.decide import Asker

    return Asker(
        user_id=caller.user_id,
        pod_id=caller.pod_id,
        organization_id=caller.organization_id,
        visibility="POD",
    )


class ContractDecisions:
    """`DecisionAsking` over `decisions.contracts.decide`."""

    def max_rows(self) -> int:
        from app.modules.decisions.contracts.decide import max_rows

        return max_rows()

    def system_decider_names(self) -> list[str]:
        from app.modules.decisions.contracts.deciders import system_decider_names

        return system_decider_names()

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
        from app.modules.decisions.contracts.decide import decide

        return await decide(
            state=state,
            asker=_asker(caller),
            decider=decider,
            definition=definition,
            subject=subject,
            options=options,
        )

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
        from app.modules.decisions.contracts.decide import decide_rows

        return await decide_rows(
            rows=rows,
            asker=_asker(caller),
            decider=decider,
            definition=definition,
            options=options,
            id_field=key,
        )

    async def get_decision(
        self, *, caller: Caller, decision_id: UUID
    ) -> DecisionEntity:
        from app.modules.decisions.contracts.decide import get_decision

        return await get_decision(
            decision_id=decision_id, pod_id=caller.pod_id, viewer_id=caller.user_id
        )

    async def answer_as_agent(
        self,
        *,
        caller: Caller,
        decision_id: UUID,
        answers: Mapping[str, AnswerValue],
    ) -> DecisionEntity:
        from app.modules.decisions.contracts.decide import Rung, answer

        return await answer(
            decision_id=decision_id,
            pod_id=caller.pod_id,
            answers=answers,
            by=Rung.AGENT,
            user_id=caller.user_id,
        )


class ContractDeciders:
    """`DeciderBook` over `decisions.contracts.deciders`."""

    async def get(self, *, pod_id: UUID, name: str) -> DeciderEntity | None:
        from app.modules.decisions.contracts.deciders import get_decider

        return await get_decider(pod_id=pod_id, name=name)

    async def names(self, *, pod_id: UUID) -> list[str]:
        from app.modules.decisions.contracts.deciders import list_deciders

        found = await list_deciders(pod_id=pod_id, limit=_DECIDERS_READ)
        return [decider.name for decider in found]

    async def define(
        self, *, caller: Caller, name: str, definition: DeciderDefinition
    ) -> tuple[DeciderEntity, list[str]]:
        from app.modules.decisions.contracts.deciders import define_decider

        return await define_decider(
            pod_id=caller.pod_id,
            user_id=caller.user_id,
            name=name,
            definition=definition,
        )

    async def trial(
        self,
        *,
        caller: Caller,
        samples: Sequence[Sample],
        decider: str | None,
        definition: DeciderDefinition | None,
    ) -> SampleResult:
        from app.modules.decisions.contracts.deciders import SampleRow, test_decider

        return await test_decider(
            rows=[
                SampleRow(state=sample.state, expected=sample.expected)
                for sample in samples
            ],
            asker=_asker(caller),
            decider=decider,
            definition=definition,
        )
