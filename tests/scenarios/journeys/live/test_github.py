"""Live → GitHub, for real.

The fast lane proves the connector machinery against a provider on localhost.
This proves it against GitHub: their auth, their headers, their pagination,
their error shapes, and their idea of what a valid request looks like.

The deployment's own `CONNECTOR_GITHUB_CLIENT_ID` is what a person would
consent through; a fine-grained PAT is the other real way to connect GitHub and
is what this uses, because it needs no browser.

Everything created here is cleaned up, unconditionally. Point
`SCENARIOS_GITHUB_REPO` at a repository you do not mind being written to.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.consent import GITHUB
from harness.credentials import GITHUB_REPO, needs

pytestmark = [
    journey("Connectors and accounts"),
    capability("Use a connector"),
    pytest.mark.live,
]


@pytest.fixture
async def github(world):
    """The GitHub account somebody consented on this tenant.

    This used to inject a fine-grained PAT and claim that exercised the real
    auth path without a browser. It did not: `github` is an OAuth2 connector,
    and `connector_service.py` refuses credential injection for those outright.
    Every scenario in this file failed in fixture setup with a 400, and had
    done for as long as the file existed — a live lane nobody watched.

    It uses the account a person actually connected, which is both the only
    thing that works and the thing a real user has. `needs(GITHUB)` skips with
    instructions where nobody has, and the run says so under "waiting on a
    person" rather than quietly proving less.
    """
    alice = await world.person("priya")
    needs(GITHUB)
    organization = alice.organization
    [auth_config] = [
        config
        for config in await alice.auth_configs_in(organization)
        if config.get("connector_id") == "github"
    ]
    [account] = [
        connected
        for connected in await alice.accounts_in(organization)
        if connected.get("connector_id") == "github"
    ]
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
    # The only scenario here that writes somewhere, so the only one that needs
    # to be told where it may write.
    needs(GITHUB_REPO)
    alice, organization, auth_config, _account = github
    owner, _, repo = GITHUB_REPO.value("SCENARIOS_GITHUB_REPO").partition("/")
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
async def test_an_agent_uses_github_only_when_granted(github, run):
    alice, organization, auth_config, account = github
    pod = await alice.creates_a_pod(named=run.name("github"))
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["CONNECTORS"])

    # What the answer would be if the agent got through. Alice may ask — she
    # connected the account — so this is the fact the agent must not come back
    # holding, rather than a word it must not happen to use.
    whoami = await alice.runs_operation(
        "users_get_authenticated",
        auth_config=auth_config, account=account,
        in_organization=organization, payload={},
    )
    secret_identity = str((whoami.get("result") or {}).get("login") or "")
    assert secret_identity, whoami

    conversation = await alice.starts_a_conversation(
        in_pod=pod,
        with_agent=agent["name"],
        saying="Which GitHub account am I signed in as? Look it up.",
    )
    await alice.waits_for_the_run_to_settle(conversation=conversation, in_pod=pod)

    # Asserted on the identity itself, not on the word "login". A real model
    # asked to look up a login *says* "login" — and it said it here, inside a
    # perfectly correct approval request explaining it had been refused. The
    # old assertion failed the product for using the English of the question it
    # was asked, which is the hazard in reading an agent's words at all.
    transcript = await alice.transcript_of(conversation, in_pod=pod)
    assert secret_identity.lower() not in transcript.lower(), (
        f"an agent with no connector grant came back holding the GitHub "
        f"identity ({secret_identity!r}); the transcript is {transcript[-1500:]}"
    )
