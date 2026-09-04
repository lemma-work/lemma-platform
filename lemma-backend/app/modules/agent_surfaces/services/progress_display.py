"""Showing a run in flight, in whatever way the surface can manage.

Split out of :mod:`progress_observer` because the observer owns a run's
lifecycle — what is buffered, what is the answer, when it is delivered — while
this owns the separate question of what the person watching sees before that
answer arrives. The two used to be tangled, and the seam between them was three
hand-maintained sets of platform names.

There is one branch here, on the platform's :class:`ProgressStyle`, and it is
the whole design:

``STREAM``
    A real streaming API. The answer is written into a live message and that
    same message is closed with it, so steps and answer are one thing. Slack.
``EDIT``
    One live message, rewritten as the work moves. Cheap — an edit costs no new
    notification — so it can be refreshed freely. Telegram, Teams. How much fits
    in it is the platform's own answer (``progress_is_one_line``): Teams holds
    the checklist, Telegram's thinking chip holds a line.
``POST``
    No edit API at all. An update can only be a *new* message in someone's chat,
    which costs their attention and, on WhatsApp, sits inside a metered 24-hour
    window. So updates are rationed rather than throttled: the plan when it has
    actually moved, and one "still going" for a long run that has none.
``NONE``
    Nothing before the reply, by design. Email.
"""

from __future__ import annotations

import time

from app.modules.agent.contracts import AgentEvent
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
    ProgressStyle,
)
from app.modules.agent_surfaces.services.progress_events import (
    _progress_text_from_event,
)
from app.modules.agent_surfaces.services.progress_plan import (
    render_plan,
    render_plan_line,
)

# How often a live, edited progress message may be rewritten. Slack never comes
# through here: it streams the answer token by token, and a step chunk appended
# into that same stream lands *inside* the sentence being written, splitting it
# mid-word. Its streamed text is its own progress indicator.
_MIN_TEXT_PROGRESS_INTERVAL_SECONDS = 2.0

# The plan is the one update that gets through a ``POST`` platform immediately:
# an agent only writes one for real multi-step work, and "here are the five
# things I am about to do" is the most useful thing the person can be told at the
# start of a long run. Everything after it waits its turn.
#
# Two minutes, not the 45s this started at. The number is not a throttle on how
# fast we *could* send; it is how long a person is willing to be told nothing
# before they read silence as broken. Under a minute is well inside the time an
# ordinary multi-step run takes to move one step, so the second message arrived
# saying nearly the same thing as the first — which is how a progress feed turns
# into noise. At two minutes an update means a step actually landed.
_POST_PROGRESS_MIN_INTERVAL_SECONDS = 120.0
_POST_PROGRESS_MAX_PER_RUN = 5
# With no plan there is nothing substantive to report, so a long run gets one
# acknowledgement that it is still alive and nothing more. Held to the same two
# minutes: a run that finishes in ninety seconds should never have said anything
# at all, and at a minute most of them were still going to beat the answer to it.
_POST_HEARTBEAT_DELAY_SECONDS = 120.0
_POST_HEARTBEAT_TEXT = (
    "Still working on this — it is a longer one. "
    "I will send the answer as soon as I have it."
)


