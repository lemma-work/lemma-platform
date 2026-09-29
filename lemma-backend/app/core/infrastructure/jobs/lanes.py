"""Which queue a task runs on, and what that queue is called.

A leaf module on purpose. Anything that needs a lane's queue name -- the stream
guard sizing the job queues, the job queue opening a client per lane -- used to
import the whole worker runtime for two small definitions, and the runtime
imports those same modules back.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.config import settings


class Lane(StrEnum):
    """Which queue a task runs on.

    Before lanes, every task type — agent runs, surface messages, workflow
    resumes, pod imports, document ingestion — shared one queue and one
    concurrency budget. A bulk upload could therefore occupy every worker slot
    and stall interactive work behind it. Splitting the queue is what makes the
    two classes of work independent; they are separate Redis queues, so a deep
    bulk backlog is invisible to the interactive lane.
    """

    #: Latency-sensitive, user-facing work. Someone is waiting on it.
    INTERACTIVE = "interactive"
    #: Throughput-oriented background work. Slower is acceptable; starving the
    #: interactive lane is not.
    BULK = "bulk"


def lane_queue_name(lane: Lane) -> str:
    """Redis queue name for a lane.

    The interactive lane keeps the bare configured name so existing queues,
    dashboards and any in-flight jobs survive the upgrade untouched; only the
    new bulk lane gets a suffix.
    """
    base = settings.worker_queue_name
    return base if lane is Lane.INTERACTIVE else f"{base}-{lane.value}"
