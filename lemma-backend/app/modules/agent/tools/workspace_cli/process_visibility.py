"""Which of a sandbox's processes one conversation may see and drive.

A workspace sandbox belongs to a *user*, not to a conversation, and several
conversations can be working in it at once -- each in its own directory. So
"the processes in this sandbox" is never the same question as "the processes
this agent started", and answering the first when the second was asked is what
filled the listing with other conversations' work.

Two things separate them. The session binding is exact but short-lived: it is
cleared the moment a process completes. The working directory outlives it, and
is what the conversations were already using to stay out of each other's way.
"""

from __future__ import annotations

from typing import Any


def within(process_cwd: str | None, own_cwd: str) -> bool:
    """Is this process running in our directory, or under it?

    A process the provider recorded no directory for is not excluded: an older
    entry, or a runtime that does not report one, must stay recoverable rather
    than vanish from the only listing that can return its id.
    """
    if not process_cwd or not own_cwd:
        return True
    return process_cwd == own_cwd or process_cwd.startswith(own_cwd.rstrip("/") + "/")


async def visible_processes(
    processes: list[dict[str, Any]],
    *,
    runtime,
    session_id: str | None,
    own_cwd: str,
) -> list[dict[str, Any]]:
    """This conversation's processes, plus unowned ones in its own tree.

    Rebinding indiscriminately would let a parent agent take over the processes
    its own sub-agents started, since a sub-agent shares the sandbox but has its
    own session -- so an unowned process is claimed only when it is running in
    this conversation's directory.
    """
    visible: list[dict[str, Any]] = []
    for process in processes:
        process_id = str(process["process_id"])
        owner = await runtime.resolve_session_for_process(process_id)
        if owner == session_id:
            visible.append(process)
            continue
        if owner is not None:
            continue
        if process.get("completed"):
            # A finished process nobody owns is somebody else's history.
            # Bindings are cleared on completion and the index was never
            # pruned, so every command any of this user's conversations had
            # ever finished arrived here unowned -- and unowned was enough to
            # be listed, and adopted.
            continue
        if not within(process.get("cwd"), own_cwd):
            continue
        # Unowned, running, and in our own tree: its binding expired, or it was
        # started outside the tool path. Claiming it here is how an agent
        # recovers a process it can otherwise no longer address.
        if session_id:
            await runtime.bind_process_to_session(
                process_id=process_id,
                session_id=session_id,
            )
        visible.append(process)
    return visible
