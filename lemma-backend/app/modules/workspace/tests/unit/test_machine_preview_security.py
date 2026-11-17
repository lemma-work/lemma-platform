"""What a preview lets through, and for how long.

A preview is somebody's dev server on its own subdomain. What it must never
get: Lemma's session cookies (which reach it when the session cookie is scoped
to a parent domain), a way to set cookies on the parent domain, a reusable
ticket, a visitor who has since lost the machine, a life with no end, or the
machine's own DevTools port.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from fakeredis import aioredis as fake_aioredis

from app.modules.workspace.api.controllers.port_proxy_controller import _set_cookies
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine import MachineKind
from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.services import machine_previews
from app.modules.workspace.services.machine_previews import (
    ACCESS_RECHECK_SECONDS,
    MachinePreviews,
    PreviewKeyMissing,
    cookie_user,
    forwarded_cookie,
    may_still_use,
    mint_cookie,
    mint_ticket,
    redeem_ticket,
    refuse_unpreviewable_port,
    upstream_set_cookie,
)


LABEL = "a" * 20


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch) -> None:
    monkeypatch.setattr(workspace_settings, "runtime_credential_key", "k" * 40)


@pytest.fixture
def redis():
    fake = fake_aioredis.FakeRedis(decode_responses=True)
    machine_previews._use_redis_for_tests(fake)
    yield fake
    machine_previews._use_redis_for_tests(None)


# --- cookies ------------------------------------------------------------------


def test_lemmas_session_cookies_never_reach_the_preview() -> None:
    sent = (
        "sAccessToken=a; sRefreshToken=r; sFrontToken=f; st-access-token=x; "
        "sAntiCsrf=c; lemma_app_session=s; lemma_preview=p; theme=dark; sid=abc"
    )

    assert forwarded_cookie(sent) == "theme=dark; sid=abc"


def test_only_lemma_cookies_is_no_cookie_header() -> None:
    assert forwarded_cookie("sAccessToken=a; lemma_preview=p") is None


def test_an_upstream_cookie_loses_its_domain() -> None:
    assert (
        upstream_set_cookie("sid=1; Domain=.preview.test; Path=/; HttpOnly")
        == "sid=1; Path=/; HttpOnly"
    )
    assert upstream_set_cookie("sid=1;domain=preview.test") == "sid=1"


@pytest.mark.parametrize(
    "name", ["sAccessToken", "sRefreshToken", "st-access-token", "lemma_preview"]
)
def test_an_upstream_may_not_set_a_lemma_cookie(name: str) -> None:
    assert upstream_set_cookie(f"{name}=forged; Path=/") is None


def test_the_proxy_answers_each_upstream_cookie_on_its_own_header() -> None:
    upstream = httpx.Headers(
        [
            ("set-cookie", "a=1; Domain=.lemma.work; Path=/"),
            ("set-cookie", "sAccessToken=forged; Domain=.lemma.work"),
            ("set-cookie", "b=2; Expires=Wed, 21 Oct 2026 07:28:00 GMT"),
        ]
    )

    assert _set_cookies(upstream) == [
        "a=1; Path=/",
        "b=2; Expires=Wed, 21 Oct 2026 07:28:00 GMT",
    ]


# --- signing ------------------------------------------------------------------


def test_signing_fails_closed_without_a_key(monkeypatch) -> None:
    monkeypatch.setattr(workspace_settings, "runtime_credential_key", "")
    with pytest.raises(PreviewKeyMissing):
        mint_ticket(LABEL, uuid4())
    with pytest.raises(PreviewKeyMissing):
        cookie_user("anything.signed", LABEL)


def test_the_cookie_names_the_person_it_was_opened_for() -> None:
    person = uuid4()
    cookie = mint_cookie(LABEL, person)

    assert cookie_user(cookie, LABEL) == person
    assert cookie_user(cookie, "b" * 20) is None, "bound to its preview"


async def test_a_ticket_is_spent_on_first_use(redis) -> None:
    person = uuid4()
    ticket = mint_ticket(LABEL, person)

    assert await redeem_ticket(ticket, LABEL) == person
    assert await redeem_ticket(ticket, LABEL) is None, "a replay is refused"


async def test_a_ticket_for_another_preview_is_refused(redis) -> None:
    assert await redeem_ticket(mint_ticket(LABEL, uuid4()), "b" * 20) is None


# --- the person's access, rechecked -------------------------------------------


async def test_a_removed_member_loses_the_preview_once_the_cache_lapses(
    redis,
) -> None:
    machine, person = uuid4(), uuid4()
    member = {"still": True}

    async def check() -> bool:
        return member["still"]

    assert await may_still_use(machine, person, check)
    member["still"] = False
    # Believed for at most the recheck window...
    assert await may_still_use(machine, person, check)
    key = f"machine:preview:access:v1:{machine}:{person}"
    assert 0 < await redis.ttl(key) <= ACCESS_RECHECK_SECONDS
    # ...and asked again once it lapses.
    await redis.delete(key)
    assert not await may_still_use(machine, person, check)


# --- lifetime and ports ---------------------------------------------------------


class _Session:
    def __init__(self) -> None:
        self.rows: list = []

    def add(self, row) -> None:
        self.rows.append(row)

    async def flush(self) -> None:
        for row in self.rows:
            row.id = uuid4()
            row.created_at = datetime.now(timezone.utc)


class _Uow:
    def __init__(self) -> None:
        self.session = _Session()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def commit(self) -> None:
        return None


async def test_a_private_preview_without_a_ttl_still_expires(monkeypatch) -> None:
    monkeypatch.setattr(workspace_settings, "machine_preview_domain", "preview.test")
    monkeypatch.setattr(workspace_settings, "machine_preview_max_ttl_seconds", 3600)

    preview = await MachinePreviews(_Uow).create(
        uuid4(), port=5173, visibility="private", ttl_seconds=None, created_by=uuid4()
    )

    assert preview.expires_at is not None
    left = preview.expires_at - datetime.now(timezone.utc)
    assert timedelta(minutes=59) < left <= timedelta(hours=1)


@pytest.mark.parametrize("port", [9222, 8080, 4848, 5900])
def test_the_machines_own_ports_and_devtools_are_not_previews(port: int) -> None:
    with pytest.raises(MachineConflict) as refused:
        refuse_unpreviewable_port(MachineKind.POD, port)
    assert refused.value.code == "MACHINE_PORT_RESERVED"


def test_a_connected_machine_keeps_8080_but_never_devtools() -> None:
    refuse_unpreviewable_port(MachineKind.CONNECTED, 8080)
    with pytest.raises(MachineConflict):
        refuse_unpreviewable_port(MachineKind.CONNECTED, 9222)


def test_an_ordinary_dev_server_port_is_a_preview() -> None:
    refuse_unpreviewable_port(MachineKind.DEFAULT, 5173)


def test_the_api_and_the_relay_refuse_from_one_list() -> None:
    from sandbox_runtime.workspace.port_relay import refusal

    assert refusal(9222) is not None
