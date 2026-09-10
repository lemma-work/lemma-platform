"""An editor can test an earlier contract and restore it as the live revision."""

import pytest
from harness import capability, covers, journey, proves, scenario

pytestmark = [
    journey("Automating work"),
    capability("Build a function"),
    pytest.mark.sandbox,
]


def _code(field: str) -> str:
    return f"""#input_type_name: Input
#output_type_name: Output
#function_name: run
from pydantic import BaseModel
class Input(BaseModel):
    {field}: int
class Output(BaseModel):
    value: int
async def run(ctx, data: Input) -> Output:
    return Output(value=data.{field} + 1)
"""


@scenario("An editor tests an earlier function contract without promoting it")
@proves("PS-FUNC-004")
@covers(
    "function.create",
    "function.update",
    "function.revision.list",
    "function.revision.get",
    "function.revision.promote",
    "function.run",
)
async def test_a_pinned_run_uses_its_own_input_contract(world, run):
    owner = await world.person("daniel")
    pod = await owner.creates_a_pod(named=run.name("function-versions"))
    original = _code("historical")
    function = await owner.creates_a_function(in_pod=pod, code=original)
    base = f"/pods/{pod['id']}/functions/{function['name']}"
    await owner.api.expect("PATCH", base, status=200, json={"code": _code("current")})
    history = await owner.api.get(base + "/revisions")
    assert [item["revision_number"] for item in history["items"]] == [2, 1]
    previous = await owner.api.get(base + "/revisions/r1")
    assert previous["code"] == original
    execution = await owner.api.post(
        base + "/runs",
        status=200,
        json={"revision": "r1", "input_data": {"historical": 4}},
    )
    assert execution["status"] == "COMPLETED", execution
    assert execution["output_data"] == {"value": 5}
    assert execution["revision_hash"] == previous["revision_hash"]
    history = await owner.api.get(base + "/revisions")
    assert history["items"][0]["is_live"], (
        "a pinned run must not change the live revision"
    )
    restored = await owner.api.post(base + "/revisions/r1/promote")
    assert restored["schema_changed"] is True
    assert restored["revision"]["is_live"] is True
