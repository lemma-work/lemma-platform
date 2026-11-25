"""What a repository conversation has changed, read where its checkout is.

The read is two halves on purpose. The rows -- may the caller have this
conversation, does it work in a repository, where did its last run work, which
agent's machine is it on -- are read in one short unit of work that has closed
before any machine is asked anything, so a slow machine never holds a database
connection.

Then the machine. It is the one a command of this conversation's agent would
run on, decided the way a command's is (``resolve_agent_machine``): the
person's own unless the agent names a pod machine, and refused -- reported as
``machine_unavailable`` -- where a run of this conversation would be refused,
as for a conversation answering somebody outside the pod. A machine that is
not running is not started to answer (``read_if_awake``).

**Who may ask.** Only the conversation's owner: a conversation is reachable by
nobody else (``validate_conversation_access``), and the agent grant the read
needs is checked as for reading its messages. A pod app's page acting as its
viewer reaches its viewer's own conversations the same way, which lets the
app's script read that diff; it could already read most of it in the
conversation's ``edit_file`` calls.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from functools import partial
from typing import Protocol
from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.domain.conversation_changes import (
    NOT_CHECKED_OUT_EXIT,
    ChangesOutputError,
    ConversationChanges,
    changes_command,
    checkout_dir,
    parse_changes,
)
from app.modules.agent.domain.project_repo import ProjectRepo
from app.modules.agent.domain.workspace_location import resolve_workspace_location
from app.modules.agent.infrastructure import repositories
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.services.conversation_access import authorized_conversation

logger = get_logger(__name__)

#: How long the read may take on the machine. ``git diff`` over a large tree
#: is seconds; past this the card is better told nothing than kept waiting.
READ_TIMEOUT_SECONDS = 30

CHANGES_UNREADABLE = "CONVERSATION_CHANGES_UNREADABLE"
CHANGES_TIMED_OUT = "CONVERSATION_CHANGES_TIMED_OUT"


@dataclass(frozen=True, slots=True)
class ChangesTarget:
    """Everything the machine half needs, read from the rows."""

    conversation_id: UUID
    pod_id: UUID
    owner_id: UUID
    repo: ProjectRepo
    cwd: str
    agent_id: UUID | None
    agent_name: str | None
    machine_ref: str | None


class MachineOutput(Protocol):
    """What the read printed, and how it ended: ``agent_machines.MachineRead``."""

    @property
    def exit_code(self) -> int | None: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...


#: The command to run, given the run's own directory on a shared machine.
CommandFor = Callable[[str | None], str]
#: Runs the command on the conversation's machine; None when it is not there to
#: run on -- refused to a run of the conversation, asleep, or unreachable.
Reach = Callable[[ChangesTarget, CommandFor], Awaitable[MachineOutput | None]]
OpenUnit = Callable[[], AbstractAsyncContextManager[SqlAlchemyUnitOfWork]]


class LoadTarget(Protocol):
    def __call__(
        self, *, conversation_id: UUID, user_id: UUID, pod_id: UUID
    ) -> Awaitable[ChangesTarget | ConversationChanges]: ...


async def reach_conversation_machine(
    target: ChangesTarget, command_for: CommandFor
) -> MachineOutput | None:
    """Run the read on the machine this conversation's agent runs commands on."""
    from app.modules.workspace.contracts.agent_machines import (
        read_if_awake,
        usable_agent_machine,
    )
    from app.modules.workspace.contracts.runtime_errors import machine_runtime_errors

    # Held for its person's approval counts as refused: until they say so, a
    # run of this conversation may not use the machine, and nor may this read
    # on its behalf.
    machine = await usable_agent_machine(
        ref=target.machine_ref,
        user_id=target.owner_id,
        pod_id=target.pod_id,
        agent_id=target.agent_id,
        agent_name=target.agent_name,
        conversation_id=target.conversation_id,
    )
    if machine is None:
        return None
    try:
        return await read_if_awake(
            machine,
            acting_user=target.owner_id,
            command=command_for(machine.run_workdir),
            timeout_seconds=READ_TIMEOUT_SECONDS,
        )
    except (*machine_runtime_errors(), OSError) as exc:
        logger.warning(
            "agent.conversation_changes.machine_unreachable.degraded",
            error_type=type(exc).__name__,
        )
        return None


