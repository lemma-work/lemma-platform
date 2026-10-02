"""The decisions toolset, driven through the tool dispatcher with its ports faked.

The decisions contract and the pod are faked at `DecisionTools`' constructor
(`decision_tool_fakes.py`), so what runs here is everything the tools
themselves decide: which permission is checked on what, where rows come from
and how many are allowed, that no unit of work is held while a decision is
asked, where a results file lands, and what the model is told.
"""

from __future__ import annotations

import csv
import io
import json
from uuid import UUID, uuid4

import pytest

from app.core.authorization.permissions import Permissions
from app.modules.agent.capabilities.assembler import (
    _deferred_capability,
    _partition_core_extra,
)
from app.modules.agent.capabilities.instructed_toolset import (
    InstructedToolsetCapability,
)
from app.modules.agent.domain.prompts import load_toolset_fragment
from app.modules.agent.domain.value_objects import AgentToolset, JsonObject
from app.modules.agent.services.openai_schema_compat import (
    InlineDefsOpenAIJsonSchemaTransformer,
)
from app.modules.agent.tests.unit.decision_tool_fakes import (
    FakeDeciders,
    FakeDecisions,
    FakePod,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.decisions.pydantic_adapter import (
    DecisionTools,
    build_decisions_toolset,
    decisions_toolset,
)
from app.modules.agent.tools.dispatcher import AgentToolDispatcher
from app.modules.agent.tools.registry import (
    POD_DEFAULT_AGENT_TOOLSETS,
    resolve_agent_toolsets,
)
from app.modules.agent.tools.toolset_selection import DECLARABLE_TOOLSETS
from app.modules.decisions.contracts.decide import DeciderDefinition
from app.modules.decisions.contracts.deciders import DeciderEntity

pytestmark = pytest.mark.unit

_TRIAGE = DeciderDefinition.model_validate(
    {
        "description": "Whether a ticket is urgent.",
        "questions": {"urgent": {"type": "yes_no", "prompt": "Is this urgent?"}},
    }
)
_URGENT_QUESTION = {"urgent": {"type": "yes_no", "prompt": "Is this urgent?"}}


class _Harness:
    """The toolset over fakes, called the way every remote caller calls it."""

    def __init__(
        self,
        *,
        pod: FakePod | None = None,
        cap: int = 500,
        saved: tuple[DeciderEntity, ...] = (),
    ) -> None:
        self.pod = pod or FakePod()
        self.decisions = FakeDecisions(
            cap=cap, open_sessions=lambda: self.pod.open_sessions
        )
        self.deciders = FakeDeciders(saved, warnings=["No fallback."])
        self.deps = BaseAgentContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            pod_cwd="/me/c/2026-10-01/triage",
        )
        self._toolset = build_decisions_toolset(
            DecisionTools(
                decisions=self.decisions, deciders=self.deciders, pod=self.pod
            )
        )

    async def call(self, tool: str, /, **arguments: object) -> JsonObject:
        # No unit of work is ever opened: with the toolsets passed in, the
        # dispatcher never reaches the assembler that would use one.
        dispatcher = AgentToolDispatcher(uow_factory=None)  # type: ignore[arg-type]  # see above
        result = await dispatcher.call_tool(
            ctx=self.deps,
            toolsets=[self._toolset],
            name=tool,
            arguments=dict(arguments),
        )
        assert isinstance(result, dict)
        return result


def _saved(name: str = "triage") -> DeciderEntity:
    return DeciderEntity(pod_id=uuid4(), name=name, definition=_TRIAGE)


