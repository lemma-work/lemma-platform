"""Operating a deployment → spend limits.

A limit that nobody can set is a promise that can never be kept: until the
deployment could state its own limits, the refusal path had never run anywhere
(DEV-OPS-004). These scenarios hold both halves of PS-OPS-012 now that the
deployment under test carries one — the probe organization's cap is part of the
stack's configuration, scoped to its slug, so nothing else on the deployment is
limited.
"""

from __future__ import annotations

from uuid import uuid4

from harness import capability, covers, journey, proves, scenario

pytestmark = [
    journey("Operating a deployment"),
    capability("Stay inside the limits"),
]

#: The stack under test configures a zero monthly cap for every organization
#: whose slug carries this prefix (see USAGE_ORG_LIMIT_OVERRIDES_JSON in the
#: backend env), so any model work at all exceeds it. The run suffix keeps the
#: slug unique, because the server under test may outlive a single suite run —
#: its organizations are real rows, not scratch. The prefix is the contract:
#: it is what the setting keys on.
PROBE_ORGANIZATION_PREFIX = "Spend Cap Probe"


@scenario("Work that would exceed the limit is refused, saying which one")
@proves("PS-OPS-012")
@covers(
    "agent.create",
    "agent.conversation.message.send",
)
async def test_work_over_the_limit_is_refused_clearly(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization(
        named=f"{PROBE_ORGANIZATION_PREFIX} {uuid4().hex[:6]}"
    )
    pod = await alice.creates_a_pod()
    agent = await alice.creates_an_agent(in_pod=pod)
    conversation = await alice.starts_a_conversation(
        in_pod=pod, with_agent=agent["name"]
    )

    asked = await alice.api.call(
        "POST",
        f"/pods/{pod['id']}/conversations/{conversation['id']}/messages",
        json={"content": "Say hello."},
    )

    # Refused, not degraded: no silently smaller model, no shortened run, no
    # dropped work — and the refusal says which limit was reached.
    assert asked.status_code == 429, (
        f"model work inside an organization whose monthly cap is 0 answered "
        f"{asked.status_code}: {asked.text[:300]}"
    )
    body = asked.json()
    assert body.get("code") == "USAGE_LIMIT_EXCEEDED", body
    assert "organization" in str(body.get("message", "")).lower(), body
