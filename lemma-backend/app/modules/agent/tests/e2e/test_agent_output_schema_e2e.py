"""E2E: an explicit null output_schema on update clears a stored schema.

Mirrors the CLI pod-import behavior (a bundle that omits output_schema sends an
explicit null) so a re-imported agent never retains a stale output schema, while
an update that simply omits the field leaves it untouched.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status

pytestmark = pytest.mark.e2e


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"agent-schema-{uuid4().hex[:8]}",
            "description": "output_schema clearing e2e",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


class TestAgentOutputSchemaClearing:
    @pytest.mark.asyncio
    async def test_explicit_null_clears_output_schema(
        self, authenticated_client, fixed_test_org
    ):
        pod_id = await _create_pod(authenticated_client, fixed_test_org)
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
        }
        created = await authenticated_client.post(
            f"/pods/{pod_id}/agents",
            json={
                "name": "triage",
                "instruction": "Answer briefly.",
                "output_schema": schema,
            },
        )
        assert created.status_code == status.HTTP_201_CREATED, created.text

        fetched = await authenticated_client.get(f"/pods/{pod_id}/agents/triage")
        assert fetched.json().get("output_schema") == schema

        # An update that omits output_schema leaves it untouched.
        kept = await authenticated_client.patch(
            f"/pods/{pod_id}/agents/triage", json={"description": "updated"}
        )
        assert kept.status_code == status.HTTP_200_OK, kept.text
        after_keep = await authenticated_client.get(f"/pods/{pod_id}/agents/triage")
        assert after_keep.json().get("output_schema") == schema

        # An explicit null clears it.
        cleared = await authenticated_client.patch(
            f"/pods/{pod_id}/agents/triage", json={"output_schema": None}
        )
        assert cleared.status_code == status.HTTP_200_OK, cleared.text
        after_clear = await authenticated_client.get(f"/pods/{pod_id}/agents/triage")
        assert not after_clear.json().get("output_schema")