def _csv(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def _tickets(count: int) -> list[dict[str, str]]:
    return [
        {
            "id": str(number),
            "subject": "URGENT: site down" if number % 3 == 0 else "hello",
        }
        for number in range(1, count + 1)
    ]


def test_the_toolset_is_the_four_decision_tools() -> None:
    assert set(decisions_toolset.tools) == {
        "decide",
        "define_decider",
        "test_decider",
        "answer_decision",
    }
    assert resolve_agent_toolsets([AgentToolset.DECISIONS]) == [decisions_toolset]


def test_a_named_agent_declares_it_and_the_pod_assistant_always_has_it() -> None:
    assert AgentToolset.DECISIONS in DECLARABLE_TOOLSETS
    assert AgentToolset.DECISIONS in POD_DEFAULT_AGENT_TOOLSETS


def test_the_pod_assistant_searches_for_the_tools_but_keeps_their_contract() -> None:
    """Deferral hides the schemas, never the fragment: what it teaches is when a
    judgement belongs to `decide` rather than to the agent's own reading."""
    core, extra = _partition_core_extra([decisions_toolset], is_pod_default=True)
    capability = _deferred_capability(decisions_toolset)

    assert (core, extra) == ([], [decisions_toolset])
    assert isinstance(capability, InstructedToolsetCapability)
    assert capability.get_instructions() == load_toolset_fragment(
        AgentToolset.DECISIONS
    )
    assert "`decide`" in capability.get_instructions()


@pytest.mark.parametrize(
    ("tool", "argument"),
    [
        ("decide", "state"),
        ("decide", "questions"),
        ("decide", "definition"),
        ("decide", "options"),
        ("define_decider", "definition"),
        ("test_decider", "expected"),
        ("answer_decision", "answers"),
    ],
)
def test_a_free_form_argument_also_takes_a_json_string(
    tool: str, argument: str
) -> None:
    """A dynamic-key object reaches the model as `properties: {}`, which many
    read as "no fields" and fill with `{}`. The string form is the way out, and
    it has to survive the provider-side schema transform."""
    schema = decisions_toolset.tools[tool].function_schema.json_schema
    transformed = InlineDefsOpenAIJsonSchemaTransformer(schema, strict=None).walk()

    branches = transformed["properties"][argument]["anyOf"]

    assert {"object", "string"} <= {branch.get("type") for branch in branches}


@pytest.mark.asyncio
async def test_decide_answers_one_state_with_who_answered_and_its_decision_id() -> None:
    harness = _Harness()

    result = await harness.call(
        "decide", questions=_URGENT_QUESTION, state={"subject": "URGENT: site down"}
    )

    assert result["success"] is True
    assert result["answers"] == {"urgent": {"value": True, "by": "rules"}}
    assert UUID(str(result["decision_id"])) in harness.decisions.decisions
    assert result["decider"] == "inline"


@pytest.mark.asyncio
async def test_decide_needs_a_grant_only_for_a_saved_decider() -> None:
    """Inline and system questions read only what the agent passes in."""
    saved = _saved()
    harness = _Harness(saved=(saved,))

    await harness.call("decide", questions=_URGENT_QUESTION, state={"subject": "hi"})
    await harness.call(
        "decide", decider="system:reply_to_question", state={"reply": "1"}
    )
    await harness.call("decide", decider="triage", state={"subject": "hi"})

    assert harness.pod.checks == [(Permissions.DECIDER_EXECUTE, saved.id)]


@pytest.mark.asyncio
async def test_decide_without_the_grant_asks_for_approval_and_asks_nothing() -> None:
    harness = _Harness(
        saved=(_saved(),), pod=FakePod(refuse=[Permissions.DECIDER_EXECUTE])
    )

    result = await harness.call("decide", decider="triage", state={"subject": "hi"})

    assert result["success"] is False
    assert result["needs_approval"] is True
    assert result["approval"]["tool_name"] == "decide"
    assert result["approval"]["permission_ids"] == [Permissions.DECIDER_EXECUTE]
    assert harness.decisions.asked == []


@pytest.mark.asyncio
async def test_decide_holds_no_unit_of_work_open_while_it_asks() -> None:
    harness = _Harness(pod=FakePod(files={"/me/tickets.csv": _csv(_tickets(40))}))

    await harness.call("decide", questions=_URGENT_QUESTION, file="/me/tickets.csv")
    await harness.call("decide", questions=_URGENT_QUESTION, state={"subject": "hi"})

    assert len(harness.decisions.sessions_open_while_asking) == 41
    assert set(harness.decisions.sessions_open_while_asking) == {0}


@pytest.mark.asyncio
async def test_decide_over_inline_items_counts_answers_and_returns_them_inline() -> (
    None
):
    harness = _Harness()

    result = await harness.call(
        "decide",
        questions=_URGENT_QUESTION,
        items=[{"subject": "URGENT: down"}, {"subject": "thanks"}, "urgent please"],
    )

    assert result["decided"] == 3
    assert result["counts"] == {"urgent": {"True": 2, "False": 1}}
    assert result["answered_by"] == {"rules": 3}
    assert [row["answers"] for row in result["results"]] == [
        {"urgent": True},
        {"urgent": False},
        {"urgent": True},
    ]
    assert "results_file" not in result


@pytest.mark.asyncio
async def test_decide_over_a_large_csv_writes_the_results_beside_it() -> None:
    harness = _Harness(pod=FakePod(files={"/sales/tickets.csv": _csv(_tickets(30))}))

    result = await harness.call(
        "decide", questions=_URGENT_QUESTION, file="/sales/tickets.csv", key="id"
    )

    reference = result["results_file"]
    assert reference["type"] == "pod_file"
    assert reference["pod_path"].startswith("/sales/tickets.decided-")
    assert "results" not in result
    written = list(
        csv.DictReader(io.StringIO(harness.pod.written[reference["pod_path"]].decode()))
    )
    assert len(written) == 30
    assert list(written[0]) == ["id", "subject", "urgent", "decision_id"]
    assert written[2]["urgent"] == "true"
    assert written[0]["urgent"] == "false"


@pytest.mark.asyncio
async def test_decide_saves_the_results_in_me_when_the_input_folder_refuses_them() -> (
    None
):
    pod = FakePod(
        files={"/sales/tickets.csv": _csv(_tickets(30))}, unwritable=["/sales"]
    )
    harness = _Harness(pod=pod)

    result = await harness.call(
        "decide", questions=_URGENT_QUESTION, file="/sales/tickets.csv"
    )

    assert result["success"] is True
    assert result["results_file"]["pod_path"].startswith(
        "/me/decisions/tickets.decided-"
    )
    assert "/me/decisions" in result["note"]


@pytest.mark.asyncio
async def test_decide_says_the_decisions_stand_when_no_results_file_can_be_saved() -> (
    None
):
    pod = FakePod(
        files={"/sales/tickets.csv": _csv(_tickets(30))},
        unwritable=["/sales", "/me/decisions"],
    )
    harness = _Harness(pod=pod)

    result = await harness.call(
        "decide", questions=_URGENT_QUESTION, file="/sales/tickets.csv"
    )

    assert result["success"] is False
    assert "decisions recorded" in result["error"]
    assert "would ask every one of them again" in result["error"]
    assert result["counts"] == {"urgent": {"True": 10, "False": 20}}
    assert pod.written == {}


@pytest.mark.asyncio
async def test_decide_refuses_a_file_over_the_row_limit_before_asking_anything() -> (
    None
):
    harness = _Harness(pod=FakePod(files={"/me/t.jsonl": b'{"a": 1}\n' * 4}), cap=3)

    result = await harness.call(
        "decide", questions=_URGENT_QUESTION, file="/me/t.jsonl"
    )

    assert result["success"] is False
    assert "more than 3 rows" in result["error"]
    assert harness.decisions.asked == []


@pytest.mark.asyncio
async def test_decide_refuses_a_table_over_the_limit_and_takes_it_in_batches() -> None:
    rows = [{"id": str(number), "subject": "hi"} for number in range(12)]
    harness = _Harness(pod=FakePod(tables={"tickets": rows}), cap=5)

    refused = await harness.call(
        "decide", questions=_URGENT_QUESTION, table={"table_name": "tickets"}
    )
    batch = await harness.call(
        "decide",
        questions=_URGENT_QUESTION,
        table={"table_name": "tickets", "limit": 5, "offset": 5},
    )

    assert refused["success"] is False
    assert "12 rows of `tickets` match" in refused["error"]
    assert batch["decided"] == 5
    assert (batch["remaining"], batch["next_offset"]) == (2, 10)
    assert [row["row"] for row in batch["results"]] == ["5", "6", "7", "8", "9"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"questions": _URGENT_QUESTION},
        {"questions": _URGENT_QUESTION, "state": {"a": 1}, "items": [{"a": 1}]},
        {"questions": _URGENT_QUESTION, "items": [{"a": 1}], "file": "/me/a.csv"},
        {"decider": "triage", "questions": _URGENT_QUESTION, "state": {"a": 1}},
        {"state": {"a": 1}},
        {"questions": _URGENT_QUESTION, "items": [{"a": 1}], "subject": "t:1"},
        {"decider": "triage", "guidance": "Be strict.", "state": {"a": 1}},
    ],
)
@pytest.mark.asyncio
async def test_decide_needs_one_thing_to_judge_and_one_way_to_name_the_judgement(
    arguments: dict[str, object],
) -> None:
    harness = _Harness()

    result = await harness.call("decide", **arguments)

    assert result["success"] is False
    assert harness.pod.checks == []
    assert harness.decisions.asked == []


