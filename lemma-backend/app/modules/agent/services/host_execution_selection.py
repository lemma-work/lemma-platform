"""Whether a run executes on the Mac of the user it runs for. Decided once per run.

See docs/architecture/desktop-host-execution.md §2. There is no installation
owner: a run is routed by the **Agent Host pairing**. It gets host execution
only when **all** of these hold, and otherwise the VM sandbox, exactly as
before:

1. this is a Desktop local install (the backend runs beside the hosts it
   would route to; a hosted deployment never routes a run to a machine);
2. the run acts as the conversation's user -- the workspace is theirs;
3. the run's triggering human is that user, in Lemma's own app (below);
4. that user -- the one the host is paired to -- has an online Agent Host
   with host execution on and available, and it opens the workspace when
   asked. With several, the one the conversation last ran on, else the most
   recently seen (``host_execution_host_id``).

A host is paired to exactly one user, and only that user's runs are ever
routed to it: somebody else's run on the same installation goes to their own
host if they have one, or to the VM.

**Who triggered the run.** A conversation here belongs to one user, and every
run in it runs as that user, so (2) already names the only human who can type
into it. What remains is *how* the run started. Only a person in Lemma's own
app qualifies: a message, a retry, an answer to a question or an approval, or
the queued follow-up of messages they sent. Continuations of a run that person
started -- a ``wait_for`` waking, a reply they were waiting on -- qualify for
the same reason, because the work they continue is theirs. Nothing a schedule
started does, and nothing that arrived on a channel does: a Slack or email or
Telegram sender is the platform's assertion, not the user's session, so an
inbound channel run never executes on the host, whoever it resolved to.
Sub-agent conversations never do either. An unknown source does not qualify --
a new source has to be added here on purpose.

**Once.** The check runs the first time the run's context is built, and the
answer -- host or VM, and for the host which Mac and which folder -- is
written on the run (``run_execution_record``). A
context rebuilt for the same run -- a worker reclaiming it, an approved tool
executing after a pause -- reads that answer back instead of deciding again,
so a run never moves. The host sandbox's id records the fabric, so its
operations can only ever go to the host, and the record names the host, so
they go to that one; a host that goes away mid-run fails the next operation
with "This Mac is not connected", and nothing falls back to the VM or to
another Mac.

**Agent Host runs** (Claude Code, Codex, ...) are not given a host sandbox:
they already run on the Mac. What they get instead is ``host_runs_native_
commands``, which withholds Lemma's duplicate command tools (§7).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from sandbox_runtime.errors import SandboxError

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.infrastructure.agent_host.host_execution import (
    host_execution_host_id,
)
from app.modules.agent.domain.agent_host import AGENT_HOST_SESSION_METADATA_KEY
from app.modules.agent.domain.entities import AgentRun, Conversation
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.agent.infrastructure.run_execution_record import (
    read_run_execution,
    record_run_execution,
)
from app.modules.agent.services.workspace_location import resolve_workspace_location
from app.modules.identity.contracts.installation import is_desktop_installation
from app.modules.workspace.contracts.host_execution import (
    HostFolder,
    HostWorkspace,
    open_host_workspace,
)

logger = get_logger(__name__)

#: A person, in Lemma, on this turn.
PERSON_SOURCES = frozenset(
    {
        "user_message",
        "queued_messages",
        "manual_retry",
        "approval_resume",
        "person",
    }
)
#: Continuing work a person started, with nobody new involved.
CONTINUATION_SOURCES = frozenset({"agent_wait", "wait_resume", "message_replies"})

_CWD_SUFFIX = re.compile(r"/c/(\d{4}-\d{2}-\d{2})/([A-Za-z0-9_-]{1,64})/?$")


def triggered_by_run_user(conversation: Conversation, agent_run: AgentRun) -> bool:
    """Whether the run's user, in Lemma's own app, started this run. See the module."""
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    if metadata.get("surface_platform") or metadata.get("is_sub_agent"):
        return False
    # Read as `brief_lines.run_source_of` does; not imported, to keep the
    # prompt-brief machinery out of this module's import graph.
    run_metadata = agent_run.metadata if isinstance(agent_run.metadata, dict) else {}
    source = run_metadata.get("source")
    if source in PERSON_SOURCES:
        return True
    started_by_schedule = str(metadata.get("started_by") or "").upper() == "SCHEDULE"
    return source in CONTINUATION_SOURCES and not started_by_schedule


async def _recorded_choice(run_id: UUID) -> dict[str, object] | None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        return await read_run_execution(uow, run_id)


async def _record_choice(run_id: UUID, value: dict[str, object]) -> None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        await record_run_execution(uow, run_id, value)
        await uow.commit()


def _choice_value(
    workspace: HostWorkspace | None, host_id: UUID | None
) -> dict[str, object]:
    if workspace is None or host_id is None:
        return {"target": "vm"}
    return {
        "target": "host",
        # Where this conversation's host sandbox is from now on: its
        # operations are routed by the latest of these (host_for_host_sandbox).
        "host_id": str(host_id),
        "sandbox_id": str(workspace.sandbox_id),
        "root": workspace.root,
    }


def workspace_from_choice(value: dict[str, object]) -> HostWorkspace | None:
    """The host workspace a recorded choice names, or None for the VM.

    A host choice that cannot be read is refused rather than read as "VM":
    running in the VM would be exactly the silent move this record prevents.
    """
    if value.get("target") != "host":
        return None
    sandbox_id, root = value.get("sandbox_id"), value.get("root")
    if not isinstance(sandbox_id, str) or not isinstance(root, str):
        raise ValueError("this run's recorded host workspace is unreadable")
    return HostWorkspace(sandbox_id=UUID(sandbox_id), root=root)


@dataclass(frozen=True, slots=True)
class HostExecutionFacts:
    """Where each rule's answer comes from; injected, so a test can state them.

    Each is a question another part of the system owns: the deployment kind is
    identity's, the paired host's state is the link's, and opening the
    workspace is the workspace module's.
    """

    is_desktop: Callable[[], bool] = is_desktop_installation
    #: ``(user_id, conversation_id) -> host_id``.
    usable_host: Callable[[UUID, UUID | None], Awaitable[UUID | None]] = (
        host_execution_host_id
    )
    open_workspace: Callable[..., Awaitable[HostWorkspace]] = open_host_workspace
    recorded: Callable[[UUID], Awaitable[dict[str, object] | None]] = _recorded_choice
    record: Callable[[UUID, dict[str, object]], Awaitable[None]] = _record_choice


FACTS = HostExecutionFacts()


async def paired_host_for(
    user_id: UUID,
    conversation_id: UUID | None = None,
    *,
    facts: HostExecutionFacts = FACTS,
) -> UUID | None:
    """Rules 1 and 4's first half: the usable host paired to this user, if any.

    Hosts are looked up by the user they are paired to, so a host is only ever
    found for its own user.
    """
    if not facts.is_desktop():
        return None
    return await facts.usable_host(user_id, conversation_id)


def host_folder_hint(conversation: Conversation) -> str | None:
    """The Mac folder this conversation is bound to, if Lemma has seen one.

    An Agent Host run in the conversation reports the directory it ran in;
    that is the same folder the folder chip binds. A conversation with no such
    run leaves the choice to the host, which also knows the chip's binding.
    """
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    binding = metadata.get(AGENT_HOST_SESSION_METADATA_KEY)
    cwd = binding.get("host_cwd") if isinstance(binding, dict) else None
    return cwd if isinstance(cwd, str) and cwd.startswith("/") else None


def default_folder(conversation: Conversation) -> tuple[str, str]:
    """``(day, slug)`` for ``~/lemma/c/<day>/<slug>``, from the conversation's cwd.

    The same suffix the VM path and the pod path use, so the three line up.
    """
    match = _CWD_SUFFIX.search(resolve_workspace_location(conversation).cwd)
    if match is not None:
        return match.group(1), match.group(2)
    return conversation.created_at.date().isoformat(), conversation.id.hex[:8]


async def host_folder_for(conversation_id: UUID) -> HostFolder | None:
    """What re-opening a conversation's host sandbox sends the Mac.

    The same folder inputs its first open sent, read again from the
    conversation. They matter only to a Mac that has lost its own record of
    the conversation's root (§5): the Mac keeps that record and prefers it, so
    a reopen lands where the run already works whatever these say.
    """
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        row = await uow.session.get(ConversationModel, conversation_id)
        if row is None:
            return None
        conversation = row.to_entity()
    day, slug = default_folder(conversation)
    return HostFolder(day=day, slug=slug, root_hint=host_folder_hint(conversation))


async def choose_host_workspace(
    *,
    conversation: Conversation,
    agent_run: AgentRun,
    user_id: UUID,
    facts: HostExecutionFacts = FACTS,
) -> HostWorkspace | None:
    """The host workspace this run executes in, or None for the VM.

    Decided once per run and recorded on it; see the module docstring. Off a
    Desktop install nothing is recorded, because nothing could be chosen.
    """
    if not facts.is_desktop():
        return None
    recorded = await facts.recorded(agent_run.id)
    if recorded is not None:
        return workspace_from_choice(recorded)
    host_id, workspace = await _select(
        conversation=conversation, agent_run=agent_run, user_id=user_id, facts=facts
    )
    await facts.record(agent_run.id, _choice_value(workspace, host_id))
    return workspace


async def recorded_host_workspace(
    run_id: UUID, *, facts: HostExecutionFacts = FACTS
) -> HostWorkspace | None:
    """What an earlier context build chose for this run; never selects anew."""
    if not facts.is_desktop():
        return None
    recorded = await facts.recorded(run_id)
    return workspace_from_choice(recorded) if recorded is not None else None


async def _select(
    *,
    conversation: Conversation,
    agent_run: AgentRun,
    user_id: UUID,
    facts: HostExecutionFacts,
) -> tuple[UUID | None, HostWorkspace | None]:
    if conversation.user_id != user_id:
        return None, None
    if not triggered_by_run_user(conversation, agent_run):
        return None, None
    host_id = await paired_host_for(user_id, conversation.id, facts=facts)
    if host_id is None:
        return None, None
    day, slug = default_folder(conversation)
    try:
        workspace = await facts.open_workspace(
            owner_id=user_id,
            conversation_id=conversation.id,
            host_id=host_id,
            day=day,
            slug=slug,
            root_hint=host_folder_hint(conversation),
        )
    except SandboxError:
        # Nothing has run anywhere yet, so the VM is still a clean choice.
        logger.warning(
            "agent.host_execution.open_failed.degraded",
            conversation_id=str(conversation.id),
            host_id=str(host_id),
            exc_info=True,
        )
        return None, None
    logger.info(
        "agent.host_execution.chosen",
        conversation_id=str(conversation.id),
        agent_run_id=str(agent_run.id),
        host_id=str(host_id),
    )
    return host_id, workspace


async def host_runs_native_commands(
    conversation: Conversation, *, facts: HostExecutionFacts = FACTS
) -> bool:
    """§7: a coding-agent run whose user's paired host has host execution on.

    Not gated on who triggered the run: the coding agent already runs on the
    Mac whoever asked, so the question is only whether Lemma's command tools
    would duplicate the ones it has there.
    """
    return (
        await paired_host_for(conversation.user_id, conversation.id, facts=facts)
        is not None
    )
