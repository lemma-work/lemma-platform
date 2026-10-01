"""The link that adds a pod's Telegram bot to a group.

A bearer credential in a URL: minted for one member, spent by the first group
that uses it, gone in an hour. Pinned here as far as it goes without a
database: what the link looks like, that it works once, and which messages the
join treats as its own -- including the ones it must swallow rather than let
reach the bot as a question.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.telegram_group_join import (
    claim_telegram_group_join,
)
from app.modules.agent_surfaces.services.telegram_group_links import (
    GroupLinkUnavailable,
    mint_group_link,
    redeem_group_link,
    start_code,
)

pytestmark = pytest.mark.unit

CODE = "k3JpZ0x9_Qm2-wT7bVn4aLs8"


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiry: dict[str, int | None] = {}

    async def set(self, key, value, *, ex=None):
        self.values[key] = value
        self.expiry[key] = ex
        return True

    async def getdel(self, key):
        return self.values.pop(key, None)


class _UnreachableRedis:
    async def set(self, *_args, **_kwargs):
        raise ConnectionError("no route to redis")

    async def getdel(self, _key):
        raise ConnectionError("no route to redis")


def _no_database():
    """The sessions the join must not open for anything that is not its own."""
    raise AssertionError("the join opened a session")


def _code_in(url: str) -> str:
    return url.split("startgroup=", 1)[1]


# ------------------------------------------------------------------ the link


async def test_the_link_opens_telegrams_own_group_picker_for_the_bot():
    redis = _Redis()

    url, expires_at = await mint_group_link(
        surface_id=uuid4(),
        user_id=uuid4(),
        bot_username="@lemma_sales_bot",
        redis=redis,
    )

    assert url.startswith("https://t.me/lemma_sales_bot?startgroup=")
    # What Telegram accepts as a start parameter is what the join reads back.
    assert start_code(f"/start@lemma_sales_bot {_code_in(url)}") == _code_in(url)
    assert list(redis.expiry.values()) == [3600]
    assert expires_at > datetime.now(timezone.utc)


async def test_a_code_is_spent_by_the_first_group_that_uses_it():
    redis = _Redis()
    surface_id, user_id = uuid4(), uuid4()
    url, _ = await mint_group_link(
        surface_id=surface_id, user_id=user_id, bot_username="lemma_bot", redis=redis
    )

    claim = await redeem_group_link(_code_in(url), redis=redis)

    assert claim is not None
    assert (claim.surface_id, claim.user_id) == (surface_id, user_id)
    assert await redeem_group_link(_code_in(url), redis=redis) is None


@pytest.mark.parametrize("name", [None, "", "  ", "Sales Bot", "@", "x"])
async def test_no_link_without_a_name_telegram_knows_the_bot_by(name):
    with pytest.raises(GroupLinkUnavailable):
        await mint_group_link(
            surface_id=uuid4(), user_id=uuid4(), bot_username=name, redis=_Redis()
        )


async def test_nowhere_to_keep_the_code_is_no_link_and_no_claim():
    with pytest.raises(GroupLinkUnavailable):
        await mint_group_link(
            surface_id=uuid4(),
            user_id=uuid4(),
            bot_username="lemma_bot",
            redis=_UnreachableRedis(),
        )
    assert await redeem_group_link(CODE, redis=_UnreachableRedis()) is None


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (f"/start@lemma_bot {CODE}", CODE),
        (f"/start {CODE}", CODE),
        (f"  /start@lemma_bot   {CODE}  ", CODE),
        ("/start@lemma_bot", None),
        ("/start@lemma_bot hello", None),
        (f"/start@lemma_bot {CODE} and then some", None),
        (f"please /start@lemma_bot {CODE}", None),
        (f"/stop@lemma_bot {CODE}", None),
        (None, None),
    ],
)
def test_only_a_bare_start_with_a_code_is_a_join(text, code):
    assert start_code(text) == code


# ------------------------------------------------------------------ the join


def _telegram(text: str, *, chat_type: str = "supergroup") -> dict:
    chat_id = 900100 if chat_type == "private" else -1009876543210
    return {
        "update_id": 7001,
        "message": {
            "message_id": 12,
            "from": {"id": 900100, "is_bot": False, "first_name": "Arjun"},
            "chat": {"id": chat_id, "type": chat_type, "title": "Launch crew"},
            "date": 1700000100,
            "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": 16}],
        },
    }


def _from_telegram(payload: dict) -> SurfacePlatformWebhookIngress:
    return SurfacePlatformWebhookIngress(source="telegram", payload=payload)


async def _claimed(request, *, redis=None) -> bool:
    return await claim_telegram_group_join(
        _no_database,
        request,
        adapters=SurfacePlatformAdapterRegistry(),
        redis=redis or _Redis(),
    )


async def test_another_platforms_webhook_is_not_read_as_a_join():
    """A bot's own webhook names its surface, and looking it up is not free."""
    whatsapp = SurfaceDirectWebhookIngress(
        surface_id=uuid4(),
        payload={"object": "whatsapp_business_account", "entry": []},
    )

    assert await _claimed(whatsapp) is False


async def test_a_start_in_a_private_chat_is_left_to_onboarding():
    assert (
        await _claimed(_from_telegram(_telegram(f"/start {CODE}", chat_type="private")))
        is False
    )


async def test_talk_in_a_group_is_not_a_join():
    assert (
        await _claimed(_from_telegram(_telegram("@lemma_bot when are proofs due?")))
        is False
    )


async def test_a_spent_or_unknown_code_is_swallowed_rather_than_answered():
    """Answering it would put a stale link to the bot as a question."""
    request = _from_telegram(_telegram(f"/start@lemma_bot {CODE}"))

    assert await _claimed(request) is True