@pytest.mark.asyncio
async def test_decide_names_the_deciders_there_are_when_one_is_missing() -> None:
    harness = _Harness(saved=(_saved("lead-fit"),))

    result = await harness.call("decide", decider="lead-fitt", state={"a": 1})

    assert result["success"] is False
    assert "No decider named 'lead-fitt'" in result["error"]
    assert "lead-fit" in result["error"]
    assert "system:reply_to_question" in result["error"]


@pytest.mark.asyncio
async def test_decide_takes_its_state_and_questions_as_json_strings() -> None:
    harness = _Harness()

    as_json = await harness.call(
        "decide",
        questions=json.dumps(_URGENT_QUESTION),
        state=json.dumps({"subject": "URGENT"}),
    )
    as_text = await harness.call(
        "decide", questions=_URGENT_QUESTION, state="not urgent at all, but urgent"
    )

    assert as_json["answers"]["urgent"]["value"] is True
    assert harness.decisions.asked[0] == {"subject": "URGENT"}
    assert harness.decisions.asked[1] == "not urgent at all, but urgent"
    assert as_text["success"] is True


@pytest.mark.asyncio
async def test_an_invalid_question_is_said_back_as_what_is_wrong_with_it() -> None:
    harness = _Harness()

    result = await harness.call(
        "decide",
        questions={"size": {"type": "scale", "prompt": "How big?", "levels": ["one"]}},
        state={"a": 1},
    )

    assert result["success"] is False
    assert result["error"].startswith(
        "`questions` is not valid: questions.size.scale.levels"
    )


