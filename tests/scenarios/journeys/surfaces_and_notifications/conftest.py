"""A pod reachable on Telegram, and somebody messaging it.

Shared because several scenarios need the same setup and none of them is about
the setup: connecting a surface is proved elsewhere, and repeating it in every
file makes each one longer than the thing it is testing.

Who the somebody is depends on the lane, and the scenarios do not have to care —
see `harness/telegram_chat.py` for the seam. Locally, Telegram is answered by
the egress proxy and the delivery is forged: the product connects to
`api.telegram.org` exactly as it would in production, nothing reaches the
internet, and Lemma runs for real at production SSRF strictness. Against a
deployment with a session configured, a real account on real Telegram sends the
message and reads the answer in its own chat.

The two lanes set up differently, and the difference is forced rather than
chosen:

* Forged, there is no bot, so every scenario can afford its own pod, its own
  sender and its own bot token — and should, because a Telegram account belongs
  to one person deployment-wide and an update id already handled is discarded.
* Live, there is exactly one real bot, and `setWebhook` is last-one-wins. Two
  surfaces cannot both own it, so the live lane uses the standing surface
  (`tenant.STANDING_REACH`) that provisioning connected once.

That has a consequence worth stating plainly, because it shapes how the
scenarios are written: live, the agent is the standing one and this fixture did
not configure it. So a scenario asks for the behaviour it wants *in the message*,
the way a person would, rather than by installing an instruction on an agent it
owns. That reads the same in both lanes and is the more honest test anyway — it
is the model choosing to use its tools, not the harness arranging for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from harness.credentials import TELEGRAM, TELEGRAM_APP, TELEGRAM_PERSON, needs
from harness.environment import MODEL_IS_REAL
from harness.telegram_chat import (
    Chat,
    ForgedChat,
    LiveChat,
    telegram_is_forged,
    telegram_is_live,
)
from harness.telegram_view import TelegramView


@dataclass
class Reachable:
    """A pod on Telegram, and the conversation somebody is having with it."""

    alice: Any
    pod: Any
    chat: Chat
    #: Conversations that were already in the pod when this started. The live
    #: lane works in a pod that stands between runs, so "the conversation this
    #: scenario opened" can only mean one that was not there before.
    _before: frozenset[str]

    # Delegated so a scenario reads as what somebody did, rather than as
    # plumbing: `await reachable.says(...)`, not `reachable.chat.says(...)`.
    async def says(self, text: str, **extra: Any) -> None:
        await self.chat.says(text, **extra)

    async def sends_file(self, name: str, *, caption: str = "") -> bytes:
        return await self.chat.sends_file(name, caption=caption)

    async def replies(self):
        return await self.chat.replies()

    async def waits_for_a_reply(self, **kwargs: Any):
        return await self.chat.waits_for_a_reply(**kwargs)

    async def conversations(self) -> list[Any]:
        """The conversations this scenario opened, newest excluded of nothing."""
        found = await self.alice.conversations_in(self.pod)
        return [thread for thread in found if str(_thread_id(thread)) not in self._before]

    def only_forged(self, why: str) -> None:
        """Skip unless this lane can deliver what Telegram never sent."""
        if not self.chat.can_forge:
            pytest.skip(f"{why} — and a real account cannot forge a delivery")

    def only_live(self, why: str) -> None:
        """Skip unless real Telegram is on the other end."""
        if not self.chat.is_live:
            pytest.skip(
                f"{why} — the stand-in cannot be asked for it. Run against a "
                f"deployment with SCENARIOS_EGRESS=off and TELEGRAM_SESSION set."
            )

    async def sends_image(self, name: str, content: bytes, *, caption: str = "") -> None:
        await self.chat.sends_image(name, content, caption=caption)


def _thread_id(thread: Any) -> Any:
    if isinstance(thread, dict):
        return thread.get("id") or thread.get("conversation_id") or thread
    return thread


async def _already_in(alice: Any, pod: Any) -> frozenset[str]:
    return frozenset(str(_thread_id(t)) for t in await alice.conversations_in(pod))


@pytest.fixture
async def reachable(world, run, stack):
    """A pod on Telegram, reached however this run can reach one.

    Takes `stack` rather than `egress` on purpose, and it matters. The `egress`
    fixture skips outright when the run targets a deployment the suite does not
    own — correctly, because there is no proxy in front of *the product* to
    stand in for anything. But that skip happens while fixtures are being
    resolved, before a line of this runs, so asking for it here would kill the
    live lane on exactly the runs the live lane exists for: a real account
    against a real deployment does not want a proxy and must not be skipped for
    lacking one.
    """
    if telegram_is_live():
        async for ready in _live(world):
            yield ready
        return
    async for ready in _forged(world, run, _proxy_or_skip(stack)):
        yield ready


def _proxy_or_skip(stack):
    """The proxy, or the same sentence `egress` would have skipped with."""
    proxy = getattr(stack, "egress", None)
    if proxy is None:
        pytest.skip(
            "no egress proxy: this run targets a deployment the suite does not "
            "own, so nothing stands in for Telegram — and no real account is "
            "configured to talk to it instead. Set TELEGRAM_SESSION (with "
            "SCENARIOS_EGRESS=off) for the live lane, or run without --base-url."
        )
    # Per scenario, for the same reason `egress` does it: a scenario asking
    # "the agent replied once" has to be asking about its own traffic.
    proxy.forget()
    return proxy


@pytest.fixture
async def forged(world, run, egress):
    """The forged lane specifically, for what only forging can ask.

    Refusing a delivery that was not signed, answering a duplicate once, being
    messaged by a stranger — none of these is something a real account can do,
    because a real account cannot make Telegram send something Telegram would
    not send. Scenarios about them take this rather than `reachable`, so they
    say what they need up front and skip cheaply when the run cannot provide it,
    instead of signing a person in and then discovering it.
    """
    async for ready in _forged(world, run, egress):
        yield ready


async def _forged(world, run, egress):
    """Telegram stood in for. Nothing here starts a server.

    The product connects to `api.telegram.org` and the proxy answers, so this
    builds the surface and then only *reads* what Lemma said. The bot token is
    unique per scenario because the stand-in remembers a webhook per token, and
    the proxy serving it outlives any one scenario.
    """
    if not telegram_is_forged():
        pytest.skip(
            "nothing is standing in for api.telegram.org, so there is no "
            "registered webhook to deliver to and no record of what the agent "
            "said. Run with SCENARIOS_EGRESS=fake, or against a deployment with "
            "TELEGRAM_SESSION set for the lane that uses a real account."
        )
    view = TelegramView(egress)
    handle = f"alice_{uuid4().hex[:10]}"
    chat_id = 66600 + (uuid4().int % 9000)
    bot_token = f"{uuid4().int % 10**10}:scenarios"
    alice = await world.person("daniel")
    await alice.is_known_on_telegram_as(handle)
    pod = await alice.creates_a_pod(named=run.name("surface"))
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["POD", "USER_INTERACTION"])
    await alice.becomes_reachable_on_telegram(
        in_pod=pod, agent=agent["name"], bot_token=bot_token
    )
    yield Reachable(
        alice=alice,
        pod=pod,
        chat=ForgedChat(alice, view, handle=handle, chat_id=chat_id),
        _before=await _already_in(alice, pod),
    )


async def _live(world):
    """A real account, on real Telegram, messaging the deployment's own bot."""
    # MODEL_IS_REAL, not credentials.REAL_MODEL. The two sound alike and are
    # asked of different people: REAL_MODEL asks whether *this suite* holds an
    # API key, which is the right question only when the suite is booting the
    # stack itself. Here the target is somebody's deployment, and whether it
    # runs agents on a real model is its own business — it says so at
    # /health/capabilities, and dev answers `llm_mode: real`. Demanding the key
    # instead would skip every one of these on the one target they exist for,
    # because a deployment run has no reason to be given model credentials and
    # is not given any.
    needs(TELEGRAM, TELEGRAM_APP, TELEGRAM_PERSON, MODEL_IS_REAL)
    from harness.telegram_person import a_person_on_telegram
    from harness.tenant import CONNECTOR_HOLDER, STANDING_REACH

    reach = STANDING_REACH[0]
    holder = await world.person(CONNECTOR_HOLDER)
    pod = await holder.works_in(reach.pod)
    surfaces = {str(s.get("name")) for s in await holder.surfaces_in(pod)}
    if reach.name not in surfaces:
        pytest.skip(
            f"the standing {reach.platform} surface is not on {reach.pod!r}; run "
            f"`make scenarios-provision` once the {reach.connector} account is "
            f"connected"
        )

    bot = await _bot_handle()
    person = await a_person_on_telegram()
    try:
        if not person.username:
            pytest.skip(
                "the Telegram account in TELEGRAM_SESSION has no @username, and "
                "that is how Lemma recognises a sender — an inbound message from "
                "it resolves to nobody, so the agent correctly answers a stranger "
                "rather than a colleague. Set one in Telegram's settings "
                "(Settings → Username); everyone the product is built for has one."
            )
        # Tell Lemma which colleague this account is. Without it the sender is a
        # stranger — which the product handles well, and is a different promise
        # from the ones these scenarios are about.
        await holder.is_known_on_telegram_as(person.username)
        before = await _already_in(holder, pod)
        async with person.talking_to(bot) as conversation:
            yield Reachable(
                alice=holder,
                pod=pod,
                chat=LiveChat(conversation),
                _before=before,
            )
    finally:
        await person.aclose()


async def _bot_handle() -> str:
    """The @username of the deployment's own bot, asked of Telegram."""
    import httpx

    token = TELEGRAM.value("TELEGRAM_BOT_TOKEN")
    async with httpx.AsyncClient(timeout=30.0) as client:
        answered = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        answered.raise_for_status()
    username = ((answered.json() or {}).get("result") or {}).get("username")
    if not username:
        raise AssertionError(
            "Telegram would not say who this bot is, so there is nothing for a "
            "person to message. Check TELEGRAM_BOT_TOKEN."
        )
    return f"@{username}"
