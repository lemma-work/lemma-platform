"""A run that fails on a surface says why, and its "Try again" runs it again.

On every chat platform: the failure notice names the cause the runner already
knew, carries the platform's own Try again control, and tapping it re-runs the
failed request through the ordinary interaction path. And a run whose observer
could not start -- a typing bubble refused by a database blip -- still delivers
its answer.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.services.progress_observer import (
    SurfaceAgentRunProgressObserver,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    run_scripted_agent_run,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import (
    CHAT_PLATFORMS,
    stage_surface,
)
from app.modules.test_support.e2e.scripted_model import script_model_error

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("platform", CHAT_PLATFORMS, ids=lambda p: p.value)
async def test_a_failed_run_says_why_and_try_again_reruns_it(
    platform: SurfacePlatform,
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    stage = await stage_surface(
        platform,
        fake=platform_fake[platform],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )

    context = await stage.say(
        "export the report",
        # 402 is not retried by the harness, so the run fails on the first turn.
        script=[script_model_error("model_http", message="no", status_code=402)],
        expected_status=AgentRunStatus.FAILED,
    )

    # The cause, not a fixed apology.
    assert await stage.saw("billing reasons")
    await stage.press(await stage.control("Try again"))
    await run_scripted_agent_run(
        db_session,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        pod_id=context.pod_id,
        agent_name=context.agent_name,
        script=[script_text("Here is the report.")],
    )
    assert await stage.saw("Here is the report.")


class _StartRefused(SurfaceAgentRunProgressObserver):
    """An observer whose start hit a database blip; the rest of it is real.

    The runner is the subject here: it used to skip the finish -- the call that
    sends the answer -- whenever the start had failed.
    """

    async def on_run_started(self, conversation: object, ctx: object) -> None:
        raise OperationalError("select", {}, Exception("connection reset"))


async def test_an_observer_that_could_not_start_still_delivers_the_answer(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    stage = await stage_surface(
        SurfacePlatform.TELEGRAM,
        fake=platform_fake[SurfacePlatform.TELEGRAM],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )

    await stage.say(
        "hello",
        script=[script_text("The answer, regardless.")],
        observer_class=_StartRefused,
    )

    assert await stage.saw("The answer, regardless.")
