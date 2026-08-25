"""The platform's own services — icons, tools, agent hosts, and the workspace.

Small surfaces that belong to no one journey, and are easy to leave untested
precisely because of that. Several are covered through their unconfigured or
refusal path, which is the behaviour that matters when a deployment has not
turned the feature on.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario

pytestmark = [journey("Operating a deployment"), capability("Platform services")]

#: A one-pixel PNG, so an icon upload carries something a decoder accepts.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c63000100000500010d0a2db400000"
    "00049454e44ae426082"
)


@pytest.fixture
async def person(world):
    alice = await world.person("daniel")
    return alice


@scenario("A person uploads an icon and it is served back")
@proves("PS-POD-003")
@covers("icon.upload", "icon.public.get")
async def test_an_icon_round_trips(world, person):
    uploaded = await person.api.post(
        "/icons/upload",
        files={"file": ("icon.png", TINY_PNG, "image/png")},
    )

    url = uploaded.get("url") or uploaded.get("icon_url") or ""
    assert url, uploaded
    # Icons are public by design — they appear beside a pod before anyone signs
    # in — so this is fetched with no credentials at all.
    anonymous = await world.new_person("anonymous", sign_up=False)
    served = await anonymous.api.call("GET", url)
    assert served.status_code == 200, (url, served.status_code)


@scenario("A person reports a problem with a tool")
@proves("PS-OPS-031")
@covers("agent.tool.report_feedback")
async def test_feedback_can_be_reported(person):
    response = await person.api.call(
        "POST",
        "/tools/report-feedback",
        json={
            "subject": "Table create rejects a valid column",
            # One of cli|skill|platform|docs|other. It used to say "BUG",
            # which the API rejects with a 422 — and the assertion below was
            # `< 500`, so the scenario stayed green while proving only that
            # the endpoint refuses malformed input.
            "category": "platform",
            "issue_encountered": "A column named `title` was refused.",
            "expected_behavior": "It should be accepted.",
            "actual_behavior": "A validation error.",
        },
    )

    assert response.status_code == 201, (
        f"reporting a broken tool answered {response.status_code}, not 201. "
        f"Asserted exactly, because "
        f"`< 500` passes for a 400, a 403 and a 404 — every "
        f"way this can be broken except the one it was checking for: "
        f"{response.text[:300]}"
    )


@scenario("Web search is refused rather than silently empty when unconfigured")
@proves("PS-OPS-030")
@covers("agent.tool.web_search")
async def test_web_search_says_when_it_is_unavailable(person):
    response = await person.api.call(
        "POST", "/tools/web-search", json={"query": "lemma platform", "max_results": 3}
    )
    body = response.json() if response.status_code == 200 else {}

    # This promise is about a deployment with *no* search provider, which is the
    # state any self-hosted install starts in and the state a CI runner is in.
    # Where a provider is configured and answering, there is nothing here to
    # judge — and asserting anyway would make the outcome depend on whether the
    # machine running the suite happens to have one.
    if body.get("results"):
        pytest.skip(
            "this deployment has a working search provider; the promise under "
            "test is about one that does not"
        )

    # So: no results. Whatever else it says, it must not report success — the
    # caller cannot tell "nothing exists" from "nothing was looked at", and the
    # two lead to opposite decisions.
    assert not body.get("success"), (
        f"web search found nothing and called it a success, so a caller is told "
        f"the web is empty on the subject: {body}"
    )


@scenario("A person pairs a machine, lists it, and revokes it")
@proves("PS-AGENT-040")
@covers("agent.host.pairing.create", "agent.host.list", "agent.host.revoke")
async def test_an_agent_host_can_be_paired_and_revoked(person):
    pairing = await person.api.post(
        "/me/runtime/agent-host-pairings", json={"display_name": "Alice's laptop"}
    )

    assert pairing, pairing
    hosts = await person.api.get("/me/runtime/agent-hosts")
    listed = hosts if isinstance(hosts, list) else hosts.get("items", [])
    assert isinstance(listed, list), hosts

    host_id = pairing.get("host_id") or pairing.get("id")
    if host_id and any(str(h.get("id")) == str(host_id) for h in listed):
        await person.api.delete(f"/me/runtime/agent-hosts/{host_id}")
        after = await person.api.get("/me/runtime/agent-hosts")
        remaining = after if isinstance(after, list) else after.get("items", [])
        assert not any(str(h.get("id")) == str(host_id) for h in remaining), remaining


@scenario("An unpaired machine cannot poll for work")
@proves("PS-AGENT-041")
@covers("agent.host.poll", "agent.host.events.append")
async def test_an_unpaired_host_is_refused(world):
    anonymous = await world.new_person("anonymous", sign_up=False)

    polled = await anonymous.api.call("POST", "/agent-host/poll", json={})
    appended = await anonymous.api.call("POST", "/agent-host/events:append", json={})

    assert polled.status_code >= 400, polled.status_code
    assert appended.status_code >= 400, appended.status_code


# Asking for browser access provisions a workspace when it can, so this needs
# the sandbox images the way a function scenario does. It sat in the fast lane
# for a while and passed — on machines that happened to have the images built,
# and nowhere else. A suite whose result depends on what is in the local Docker
# cache is exactly what the sandbox marker exists to prevent.
@pytest.mark.sandbox
@scenario("Asking for browser access answers, rather than failing open or falling over")
@proves("PS-FUNC-002")
@covers("workspace.browser.access")
async def test_browser_access_is_granted_or_refused_but_never_crashes(person):
    """Two ways this goes wrong, and `< 500` alone caught neither.

    It was named "refused without a workspace to open" and asserted only
    `status_code < 500` — which passes on a 200, so a build that handed out
    browser access to anyone would have kept it green. And a 5xx is not a
    refusal either: it is the platform failing to answer, which under
    `PS-FUNC-002` is the case a function must be able to see and report rather
    than hang on.

    So both ends are pinned. Granted is a legitimate answer here — the request
    provisions a workspace when it can — but it has to come with somewhere to
    go, and a refusal has to read as a refusal.
    """
    response = await person.api.call(
        "POST", "/workspace/apps/browser/access", json={"ttl_seconds": 60}
    )

    assert response.status_code < 500, (
        f"asking for browser access answered {response.status_code}. A server "
        f"error is not a refusal — a function cannot report it usefully, and "
        f"in this lane it usually means the workspace image is missing "
        f"(`make scenarios-images`): {response.text[:300]}"
    )
    if response.status_code < 400:
        body = response.json()
        assert body.get("url") or body.get("access_url"), (
            f"browser access was granted with nowhere to go: {body}"
        )
    else:
        assert response.status_code in {400, 403, 404, 409}, (
            f"the refusal answered {response.status_code}, which reads as "
            f"neither a grant nor a refusal a caller can act on"
        )


@scenario("An embed token is refused for a result that does not exist")
@proves("PS-AGENT-031")
@covers("widget.embed_token")
async def test_an_embed_token_needs_a_real_result(person):
    pod = await person.works_in("company-wide")
    agent = await person.creates_an_agent(in_pod=pod)
    conversation = await person.starts_a_conversation(
        in_pod=pod, with_agent=agent["name"]
    )

    response = await person.api.call(
        "POST",
        f"/pods/{pod['id']}/widgets/{conversation['id']}/no-such-tool-call/embed-token",
    )

    assert response.status_code >= 400, (
        f"a token for a result that was never produced must be refused "
        f"({response.status_code})"
    )


@scenario("Promoting a result that does not exist into an app is refused")
@proves("PS-PACK-030")
@covers("app.create_from_widget")
async def test_promoting_a_missing_result_is_refused(person):
    pod = await person.works_in("company-wide")
    agent = await person.creates_an_agent(in_pod=pod)
    conversation = await person.starts_a_conversation(
        in_pod=pod, with_agent=agent["name"]
    )

    response = await person.api.call(
        "POST",
        f"/pods/{pod['id']}/apps/from-widget",
        json={
            "name": "promoted_app",
            "conversation_id": str(conversation["id"]),
            "tool_call_id": "no-such-tool-call",
        },
    )

    assert response.status_code >= 400, response.status_code


@scenario("An unpaired machine cannot complete a pairing or publish harnesses")
@proves("PS-AGENT-040")
@covers(
    "agent.host.pairing.complete",
    "agent.host.harnesses.publish",
    "agent.host.self_revoke",
)
async def test_an_unpaired_host_cannot_claim_anything(world):
    anonymous = await world.new_person("anonymous", sign_up=False)

    completed = await anonymous.api.call(
        "POST",
        "/agent-host/pairings:complete",
        json={"pairing_code": "not-a-code", "display_name": "Someone's laptop"},
    )
    published = await anonymous.api.call(
        "PUT", "/agent-host/harnesses", json={"harnesses": []}
    )
    revoked = await anonymous.api.call("POST", "/agent-host/revoke", json={})

    # A pairing code is the only thing standing between a stranger and running
    # work on someone's machine, so every one of these has to refuse.
    for label, response in (
        ("pairing.complete", completed),
        ("harnesses.publish", published),
        ("self_revoke", revoked),
    ):
        assert response.status_code >= 400, (
            f"{label} answered {response.status_code} to an unpaired caller"
        )


@scenario("Asking what a machine offers when it is not one of yours is refused")
@proves("PS-AGENT-040")
@covers("agent.host.harnesses.list")
async def test_harnesses_of_an_unknown_host_are_refused(person):
    # A host id belongs to one person. Asking about one that is not theirs must
    # not answer, whether it exists on someone else's account or not at all.
    someone_elses = "00000000-0000-0000-0000-000000000001"

    response = await person.api.call(
        "GET", f"/me/runtime/agent-hosts/{someone_elses}/harnesses"
    )

    assert response.status_code >= 400, (
        f"a host that is not yours must not report what it can run "
        f"({response.status_code})"
    )
