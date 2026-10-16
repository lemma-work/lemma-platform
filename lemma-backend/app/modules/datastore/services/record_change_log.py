"""Which path a record write's changes take, and the wake after it commits.

Until every pod schema carries the change log, writes stage outbox events as
they always have; after, they append to the log (``ChangeCapture``). The
choice is made per write by the gate the wiring injects. A service built
without one -- a unit test's, an in-process caller's -- keeps the outbox path,
which is what those callers have always had.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace

from app.modules.datastore.changes.capture import ChangeCapture
from app.modules.datastore.domain.write_intent import WriteIntent
from app.modules.datastore.services.authorization import outside_writer
from app.modules.datastore.services.table_context import TableContext

type ChangeLogGate = Callable[[], Awaitable[bool]]


class RecordChangeLog:
    def __init__(
        self, gate: ChangeLogGate | None, *, platform_session: object | None
    ) -> None:
        self._gate = gate
        self._platform_session = platform_session

    async def capture(
        self, ctx: TableContext, intent: WriteIntent
    ) -> ChangeCapture | None:
        """This write's appends, or ``None`` when it still goes to the outbox."""
        if self._gate is None or not await self._gate():
            return None
        writer = outside_writer(ctx)
        if writer != ctx.outside_actor:
            ctx = replace(ctx, outside_actor=writer)
        return ChangeCapture(ctx, intent=intent, outside_actor=writer)

    @staticmethod
    def kwargs(changes: ChangeCapture | None) -> dict[str, ChangeCapture]:
        """The repository keyword, only when there is one to pass."""
        return {} if changes is None else {"changes": changes}

    async def after_commit(self, changes: ChangeCapture) -> None:
        """After commit, the live wake: started in the background, so the
        request holds neither its platform connection nor its caller for
        Redis."""
        changes.publish_wake()


__all__ = ["ChangeLogGate", "RecordChangeLog"]
