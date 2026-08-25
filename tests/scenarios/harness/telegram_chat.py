"""One conversation with an agent on Telegram, whichever Telegram is answering.

A scenario about somebody messaging an agent should be one scenario. Until now
it was two, because the two ways of being that somebody look nothing alike:

* **Locally** there is no person, so the suite forges the delivery. The product
  registers a webhook with `api.telegram.org`, the egress proxy answers and
  writes down where, and the scenario POSTs an update to that path with the
  secret Lemma chose. Fast, offline, and it proves Lemma's half.
* **Against a deployment** there is a real account signed in over MTProto. It
  sends a real message through Telegram's own infrastructure and reads the
  reply back out of its own chat. Slower, needs a session minted by hand, and
  it proves the whole round trip.

Both are worth having, and neither is worth writing twice. What a scenario
actually does — say something, wait for the answer, look at the buttons — is
identical; only who carries the words differs. So that is the seam: one small
interface, two implementations, and scenarios that read the same either way.

    chat = ...                       # whichever lane this run is
    await chat.says("send me a report")
    answer = await chat.waits_for_a_reply()
    assert answer.offers("Weekly summary")

Some things only one lane can do, and pretending otherwise would be worse than
the duplication. Forging a delivery is how you prove a *bad* delivery is
refused, and a real account cannot forge one; a real account is the only way to
prove Telegram itself accepts what Lemma sends, and the proxy cannot be
Telegram. Those capabilities are named — `can_forge`, `is_live` — so a scenario
that needs one skips with a reason rather than failing somewhere confusing.

The lanes are also not equally strict, which is the point of running both.
`Said.text` unwraps `sendRichMessage`'s nested markdown, so a message the proxy
reads as full of words can arrive at a real client empty. That is `DEV-SURF-002`,
and it is visible only from the live lane — which is exactly the kind of thing
a stand-in is supposed to be checked against.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from itertools import count
from typing import Any

from harness.credentials import TELEGRAM, TELEGRAM_APP, TELEGRAM_PERSON

#: How long to wait for an agent to answer. Generous on purpose: the reply is a
#: queued run against a real model, not a synchronous call, and a scenario that
#: fails here should report "the agent never answered" rather than "the model
#: was slow today".
REPLY_TIMEOUT = 120.0

#: How often to ask. Telegram rate-limits, and an agent answering takes seconds
#: rather than milliseconds, so there is nothing to gain from asking harder.
POLL_EVERY = 2.0


@dataclass(frozen=True, slots=True)
class Spoken:
    """One thing the agent said, as the person on the other end received it."""

    text: str
    #: Button labels offered with the message, flattened. `PS-SURF-021` is about
    #: a person being given real choices rather than a sentence listing them.
    choices: tuple[str, ...]

    def offers(self, label: str) -> bool:
        """Is `label` one of the choices? Matched by containment, because the
        product decorates a recommended choice ("⭐ Weekly summary") and that is
        it being helpful rather than it offering something else."""
        return any(label.lower() in choice.lower() for choice in self.choices)

    @property
    def audible(self) -> bool:
        """Did this reach the person as anything at all?

        Lemma streams, so the first message in a chat is an empty placeholder it
        fills in as the answer arrives. Waiting for "a message" returns that one
        and a scenario ends up asserting against `''`, reporting a working
        product as broken. Words or buttons — buttons being an answer without
        words — is what a person would call having been answered.
        """
        return bool(self.text.strip() or self.choices)


def telegram_is_forged() -> bool:
    """Can this run deliver an update Telegram never sent?

    Only when the proxy is standing in for `api.telegram.org`. Against a real
    Telegram the bot token is real, the webhook is registered with the real
    platform, and there is no recorded `setWebhook` to read a path out of.
    """
    from harness import egress as egress_module

    return egress_module.wanted_mode() in {"fake", "replay"}


def telegram_is_live() -> bool:
    """Will this run drive Telegram through a real account?

    Answerable at collection time, from configuration alone, because a scenario
    that behaves differently per lane has to be able to say so in a mark.

    Two conditions, and both matter. The proxy standing in for `api.telegram.org`
    means the agent's reply goes to the fake while a person would be waiting on
    real Telegram — two conversations that never meet, and a timeout with no
    explanation. And an account has to actually be signed in.
    """
    from harness import egress as egress_module

    if egress_module.wanted_mode() in {"fake", "replay"}:
        return False
    return all(
        capability.available for capability in (TELEGRAM, TELEGRAM_APP, TELEGRAM_PERSON)
    )


class Chat:
    """What every scenario about messaging an agent needs, and nothing else."""

    #: Can this lane deliver an update Telegram never sent — a duplicate id, a
    #: missing signature, a second chat? Only a forged one can.
    can_forge = False
    #: Is a real account on the other end, on real Telegram?
    is_live = False
    #: For assertion messages, so a failure says which lane produced it.
    lane = "unknown"

    async def says(self, text: str) -> None:
        raise NotImplementedError

    async def sends_file(self, name: str, *, caption: str = "") -> bytes:
        """Attach a document, and answer with the bytes that were sent.

        Returned rather than assumed: the forged lane cannot choose the content
        (the stand-in serves one fixed file for every id), and a scenario that
        hard-codes what it expects to find in the pod is really asserting
        against the fake. Asking what was sent means the same assertion is true
        in both lanes.
        """
        raise NotImplementedError

    async def sends_image(self, name: str, content: bytes, *, caption: str = "") -> None:
        """An actual image, which only a real Telegram can carry."""
        raise NotImplementedError

    async def replies(self) -> list[Spoken]:
        """Everything the agent has said in this conversation, oldest first."""
        raise NotImplementedError

    async def waits_for_a_reply(
        self, *, after: int = 0, timeout: float = REPLY_TIMEOUT
    ) -> Spoken:
        """Wait until the agent has answered more than `after` times; give the last.

        `after` is how a scenario says "the *second* reply" without racing the
        first: two messages in one chat should be answered twice, and waiting
        for "a reply" would return the answer to the first message immediately.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            spoken = [reply for reply in await self.replies() if reply.audible]
            if len(spoken) > after:
                return spoken[-1]
            if loop.time() >= deadline:
                break
            await asyncio.sleep(POLL_EVERY)
        raise AssertionError(
            f"waited {timeout:.0f}s for the agent to answer on Telegram "
            f"({self.lane}) and it never did"
            + (f" more than {after} time(s)" if after else "")
            + ". The message was delivered; nothing came back. Check the surface "
            "is connected and the worker is running."
        )


