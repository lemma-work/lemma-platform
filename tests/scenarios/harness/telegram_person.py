"""A real Telegram account the suite can send from, and read replies with.

This is the half of Telegram a bot cannot do. A bot never receives a message
nobody sent it, and cannot send one *as* a human — which is why every scenario
about somebody messaging an agent stood Telegram in for on loopback, and why
the live lane could only ever prove the outbound direction.

With a person signed in, the round trip is real end to end: this account sends
a message to the bot exactly as a colleague would, Lemma receives it through
the product's own polling receiver, the agent answers, and the answer arrives
back here as a message this account can read.

    person = await a_person_on_telegram()
    async with person.talking_to(bot) as chat:
        await chat.says("what is in the sales pod?")
        answer = await chat.waits_for_a_reply()

Signing in happens once, by hand — `harness/telegram_login.py` — because
Telegram sends a code to a phone. Scenarios that need this declare
`needs(TELEGRAM_PERSON)` and skip with those instructions where nobody has.

Everything here is scoped to a conversation with one bot. It never reads the
account's other chats, and `talking_to` clears the history it created on the
way out, so a run leaves the account as it found it.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from harness.credentials import TELEGRAM_APP, TELEGRAM_PERSON

#: How long to wait for an agent to answer. Generous on purpose: the reply is a
#: queued agent run against a real model, not a synchronous call, and a scenario
#: that fails here should be reporting "the agent never answered" rather than
#: "the model was slow today".
REPLY_TIMEOUT = 120.0

#: How often to ask. Telegram rate-limits, and an agent answering takes seconds
#: rather than milliseconds, so there is nothing to gain from asking harder.
POLL_EVERY = 2.0


@dataclass(frozen=True, slots=True)
class Reply:
    """Something the bot said back, as this account received it."""

    text: str
    #: Button labels the bot offered, flattened. `PS-SURF-021` is about a
    #: person being given real choices rather than a sentence listing them.
    choices: tuple[str, ...]

    def offers(self, label: str) -> bool:
        return any(label.lower() in choice.lower() for choice in self.choices)


class Conversation:
    """One chat with one bot, from the person's side."""

    def __init__(self, client: Any, bot: str, since: int) -> None:
        self._client = client
        self._bot = bot
        # Everything already in this chat is somebody else's business. A reply
        # is only this scenario's if it arrived after we started talking —
        # otherwise the first run seeds a chat and every run after it passes on
        # the answer to a question it never asked.
        self._since = since

    async def says(self, text: str) -> None:
        """Send a message, as the person would type it."""
        await self._client.send_message(self._bot, text)

    async def sends_file(self, name: str, content: bytes, *, caption: str = "") -> None:
        """Attach a document, which is the thing a bot cannot fake for itself."""
        import io

        handle = io.BytesIO(content)
        handle.name = name
        await self._client.send_file(self._bot, handle, caption=caption)

    async def replies(self) -> list[Reply]:
        """What the bot has said since this conversation started."""
        found: list[Reply] = []
        async for message in self._client.iter_messages(self._bot, min_id=self._since):
            if message.out:
                # Ours, not the bot's.
                continue
            found.append(Reply(text=message.text or "", choices=_choices(message)))
        found.reverse()
        return found

    async def waits_for_a_reply(self, *, timeout: float = REPLY_TIMEOUT) -> Reply:
        """Wait for the agent to answer, or say plainly that it never did."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            said = await self.replies()
            if said:
                return said[-1]
            await asyncio.sleep(POLL_EVERY)
        raise AssertionError(
            f"waited {timeout:.0f}s for the agent to answer on Telegram, and it "
            f"never did. The message was delivered to {self._bot}; nothing came "
            f"back. Check the surface is connected and the worker is running."
        )

    async def forget(self) -> None:
        """Delete this conversation's messages, both sides."""
        await self._client.delete_messages(
            self._bot,
            [
                m.id
                async for m in self._client.iter_messages(self._bot, min_id=self._since)
            ],
            revoke=True,
        )


def _choices(message: Any) -> tuple[str, ...]:
    markup = getattr(message, "reply_markup", None)
    rows = getattr(markup, "rows", None) or []
    return tuple(
        str(getattr(button, "text", "") or "")
        for row in rows
        for button in (getattr(row, "buttons", None) or [])
    )


class Person:
    """The account, signed in. Made by :func:`a_person_on_telegram`."""

    def __init__(self, client: Any, username: str | None, user_id: int) -> None:
        self._client = client
        self.username = username
        self.user_id = user_id

    @asynccontextmanager
    async def talking_to(self, bot: str) -> AsyncIterator[Conversation]:
        """A conversation with `bot`, cleaned up afterwards.

        The watermark is read before anything is sent, which is the only way an
        assertion about "the agent answered" can mean this run's answer rather
        than one still sitting in the chat from last time.
        """
        latest = 0
        async for message in self._client.iter_messages(bot, limit=1):
            latest = message.id
        chat = Conversation(self._client, bot, latest)
        try:
            yield chat
        finally:
            try:
                await chat.forget()
            except Exception:  # noqa: BLE001 — tidying must not fail a scenario
                pass

    async def aclose(self) -> None:
        await self._client.disconnect()


async def a_person_on_telegram() -> Person:
    """Sign the configured account in. Ask `needs(TELEGRAM_PERSON)` first."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(
        StringSession(TELEGRAM_PERSON.value("TELEGRAM_SESSION")),
        int(TELEGRAM_APP.value("TELEGRAM_API_ID")),
        TELEGRAM_APP.value("TELEGRAM_API_HASH"),
    )
    await client.connect()
    if not await client.is_user_authorized():
        raise AssertionError(
            "TELEGRAM_SESSION did not sign in. Sessions can be revoked from "
            "Telegram's own device list; run `uv run python -m "
            "harness.telegram_login` again to mint a new one."
        )
    me = await client.get_me()
    if getattr(me, "bot", False):
        raise AssertionError(
            "TELEGRAM_SESSION belongs to a bot account. The point of it is to "
            "be a person: a bot cannot start a conversation with another bot."
        )
    return Person(client, getattr(me, "username", None), me.id)
