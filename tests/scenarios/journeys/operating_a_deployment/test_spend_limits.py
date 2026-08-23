"""Operating a deployment → spend limits.

A limit that nobody can set is a promise that can never be kept: until the
deployment could state its own limits, the refusal path had never run anywhere
(DEV-OPS-004). These scenarios hold both halves of PS-OPS-012 now that the
deployment under test carries one — the probe organization's cap is part of the
stack's configuration, scoped to its slug, so nothing else on the deployment is
limited.
"""

from __future__ import annotations


from harness import capability, covers, journey, proves, scenario
from harness.credentials import needs
from harness.environment import SERVER_SPEND_CAPS
from harness.stack import SPEND_CAP_PROBE_SLUG_PREFIX

pytestmark = [
    journey("Operating a deployment"),
    capability("Stay inside the limits"),
]

#: A display name that slugifies to the prefix the deployment caps at zero, so
#: any model work at all exceeds the limit. Derived from the harness constant
#: rather than repeated, because the prefix IS the contract between the
#: scenario and the deployment's configuration — written twice, it would drift
#: and the scenario would silently stop testing anything. The run suffix keeps
#: the slug unique: the server under test may outlive a single suite run, and
#: its organizations are real rows rather than scratch.
PROBE_ORGANIZATION_PREFIX = SPEND_CAP_PROBE_SLUG_PREFIX


@scenario("Work that would exceed the limit is refused, saying which one")
@proves("PS-OPS-012")
@covers(
    "agent.create",
    "agent.conversation.message.send",
)
async def test_work_over_the_limit_is_refused_clearly(world, run):
    needs(SERVER_SPEND_CAPS)
    alice = await world.person("priya")
    # Created for its slug: the deployment caps this organization and no other.
    # The prefix is what the deployment's override matches on, and the run mark
    # goes on the end where it always does — so the cap still lands on this
    # organization and nothing else, and the organization is still traceable to
    # the run that made it. It cannot be deleted afterwards either way.
    await alice.creates_an_organization(named=run.name(PROBE_ORGANIZATION_PREFIX))
    pod = await alice.creates_a_pod(named=run.name("pod"))
    agent = await alice.creates_an_agent(in_pod=pod)
    conversation = await alice.starts_a_conversation(in_pod=pod, with_agent=agent["name"])

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
