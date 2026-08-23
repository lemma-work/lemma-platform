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
