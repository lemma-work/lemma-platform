"""An oversized instruction is refused without losing a person's saved work."""

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Agents and conversations"), capability("Define an agent")]


@scenario("A person gets a clear size limit without losing their saved instruction")
@proves("PS-AGENT-001")
@covers("agent.create", "agent.update", "agent.get")
async def test_an_instruction_has_a_bounded_text_budget(world, run):
    owner = await world.person("daniel")
    pod = await owner.creates_a_pod(named=run.name("instruction-budget"))
    saved = "Follow the documented workflow. " * 1_000
    agent = await owner.creates_an_agent(in_pod=pod, instruction=saved)
    path = f"/pods/{pod['id']}/agents/{agent['name']}"

    rejected = await owner.api.expect(
        "PATCH", path, status=422, json={"instruction": "x" * 60_001}
    )
    assert "60000" in str(rejected)
    reopened = await owner.api.get(path)
    assert reopened["instruction"] == saved

    boundary = "x" * 60_000
    await owner.api.expect("PATCH", path, status=200, json={"instruction": boundary})
    reopened = await owner.api.get(path)
    assert reopened["instruction"] == boundary
