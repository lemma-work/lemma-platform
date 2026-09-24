"""Whether a run executes on the installation owner's Mac. Decided once per run.

See docs/architecture/desktop-host-execution.md §2. A run gets host execution
only when **all** of these hold, and otherwise the owner-agnostic VM sandbox,
exactly as before:

1. this is a Desktop local install;
2. the workspace -- the conversation's user -- is the installation owner;
3. the run's triggering human is the owner (below);
4. the owner has an online paired Agent Host with host execution on and
   available, and it opens the workspace when asked.

**Who triggered the run.** A conversation here belongs to one user, and every
run in it runs as that user, so (2) already names the only human who can type
into it. What remains is *how* the run started. Only a person in Lemma's own
app qualifies: a message, a retry, an answer to a question or an approval, or
the queued follow-up of messages they sent. Continuations of a run the owner
started -- a ``wait_for`` waking, a reply they were waiting on -- qualify for
the same reason, because the work they continue is the owner's. Nothing a
schedule started does, and nothing that arrived on a channel does: a Slack or
email or Telegram sender is the platform's assertion, not an owner's session,
so an inbound channel run never executes on the host, whoever it resolved to.
An unknown source does not qualify -- a new source has to be added here on
purpose.

**Once.** The check runs when the run's context is built, and the answer is
carried on the context for every tool call of the run. The host sandbox's id
records the fabric, so its operations can only ever go to the host; a host
that goes away mid-run fails the next operation with a sentence, and nothing
falls back to the VM.

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

from app.core.log.log import get_logger
from app.modules.agent.infrastructure.agent_host.host_execution import (
    host_execution_host_id,
)
from app.modules.agent.domain.agent_host import AGENT_HOST_SESSION_METADATA_KEY
from app.modules.agent.domain.entities import AgentRun, Conversation
from app.modules.agent.services.workspace_location import resolve_workspace_location
from app.modules.identity.contracts.installation import (
    is_desktop_installation,
    is_installation_owner,
)
from app.modules.workspace.contracts.host_execution import (
    HostWorkspace,
    open_host_workspace,
)

logger = get_logger(__name__)

#: A person, in Lemma, on this turn.
OWNER_SOURCES = frozenset(
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


def triggered_by_owner(conversation: Conversation, agent_run: AgentRun) -> bool:
    """Whether a person in Lemma's own app started this run. See the module."""
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    if metadata.get("surface_platform") or metadata.get("is_sub_agent"):
        return False
    # Read as `brief_lines.run_source_of` does; not imported, to keep the
    # prompt-brief machinery out of this module's import graph.
    run_metadata = agent_run.metadata if isinstance(agent_run.metadata, dict) else {}
    source = run_metadata.get("source")
    if source in OWNER_SOURCES:
        return True
    started_by_schedule = str(metadata.get("started_by") or "").upper() == "SCHEDULE"
    return source in CONTINUATION_SOURCES and not started_by_schedule


@dataclass(frozen=True, slots=True)
class HostExecutionFacts:
    """Where each rule's answer comes from; injected, so a test can state them.

    Each is a question another part of the system owns: the deployment kind and
    the owner are identity's, the host's state is the link's, and opening the
    workspace is the workspace module's.
    """

    is_desktop: Callable[[], bool] = is_desktop_installation
    is_owner: Callable[[UUID], Awaitable[bool]] = is_installation_owner
    usable_host: Callable[[UUID], Awaitable[UUID | None]] = host_execution_host_id
    open_workspace: Callable[..., Awaitable[HostWorkspace]] = open_host_workspace


FACTS = HostExecutionFacts()


async def owner_may_execute_on_host(
    user_id: UUID, *, facts: HostExecutionFacts = FACTS
) -> UUID | None:
    """Rules 1, 2 and 4's first half: the owner's usable host, if any."""
    if not facts.is_desktop():
        return None
    if not await facts.is_owner(user_id):
        return None
    return await facts.usable_host(user_id)


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


async def choose_host_workspace(
    *,
    conversation: Conversation,
    agent_run: AgentRun,
    user_id: UUID,
    facts: HostExecutionFacts = FACTS,
) -> HostWorkspace | None:
    """The host workspace this run executes in, or None for the VM."""
    if conversation.user_id != user_id:
        return None
    if not triggered_by_owner(conversation, agent_run):
        return None
    host_id = await owner_may_execute_on_host(user_id, facts=facts)
    if host_id is None:
        return None
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
        return None
    logger.info(
        "agent.host_execution.chosen",
        conversation_id=str(conversation.id),
        agent_run_id=str(agent_run.id),
        host_id=str(host_id),
    )
    return workspace


async def host_runs_native_commands(
    conversation: Conversation, *, facts: HostExecutionFacts = FACTS
) -> bool:
    """§7: an owner's coding-agent run with host execution on.

    Not gated on who triggered the run: the coding agent already runs on the
    Mac whoever asked, so the question is only whether Lemma's command tools
    would duplicate the ones it has there.
    """
    return (
        await owner_may_execute_on_host(conversation.user_id, facts=facts) is not None
    )
