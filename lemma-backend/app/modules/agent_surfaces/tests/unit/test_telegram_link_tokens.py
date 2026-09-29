"""The one-time links that connect a Telegram chat to a signed-in user."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import fakeredis.aioredis
import pytest

from app.modules.agent_surfaces.services.telegram_link_tokens import (
    START_PAYLOAD_PREFIX,
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramLinkTokenStore,
    link_token_from_start,
)

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def _store(clock: _Clock | None = None) -> TelegramLinkTokenStore:
    return TelegramLinkTokenStore(
        redis=fakeredis.aioredis.FakeRedis(), clock=clock or _Clock()
    )


async def test_a_link_resolves_to_the_user_and_pod_it_was_minted_for():
    store = _store()
    user_id, pod_id = uuid4(), uuid4()

    minted = await store.mint(user_id=user_id, pod_id=pod_id)
    grant = await store.consume(minted.token)

    assert grant is not None
    assert (grant.user_id, grant.pod_id) == (user_id, pod_id)


async def test_a_link_works_once():
    """A redelivered or double-tapped `/start` must not link twice."""
    store = _store()
    minted = await store.mint(user_id=uuid4())

    assert await store.consume(minted.token) is not None
    assert await store.consume(minted.token) is None


async def test_a_link_past_its_ten_minutes_is_refused():
    clock = _Clock()
    store = _store(clock)
    minted = await store.mint(user_id=uuid4())

    clock.now += timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS)

    assert await store.consume(minted.token) is None


async def test_a_token_only_ever_answers_for_the_user_who_minted_it():
    """Two people's links never cross, and a guessed token is nobody's."""
    store = _store()
    alice, bob = uuid4(), uuid4()
    alices = await store.mint(user_id=alice)
    bobs = await store.mint(user_id=bob)

    assert alices.token != bobs.token
    assert (await store.consume(bobs.token)).user_id == bob
    assert (await store.consume(alices.token)).user_id == alice
    assert await store.consume("x" * len(alices.token)) is None


async def test_the_token_is_not_stored_in_the_clear():
    redis = fakeredis.aioredis.FakeRedis()
    store = TelegramLinkTokenStore(redis=redis)
    minted = await store.mint(user_id=uuid4())

    keys = [key.decode() for key in await redis.keys("*")]
    assert len(keys) == 1
    assert minted.token not in keys[0]


async def test_a_malformed_token_is_refused_without_a_lookup():
    store = TelegramLinkTokenStore(redis=None)

    # No Redis was given and none is reached for: the shape check comes first.
    assert await store.consume("not a token") is None
    assert await store.consume("") is None


async def test_the_start_payload_fits_telegrams_limit():
    minted = await _store().mint(user_id=uuid4())

    assert minted.start_payload.startswith(START_PAYLOAD_PREFIX)
    assert len(minted.start_payload) <= 64


@pytest.mark.parametrize(
    ("text", "token"),
    [
        ("/start link_abcdefghijklmnop", "abcdefghijklmnop"),
        ("  /start   link_abcdefghijklmnop ", "abcdefghijklmnop"),
        ("/start@lemmabot link_abcdefghijklmnop", "abcdefghijklmnop"),
        ("/START link_abcdefghijklmnop", "abcdefghijklmnop"),
        ("/start", None),
        ("/start hello", None),
        ("/help link_abcdefghijklmnop", None),
        ("please /start link_abcdefghijklmnop", None),
        ("", None),
    ],
)
def test_only_a_start_command_with_a_link_payload_carries_a_token(text, token):
    assert link_token_from_start(text) == token
