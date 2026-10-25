"""End-to-end pod bundle publish.

A publish runs as whatever the pod's `github` connector resolves to for the
person publishing, and nothing else: no account is named. With no such
connector, or no account of theirs on it, the request is refused before
anything is queued, with the connector's own typed answer and its connect
link. The happy publish path (create repo + upload + chunk fallback) is
covered by the publisher unit tests with a fake GithubOps.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status

from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.datastore.tests.e2e.harness import (
    auth_headers,
    invite_to_pod,
)
from app.modules.test_support.e2e_authz import signup_user

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


async def test_publish_in_a_pod_without_a_github_connector_is_refused(
    authenticated_client, test_pod, worker
):
    res = await authenticated_client.post(
        f"/pods/{test_pod['id']}/bundle/publishes",
        json={"repo_name": f"crm-{uuid4().hex[:6]}", "private": True},
    )
    assert res.status_code == status.HTTP_404_NOT_FOUND, res.text
    assert res.json()["code"] == "POD_CONNECTOR_NOT_FOUND"


async def test_publish_asks_the_person_to_connect_github_first(
    authenticated_client, db_session, fixed_test_org, test_pod, worker
):
    """A per-member `github` connector and no account of the person's: the
    answer is the connect link, at the request -- not a failed job later."""
    if await db_session.get(Connector, "github") is None:
        db_session.add(
            Connector(
                id="github",
                title="GitHub",
                description="GitHub",
                kinds=[{"kind": "http", "auth_scheme": "OAUTH2"}],
                is_active=True,
            )
        )
    install = AuthConfig(
        id=uuid4(),
        organization_id=fixed_test_org["id"],
        connector_id="github",
        name=f"github-{uuid4().hex[:6]}",
        kind="http",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
    )
    db_session.add(install)
    await db_session.commit()
    declared = await authenticated_client.post(
        f"/pods/{test_pod['id']}/connectors",
        json={
            "slug": "github",
            "auth_config_id": str(install.id),
            "mode": "PER_MEMBER",
        },
    )
    assert declared.status_code == status.HTTP_201_CREATED, declared.text

    res = await authenticated_client.post(
        f"/pods/{test_pod['id']}/bundle/publishes",
        json={"repo_name": f"crm-{uuid4().hex[:6]}"},
    )

    assert res.status_code == status.HTTP_409_CONFLICT, res.text
    body = res.json()
    assert body["code"] == "CONNECTION_REQUIRED"
    assert f"/pods/{test_pod['id']}/connections" in body["details"]["connect_url"]


async def test_publish_ignores_a_deprecated_account_id(
    authenticated_client, test_pod, worker
):
    """The account is the pod's `github` connector's decision. A client still
    sending `account_id` is not refused for it: it is ignored, and the publish
    goes the way any other does -- here, to the pod having no `github`."""
    res = await authenticated_client.post(
        f"/pods/{test_pod['id']}/bundle/publishes",
        json={"repo_name": "crm", "account_id": str(uuid4())},
    )
    assert res.status_code == status.HTTP_404_NOT_FOUND, res.text
    assert res.json()["code"] == "POD_CONNECTOR_NOT_FOUND"


async def test_publish_status_expired_returns_410(
    authenticated_client, test_pod, worker
):
    pod_id = test_pod["id"]
    res = await authenticated_client.get(f"/pods/{pod_id}/bundle/publishes/{uuid4()}")
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

    payload = {"repo_name": f"permission-{uuid4().hex[:6]}"}
    denied = await async_client.post(
        f"/pods/{test_pod['id']}/bundle/publishes",
        headers=auth_headers(viewer["token"]),
        json=payload,
    )
    assert denied.status_code == status.HTTP_403_FORBIDDEN, denied.text

    # Past authorization: what stops the editor is the pod having no `github`
    # connector, which is the next thing a publish needs.
    allowed = await async_client.post(
        f"/pods/{test_pod['id']}/bundle/publishes",
        headers=auth_headers(editor["token"]),
        json=payload,
    )
    assert allowed.status_code == status.HTTP_404_NOT_FOUND, allowed.text
    assert allowed.json()["code"] == "POD_CONNECTOR_NOT_FOUND"
