"""What makes two spawns from one run the same spawn.

A model that re-issues `spawn` after a tool retry or a timeout means the child
it already asked for, not a second one doing the same work beside it. So a
child is identified, within the run that spawned it, by which agent it is and
the task it was given; a second spawn with both the same gets the first child.
Two runs asking for the same task are two requests, and get two children.
"""

from __future__ import annotations

import hashlib


def spawn_key(*, target_name: str | None, task: str) -> str:
    """A digest of who was spawned and what they were asked to do."""
    digest = hashlib.sha256()
    digest.update((target_name or "").encode("utf-8"))
    digest.update(b"\x00")
    digest.update(task.encode("utf-8"))
    return digest.hexdigest()
