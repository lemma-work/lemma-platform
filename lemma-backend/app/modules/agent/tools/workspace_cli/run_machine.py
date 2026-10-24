"""Which machine an agent's command runs on, and what a shared one needs.

The person's own machine is the default and needs nothing here. A pod
machine named by the call or by the agent's ``machine:`` field is resolved
through the machine resolver, gives the run a slot and its own directory, and
has the processes the run leaves behind tagged so they end with it.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.tools.context import BaseAgentContext

#: How a session id says which machine it is on, when it is not the default.
#: Carried in the id because a process is found again by its session id: the
#: poll that comes back for a long build has to reach the machine it started on.
_MACHINE_MARK = "@m:"


def machine_from_session(session_id: str | None) -> str | None:
    if session_id and _MACHINE_MARK in session_id:
        return session_id.rsplit(_MACHINE_MARK, 1)[1] or None
    return None


def _on_machine(session_id: str | None, machine_id) -> str | None:
    if session_id is None:
        return None
    base = session_id.split(_MACHINE_MARK, 1)[0]
    return f"{base}{_MACHINE_MARK}{machine_id}"


async def resolve_run_machine(ctx: BaseAgentContext, machine: str | None):
    """The machine a command goes to: the call's, else the agent's, else the
    person's own -- decided by the machine resolver, never assumed."""
    from app.modules.workspace.contracts.agent_machines import resolve_agent_machine

    return await resolve_agent_machine(
        ref=machine or getattr(ctx, "machine", None),
        user_id=ctx.user_id,
        pod_id=ctx.pod_id,
        agent_id=ctx.workload_id,
        agent_name=ctx.agent_name,
        conversation_id=ctx.conversation_id,
        # The conversation, not the run: a conversation waiting on its own
        # build ends one run and starts another when the build is done, and
        # both must find the same directory, slot and processes.
        run_id=ctx.conversation_id,
    )


async def _named_machine(ctx: BaseAgentContext, machine: str | None):
    """The machine a call names, or None for the person's own.

    The person's own is decided without a lookup: whether the run may use it
    was settled when its context was built (`started_in_lemma`). Any other
    machine goes through the machine resolver.
    """
    ref = (machine or getattr(ctx, "machine", None) or "").strip()
    if ref in ("", "me", "default"):
        if not getattr(ctx, "started_in_lemma", True):
            from app.modules.workspace.contracts.agent_machines import (
                ChannelRunHasNoMachine,
            )

            raise ChannelRunHasNoMachine()
        return None
    return await resolve_run_machine(ctx, ref)


async def place_on_machine(
    ctx: BaseAgentContext,
    machine: str | None,
    *,
    session_id: str | None,
    initial_cwd: str,
) -> tuple[str | None, str, UUID | None]:
    """Where a session opens: ``(session_id, cwd, machine_id)``.

    The person's own machine changes nothing. A shared machine takes a run
    slot and gives the run its own directory -- and a project its own
    checkout inside it, so two reviews of one repo never share a tree.
    """
    target = await _named_machine(ctx, machine)
    if target is None or not target.shared:
        return session_id, initial_cwd, None
    from app.modules.workspace.contracts.agent_machines import begin_run

    await begin_run(target, run_id=ctx.conversation_id, acting_user=ctx.user_id)
    cwd = target.run_workdir or initial_cwd
    repo = ctx.workspace_repo
    if repo is not None and target.run_workdir:
        cwd = f"{target.run_workdir}/repos/{repo.owner}/{repo.repo}"
    return _on_machine(session_id, target.machine_id), cwd, target.machine_id


async def tag_for_run(
    ctx: BaseAgentContext, session, process_id: str, cmd: str
) -> None:
    """A process left running on a shared machine ends with the run."""
    machine_ref = machine_from_session(session.session_id)
    if machine_ref is None:
        return
    from app.modules.workspace.contracts.agent_machines import tag_run_process

    target = await resolve_run_machine(ctx, machine_ref)
    await tag_run_process(
        target,
        run_id=ctx.conversation_id,
        process_id=str(process_id),
        command=cmd,
        cwd=getattr(session, "_cwd", "") or "",
        started_by=ctx.user_id,
    )


__all__ = [
    "machine_from_session",
    "place_on_machine",
    "resolve_run_machine",
    "tag_for_run",
]
