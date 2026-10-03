"""E2E for the user-scoped ``/surfaces/me`` routes (WS4): list a user's surfaces
across their pods and set a per-platform default when several could answer them.

Exercises the real HTTP layer + service + ``users.preferences`` round-trip
(persisted through the DB), which powers the shared-bot/multi-pod
disambiguation the resolver consumes."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.tests.e2e.helpers import _create_surface

pytestmark = pytest.mark.e2e


def _group_by_platform(payload: dict) -> dict:
    return {g["platform"]: g for g in payload["groups"]}


async def test_surfaces_me_lists_and_sets_default(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]

    surface = await _create_surface(
        authenticated_client, pod_id, config={"type": "TELEGRAM"}
    )

    # 1. List — the surface shows up, not a conflict, no default yet.
    resp = await authenticated_client.get("/surfaces/me")
    assert resp.status_code == 200, resp.text
    groups = _group_by_platform(resp.json())
    assert "TELEGRAM" in groups
    tg = groups["TELEGRAM"]
    assert tg["conflict"] is False
    assert tg["default_surface_id"] is None
    assert any(
        s["id"] == surface["id"] and s["is_default"] is False for s in tg["surfaces"]
    )
    # Nothing else answers at this address, so there is nothing to choose.
    assert all(s["shares_address"] is False for s in tg["surfaces"])

    # 2. Set the default.
    put = await authenticated_client.put(
        "/surfaces/me/default",
        json={"platform": "TELEGRAM", "surface_id": surface["id"]},
    )
    assert put.status_code == 200, put.text
    tg_after = _group_by_platform(put.json())["TELEGRAM"]
    assert tg_after["default_surface_id"] == surface["id"]
    assert any(
        s["id"] == surface["id"] and s["is_default"] is True
        for s in tg_after["surfaces"]
    )

    # 3. It persists (users.preferences round-trips through the DB).
    resp2 = await authenticated_client.get("/surfaces/me")
    tg_persisted = _group_by_platform(resp2.json())["TELEGRAM"]
    assert tg_persisted["default_surface_id"] == surface["id"]


async def test_surfaces_me_rejects_default_for_surface_outside_user_pods(
    authenticated_client: AsyncClient,
    test_pod,
    fixed_test_user,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")

    # A surface id the user has no access to → 404 (no existence leak).
    put = await authenticated_client.put(
        "/surfaces/me/default",
        json={"platform": "TELEGRAM", "surface_id": str(uuid4())},
    )
    assert put.status_code == 404, put.text


@pytest.fixture
def shared_whatsapp(monkeypatch):
    """The deployment's shared WhatsApp number, configured."""
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")


async def test_a_pod_without_a_whatsapp_surface_can_be_chosen(
    authenticated_client: AsyncClient,
    test_pod,
    fixed_test_user,
    shared_whatsapp,
):
    """The profile picker lists every pod, and picking one makes its surface.

    Before, the default could only point at a pod that already had a surface on
    the shared number -- so somebody whose WhatsApp signup attached one pod had
    no way to move to another they had never chatted with.
    """
    second = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": test_pod["organization_id"],
            "name": f"Second {uuid4().hex[:6]}",
        },
    )
    assert second.status_code == 201, second.text

    listed = await authenticated_client.get("/surfaces/me")
    assert listed.status_code == 200, listed.text
    whatsapp = _group_by_platform(listed.json())["WHATSAPP"]
    assert whatsapp["surfaces"] == []
    assert whatsapp["default_pod_id"] is None
    assert {pod["pod_id"] for pod in whatsapp["available_pods"]} == {
        test_pod["id"],
        second.json()["id"],
    }
    assert all(pod["organization_name"] for pod in whatsapp["available_pods"])

    put = await authenticated_client.put(
        "/surfaces/me/default",
        json={"platform": "WHATSAPP", "pod_id": second.json()["id"]},
    )
    assert put.status_code == 200, put.text
    chosen = _group_by_platform(put.json())["WHATSAPP"]
    assert chosen["default_pod_id"] == second.json()["id"]
    assert [s["pod_id"] for s in chosen["surfaces"]] == [second.json()["id"]]
    assert chosen["default_surface_id"] == chosen["surfaces"][0]["id"]


async def test_a_pod_whose_assistant_has_its_own_whatsapp_is_refused_with_why(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    shared_whatsapp,
):
    from uuid import UUID

    from app.modules.agent_surfaces.infrastructure.models import AgentSurface

    db_session.add(
        AgentSurface(
            pod_id=UUID(test_pod["id"]),
            organization_id=UUID(test_pod["organization_id"]),
            agent_id=UUID(test_pod["id"]),
            name=f"whatsapp-own-{uuid4().hex[:6]}",
            surface_type="WHATSAPP",
            event_mode="WEBHOOK",
            credential_mode="CUSTOM",
            config={},
        )
    )
    await db_session.commit()

    put = await authenticated_client.put(
        "/surfaces/me/default",
        json={"platform": "WHATSAPP", "pod_id": test_pod["id"]},
    )
    assert put.status_code == 409, put.text
    assert "through its own connection" in put.json()["message"]


async def test_a_default_names_one_target_not_both(
    authenticated_client: AsyncClient, test_pod, fixed_test_user
):
    put = await authenticated_client.put(
        "/surfaces/me/default", json={"platform": "WHATSAPP"}
    )
    assert put.status_code == 422, put.text


async def test_a_pod_they_are_not_in_cannot_be_chosen(
    authenticated_client: AsyncClient, test_pod, fixed_test_user, shared_whatsapp
):
    put = await authenticated_client.put(
        "/surfaces/me/default",
        json={"platform": "WHATSAPP", "pod_id": str(uuid4())},
    )
    assert put.status_code == 404, put.text
