"""A template imported into an existing pod counts against the daily import cap.

It adds tables and schedules like any bundle, and schedules start agent runs
that cost money. A brand-new pod's start stages its template without passing
through the import endpoint, so only this path is counted.
"""

from __future__ import annotations

import pytest
from fastapi import status

from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.infrastructure.rate_limiter import (
    BundleRateLimiter,
    get_bundle_rate_limiter,
)
from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import new_pod
from app.modules.pod_bundle.tests.e2e.bundle_work_support import pause_admin

pytestmark = [pytest.mark.e2e]


async def test_a_template_import_into_a_pod_spends_the_daily_cap(
    authenticated_client, fixed_test_org
) -> None:
    pod_id = await new_pod(authenticated_client, fixed_test_org["id"], label="Cap")
    await pause_admin(pod_id)
    me = (await authenticated_client.get("/users/me")).json()
    limiter = get_bundle_rate_limiter()
    redis = await limiter._get_redis()
    key = BundleRateLimiter._key("import", me["id"])
    spent = await redis.get(key)
    limit = pod_bundle_settings.pod_bundle_daily_import_limit
    # One left today: the first template import takes it, the second is refused.
    await redis.set(key, limit - 1, ex=60)
    try:
        body = {"kind": "TEMPLATE", "template": "support"}
        first = await authenticated_client.post(
            f"/pods/{pod_id}/bundle/imports", json=body
        )
        assert first.status_code == status.HTTP_202_ACCEPTED, first.text
        second = await authenticated_client.post(
            f"/pods/{pod_id}/bundle/imports", json=body
        )
        assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS, second.text

        cancel = await authenticated_client.delete(
            f"/pods/{pod_id}/bundle/imports/{first.json()['import_id']}"
        )
        assert cancel.status_code < 300, cancel.text
        await pause_admin(pod_id, paused=False)
    finally:
        if spent is None:
            await redis.delete(key)
        else:
            await redis.set(key, spent, ex=24 * 60 * 60)
