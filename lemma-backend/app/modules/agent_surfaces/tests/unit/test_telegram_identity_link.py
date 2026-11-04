"""A member proves their Telegram account: the code's rules.

The failure this answers: a pod member with no verified phone could never be
recognised by a pod's own Telegram bot, and in a group ran as a stranger. Each
test pins one rule of the code that proves the account instead:

* only a private chat's ``/start lk_<code>`` is one;
* the code is its minter's to read, confirm or decline, nobody else's;
* opened from the account it was made for, the member sees that account;
  opened from another, it is spent and the member is told -- the reply to
  whoever opened it never names the member;
* it opens once, and only through the bot it names.

Binding the confirmed account is pinned end to end
(``test_telegram_identity_link_e2e``).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.telegram_identity_link import (
    EXPIRED_REPLY,
    OPENED_REPLY,
    OTHER_ACCOUNT_REPLY,
    LinkCode,
    LinkState,
    decline_link,
    link_status,
    mint_link,
    open_link,
    start_code,
)

pytestmark = pytest.mark.unit


class _Redis:
    """The four calls the link makes, kept in a dict."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int, nx: bool = False):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def get(self, key: str):
        return self.values.get(key)

    async def delete(self, *keys: str) -> int:
        return sum(1 for key in keys if self.values.pop(key, None) is not None)


def _dm(sender: str, *, name: str = "Anu", is_dm: bool = True):
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id=sender,
        sender_external_user_id=sender,
        sender_display_name=name,
        message_text="/start lk_x",
        is_dm=is_dm,
        metadata={"sender_username": "anu_k"},
    )


async def _minted(redis: _Redis, *, expected: str | None = None):
    member, surface = uuid4(), uuid4()
    code, url, _ = await mint_link(
        LinkCode(
            user_id=member,
            pod_id=uuid4(),
            surface_id=surface,
            expected_external_user_id=expected,
        ),
        bot_username="pod_bot",
        redis=redis,
    )
    assert url == f"https://t.me/pod_bot?start=lk_{code}"
    return code, member, surface


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("/start lk_" + "a" * 32, "a" * 32),
        ("/start@pod_bot lk_" + "b" * 32, "b" * 32),
        # A group's add-the-bot code is not a link to an account.
        ("/start " + "c" * 32, None),
        ("hello lk_" + "d" * 32, None),
    ],
)
def test_only_a_start_with_a_link_code_is_one(text: str, code: str | None):
    assert start_code(text) == code


async def test_a_code_opened_from_the_account_it_was_made_for_shows_that_account():
    redis = _Redis()
    code, member, surface = await _minted(redis, expected="555")

    reply = await open_link(
        code,
        parsed=_dm("555"),
        received_by=[surface],
        shared_bot_surface=False,
        redis=redis,
    )
    status = await link_status(code, user_id=member, redis=redis)

    assert reply == OPENED_REPLY
    assert status.state is LinkState.OPENED
    assert status.account is not None
    assert (status.account.name, status.account.username) == ("Anu", "anu_k")


async def test_a_code_opened_from_another_account_is_spent_and_the_member_told():
    """A forwarded link: whoever opened it learns nothing about the member."""
    redis = _Redis()
    code, member, surface = await _minted(redis, expected="555")

    reply = await open_link(
        code,
        parsed=_dm("999"),
        received_by=[surface],
        shared_bot_surface=False,
        redis=redis,
    )

    assert reply == OTHER_ACCOUNT_REPLY
    assert "Lemma" not in reply or "Anu" not in reply
    assert (await link_status(code, user_id=member, redis=redis)).state is (
        LinkState.OTHER_ACCOUNT
    )
    # Spent: the right account opening it now is too late.
    again = await open_link(
        code,
        parsed=_dm("555"),
        received_by=[surface],
        shared_bot_surface=False,
        redis=redis,
    )
    assert again == EXPIRED_REPLY


async def test_a_code_opens_once():
    redis = _Redis()
    code, member, surface = await _minted(redis)

    first = await open_link(
        code,
        parsed=_dm("555"),
        received_by=[surface],
        shared_bot_surface=False,
        redis=redis,
    )
    second = await open_link(
        code,
        parsed=_dm("777"),
        received_by=[surface],
        shared_bot_surface=False,
        redis=redis,
    )

    assert (first, second) == (OPENED_REPLY, EXPIRED_REPLY)
    status = await link_status(code, user_id=member, redis=redis)
    assert status.account is not None and status.account.external_user_id == "555"


async def test_a_code_opens_only_through_the_bot_it_names():
    redis = _Redis()
    code, _, _ = await _minted(redis)

    elsewhere = await open_link(
        code,
        parsed=_dm("555"),
        received_by=[uuid4()],
        shared_bot_surface=False,
        redis=redis,
    )
    # A platform-wide delivery is the shared bot's, never a pod's own.
    fanned_in = await open_link(
        code, parsed=_dm("555"), received_by=None, shared_bot_surface=False, redis=redis
    )

    assert elsewhere == fanned_in == EXPIRED_REPLY


async def test_the_shared_bot_may_carry_a_code_for_its_own_surface():
    redis = _Redis()
    code, member, _ = await _minted(redis)

    reply = await open_link(
        code, parsed=_dm("555"), received_by=None, shared_bot_surface=True, redis=redis
    )

    assert reply == OPENED_REPLY
    assert (await link_status(code, user_id=member, redis=redis)).state is (
        LinkState.OPENED
    )


async def test_a_code_in_a_group_is_not_an_account_proving_itself():
    redis = _Redis()
    code, member, surface = await _minted(redis)

    reply = await open_link(
        code,
        parsed=_dm("555", is_dm=False),
        received_by=[surface],
        shared_bot_surface=False,
        redis=redis,
    )

    assert reply == EXPIRED_REPLY
    assert (await link_status(code, user_id=member, redis=redis)).state is (
        LinkState.WAITING
    )


async def test_a_code_is_its_minters_alone():
    redis = _Redis()
    code, member, surface = await _minted(redis)
    await open_link(
        code,
        parsed=_dm("555"),
        received_by=[surface],
        shared_bot_surface=False,
        redis=redis,
    )

    assert (await link_status(code, user_id=uuid4(), redis=redis)).state is (
        LinkState.GONE
    )
    await decline_link(code, user_id=uuid4(), redis=redis)
    assert (await link_status(code, user_id=member, redis=redis)).state is (
        LinkState.OPENED
    )


async def test_declining_spends_it():
    redis = _Redis()
    code, member, surface = await _minted(redis)
    await open_link(
        code,
        parsed=_dm("555"),
        received_by=[surface],
        shared_bot_surface=False,
        redis=redis,
    )

    await decline_link(code, user_id=member, redis=redis)

    assert (await link_status(code, user_id=member, redis=redis)).state is (
        LinkState.GONE
    )


async def test_no_bot_username_no_link():
    with pytest.raises(Exception, match="called"):
        await mint_link(
            LinkCode(user_id=uuid4(), pod_id=uuid4(), surface_id=uuid4()),
            bot_username="Sales Bot",
            redis=_Redis(),
        )