class ForgedChat(Chat):
    """Telegram stood in for: the scenario delivers the update itself.

    Nothing here runs a server. The product connected to `api.telegram.org`, the
    proxy answered, and this reads the webhook out of Lemma's own `setWebhook`
    call — deliberately, rather than building the path from a template. A
    scenario delivering to a path it guessed proves the product answers that
    path, not that the product registered it.
    """

    can_forge = True
    lane = "the egress proxy standing in for Telegram"

    def __init__(self, alice: Any, view: Any, *, handle: str, chat_id: int) -> None:
        self._alice = alice
        self._view = view
        self._handle = handle
        self.chat_id = chat_id
        # Update ids are how a platform names a delivery, and Lemma discards one
        # it has already handled. Counting from the chat id keeps one scenario's
        # ids clear of another's without anybody having to remember which
        # numbers are taken — which is a bookkeeping mistake that used to show
        # up as a message silently deduplicated and a wait that never ended.
        self._next_id = count(chat_id * 1000)

    @property
    def webhook_path(self) -> str:
        return self._view.webhook_path

    @property
    def webhook_secret(self) -> str:
        return self._view.webhook_secret

    def update(self, text: str, **extra: Any) -> dict:
        """One inbound message, shaped the way Telegram shapes them."""
        update_id = next(self._next_id)
        message: dict = {
            "message_id": update_id,
            "date": 1700000000,
            "chat": {"id": self.chat_id, "type": "private"},
            "from": {
                "id": self.chat_id,
                "is_bot": False,
                "first_name": "Alice",
                # What makes this a message from somebody rather than a stranger:
                # a sender whose @username matches a user's telegram_username
                # resolves to that user with no linking round trip.
                **({"username": self._handle} if self._handle else {}),
            },
            "text": text,
        }
        message.update(extra)
        return {"update_id": update_id, "message": message}

    async def delivers(
        self, update: dict, *, secret: str | None = None, signed: bool = True
    ) -> Any:
        """POST an update to Lemma's webhook, signed as Telegram signs one.

        `signed=False` sends no signature at all and `secret=` sends the wrong
        one. Both are deliveries Telegram would never make, which is the point:
        a webhook nobody can route to from the internet is only as safe as its
        refusal to believe whatever arrives.
        """
        headers = {}
        if signed:
            headers["X-Telegram-Bot-Api-Secret-Token"] = (
                self.webhook_secret if secret is None else secret
            )
        return await self._alice.api.call(
            "POST", self.webhook_path, json=update, headers=headers
        )

    async def says(self, text: str, **extra: Any) -> None:
        delivered = await self.delivers(self.update(text, **extra))
        assert delivered.status_code < 400, (
            f"a correctly signed delivery was rejected: {delivered.status_code} "
            f"{delivered.text[:300]}"
        )

    async def sends_file(self, name: str, *, caption: str = "") -> bytes:
        from harness.fake_upstreams import FILE_CONTENTS

        await self.says(
            caption or "here is the file",
            document={
                "file_id": f"scenarios-{next(self._next_id)}",
                "file_unique_id": f"scenarios-{self.chat_id}",
                "file_name": name,
                "mime_type": "text/csv",
                "file_size": len(FILE_CONTENTS),
            },
        )
        return FILE_CONTENTS

    async def replies(self) -> list[Spoken]:
        return [
            Spoken(text=said.text, choices=tuple(said.native_choices))
            for said in self._view.messages_to(self.chat_id)
        ]

    def as_a_stranger(self) -> ForgedChat:
        """The same bot, messaged by somebody Lemma has never heard of.

        A sender is recognised by an @username that matches a user's
        `telegram_username`; leaving it off is what a real stranger looks like,
        rather than what a broken message looks like. Its own chat, too — a
        stranger writing into the colleague's conversation would be a different
        and much more alarming scenario.
        """
        stranger = ForgedChat(
            self._alice, self._view, handle="", chat_id=self.chat_id + 500
        )
        return stranger

    def in_another_chat(self) -> ForgedChat:
        """The same sender, in a different conversation.

        A person has exactly one chat with a bot, so this is forged-only — and a
        scenario about two chats staying separate has to say so.
        """
        return ForgedChat(
            self._alice,
            self._view,
            handle=self._handle,
            chat_id=self.chat_id + 1,
        )


