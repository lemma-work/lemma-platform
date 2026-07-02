"""End-to-end pod bundle publish.

The test harness has no connected GitHub account, so the real publish job runs
through export → README → Composio resolution and terminates cleanly at FAILED
with a connect-GitHub message (the deterministic path). The happy publish path
(create repo + upload + chunk fallback) is covered by the publisher unit tests
with a fake GithubOps.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi import status

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


async def _wait(client, pod_id, publish_id, *, until, timeout=60) -> dict:
    for _ in range(timeout):
        res = await client.get(f"/pods/{pod_id}/bundle/publishes/{publish_id}")
        assert res.status_code == status.HTTP_200_OK, res.text
        body = res.json()
        if body["status"] in until:
            return body
        await asyncio.sleep(1)
    raise AssertionError(f"Publish stuck at {body['status']}")


async def test_publish_without_github_account_fails_cleanly(
    authenticated_client, test_pod, worker
):
    pod_id = test_pod["id"]
    res = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/publishes",
        json={"repo_name": f"crm-{uuid4().hex[:6]}", "private": True},
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    body = res.json()
    assert body["status"] in ("QUEUED", "EXPORTING")
    publish_id = body["publish_id"]

    # No GitHub connection in the harness → the job resolves to a terminal FAILED
    # (never hangs, never 500s the request).
    final = await _wait(authenticated_client, pod_id, publish_id, until={"COMPLETED", "FAILED"})
    assert final["status"] == "FAILED", final
    assert final["error"]


async def test_publish_status_expired_returns_410(authenticated_client, test_pod, worker):
    pod_id = test_pod["id"]
    res = await authenticated_client.get(
        f"/pods/{pod_id}/bundle/publishes/{uuid4()}"
    )
    assert res.status_code == status.HTTP_410_GONE, res.text
    assert res.json()["code"] == "POD_BUNDLE_EXPIRED"
