"""A pod reachable on Telegram, with a person the platform recognises.

Shared because several scenarios need the same setup and none of them is about
the setup: connecting a surface is proved elsewhere, and repeating it in every
file makes each one longer than the thing it is testing.

Telegram is answered by the egress proxy — the product connects to
`api.telegram.org` exactly as it would in production, and nothing reaches the
internet. Lemma runs entirely for real, at production SSRF strictness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from harness.telegram_view import TelegramView
from harness.waiting import eventually


@dataclass
class Reachable:
    """A pod on Telegram, and the person the platform knows."""

    alice: Any
    pod: Any
    fake: Any
    handle: str
    chat_id: int
    agent: Any

    def update(
        self,
        text: str,
        *,
        update_id: int,
        chat_id: int | None = None,
        thread_id: int | None = None,
        document: dict | None = None,
        voice: dict | None = None,
    ) -> dict:
        """One inbound message, shaped the way Telegram shapes them."""
        chat = chat_id if chat_id is not None else self.chat_id
        message: dict = {
            "message_id": update_id,
            "date": 1700000000,
            "chat": {"id": chat, "type": "private"},
            "from": {
                "id": self.chat_id,
                "is_bot": False,
                "first_name": "Alice",
                # What makes this a message from somebody rather than a stranger:
                # a sender whose @username matches a user's telegram_username
                # resolves to that user with no linking round trip.
                "username": self.handle,
            },
            "text": text,
        }
        if thread_id is not None:
            message["message_thread_id"] = thread_id
        if document is not None:
            message["document"] = document
        if voice is not None:
            message["voice"] = voice
        return {"update_id": update_id, "message": message}

    async def says(self, text: str, **kwargs: Any) -> Any:
        """Deliver a message the way the platform would, and insist it landed."""
        delivered = await self.alice.api.call(
            "POST",
            self.fake.webhook_path,
            json=self.update(text, **kwargs),
            headers={"X-Telegram-Bot-Api-Secret-Token": self.fake.webhook_secret},
        )
        assert delivered.status_code < 400, (
            f"a correctly signed delivery was rejected: {delivered.status_code} "
            f"{delivered.text[:300]}"
        )
        return delivered

    def replies(self, chat_id: int | None = None):
        return self.fake.messages_to(chat_id if chat_id is not None else self.chat_id)

    async def waits_for_a_reply(self, *, chat_id: int | None = None, after: int = 0):
        async def seen():
            return self.replies(chat_id)

        return await eventually(
            seen,
            lambda messages: len(messages) > after,
            describe="the agent to answer on Telegram",
            timeout=90.0,
        )


@pytest.fixture
async def reachable(world, run, egress):
    """A pod on Telegram. Nothing here starts a server.

    The product connects to `api.telegram.org` and the proxy answers, so this
    builds the surface and then only *reads* what Lemma said. The bot token is
    unique per scenario because the fake remembers a webhook per token, and the
    proxy serving it outlives any one scenario.
    """
    fake = TelegramView(egress)
    # A Telegram account belongs to one person deployment-wide, and an update id
    # already handled is discarded as a duplicate. Both are per-scenario, or one
    # scenario's sender collides with another's.
    handle = f"alice_{uuid4().hex[:10]}"
    chat_id = 66600 + (uuid4().int % 9000)
    bot_token = f"{uuid4().int % 10**10}:scenarios"
    alice = await world.person("daniel")
    await alice.is_known_on_telegram_as(handle)
    organization = alice.organization
    pod = await alice.creates_a_pod(named=run.name("surface"))
    agent = await alice.creates_an_agent(in_pod=pod, toolsets=["POD", "USER_INTERACTION"])
    auth_config = await alice.installs_connector("telegram", in_organization=organization)
    account = await alice.connects_account(
        in_organization=organization,
        auth_config=auth_config,
        # No api_base_url: the product talks to the real Telegram host and
        # the proxy is what answers. That is what lets this run with the
        # SSRF guard at production strictness.
        credentials={"bot_token": bot_token},
    )
    await alice.connects_a_surface(
        in_pod=pod,
        platform="TELEGRAM",
        named="tg",
        agent=agent["name"],
        account=account,
    )
    yield Reachable(
        alice=alice,
        pod=pod,
        fake=fake,
        handle=handle,
        chat_id=chat_id,
        agent=agent,
    )