class LiveChat(Chat):
    """Real Telegram, driven by a real account over MTProto.

    Wraps `telegram_person.Conversation`, which owns signing in, the watermark
    that keeps last run's answers out of this one, and deleting what it said on
    the way out.
    """

    is_live = True
    lane = "a real account on real Telegram"

    def __init__(self, conversation: Any) -> None:
        self._conversation = conversation

    async def says(self, text: str) -> None:
        await self._conversation.says(text)

    async def sends_file(self, name: str, *, caption: str = "") -> bytes:
        from harness.fake_upstreams import FILE_CONTENTS

        # The same bytes the stand-in serves, so a scenario asserting on the
        # content of what arrived reads identically in both lanes.
        await self._conversation.sends_file(name, FILE_CONTENTS, caption=caption)
        return FILE_CONTENTS

    async def sends_image(self, name: str, content: bytes, *, caption: str = "") -> None:
        """An actual image, which only this lane can be asked for.

        The stand-in serves one fixed CSV for every file id, so there is nothing
        for a model to look at in the forged lane however the scenario asks.
        """
        await self._conversation.sends_file(name, content, caption=caption)

    async def replies(self) -> list[Spoken]:
        return [
            Spoken(text=reply.text, choices=reply.choices)
            for reply in await self._conversation.replies()
        ]
