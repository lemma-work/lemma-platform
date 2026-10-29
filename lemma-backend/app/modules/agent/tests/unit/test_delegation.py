"""Delegating: one call hands out the tasks, one wake brings back every report."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.delegation import (
    FAILED,
    FINISHED,
    MIN_REPORT_CHARS,
    REPORTS_BUDGET_CHARS,
    ChildReport,
    report_payload,
)
from app.modules.agent.domain.subagent_results import (
    combined_metadata,
    combined_results,
)
from app.modules.agent.infrastructure.tools.subagents import delegate as delegation
from app.modules.agent.infrastructure.tools.waiting.models import WaitForRequest
from app.modules.agent.infrastructure.tools.waiting.tools import _target_of
from app.modules.agent.services.subagent_service import SubAgentError

pytestmark = pytest.mark.unit


def _report(status: str = FINISHED, **fields: object) -> ChildReport:
    return ChildReport(
        conversation_id=str(uuid4()),
        run_id=str(uuid4()),
        agent="researcher",
        status=status,
        **fields,
    )


def test_a_failed_child_says_why_and_a_finished_one_says_what() -> None:
    done, failed = report_payload(
        [
            _report(output={"answer": "Vendor A: SSO yes."}),
            _report(FAILED, error="the site refused"),
        ]
    )
    assert done["report"] == "Vendor A: SSO yes."
    assert "error" not in done
    assert failed["status"] == FAILED and failed["error"] == "the site refused"
    assert "report" not in failed


def test_reports_share_one_budget_and_say_where_the_rest_is() -> None:
    reports = [_report(output="x" * REPORTS_BUDGET_CHARS) for _ in range(4)]
    payload = report_payload(reports)
    share = REPORTS_BUDGET_CHARS // 4
    for item in payload:
        text = str(item["report"])
        assert text.startswith("x" * share)
        assert "query_subagents" in text
        assert len(text) < share + 300


def test_many_children_each_keep_a_floor() -> None:
    payload = report_payload([_report(output="y" * 5_000) for _ in range(40)])
    assert all(
        str(item["report"]).startswith("y" * MIN_REPORT_CHARS) for item in payload
    )


def test_several_runs_are_one_target() -> None:
    target = _target_of(WaitForRequest(reason="r", subagent_run_ids=["a", "b", "a"]))
    assert target is not None
    assert target[1] == ["a", "b"]
    # A run set and a process are two kinds of thing: refused.
    assert (
        _target_of(WaitForRequest(reason="r", subagent_run_ids=["a"], process_id="p"))
        is None
    )


def test_owed_results_arrive_as_one_message() -> None:
    text = combined_results(["A finished.", "B failed: no key."])
    assert text.startswith("2 sub-agents finished.")
    assert "A finished." in text and "B failed: no key." in text
    metadata = combined_metadata(
        [
            {
                "source": "subagent_result",
                "subagent_run_id": "r1",
                "subagent_conversation_id": "c1",
            },
            {
                "source": "subagent_result",
                "subagent_run_id": "r2",
                "subagent_conversation_id": "c2",
            },
        ]
    )
    assert metadata["source"] == "subagent_result"
    assert metadata["subagent_run_ids"] == ["r1", "r2"]


class _Service:
    def __init__(self, refuse_after: int) -> None:
        self.spawned: list[dict[str, object]] = []
        self.refuse_after = refuse_after

    async def spawn(self, ctx, **kwargs):
        if len(self.spawned) >= self.refuse_after:
            raise SubAgentError("This run has already delegated to 16 sub-agents")
        self.spawned.append(kwargs)
        return SimpleNamespace(conversation_id=uuid4(), run_id=uuid4())


@pytest.mark.anyio
async def test_without_waiting_it_starts_every_task_and_says_so(monkeypatch) -> None:
    service = _Service(refuse_after=2)
    monkeypatch.setattr(delegation, "_service", lambda: service)
    ctx = SimpleNamespace(tool_call_id="call-1")

    result = await delegation.delegate(
        ctx,
        delegation.DelegateRequest(
            tasks=[
                delegation.DelegateTask(brief="one"),
                delegation.DelegateTask(brief="two", model="fast-model"),
                delegation.DelegateTask(brief="three"),
            ],
            wait="none",
        ),
    )

    assert result["success"] is True
    children = result["children"]
    assert [child.get("error") is None for child in children] == [True, True, False]
    assert "16 sub-agents" in str(children[2]["error"])
    # Keyed per call and task: a resumed turn gets the same children.
    assert [call["tool_call_id"] for call in service.spawned] == [
        "call-1:0",
        "call-1:1",
    ]
    assert service.spawned[1]["model"] == "fast-model"
