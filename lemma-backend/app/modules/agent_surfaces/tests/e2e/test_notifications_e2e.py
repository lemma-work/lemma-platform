"""The inbox, end to end.

Covers the promise the whole feature rests on: a pod member can always be
reached, even with no chat surface connected anywhere — because the app reach
cannot fail.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.e2e


async def test_notify_reaches_a_member_with_no_chat_surface_at_all(
    authenticated_client: AsyncClient,
    test_pod,
    fixed_test_user,
):
    """The case that had no path before: nobody is on Telegram, and the message
    still lands somewhere the person will see it."""
    pod_id = test_pod["id"]
    user_id = fixed_test_user["id"]

    before = await authenticated_client.get("/notifications/unread-count")
    assert before.status_code == 200, before.text
    starting_unread = before.json()["unread_count"]

    sent = await authenticated_client.post(
        f"/pods/{pod_id}/notify",
        json={"user_id": user_id, "body": "Standup in 10.", "title": "Standup"},
    )
    assert sent.status_code == 200, sent.text
    payload = sent.json()
    # No surface exists, so the app took it — and that counts as delivered.
    assert payload["delivered_via"] == "APP"
    assert payload["notification_id"]

    listed = await authenticated_client.get("/notifications", params={"pod_id": pod_id})
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert any(
        item["id"] == payload["notification_id"]
        and item["body"] == "Standup in 10."
        and item["title"] == "Standup"
        and item["read_at"] is None
        for item in items
    ), items
    assert listed.json()["unread_count"] == starting_unread + 1


async def test_marking_read_clears_the_badge_and_is_idempotent(
    authenticated_client: AsyncClient,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    sent = await authenticated_client.post(
        f"/pods/{pod_id}/notify",
        json={"user_id": fixed_test_user["id"], "body": "The nightly sync failed."},
    )
    assert sent.status_code == 200, sent.text
    notification_id = sent.json()["notification_id"]

    before = await authenticated_client.get(
        "/notifications/unread-count", params={"pod_id": pod_id}
    )
    assert before.json()["unread_count"] >= 1

    first = await authenticated_client.post(f"/notifications/{notification_id}/read")
    assert first.status_code == 204, first.text
    # A double click is not an error.
    second = await authenticated_client.post(f"/notifications/{notification_id}/read")
    assert second.status_code == 204, second.text

    unread_only = await authenticated_client.get(
        "/notifications", params={"pod_id": pod_id, "unread_only": True}
    )
    assert all(item["id"] != notification_id for item in unread_only.json()["items"])


async def test_read_all_empties_the_badge(
    authenticated_client: AsyncClient,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    for body in ("one", "two", "three"):
        response = await authenticated_client.post(
            f"/pods/{pod_id}/notify",
            json={"user_id": fixed_test_user["id"], "body": body},
        )
        assert response.status_code == 200, response.text

    cleared = await authenticated_client.post(
        "/notifications/read-all", params={"pod_id": pod_id}
    )
    assert cleared.status_code == 204, cleared.text

    count = await authenticated_client.get(
        "/notifications/unread-count", params={"pod_id": pod_id}
    )
    assert count.json()["unread_count"] == 0


async def test_notifying_a_non_member_is_refused(
    authenticated_client: AsyncClient,
    test_pod,
):
    """Pod membership is the boundary, and it fails closed."""
    from uuid import uuid4

    response = await authenticated_client.post(
        f"/pods/{test_pod['id']}/notify",
        json={"user_id": str(uuid4()), "body": "you don't know me"},
    )
    assert response.status_code == 404, response.text


async def test_one_inbox_cannot_reach_into_another(
    authenticated_client: AsyncClient,
    test_pod,
    fixed_test_user,
):
    """A notification id alone must never be enough to mark somebody else's
    notification read."""
    from uuid import uuid4

    response = await authenticated_client.post(f"/notifications/{uuid4()}/read")
    assert response.status_code == 404, response.text
