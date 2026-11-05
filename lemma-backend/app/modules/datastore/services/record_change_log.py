"""A record write's change-log capture, and the wake after it commits.

Every record write appends its changes to its pod's change log in the
statement that writes the rows (``ChangeCapture``). A pod schema still below
the change-log version is migrated by the write's ``prepare`` on first use.
"""

from __future__ import annotations

from dataclasses import replace

from app.modules.datastore.changes.capture import ChangeCapture
from app.modules.datastore.domain.write_intent import WriteIntent
from app.modules.datastore.services.authorization import outside_writer
from app.modules.datastore.services.table_context import TableContext


class RecordChangeLog:
    async def capture(self, ctx: TableContext, intent: WriteIntent) -> ChangeCapture:
        """This write's appends, naming whoever outside the pod it is for."""
        writer = outside_writer(ctx)
        if writer != ctx.outside_actor:
            ctx = replace(ctx, outside_actor=writer)
        return ChangeCapture(ctx, intent=intent, outside_actor=writer)

    async def after_commit(self, changes: ChangeCapture) -> None:
        """After commit, the live wake: started in the background, so the
        request holds neither its platform connection nor its caller for
        Redis."""
        changes.publish_wake()


__all__ = ["RecordChangeLog"]
