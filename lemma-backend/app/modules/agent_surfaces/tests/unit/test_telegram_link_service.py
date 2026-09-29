"""Minting a Telegram link for the signed-in user."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import fakeredis.aioredis
import pytest

from app.modules.agent_surfaces.domain.errors import (
    TelegramLinkPodUnavailableError,
    TelegramSystemBotUnavailableError,
)
from app.modules.agent_surfaces.services.telegram_link_service import (
    LinkablePod,
    LinkablePods,
    TelegramLinkService,
)
from app.modules.agent_surfaces.services.telegram_link_tokens import (
    START_PAYLOAD_PREFIX,
    TelegramLinkTokenStore,
)

pytestmark = pytest.mark.unit

HOME = LinkablePod(uuid4(), "Home")
WORK = LinkablePod(uuid4(), "Work")


def _service(
    *,
    username: str | None = "lemma_home_bot",
    configured: bool = True,
    pods: LinkablePods = ([HOME, WORK], WORK.id),
) -> tuple[TelegramLinkService, TelegramLinkTokenStore]:
    tokens = TelegramLinkTokenStore(redis=fakeredis.aioredis.FakeRedis())

    async def bot_username() -> str | None:
        return username

    async def pods_for(_user_id: UUID) -> LinkablePods:
        return pods

    service = TelegramLinkService(
        uows=None,  # type: ignore[arg-type]  # every read goes through pods_for
        tokens=tokens,
        bot_username=bot_username,
        bot_configured=lambda: configured,
        pods_for=pods_for,
    )
    return service, tokens


def _token(url: str) -> str:
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https",
        "t.me",
        "/lemma_home_bot",
    )
    payload = parse_qs(parsed.query)["start"][0]
    assert payload.startswith(START_PAYLOAD_PREFIX)
    return payload.removeprefix(START_PAYLOAD_PREFIX)


async def test_a_minted_link_opens_the_bot_and_redeems_to_the_caller():
    service, tokens = _service()
    user_id = uuid4()

    link = await service.mint(user_id, HOME.id)

    assert link.bot_username == "lemma_home_bot"
    grant = await tokens.consume(_token(link.url))
    assert grant is not None
    assert (grant.user_id, grant.pod_id) == (user_id, HOME.id)


async def test_without_a_pod_the_link_answers_from_the_suggested_one():
    service, tokens = _service()

    link = await service.mint(uuid4(), None)

    assert link.pod_id == WORK.id
    assert (await tokens.consume(_token(link.url))).pod_id == WORK.id


async def test_a_pod_the_caller_cannot_attach_to_is_refused():
    service, _ = _service()

    with pytest.raises(TelegramLinkPodUnavailableError) as refused:
        await service.mint(uuid4(), uuid4())
    assert refused.value.status_code == 403


async def test_no_shared_bot_is_a_clear_conflict():
    service, _ = _service(configured=False)

    with pytest.raises(TelegramSystemBotUnavailableError) as refused:
        await service.mint(uuid4(), None)
    assert refused.value.status_code == 409
    assert "Telegram" in refused.value.message


async def test_a_token_telegram_does_not_recognise_is_the_same_conflict():
    service, _ = _service(username=None)

    with pytest.raises(TelegramSystemBotUnavailableError):
        await service.options(uuid4())


async def test_options_name_the_bot_and_every_pod():
    service, _ = _service()

    options = await service.options(uuid4())

    assert options.bot_username == "lemma_home_bot"
    assert options.pods == [HOME, WORK]
    assert options.pod_id == WORK.id
