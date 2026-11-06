"""The call's transcript, written into its conversation as the call goes.

As the call goes rather than only at its end: a call the process dies under
keeps all but the last few lines, and the conversation shows the call while it
happens. Headed as call speech, so a later run reads it as what the person
and the voice said, not as its own words.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.exc import SQLAlchemyError

from app.core.domain.errors import DomainError
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: Lines kept back before they are written into the conversation, so a call
#: that ends abruptly loses at most this many.
FLUSH_LINES = 8

TRANSCRIPT_HEADING = "Said on the call -- by the person and the voice, not by you"


class Transcript:
    """What was said on one connection, and how much of it is written."""

    def __init__(self, record: Callable[[str], Awaitable[None]]) -> None:
        self._record = record
        self.lines: list[str] = []
        self._flushed = 0
        #: Chunks taken but not yet written: in flight, failed, or cancelled.
        #: A chunk leaves only once its write succeeds, so a write the
        #: session's drain cancels -- even one that never started -- is still
        #: here for the final flush.
        self._unwritten: list[list[str]] = []

    def text(self) -> str:
        return "\n".join(self.lines)

    def take(self, *, final: bool) -> list[str]:
        """The lines not yet written, cut now: what a write takes is fixed
        when it is asked for, not when it gets to run."""
        pending = self.lines[self._flushed :]
        if not pending or (not final and len(pending) < FLUSH_LINES):
            return []
        self._flushed = len(self.lines)
        self._unwritten.append(pending)
        return pending

    async def write(self, chunk: list[str]) -> None:
        try:
            await self._record(TRANSCRIPT_HEADING + "\n\n" + "\n".join(chunk))
        except DomainError, SQLAlchemyError, OSError:
            # Kept, not dropped: the final flush tries it again.
            logger.warning("calls.session.transcript.failed", exc_info=True)
            return
        self._unwritten = [one for one in self._unwritten if one is not chunk]

    async def flush(self, *, final: bool = False) -> None:
        chunk = self.take(final=final)
        if not final:
            if chunk:
                await self.write(chunk)
            return
        # Everything not yet written, in the order it was said: chunks that
        # failed or were cancelled, then the last lines.
        for one in list(self._unwritten):
            await self.write(one)
