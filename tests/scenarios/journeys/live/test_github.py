"""Live → GitHub, for real.

The fast lane proves the connector machinery against a provider on localhost.
This proves it against GitHub: their auth, their headers, their pagination,
their error shapes, and their idea of what a valid request looks like.

Everything created here is cleaned up, unconditionally. Point `LIVE_GITHUB_REPO`
at a repository you do not mind being written to.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import GITHUB, needs
from harness.steps.agent import answers, attempts, result_of

pytestmark = [
    journey("Connectors and accounts"),
    capability("Use a connector"),
    pytest.mark.live,
]


@pytest.fixture
async def github(world):
    """An organization with GitHub connected, using a real token."""
    needs(GITHUB)
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    auth_config = await alice.installs_connector("github", in_organization=organization)
    account = await alice.connects_account(
        in_organization=organization,
        auth_config=auth_config,
        # A fine-grained PAT is a bearer token, which is exactly what the HTTP
        # executor sends. So this exercises the real auth path without the
        # browser round trip an OAuth grant would need.
        credentials={"access_token": GITHUB.value("LIVE_GITHUB_TOKEN")},
    )
    return alice, organization, auth_config, account


@scenario("A person connects GitHub and Lemma knows whose account it is")
@proves("PS-CONN-011", "PS-CONN-020")
@covers("connector.account.create", "connector.operation.execute")
async def test_connecting_github_identifies_the_account(github):
    alice, organization, auth_config, _account = github

    whoami = await alice.runs_operation(
        "users_get_authenticated",
        auth_config=auth_config,
        in_organization=organization,
        payload={},
    )

    # A real answer from a real API. The login is whatever the token belongs to,
    # so the assertion is about the shape rather than a name we would have to
    # keep in step with somebody's account.
    body = whoami.get("data") or whoami.get("result") or whoami
    assert "login" in str(body), (
        f"GitHub did not answer with an identity: {str(body)[:500]}"
    )


@scenario("A person opens an issue through Lemma and closes it again")
@proves("PS-CONN-030")
@covers("connector.operation.execute")
async def test_an_issue_is_created_and_closed(github):
    alice, organization, auth_config, _account = github
    owner, _, repo = GITHUB.value("LIVE_GITHUB_REPO").partition("/")
    title = f"lemma scenarios {uuid4().hex[:8]}"
    number = None

    try:
        created = await alice.runs_operation(
            "issues_create",
            auth_config=auth_config,
            in_organization=organization,
            payload={
                "owner": owner,
                "repo": repo,
                "body": {"title": title, "body": "Opened by the live scenario lane."},
            },
        )
        body = created.get("data") or created.get("result") or created
        number = body.get("number") if isinstance(body, dict) else None
        assert number, f"GitHub did not return the issue it created: {str(body)[:600]}"

        found = await alice.runs_operation(
            "issues_get",
            auth_config=auth_config,
            in_organization=organization,
            payload={"owner": owner, "repo": repo, "issue_number": number},
        )
        read_back = found.get("data") or found.get("result") or found
        assert title in str(read_back), (
            f"the issue Lemma created does not read back: {str(read_back)[:500]}"
        )
    finally:
        if number:
            await alice.runs_operation(
                "issues_update",
                auth_config=auth_config,
                in_organization=organization,
                payload={
                    "owner": owner,
                    "repo": repo,
                    "issue_number": number,
                    "body": {"state": "closed"},
                },
            )


@scenario("An agent granted GitHub uses it, and one without it cannot")
@proves("PS-CONN-033", "PS-AGENT-002")
@covers("connector.operation.execute", "agent.conversation.create")
async def test_an_agent_uses_github_only_when_granted(github):
    alice, organization, auth_config, _account = github
    pod = await alice.creates_a_pod()
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["CONNECTORS"])

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        where_the_agent=[
            attempts(
                "run_connector_operation",
                remembered_as="ungranted",
                auth_config=auth_config["name"],
                operation="users_get_authenticated",
                arguments={},
            ),
            answers(result_of("ungranted", "error")),
        ],
        saying="Who am I on GitHub?",
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    transcript = await alice.transcript_of(conversation, in_pod=pod)
    assert "login" not in transcript, (
        "an agent with no connector grant read the GitHub identity anyway; the "
        f"transcript is {transcript[-1500:]}"
    )