@pytest.mark.asyncio
async def test_decide_adds_the_options_passed_with_the_call() -> None:
    harness = _Harness()

    await harness.call(
        "decide",
        questions={"pod": {"type": "choice", "prompt": "Which pod?"}},
        options={
            "pod": {"sales": "The sales pod", "ops": {"description": "Operations"}}
        },
        state={"text": "the sales one"},
    )

    (options,) = harness.decisions.options_seen
    assert {key: option.description for key, option in options["pod"].items()} == {
        "sales": "The sales pod",
        "ops": "Operations",
    }


@pytest.mark.asyncio
async def test_define_decider_creates_with_create_and_revises_with_update_on_it() -> (
    None
):
    harness = _Harness()
    definition = {
        "description": "Where a lead stands.",
        "questions": {
            "fit": {
                "type": "choice",
                "prompt": "Is this lead a fit?",
                "options": {"yes": "A clear fit.", "no": "Not a fit."},
            }
        },
    }

    created = await harness.call(
        "define_decider", name="lead-fit", definition=definition
    )
    revised = await harness.call(
        "define_decider", name="lead-fit", definition=json.dumps(definition)
    )

    saved_id = harness.deciders.saved["lead-fit"].id
    assert harness.pod.checks == [
        (Permissions.DECIDER_CREATE, None),
        (Permissions.DECIDER_UPDATE, saved_id),
    ]
    assert (created["created"], created["version"]) == (True, 1)
    assert (revised["created"], revised["version"]) == (False, 2)
    options = created["definition"]["questions"]["fit"]["options"]
    assert options["yes"]["description"] == "A clear fit."
    assert created["warnings"] == ["No fallback."]


