"""Who is watching a display, and putting it back when nobody is.

Its own module because the socket controller was over the file-size ratchet
with it inline, and because it is a self-contained question: the socket
handler counts viewers, and this decides what that count means.
"""

from __future__ import annotations

import asyncio
import contextlib
from uuid import UUID

from app.core.request_context import create_inherited_task
from app.modules.workspace.services.browser_view_service import BrowserViewService

#: How many sockets are currently watching each person's display.
#:
#: In memory, and that is the right scope rather than a compromise: the count
#: exists to answer "is anybody still looking at *this* display", a display
#: lives in one sandbox, and a sandbox is reached through one API process at a
#: time. A restart loses the count and the display keeps whatever shape it
#: had, which is exactly what happens today and is what this improves on
#: rather than something it must also solve.
_watchers: dict[UUID, int] = {}


#: How long "nobody is watching" has to hold before the display is put back.
#:
#: Not zero, which is what this was, and the difference is a resize landing
#: in the middle of somebody's reconnect. A socket closing is not the same
#: as a person leaving: a dropped network, a reload, and the pane's own
#: retry after `CLOSE_NO_BROWSER` all close one and open another a moment
#: later. Resetting on the close resized the display under the handshake
#: that followed it -- which the browser e2e caught as a framebuffer that
#: never painted, having agreed its dimensions a moment before they changed.
_SETTLE_SECONDS = 5.0


async def _reset_after_settling(user_id: UUID) -> None:
    """Put the display back, once nobody has been watching for a moment.

    Its own service, because this outlives the socket that scheduled it and
    the one that socket held is closed on the way out.

    Best effort throughout. Failing to tidy up a display is not worth a log
    line on every network blip, let alone an error.
    """
    await asyncio.sleep(_SETTLE_SECONDS)
    if _watchers.get(user_id):
        return
    service = BrowserViewService()
    try:
        with contextlib.suppress(Exception):
            await service.reset_display(user_id)
    finally:
        with contextlib.suppress(Exception):
            await service.close()


def watch_ended(user_id: UUID) -> None:
    """Drop this viewer, and schedule a reset if they were the last.

    Server-side, not in the pane's cleanup, because the pane often does not
    get to run one: a closed tab, a killed renderer or a dropped network
    never fires an unmount. The socket closing is the only signal that is
    always there.

    Scheduled rather than awaited: this runs in the socket's teardown, and
    holding that open for the settle window would keep a connection and a
    service alive for five seconds after the person had gone.
    """
    remaining = _watchers.get(user_id, 1) - 1
    if remaining > 0:
        _watchers[user_id] = remaining
        return
    _watchers.pop(user_id, None)
    create_inherited_task(
        _reset_after_settling(user_id), name="workspace.browser_view.reset_display"
    )


def watch_begun(user_id: UUID) -> None:
    """One more socket is looking at this person's display."""
    _watchers[user_id] = _watchers.get(user_id, 0) + 1
