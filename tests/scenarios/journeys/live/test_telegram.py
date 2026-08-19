"""Live → a real Telegram bot, reached the way a person reaches it.

The fast lane stands Telegram in for on localhost, which proves Lemma's half of
the conversation. This proves the other half: a real bot, real delivery, real
message formatting, and real buttons a person could press.

No public URL is needed. The worker receives by polling (`getUpdates`), which is
a supported deployment mode and the one a self-hosted install behind a firewall
uses — the stack turns it on when a live bot token is present.

The bot is the deployment's own `TELEGRAM_BOT_TOKEN`. Messages go to
`SCENARIOS_TELEGRAM_CHAT_ID`, so point that at a chat you do not mind being
written to.
"""

from __future__ import annotations

import httpx
import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import REAL_MODEL, TELEGRAM, TELEGRAM_CHAT, needs
from harness.waiting import eventually

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Receive a message from outside"),
    pytest.mark.live,
]


def _bot(path: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM.value('TELEGRAM_BOT_TOKEN')}/{path}"


async def _latest_message_id() -> int:
    """Where the bot's history is now, so a scenario can watch for what follows.

    Telegram keeps a bounded backlog, and a chat used by an earlier run has
    replies in it. Without a mark, a scenario asserting "the agent answered"
    passes on last night's answer.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(_bot("getUpdates"), params={"limit": 1, "offset": -1})
        response.raise_for_status()
        results = response.json().get("result") or []
    return int(results[-1]["update_id"]) if results else 0


@pytest.fixture
async def bot_pod(world):
    """A pod reachable on a real Telegram bot."""
    needs(TELEGRAM, TELEGRAM_CHAT, REAL_MODEL)
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
    agent = await alice.creates_an_agent(
        in_pod=pod,
        toolsets=["POD", "USER_INTERACTION"],
        instruction=(
            "You are terse. When asked to prove where you are, answer in one "
            "short sentence."
        ),
    )
    auth_config = await alice.installs_connector("telegram", in_organization=organization)
    account = await alice.connects_account(
        in_organization=organization,
        auth_config=auth_config,
        credentials={"bot_token": TELEGRAM.value("TELEGRAM_BOT_TOKEN")},
    )
    surface = await alice.connects_a_surface(
        in_pod=pod,
        platform="TELEGRAM",
        named="tg",
        agent=agent["name"],
        account=account,
    )
    try:
        yield alice, pod, agent
    finally:
        # Leave the bot as it was found: a surface still holding a webhook or a
        # poll on a shared bot would swallow the next run's messages.
        await alice.deletes_surface(surface["name"], in_pod=pod)


@scenario("A real Telegram bot carries a message to the pod and an answer back")
@proves("PS-SURF-010", "PS-SURF-020")
@covers("agent.surface.create", "agent.surface.send", "surface.message_answered")
async def test_a_real_bot_answers(bot_pod):
    alice, pod, _agent = bot_pod
    chat_id = TELEGRAM_CHAT.value("SCENARIOS_TELEGRAM_CHAT_ID")
    del alice, pod

    before = await _latest_message_id()
    async with httpx.AsyncClient(timeout=30.0) as client:
        sent = await client.post(
            _bot("sendMessage"),
            json={"chat_id": chat_id, "text": "Say hello back, please."},
        )
        sent.raise_for_status()

    async def replies() -> list[dict]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                _bot("getUpdates"), params={"offset": before + 1, "timeout": 0}
            )
        return [
            update
            for update in (response.json().get("result") or [])
            if (update.get("message") or {}).get("from", {}).get("is_bot")
        ]

    answered = await eventually(
        replies,
        bool,
        describe="the bot to answer in the real chat",
        timeout=120.0,
    )
    text = (answered[-1].get("message") or {}).get("text") or ""
    assert text.strip(), f"the bot replied with nothing: {answered[-1]}"
