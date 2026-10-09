"""``fill_form``: fill the visitor's form from the conversation, never send it."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.tools.form_tools import FillFormRequest, build_form_toolset
from app.modules.datastore.contracts.public_rows import (
    OpenTable,
    PublicAudience,
    PublicColumn,
)

pytestmark = pytest.mark.unit

POD = uuid4()
SIGNUPS = OpenTable(
    pod_id=POD,
    name="signups",
    audience=PublicAudience.ANYONE,
    contact_owned=False,
    columns=(
        PublicColumn(name="full_name", type="TEXT", required=True),
        PublicColumn(name="seats", type="INTEGER", required=False),
        PublicColumn(
            name="track", type="ENUM", required=False, options=("Design", "Code")
        ),
        PublicColumn(name="newsletter", type="BOOLEAN", required=False),
    ),
)


def _tool(tables: dict[str, OpenTable]):
    async def read_table(*, pod_id, table):
        assert pod_id == POD
        return tables.get(table)

    async def list_tables(*, pod_id):
        return list(tables)

    toolset = build_form_toolset(
        uow_factory=None, read_table=read_table, list_tables=list_tables
    )
    return toolset.tools["fill_form"].function


def _ctx():
    return SimpleNamespace(deps=SimpleNamespace(pod_id=POD))


async def test_only_open_columns_with_fitting_values_are_filled():
    result = await _tool({"signups": SIGNUPS})(
        _ctx(),
        FillFormRequest(
            table="signups",
            values={
                "full_name": "Priya Shah",
                "seats": 2,
                "track": "Cooking",
                "newsletter": "yes",
                "internal_notes": "VIP",
            },
        ),
    )
    assert result["success"] is True
    assert result["filled"] == {
        "full_name": "Priya Shah",
        "seats": "2",
        "newsletter": True,
    }
    assert result["skipped"] == ["internal_notes", "track"]


async def test_with_one_form_the_table_can_be_left_out():
    result = await _tool({"signups": SIGNUPS})(
        _ctx(), FillFormRequest(values={"full_name": "Priya"})
    )
    assert result["table"] == "signups"
    assert result["filled"] == {"full_name": "Priya"}


async def test_asking_without_answers_says_what_the_form_asks():
    result = await _tool({"signups": SIGNUPS})(_ctx(), FillFormRequest(table="signups"))
    assert result["success"] is False
    assert [a["name"] for a in result["asks"]] == [
        "full_name",
        "seats",
        "track",
        "newsletter",
    ]


async def test_an_unknown_table_lists_the_forms_there_are():
    other = OpenTable(
        pod_id=POD,
        name="feedback",
        audience=PublicAudience.ANYONE,
        contact_owned=False,
        columns=(PublicColumn(name="message", type="TEXT", required=True),),
    )
    result = await _tool({"signups": SIGNUPS, "feedback": other})(
        _ctx(), FillFormRequest(table="members", values={"x": "y"})
    )
    assert result["success"] is False
    assert [form["table"] for form in result["forms"]] == ["signups", "feedback"]
