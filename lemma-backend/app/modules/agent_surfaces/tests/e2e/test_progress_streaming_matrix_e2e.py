"""Progress-streaming tool-coverage matrix: tool activity as a live message.

Which platforms belong here is decided by ``ProgressStyle`` on the platform
capability (see ``progress_display.py``), and the cells this file does not cover
are the styles that are not a live message:

- **WhatsApp** (``POST``) has no message-edit API, so it cannot appear in a
  matrix about edits. It is not silent — it posts the agent's plan as its own
  message, rationed — but that path is driven by plan changes rather than by
  per-tool comments, and is covered in ``tests/unit/test_progress_observer.py``.
- **Email** (``NONE``) gets one composed reply, never a stream — Gmail/Outlook/
  Resend recipients would find a live-editing inbox message bizarre.

Each scripted tool call carries a ``comment`` (nested under ``request``, since
every platform tool takes a single ``request: Model`` parameter and no such
model uses ``extra="forbid"`` — see ``script_progress``'s docstring). The
progress observer reads that comment straight off the persisted (pre-tool-
execution) event to drive the live status text, independent of whatever the
wrapped tool itself returns.

The journey is one sentence on every platform: say something, and watch the
work happen before the answer arrives. What differs is the shape the platform
gives that, and *that* is the subject here — so the staging is shared through
`stage_surface` and the assertions are deliberately not. Telegram and Teams
both edit a message in place and share a case; Slack opens a native stream,
which is a different thing and keeps its own.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
    wait_for_slack_text,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import script_progress
from app.modules.agent_surfaces.tests.e2e.surface_journey import stage_surface

pytestmark = pytest.mark.e2e

COMMENTS = ["Searching the web", "Reading the results"]
FINAL = "Here is the answer."

#: The platform tool each script calls. The tool is incidental — the progress
#: comment is read off the persisted event, not off what the tool returns — but
#: it has to be one the platform actually offers.
CONTEXT_TOOL = {
    SurfacePlatform.SLACK: "slack_get_recent_channel_messages",
    SurfacePlatform.TELEGRAM: "telegram_get_current_chat",
    SurfacePlatform.TEAMS: "teams_get_recent_channel_messages",
}

#: Where an in-place edit lands in the message store, per platform.
EDIT_BUCKET = {
    SurfacePlatform.TELEGRAM: "TELEGRAM_EDIT",
    SurfacePlatform.TEAMS: "TEAMS_UPDATE",
}


@pytest.fixture
def platform_fake(fake_slack, fake_teams, fake_telegram):
    return {
        SurfacePlatform.SLACK: fake_slack,
        SurfacePlatform.TEAMS: fake_teams,
        SurfacePlatform.TELEGRAM: fake_telegram,
    }


@pytest.fixture(autouse=True)
def _stream_every_comment(monkeypatch):
    """Both comments stream: the inter-update throttle is off for these tests."""
    from app.modules.agent_surfaces.services import progress_display

    monkeypatch.setattr(progress_display, "_MIN_TEXT_PROGRESS_INTERVAL_SECONDS", 0.0)


async def _staged(platform, platform_fake, **kwargs):
    return await stage_surface(platform, fake=platform_fake[platform], **kwargs)


@pytest.mark.parametrize(
    "platform",
    [SurfacePlatform.TELEGRAM, SurfacePlatform.TEAMS],
    ids=lambda p: p.value,
)
async def test_progress_is_an_edited_message_and_the_answer_is_a_new_one(
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
    stage = await _staged(
        platform,
        platform_fake,
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )

    await stage.say(
        "do some work",
        script=script_progress(
            COMMENTS, final_text=FINAL, tool_name=CONTEXT_TOOL[platform]
        ),
    )

    edits = await wait_for_messages(message_store, EDIT_BUCKET[platform], min_count=1)
    assert any(COMMENTS[-1] in json.dumps(edit, default=str) for edit in edits), (
        f"{platform.value}: the work never showed up as an edit: {edits}"
    )
    # Not the full sentence: Telegram renders MarkdownV2 and escapes the
    # trailing period, so the punctuation is the platform's business.
    assert await stage.saw("Here is the answer")


async def test_progress_opens_one_native_stream_on_slack(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    """Slack is not an edit: it opens a stream, appends, and closes it.

    Kept apart from the pair above because the assertion is about a lifecycle
    — one start, appends, and a stop naming the same message — rather than
    about an edit landing.
    """
    stage = await _staged(
        SurfacePlatform.SLACK,
        platform_fake,
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )

    await stage.say(
        "do some work",
        script=script_progress(
            COMMENTS,
            final_text=FINAL,
            tool_name=CONTEXT_TOOL[SurfacePlatform.SLACK],
        ),
    )

    starts = await wait_for_messages(message_store, "SLACK_STREAM_START", min_count=1)
    assert starts[-1]["channel"] == stage.surface["_dm_channel"]
    chunks = await wait_for_messages(message_store, "SLACK_STREAM_APPEND", min_count=1)
    # Across appends, not within one: the token buffer flushes on a size *or*
    # time trigger, so the answer can be split at an arbitrary character.
    delivered = await wait_for_slack_text(message_store, FINAL)
    assert any(FINAL in text for text in delivered), delivered
    stops = await wait_for_messages(message_store, "SLACK_STREAM_STOP", min_count=1)
    assert stops[-1]["ts"] == chunks[-1]["ts"], (
        "the stream that was closed is not the one that was appended to"
    )

    # Counted only now, with the answer delivered and the stream closed: a
    # count taken while the turn is still running says nothing, because a
    # second start has not had its chance to arrive yet. "One stream" is the
    # claim in this test's name, and two would be two live messages racing to
    # show the same work.
    assert len(message_store.get_all("SLACK_STREAM_START")) == 1, (
        f"expected one stream, got {message_store.get_all('SLACK_STREAM_START')}"
    )
    assert len(message_store.get_all("SLACK_STREAM_STOP")) == 1, (
        f"expected one close, got {message_store.get_all('SLACK_STREAM_STOP')}"
    )
