"""Things the run needs told about itself, waiting to be handed over.

A mailbox rather than a direct write, because the two halves of telling a run
something sit in different places and neither can do the other's job. What
notices a threshold is where the counting happens -- the node loop for spend,
the compactor for history size -- and neither can reach the message list. What
can reach the message list is a capability, and it cannot see a clock or a
token count. So one side posts and the other delivers.

One mailbox per run, carried on ``HarnessOptions`` beside the other things a run
is given.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RunNotices:
    """Notices posted for this run, oldest first."""

    pending: list[str] = field(default_factory=list)

    def post(self, notice: str) -> None:
        self.pending.append(notice)

    def take(self) -> list[str]:
        """Hand over what is waiting and forget it.

        Taken rather than read, so a notice is delivered once. Left in place it
        would be re-appended on every model request for the rest of the run,
        which both nags and -- because the delivered message is written back
        into run state -- grows the prompt a copy at a time.
        """
        waiting, self.pending = self.pending, []
        return waiting
