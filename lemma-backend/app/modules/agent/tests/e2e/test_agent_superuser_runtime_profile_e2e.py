from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.value_objects import HarnessKind
from app.modules.agent.infrastructure.models import AgentRuntimeDaemonModel


@pytest.mark.asyncio
async def test_superuser_bypass_lets_member_create_org_wide_runtime_profile(
    async_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    """End-to-end regression for the cloud UI symptom: an account marked
    ``users.is_superuser = true`` can create an org-wide runtime profile even
    when its org role grants nothing beyond ``org.read``.

    The pre-fix ``Authorizer.authorize`` short-circuit at ``ctx.is_superuser``
    never fired because ``build_user_context`` did not propagate the flag from
    the user row to the ``Context``. The unit-level regression lives in
    ``app/core/authorization/tests/unit/test_superuser_bypass.py``; this e2e
    test pins the *controller-level* round trip -- the path that was returning
    ``403 Missing permission org.update`` to operators trying to register a
    daemon in the cloud UI.
    """
    from sqlalchemy import update

    from app.core.authorization.cache import invalidate_role_snapshot_cache
    from app.modules.identity.infrastructure.models.user_models import User

    org_id = UUID(fixed_test_org["id"])
    user_id = UUID(fixed_test_user["id"])

    # Register a daemon owned by this user so USER_DAEMON profile creation
    # has a real daemon_id to attach to.
    daemon = AgentRuntimeDaemonModel(
        user_id=user_id,
        device_key=f"superuser-test-device-{uuid4().hex[:8]}",
        display_name="Superuser test laptop",
        status="ONLINE",
        device_info={"platform": "test"},
        harness_catalog={
            "GG_CODER": {
                "available": True,
                "display_name": "GG Coder",
                "models": ["ggcoder/default"],
            },
        },
    )
    db_session.add(daemon)
    await db_session.flush()
    daemon_id = str(daemon.id)
    await db_session.commit()

    # Mark the user a superuser and flush any prior role snapshot so the next
    # request rebuilds from the current DB state. ``build_user_context`` reads
    # ``is_superuser`` directly off the user row on every request, so the
    # bypass fires even when no role grant is present.
    await db_session.execute(
        update(User).where(User.id == user_id).values(is_superuser=True)
    )
    await db_session.commit()
    await invalidate_role_snapshot_cache(user_id=user_id)

    try:
        response = await async_client.post(
            f"/organizations/{org_id}/agent-runtime/profiles",
            headers={"Authorization": f"Bearer {fixed_test_user['token']}"},
            json={
                "source": "USER_DAEMON",
                "daemon_id": daemon_id,
                "harness_kind": HarnessKind.GG_CODER.value,
                "scope": "ORGANIZATION",
                "name": f"GG Coder {uuid4().hex[:8]}",
                "default_model_name": "ggcoder/default",
            },
        )
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["scope"] == "ORGANIZATION"
        assert payload["derived_harness_kind"] == HarnessKind.GG_CODER.value
        assert payload["user_id"] == fixed_test_user["id"]
        assert payload["daemon_id"] == daemon_id
    finally:
        # Reset privileged state so the next test starts from a clean baseline
        # (the fixtures reuse the same module-scoped user).
        await db_session.execute(
            update(User).where(User.id == user_id).values(is_superuser=False)
        )
        await db_session.commit()
        await invalidate_role_snapshot_cache(user_id=user_id)


@pytest.mark.asyncio
async def test_harness_list_includes_offline_daemons_with_daemon_offline_status(
    async_client,
    fixed_test_org,
    fixed_test_user,
    db_session,
):
    """Regression: the ``/me/agent-runtime/harnesses`` endpoint used to drop
    any daemon whose status wasn't ``ONLINE``, surfacing "Not detected" in the
    UI even when the binary was installed on the user's PATH but the daemon
    process was stopped. The fix keeps the row visible with
    ``availability_status='DAEMON_OFFLINE'`` so the operator sees the right
    hint ("start the Lemma daemon") instead of the misleading "Install X".
    """
    from app.modules.agent.infrastructure.models import AgentRuntimeDaemonModel

    user_id = UUID(fixed_test_user["id"])

    # Register an OFFLINE daemon with GG_CODER installed in its catalog.
    daemon = AgentRuntimeDaemonModel(
        user_id=user_id,
        device_key=f"offline-test-device-{uuid4().hex[:8]}",
        display_name="Offline laptop",
        status="OFFLINE",
        device_info={"platform": "test"},
        harness_catalog={
            "GG_CODER": {
                "available": True,
                "display_name": "GG Coder",
                "models": ["ggcoder/default"],
                "path": "/usr/local/bin/ggcoder",
                "version": "5.19.4",
            },
            "CLAUDE_CODE": {
                "available": True,
                "display_name": "Claude Code",
                "models": ["claude-3.5"],
            },
        },
    )
    db_session.add(daemon)
    await db_session.commit()

    try:
        response = await async_client.get(
            "/agent-runtime/harnesses",
            headers={"Authorization": f"Bearer {fixed_test_user['token']}"},
        )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        gg_coder = next(
            (item for item in items if item["harness_kind"] == "GG_CODER"), None
        )
        assert gg_coder is not None, (
            "GG_CODER must be listed even though the daemon is OFFLINE so the "
            "UI can render 'Daemon offline' instead of dropping it entirely."
        )
        assert gg_coder["availability_status"] == "DAEMON_OFFLINE"
        assert gg_coder["daemon_status"] == "OFFLINE"
        # available must be False -- the operator can't add a profile against an
        # offline daemon, the controller-side WS would reject it.
        assert gg_coder["available"] is False
        # No models returned for an offline daemon (avoids the picker offering
        # a model that the runtime can't actually serve right now).
        assert gg_coder["models"] == []
    finally:
        await db_session.delete(daemon)
        await db_session.commit()