@pytest.mark.asyncio
async def test_define_decider_says_what_is_wrong_with_a_definition() -> None:
    harness = _Harness()

    result = await harness.call(
        "define_decider", name="broken", definition={"questions": _URGENT_QUESTION}
    )

    assert result["success"] is False
    assert "`definition` is not valid: description" in result["error"]
    assert harness.pod.checks == []


@pytest.mark.asyncio
async def test_trying_a_decider_hides_the_known_answers_and_reports_agreement() -> None:
    labelled = [
        {"id": "a", "subject": "URGENT: down", "label": "true"},
        {"id": "b", "subject": "hello", "label": "true"},
        {"id": "c", "subject": "hello", "label": ""},
    ]
    harness = _Harness(
        pod=FakePod(files={"/me/labelled.csv": _csv(labelled)}), saved=(_saved(),)
    )

    result = await harness.call(
        "test_decider",
        decider="triage",
        file="/me/labelled.csv",
        key="id",
        expected={"urgent": "label"},
    )

    (samples,) = harness.deciders.trials
    assert [sample.state for sample in samples] == [
        {"id": "a", "subject": "URGENT: down"},
        {"id": "b", "subject": "hello"},
        {"id": "c", "subject": "hello"},
    ]
    assert [sample.expected for sample in samples] == [
        {"urgent": True},
        {"urgent": True},
        None,
    ]
    assert result["agreement"] == {"urgent": {"agreed": 1, "total": 2, "rate": 0.5}}
    assert result["disagreements"]["items"][0]["row"] == "b"
    assert result["recorded"] is False
    assert harness.decisions.asked == []


@pytest.mark.asyncio
async def test_trying_a_decider_refuses_expectations_for_questions_it_does_not_ask() -> (
    None
):
    harness = _Harness(saved=(_saved(),))

    result = await harness.call(
        "test_decider",
        decider="triage",
        items=[{"subject": "x", "label": "true"}],
        expected={"priority": "label"},
    )

    assert result["success"] is False
    assert "priority" in result["error"] and "urgent" in result["error"]


@pytest.mark.asyncio
async def test_answer_decision_records_the_agent_and_authorizes_on_its_decider() -> (
    None
):
    saved = _saved()
    harness = _Harness(saved=(saved,))
    asked = await harness.call("decide", decider="triage", state={"subject": "hi"})

    result = await harness.call(
        "answer_decision",
        decision_id=asked["decision_id"],
        answers=json.dumps({"urgent": True}),
    )

    assert harness.decisions.agent_answers == [
        (UUID(str(asked["decision_id"])), {"urgent": True})
    ]
    assert result["answers"]["urgent"] == {"value": True, "by": "agent"}
    assert harness.pod.checks[-1] == (Permissions.DECIDER_EXECUTE, saved.id)
    assert "never becomes an example" in result["note"]


@pytest.mark.asyncio
async def test_answer_decision_refuses_what_is_not_a_decision_id() -> None:
    harness = _Harness()

    result = await harness.call(
        "answer_decision", decision_id="the last one", answers={"a": "b"}
    )

    assert result["success"] is False
    assert "is not a decision id" in result["error"]
    assert harness.decisions.agent_answers == []
