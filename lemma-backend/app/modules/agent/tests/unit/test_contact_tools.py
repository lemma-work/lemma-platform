"""A contact's run reads only its contact's rows and acts only for its contact.

The contact always comes from the run, never from the model: these pin that no
argument can name somebody else, and that a run that is not a contact's gets
nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.tools.contact_tools import (
    ContactFunctionRequest,
    ContactRecordsRequest,
    build_contact_toolset,
)
from app.modules.datastore.contracts.contact_rows import ContactRowsUnavailable
from app.modules.function.contracts.contact_functions import ContactFunctionOutcome

pytestmark = pytest.mark.unit

POD = uuid4()
CONTACT = uuid4()


def _ctx(*, contact_id=CONTACT):
    audience = (
        Audience.contact(contact_id) if contact_id is not None else Audience.outsiders()
    )
    return SimpleNamespace(deps=SimpleNamespace(audience=audience, pod_id=POD))


def _run(completed: bool, output=None) -> ContactFunctionOutcome:
    return ContactFunctionOutcome(
        completed=completed,
        output=output,
        status="COMPLETED" if completed else "FAILED",
    )


class _Calls:
    def __init__(self, *, rows=None, run=None, error=None) -> None:
        self.reads: list[dict] = []
        self.runs: list[dict] = []
        self._rows, self._run, self._error = rows or [], run, error

    async def read_rows(self, **kwargs):
        self.reads.append(kwargs)
        if self._error:
            raise self._error
        return self._rows

    async def run_function(self, **kwargs):
        self.runs.append(kwargs)
        return self._run


def _tools(calls: _Calls):
    toolset = build_contact_toolset(
        uow_factory=lambda: None,
        read_rows=calls.read_rows,
        run_function=calls.run_function,
    )
    return {name: tool.function for name, tool in toolset.tools.items()}


async def test_records_are_read_for_the_runs_contact():
    calls = _Calls(rows=[{"id": uuid4(), "status": "shipped"}])

    result = await _tools(calls)["contact_records"](
        _ctx(), ContactRecordsRequest(table="orders")
    )

    assert result["success"] is True
    assert result["count"] == 1
    assert isinstance(result["rows"][0]["id"], str)
    assert calls.reads[0]["contact_id"] == CONTACT
    assert calls.reads[0]["table_name"] == "orders"


async def test_a_table_that_is_not_contact_owned_reads_as_missing():
    calls = _Calls(error=ContactRowsUnavailable("Table 'members' not found"))

    result = await _tools(calls)["contact_records"](
        _ctx(), ContactRecordsRequest(table="members")
    )

    assert result == {"success": False, "error": "Table 'members' not found"}


async def test_a_function_is_told_the_runs_contact_whatever_the_model_says():
    calls = _Calls(run=_run(True, {"ticket": "T-9"}))
    somebody_else = str(uuid4())

    result = await _tools(calls)["contact_function"](
        _ctx(),
        ContactFunctionRequest(
            name="create_ticket", input={"subject": "Late", "contact_id": somebody_else}
        ),
    )

    assert result == {"success": True, "output": {"ticket": "T-9"}}
    assert calls.runs[0]["contact_id"] == CONTACT
    assert calls.runs[0]["name"] == "create_ticket"


async def test_a_function_that_did_not_finish_is_reported_not_guessed():
    calls = _Calls(run=_run(False))

    result = await _tools(calls)["contact_function"](
        _ctx(), ContactFunctionRequest(name="create_ticket")
    )

    assert result["success"] is False


@pytest.mark.parametrize("tool", ["contact_records", "contact_function"])
async def test_a_run_that_is_not_a_contacts_gets_nothing(tool):
    calls = _Calls()
    request = (
        ContactRecordsRequest(table="orders")
        if tool == "contact_records"
        else ContactFunctionRequest(name="create_ticket")
    )

    result = await _tools(calls)[tool](_ctx(contact_id=None), request)

    assert result["success"] is False
    assert calls.reads == [] and calls.runs == []
