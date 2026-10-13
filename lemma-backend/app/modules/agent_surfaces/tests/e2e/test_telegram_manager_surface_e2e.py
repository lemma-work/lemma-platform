"""Telegram managed-bot self-service lifecycle: a person drives the manager bot
through Lemma's ``/pods/{pod_id}/telegram-bot-setups`` API and the manager
bot's own webhook to provision a dedicated Telegram bot for a surface.

Exercises the real state machine in ``telegram_manager_service.py`` /
``telegram_manager_updates.py`` (PENDING -> WAITING_FOR_TELEGRAM ->
PROVISIONING -> COMPLETE/FAILED) end to end: real HTTP through
``authenticated_client``, the real webhook endpoint for manager-bot updates,
and ``fake_telegram``'s new managed-bot routes (``getManagedBotToken``,
``setMyProfilePhoto``, ``setMyDescription``, ``setMyShortDescription``) for
the outbound side.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent,
    _create_surface,
    _ensure_connector,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_telegram_secret_headers,
    wait_for_messages,
)

pytestmark = pytest.mark.e2e


def _wire_telegram_manager(monkeypatch, *, username: str, secret: str) -> None:
    monkeypatch.setattr(
        surface_settings, "telegram_manager_bot_token", "manager-bot-token"
    )
    monkeypatch.setattr(surface_settings, "telegram_manager_bot_username", username)
    monkeypatch.setattr(surface_settings, "telegram_manager_webhook_secret", secret)


async def test_telegram_managed_bot_full_lifecycle_completes_and_creates_surface(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """PENDING -> WAITING_FOR_TELEGRAM -> PROVISIONING -> COMPLETE, driven by
    real HTTP: the setup API, then the two updates the manager bot delivers
    (``/start`` and ``managed_bot_created``)."""
    _wire_telegram_manager(
        monkeypatch, username="lemma_manager_bot", secret="manager-secret"
    )
    await _ensure_connector(db_session, "telegram")
    # Provisioning persists through its own uow_factory() connection, not
    # db_session's -- flush() alone is invisible to it. Same pattern as
    # test_surface_api_e2e.py's connector-catalog setup.
    await db_session.commit()
    pod_id = test_pod["id"]
    agent = await _create_agent(authenticated_client, pod_id, name="Managed Bot Agent")

    start = await authenticated_client.post(
        f"/pods/{pod_id}/telegram-bot-setups",
        json={"name": "telegram-managed", "default_agent_name": agent["name"]},
    )
    assert start.status_code == 200, start.text
    setup = start.json()
    assert setup["status"] == "PENDING"
    setup_id = setup["setup_id"]
    # The link names the bot Telegram says this token belongs to, not the
    # username configured above — a stale one there is a t.me page that does not
    # open, and this is the test's stand-in for it.
    assert setup["launch_url"] == f"https://t.me/lemmabot?start=surface_{setup_id}"
    assert setup["manager_bot_username"] == "lemmabot"

    telegram_user_id = 900555001
    chat_id = 555001
    start_update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "text": f"/start surface_{setup_id}",
            "chat": {"id": chat_id},
            "from": {
                "id": telegram_user_id,
                "username": "managed_bot_owner",
                "first_name": "Owner",
            },
        },
    }
    started = await authenticated_client.post(
        "/surfaces/webhooks/telegram-manager",
        json=start_update,
        headers=build_telegram_secret_headers("manager-secret"),
    )
    assert started.status_code == 200, started.text

    waiting = await authenticated_client.get(
        f"/pods/{pod_id}/telegram-bot-setups/{setup_id}"
    )
    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["status"] == "WAITING_FOR_TELEGRAM"

    keyboard_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert "Create a dedicated Telegram bot" in keyboard_messages[-1]["text"]
    keyboard = keyboard_messages[-1]["reply_markup"]["keyboard"][0][0]
    assert keyboard["request_managed_bot"]["request_id"]

    created_update = {
        "update_id": 2,
        "message": {
            "message_id": 2,
            "chat": {"id": chat_id},
            "from": {
                "id": telegram_user_id,
                "username": "managed_bot_owner",
                "first_name": "Owner",
            },
            "managed_bot_created": {
                "bot": {"id": 777888999, "username": "surface_e2e_bot"},
            },
        },
    }
    created = await authenticated_client.post(
        "/surfaces/webhooks/telegram-manager",
        json=created_update,
        headers=build_telegram_secret_headers("manager-secret"),
    )
    assert created.status_code == 200, created.text

    completed = await authenticated_client.get(
        f"/pods/{pod_id}/telegram-bot-setups/{setup_id}"
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["status"] == "COMPLETE"
    assert body["bot_username"] == "surface_e2e_bot"
    assert body["account_id"]
    assert body["surface_id"]
    assert body["bot_launch_url"] == "https://t.me/surface_e2e_bot?start=lemma"
    assert body["error"] is None

    surface_resp = await authenticated_client.get(
        f"/pods/{pod_id}/surfaces/telegram-managed"
    )
    assert surface_resp.status_code == 200, surface_resp.text
    surface = surface_resp.json()
    assert surface["platform"] == "TELEGRAM"
    assert surface["status"] == "ACTIVE"
    assert surface["credential_mode"] == "CUSTOM"
    assert surface["agent_name"] == agent["name"]

    token_calls = message_store.get_all("TELEGRAM_MANAGED_BOT_TOKEN")
    assert token_calls
    assert token_calls[-1]["user_id"] == 777888999

    configuration_calls = message_store.get_all("TELEGRAM_CONFIGURATION")
    methods = {call["method"] for call in configuration_calls}
    assert {
        "setMyDescription",
        "setMyShortDescription",
        "setMyCommands",
        "setChatMenuButton",
        "setMyProfilePhoto",
    } <= methods
    photo_call = next(
        call for call in configuration_calls if call["method"] == "setMyProfilePhoto"
    )
    assert photo_call["has_file"] is True
    assert photo_call["filename"] == "lemma-agent.jpg"

    success_texts = [msg.get("text") for msg in message_store.get_all("TELEGRAM")]
    assert any(
        "connected to Lemma and ready to use" in (t or "") for t in success_texts
    )


async def test_telegram_managed_bot_without_an_agent_goes_to_the_pod_assistant(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """The web app starts a setup naming no agent. That means the pod's own
    assistant, as on the plain create -- not an ownerless surface refused at
    the end, after Telegram has already made the bot."""
    _wire_telegram_manager(
        monkeypatch, username="lemma_manager_bot", secret="manager-secret"
    )
    await _ensure_connector(db_session, "telegram")
    await db_session.commit()
    pod_id = test_pod["id"]

    start = await authenticated_client.post(
        f"/pods/{pod_id}/telegram-bot-setups", json={}
    )
    assert start.status_code == 200, start.text
    setup_id = start.json()["setup_id"]

    sender = {"id": 900555077, "username": "assistant_owner", "first_name": "Owner"}
    for update in (
        {
            "update_id": 77001,
            "message": {
                "message_id": 1,
                "text": f"/start surface_{setup_id}",
                "chat": {"id": 555077},
                "from": sender,
            },
        },
        {
            "update_id": 77002,
            "message": {
                "message_id": 2,
                "chat": {"id": 555077},
                "from": sender,
                "managed_bot_created": {
                    "bot": {"id": 777888077, "username": "assistant_e2e_bot"},
                },
            },
        },
    ):
        delivered = await authenticated_client.post(
            "/surfaces/webhooks/telegram-manager",
            json=update,
            headers=build_telegram_secret_headers("manager-secret"),
        )
        assert delivered.status_code == 200, delivered.text

    completed = await authenticated_client.get(
        f"/pods/{pod_id}/telegram-bot-setups/{setup_id}"
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["status"] == "COMPLETE", body
    assert body["error"] is None

    surfaces = await authenticated_client.get(f"/pods/{pod_id}/surfaces")
    assert surfaces.status_code == 200, surfaces.text
    telegram = [
        surface
        for surface in surfaces.json()["items"]
        if surface["id"] == body["surface_id"]
    ]
    assert len(telegram) == 1
    assert telegram[0]["status"] == "ACTIVE"
    # The pod's own assistant is the surface's agent, named by no agent name.
    assert telegram[0]["agent_name"] is None


async def test_telegram_managed_bot_provisioning_failure_marks_setup_failed(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """When persistence fails permanently (here: the ``telegram`` connector
    catalog entry is missing, so the auth-config insert violates its FK), the
    setup lands in FAILED with a caller-facing error and reservations are
    released rather than left stuck in PROVISIONING."""
    _wire_telegram_manager(
        monkeypatch, username="lemma_manager_bot", secret="manager-secret-2"
    )
    # Deliberately do NOT call `_ensure_connector(db_session, "telegram")`:
    # `persist_managed_bot` inserts an AuthConfig row with connector_id
    # "telegram", and without a matching `connectors` row the FK violation
    # raises `IntegrityError`, which `_provision` classifies as permanent.
    pod_id = test_pod["id"]

    start = await authenticated_client.post(
        f"/pods/{pod_id}/telegram-bot-setups",
        json={"name": "telegram-managed-failure"},
    )
    assert start.status_code == 200, start.text
    setup_id = start.json()["setup_id"]

    telegram_user_id = 900555002
    chat_id = 555002
    start_update = {
        "update_id": 10,
        "message": {
            "message_id": 10,
            "text": f"/start surface_{setup_id}",
            "chat": {"id": chat_id},
            "from": {"id": telegram_user_id, "username": "failure_owner"},
        },
    }
    started = await authenticated_client.post(
        "/surfaces/webhooks/telegram-manager",
        json=start_update,
        headers=build_telegram_secret_headers("manager-secret-2"),
    )
    assert started.status_code == 200, started.text

    created_update = {
        "update_id": 11,
        "message": {
            "message_id": 11,
            "chat": {"id": chat_id},
            "from": {"id": telegram_user_id, "username": "failure_owner"},
            "managed_bot_created": {
                "bot": {"id": 424242, "username": "will_fail_bot"},
            },
        },
    }
    created = await authenticated_client.post(
        "/surfaces/webhooks/telegram-manager",
        json=created_update,
        headers=build_telegram_secret_headers("manager-secret-2"),
    )
    assert created.status_code == 200, created.text

    failed = await authenticated_client.get(
        f"/pods/{pod_id}/telegram-bot-setups/{setup_id}"
    )
    assert failed.status_code == 200, failed.text
    body = failed.json()
    assert body["status"] == "FAILED"
    assert body["error"]
    assert body["account_id"] is None
    assert body["surface_id"] is None

    failure_texts = [msg.get("text") for msg in message_store.get_all("TELEGRAM")]
    assert any("could not finish connecting" in (t or "") for t in failure_texts), (
        failure_texts
    )

    # A fresh setup for the SAME target is allowed again -- FAILED released
    # its target/telegram-user reservations rather than leaving them stuck.
    retry = await authenticated_client.post(
        f"/pods/{pod_id}/telegram-bot-setups",
        json={"name": "telegram-managed-failure"},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["setup_id"] != setup_id


async def test_telegram_managed_bot_setup_rejects_duplicate_surface_name(
    authenticated_client: AsyncClient,
    test_pod,
    fake_telegram,
    monkeypatch,
):
    """Starting a setup for a surface name that already exists is rejected
    up front, before any Telegram interaction (no dangling PENDING setup)."""
    _wire_telegram_manager(
        monkeypatch, username="lemma_manager_bot", secret="manager-secret-3"
    )
    pod_id = test_pod["id"]
    # No explicit name on either side: both resolve to the same platform
    # default ("telegram"), so the setup collides with the surface that
    # already occupies it.
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})

    conflict = await authenticated_client.post(
        f"/pods/{pod_id}/telegram-bot-setups",
        json={},
    )
    assert conflict.status_code == 409, conflict.text