class ProgressDisplayMixin:
    """The "what does the person see while they wait" half of the observer."""

    async def _maybe_send_text_progress(
        self,
        event: AgentEvent,
        platform: str | None,
        conversation_id,
    ) -> None:
        """Show what the run is doing, in whatever way this platform can.

        Telegram/Teams rewrite one live message; WhatsApp has no edit API and can
        only post a new one, so it is rationed instead of throttled. Slack does
        not come through here at all — its streamed answer is its own progress
        indicator. Email shows nothing before the reply, by design.
        """
        capabilities = PLATFORM_CAPABILITIES.get(platform or "")
        if capabilities is None:
            return
        activity = _progress_text_from_event(event)
        if capabilities.progress_style is ProgressStyle.EDIT:
            await self._edit_live_progress(
                conversation_id,
                activity,
                one_line=capabilities.progress_is_one_line,
            )
        elif capabilities.progress_style is ProgressStyle.POST:
            await self._maybe_post_progress(conversation_id, activity)

    async def _edit_live_progress(
        self,
        conversation_id,
        activity: str | None,
        *,
        one_line: bool,
    ) -> None:
        """Rewrite the one live message: the plan, then what is happening now.

        A plan that has moved is always worth an edit — that is the update the
        person is waiting on — so it bypasses the activity throttle, which exists
        only to stop a fast tool loop rewriting the message every few hundred
        milliseconds.
        """
        plan_changed = self._plan is not None and (
            self._plan.signature != self._shown_plan_signature
        )
        now = time.monotonic()
        if not plan_changed:
            if not activity:
                return
            if (
                activity == self._last_text_progress
                or now - self._last_text_progress_at
                < _MIN_TEXT_PROGRESS_INTERVAL_SECONDS
            ):
                return
        body = self._live_progress_body(activity, one_line=one_line)
        if not body:
            return
        self._last_text_progress = activity
        self._last_text_progress_at = now
        if self._plan is not None:
            self._shown_plan_signature = self._plan.signature
        await self._stream_progress(conversation_id, body)

    def _live_progress_body(self, activity: str | None, *, one_line: bool) -> str:
        """The checklist over the current step, for a message that gets edited.

        Where the surface holds one line, the plan is drawn as that one line and
        the activity is dropped rather than appended: the step being worked on
        already names the moment the tool name was naming, and a chip that has
        to choose is better off saying "Render the video" than "Using
        execute_python". With no plan there is nothing but the activity, which is
        what this showed before plans were drawn at all — folded onto one line
        too, because a tool's comment is free text and a surface that collapses
        newlines will run a two-line one together without the space.
        """
        if one_line:
            if self._plan is not None:
                return render_plan_line(self._plan)
            return " ".join((activity or "").split())
        plan_text = render_plan(self._plan) if self._plan is not None else ""
        if plan_text and activity:
            return f"{plan_text}\n\n{activity}"
        return plan_text or (activity or "")

    async def _maybe_post_progress(self, conversation_id, activity: str | None) -> None:
        """Send a standing-alone progress message, sparingly.

        Before this, a long run on WhatsApp was silence. The inbound path marks
        the message read and shows a typing bubble when it picks the message up,
        but that bubble expires after ~25s; past it the next thing the person saw
        was the answer, minutes later, with no way to tell a slow run from a
        dropped one.

        What gets through is the plan, and only when it has actually moved. A run
        with no plan gets a single "still going" once it is long enough to look
        broken. Both are capped per run, because every one of these is a message
        in someone's chat.
        """
        del activity
        now = time.monotonic()
        if self._posted_updates >= _POST_PROGRESS_MAX_PER_RUN:
            return
        plan = self._plan
        if plan is not None and plan.signature != self._shown_plan_signature:
            first_update = self._posted_updates == 0
            if (
                not first_update
                and now - self._last_post_at < _POST_PROGRESS_MIN_INTERVAL_SECONDS
            ):
                return
            self._shown_plan_signature = plan.signature
            await self._post_progress(conversation_id, render_plan(plan), now)
            return
        if (
            plan is None
            and not self._heartbeat_posted
            and now - self._run_started_at >= _POST_HEARTBEAT_DELAY_SECONDS
        ):
            self._heartbeat_posted = True
            await self._post_progress(conversation_id, _POST_HEARTBEAT_TEXT, now)

    async def _post_progress(self, conversation_id, body: str, now: float) -> None:
        if not body:
            return
        self._posted_updates += 1
        self._last_post_at = now
        await self._stream_progress(conversation_id, body)

    async def _stream_progress(self, conversation_id, progress_text: str) -> None:
        async with self.uow_factory() as uow:
            service = self.service_factory(uow)
            handle = await service.send_progress_update_for_conversation(
                conversation_id=conversation_id,
                progress_text=progress_text,
                progress_handle=self._progress_handle,
            )
        if handle is not None:
            self._progress_handle = handle