async def load_target(
    open_unit: OpenUnit, *, conversation_id: UUID, user_id: UUID, pod_id: UUID
) -> ChangesTarget | ConversationChanges:
    """The rows half: the conversation, authorized for the caller, and where
    its checkout is -- or the answer already, when the rows settle it."""
    from app.modules.agent.agent_host.api import ran_on_a_mac

    async with open_unit() as uow:
        agents = repositories.AgentRepository(uow)
        conversation = await authorized_conversation(
            Conversations(uow),
            agents,
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=None,
            action=Permissions.AGENT_READ,
            include_runs=True,
        )
        repo = conversation.repo
        if repo is None:
            return ConversationChanges(state="no_repository")
        # Its latest run: a coding agent through Agent Host, or a run with host
        # execution, worked on its person's Mac, and its checkout is there.
        if conversation.agent_runs and await ran_on_a_mac(
            uow, conversation.agent_runs[-1].id
        ):
            return ConversationChanges(state="elsewhere")
        agent = (
            await agents.get(conversation.agent_id)
            if conversation.agent_id is not None
            else None
        )
        return ChangesTarget(
            conversation_id=conversation.id,
            pod_id=conversation.pod_id,
            owner_id=conversation.user_id,
            repo=repo,
            cwd=resolve_workspace_location(conversation).cwd,
            agent_id=agent.id if agent is not None else None,
            agent_name=agent.name if agent is not None else None,
            machine_ref=agent.machine if agent is not None else None,
        )


class ConversationChangesReader:
    """Reads one conversation's changes for its owner. See the module.

    Both halves are arguments: ``load_target`` reads the rows
    (``load_target`` above, bound to a unit of work), ``reach`` asks the
    machine (``reach_conversation_machine``).
    """

    def __init__(
        self, load: LoadTarget, *, reach: Reach = reach_conversation_machine
    ) -> None:
        self._load = load
        self._reach = reach

    @classmethod
    def build(cls, open_unit: OpenUnit) -> ConversationChangesReader:
        return cls(partial(load_target, open_unit))

    async def read(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        patch_bytes: int | None,
    ) -> ConversationChanges:
        target = await self._load(
            conversation_id=conversation_id, user_id=user_id, pod_id=pod_id
        )
        if isinstance(target, ConversationChanges):
            return target
        repo = target.repo

        def command_for(run_workdir: str | None) -> str:
            return changes_command(
                directory=checkout_dir(repo, target.cwd, run_workdir=run_workdir),
                ref=repo.ref,
                patch_bytes=patch_bytes,
            )

        output = await self._reach(target, command_for)
        if output is None:
            return ConversationChanges(state="machine_unavailable")
        if output.exit_code is None:
            # The machine answered; the read is what ran out of time, and may
            # still be running there. Not "asleep", which invites a retry.
            logger.warning(
                "agent.conversation_changes.read_timed_out.degraded",
                timeout_seconds=READ_TIMEOUT_SECONDS,
            )
            raise DomainError(
                "Reading the checkout's changes took longer than "
                f"{READ_TIMEOUT_SECONDS} seconds, and may still be running on "
                "the machine.",
                code=CHANGES_TIMED_OUT,
                status_code=504,
            )
        if output.exit_code == NOT_CHECKED_OUT_EXIT:
            return ConversationChanges(state="not_checked_out")
        if output.exit_code != 0:
            raise _unreadable(output.stderr)
        try:
            return parse_changes(output.stdout, patch_bytes=patch_bytes)
        except ChangesOutputError as exc:
            raise _unreadable(str(exc)) from exc


def _unreadable(detail: str) -> DomainError:
    return DomainError(
        "The checkout's changes could not be read: "
        f"{detail.strip() or 'git failed without saying why'}",
        code=CHANGES_UNREADABLE,
        status_code=502,
    )
