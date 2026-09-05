"""Rejected instruction edits leave the persisted agent unchanged."""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.e2e


async def test_instruction_limit_is_atomic_on_create_and_update(
    authenticated_client, fixed_test_org
):
    pod = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Instructions {uuid4().hex[:8]}",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod.status_code == 201, pod.text
    base = f"/pods/{pod.json()['id']}/agents"
    name = f"bounded_{uuid4().hex[:8]}"
    rejected = await authenticated_client.post(
        base, json={"name": name, "instruction": "x" * 60_001, "toolsets": []}
    )
    assert rejected.status_code == 422, rejected.text
    missing = await authenticated_client.get(f"{base}/{name}")
    assert missing.status_code == 404, missing.text

    original = "x" * 60_000
    created = await authenticated_client.post(
        base, json={"name": name, "instruction": original, "toolsets": []}
    )
    assert created.status_code == 201, created.text
    assert created.json()["instruction"] == original
    unicode_instruction = "🙂" * 60_000
    updated = await authenticated_client.patch(
        f"{base}/{name}", json={"instruction": unicode_instruction}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["instruction"] == unicode_instruction

    rejected = await authenticated_client.patch(
        f"{base}/{name}",
        json={"instruction": unicode_instruction + "x", "description": "Discard this"},
    )
    assert rejected.status_code == 422, rejected.text
    reopened = await authenticated_client.get(f"{base}/{name}")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["instruction"] == unicode_instruction
    assert reopened.json()["description"] is None

    description_only = await authenticated_client.patch(
        f"{base}/{name}", json={"description": "Keep the instruction"}
    )
    assert description_only.status_code == 200, description_only.text
    assert description_only.json()["instruction"] == unicode_instruction
