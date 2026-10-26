"""The browser path of `web_fetch`: `save-webpage` in the sandbox.

Split from `web_fetch` by use case. Everything here is about one render: the
command that runs it, how long it may take, and how many a call may ask for.
The batch around it -- which pages go here at all, and the deadline over the
whole call -- stays in `web_fetch`.
"""

from __future__ import annotations

import shlex

from app.core.log.log import get_logger
from app.modules.agent.infrastructure.tools.web.capture_result import finish, slugify
from app.modules.agent.infrastructure.tools.web.models import WebFetchPage
from sandbox_runtime.paths import OVERLAY_FIRST_ON_PATH

logger = get_logger(__name__)

# A browser render has to start Chrome and wait for the page to settle. This is
# how long the call waits; the capture itself is held to less (below), so it has
# ended -- and let go of the browser -- before the call stops waiting for it.
BROWSER_TIMEOUT_SECONDS = 75

# The capture's own ceiling, enforced inside the sandbox by `save-webpage`.
# Nothing in the backend can end a capture the call stopped waiting for: the
# process outlived its 75 s window, kept the browser lock, and starved every
# capture queued behind it, each of which then overran too. Held inside the
# call's window, with the lock wait well inside that, so a capture that queued
# still has time to render.
CAPTURE_SECONDS = 65
CAPTURE_LOCK_WAIT_SECONDS = 40

#: What the time a render may take adds to, besides the render: checking which
#: files landed and reading the head of the markdown.
_RENDER_OVERHEAD_SECONDS = 15

#: Exit statuses that mean the capture was stopped for time, not that it failed:
#: `timeout`'s own, and SIGKILL's when the grace after it ran out.
_TIMED_OUT_EXITS = frozenset({124, 137})
#: `save-webpage`'s "another capture held the browser" exit.
_LOCK_BUSY_EXIT = 75

# Browser renders are the serialised, expensive path, and the batch budget is
# the only thing bounding them.
#
# This is deliberately the same number as `WebFetchRequest.urls`' `max_length`,
# and `test_web_fetch_limits_agree` fails if the two ever drift: the cap is
# only honest when the tool cannot accept more pages than it will render. It
# used to accept ten and render three, so a caller who sent ten JS-heavy pages
# discovered seven were skipped only after paying for the call.
#
# Raising it does not endanger the cheap pages the old comment worried about --
# the http path runs to completion first, concurrently, before any render
# starts. What bounds the pathological case is the batch deadline, which
# reports every page it did not reach. Measured, a real render is ~5s, so five
# of them sit an order of magnitude inside that budget.
MAX_BROWSER_RENDERS = 5


def needs_browser(formats: list[str], render: bool) -> bool:
    # A PDF or screenshot is a render by definition; markdown alone is not.
    return render or any(fmt in {"pdf", "jpeg", "png"} for fmt in formats)


def render_fits(remaining_seconds: float) -> bool:
    """Whether a render started now can finish inside what is left of the call.

    Starting one that cannot only spends the browser on a page the caller will
    be told was cut off -- and, before the capture had its own ceiling, left it
    holding the browser after the call had returned.
    """
    return remaining_seconds >= BROWSER_TIMEOUT_SECONDS + _RENDER_OVERHEAD_SECONDS


def browser_script(url: str, out_dir: str, name: str, formats: list[str]) -> str:
    """The capture, by name: the runtime overlay's `save-webpage`, else the image's."""
    return (
        OVERLAY_FIRST_ON_PATH + f"mkdir -p {shlex.quote(out_dir)} && "
        f"SAVE_WEBPAGE_TIMEOUT={CAPTURE_SECONDS} "
        f"AGENT_BROWSER_CAPTURE_LOCK_WAIT={CAPTURE_LOCK_WAIT_SECONDS} "
        f"save-webpage {shlex.quote(url)} "
        f"--formats {shlex.quote(','.join(formats))} "
        f"--out {shlex.quote(out_dir)} --name {shlex.quote(name)}"
    )


async def capture_with_browser(
    session,
    *,
    url: str,
    out_dir: str,
    formats: list[str],
) -> WebFetchPage:
    name = slugify(url)
    browser_formats = formats if "markdown" in formats else [*formats, "markdown"]
    result = await session.exec_command(
        cmd=browser_script(url, out_dir, name, browser_formats),
        timeout=BROWSER_TIMEOUT_SECONDS,
    )
    if result.get("completed") is False and result.get("process_id"):
        # Belt and braces behind the capture's own ceiling: a capture still
        # running here holds the browser lock, and the next one queues on it.
        await _stop(session, str(result["process_id"]))
    return await finish(
        session,
        url=url,
        out_dir=out_dir,
        name=name,
        formats=browser_formats,
        fetched_with="browser",
        failure_output=_failure_output(result),
    )


def _failure_output(result: dict[str, object]) -> str:
    """Why a capture produced nothing, in terms of what actually happened.

    A capture cut off for time used to be reported as "some sites refuse
    automated clients", which sent agents looking for another source for a
    page that was fine and only queued behind another render.
    """
    exit_code = result.get("exit_code")
    if result.get("completed") is False or exit_code in _TIMED_OUT_EXITS:
        return (
            f"The browser did not finish this page within {CAPTURE_SECONDS}s -- "
            "it was busy with another capture, or the page kept loading -- and "
            "the capture was stopped. Ask for this page again on its own."
        )
    output = str(result.get("stderr") or result.get("stdout") or "")
    if exit_code == _LOCK_BUSY_EXIT and not output.strip():
        return (
            "The browser was busy with another capture for the whole wait. "
            "Ask for this page again on its own."
        )
    return output


async def _stop(session, process_id: str) -> None:
    try:
        await session.terminate_process(process_id)
    except Exception:  # noqa: BLE001 - the page result is still worth returning
        logger.warning(
            "agent.web_fetch.capture_stop_failed.degraded",
            process_id=process_id,
            exc_info=True,
        )
