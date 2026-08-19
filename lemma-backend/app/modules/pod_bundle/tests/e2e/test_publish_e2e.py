"""End-to-end pod bundle publish.

The test harness has no connected GitHub account, so the real publish job runs
through export → README → Composio resolution and terminates cleanly at FAILED
with a connect-GitHub message (the deterministic path). The happy publish path
(create repo + upload + chunk fallback) is covered by the publisher unit tests
with a fake GithubOps.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status

from app.modules.datastore.tests.e2e.harness import (
    auth_headers,
    invite_to_pod,
)
from app.modules.test_support.e2e.waiters import wait_for_status
from app.modules.test_support.e2e_authz import signup_user

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


async def _wait(client, pod_id, publish_id, *, until, timeout=60) -> dict:
    async def probe() -> dict:
        res = await client.get(f"/pods/{pod_id}/bundle/publishes/{publish_id}")
        assert res.status_code == status.HTTP_200_OK, res.text
        return res.json()

    # failed=set(): the module's own docstring says FAILED is this file's
    # deterministic, expected outcome (no connected GitHub account) -- only
    # stop on a status in `until`, never fail-fast on FAILED itself.
    return await wait_for_status(
        label=f"pod {pod_id} bundle publish {publish_id} to reach {until}",
        probe=probe,
        expected=set(until),
        failed=set(),
        timeout_seconds=timeout,
        interval_seconds=0.15,
    )


async def test_publish_without_github_account_fails_cleanly(
    authenticated_client, test_pod, worker
):
    pod_id = test_pod["id"]
    res = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/publishes",
        json={
            "repo_name": f"crm-{uuid4().hex[:6]}",
            "private": True,
            "account_id": str(uuid4()),
        },
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


async def test_publish_requires_editor_access(
    authenticated_client,
    async_client,
    fixed_test_org,
    test_pod,
    worker,
):
    viewer = await signup_user(async_client, "bundle-publish-viewer")
    editor = await signup_user(async_client, "bundle-publish-editor")
    for user, role in ((viewer, "POD_VIEWER"), (editor, "POD_EDITOR")):
        await invite_to_pod(
            authenticated_client,
            async_client,
            org_id=fixed_test_org["id"],
            pod_id=test_pod["id"],
            user=user,
            role=role,
        )

    payload = {
        "repo_name": f"permission-{uuid4().hex[:6]}",
        "account_id": str(uuid4()),
    }
    denied = await async_client.post(
        f"/pods/{test_pod['id']}/bundle/publishes",
        headers=auth_headers(viewer["token"]),
        json=payload,
    )
    assert denied.status_code == status.HTTP_403_FORBIDDEN, denied.text

    allowed = await async_client.post(
        f"/pods/{test_pod['id']}/bundle/publishes",
        headers=auth_headers(editor["token"]),
        json=payload,
    )
    assert allowed.status_code == status.HTTP_202_ACCEPTED, allowed.text
