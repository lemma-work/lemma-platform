"""Getting one run onto a host, and keeping its credential alive.

Separated from the harness because dispatch and consumption are different
jobs with different failure modes: this one assembles a spec, mints a
run-scoped credential and hands both to the control plane, while the
harness drives whatever comes back. They share only the run id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.transient_errors import (
    is_deadlock,
    is_transient_database_error,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.private_notes import is_private_note, run_is_private
from app.modules.agent.domain.agent_host import (
    NEW_SESSION_ONLY,
    AgentHostPreApproval,
    AgentHostRunSpec,
)
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation, Message
from app.modules.agent.domain.prompts import load_agent_host_runtime_prompt
from app.modules.agent.domain.queued_messages import is_queued
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.agent_host.channels import poke_host
from app.modules.agent.infrastructure.agent_host.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host.repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.agent_host import run_resume, session_memory
from app.modules.agent.infrastructure.agent_host.repository_common import (
    AgentHostRepositoryError,
)
from app.modules.agent.infrastructure.harnesses.agent_host.pre_approvals import (
    pre_approvals_for,
    supports_pre_approval,
)
from app.modules.agent.infrastructure.harnesses.agent_host.run_config import (
    AgentHostRunConfig,
    joined_prompt,
    json_object,
    offered_selections,
)
from app.modules.agent.infrastructure.harnesses.agent_host.run_window import (
    DispatchedRun,
    credential_bounded_timeout,
)
from app.modules.agent.infrastructure.harnesses.agent_host.steering import (
    supports_steering,
)
from app.modules.agent.infrastructure.harnesses.remote_payload import (
    mcp_payload,
    run_start_payload,
    token_expires_at,
)
from app.modules.agent.infrastructure.mcp import exported_tool_name
from app.modules.agent.tools.final_answer.final_answer_toolset import (
    FINAL_ANSWER_TOOL_NAME,
    final_answer_expected,
)
from app.modules.agent.domain.workspace_location import resolve_workspace_location

logger = get_logger(__name__)


def run_spec_from(
    payload: JsonObject,
    *,
    conversation: Conversation,
    harness_id: UUID,
    profile_revision: str,
    run_config: AgentHostRunConfig,
    system_prompt: str,
    system_prompt_delivery: str | None,
    resume_session_id: str | None,
    run_deadline: datetime,
    pre_approvals: Sequence[AgentHostPreApproval] = (),
) -> AgentHostRunSpec:
    """The START_RUN spec for one run, out of its ``run_start_payload``.

    ``context`` stays on the wire, empty: both sides declare it
    (``tests/fixtures/wire_contract.json``) and the host accepts it absent or
    empty, but nothing on the host reads it, so nothing is put there. The spec
    rests in plaintext in the command row and in the host's own journal.
    """
    prompt = json_object(payload.get("prompt"))
    return AgentHostRunSpec(
        agent_run_id=UUID(str(payload["agent_run_id"])),
        conversation_id=conversation.id,
        harness_id=harness_id,
        profile_revision=profile_revision,
        model_name=run_config.model_name,
        config_selections=run_config.config_selections,
        system_prompt=system_prompt,
        system_prompt_delivery=system_prompt_delivery,
        prompt=[{"type": "text", "text": str(prompt.get("user_prompt") or "")}],
        resume_session_id=resume_session_id,
        workspace_cwd=resolve_workspace_location(conversation).cwd,
        run_deadline=run_deadline,
        pre_approvals=list(pre_approvals),
    )


async def refresh_credential[DepsT: AgentContext](
    *,
    uow_factory: UnitOfWorkFactory,
    agent_run_id: UUID,
    ctx: DepsT,
    options: HarnessOptions[DepsT],
    agent: Agent,
    conversation: Conversation,
) -> datetime | None:
    """Mint a replacement Lemma credential and send it to the host.

    The token dispatched with the run lasts an hour and nothing renewed it,
    so a long turn either had to be cut short at that expiry or carry on
    with every ``lemma_*`` call returning 401 — which the agent experiences
    as its tools disappearing part-way through the task.

    Returns the new expiry, or ``None`` if the refresh did not land, in
    which case the caller keeps the old one and tries again next cycle.
    """
    try:
        mcp = await mcp_payload(
            agent_run_id=agent_run_id,
            conversation_id=conversation.id,
            ctx=ctx,
            options=options,
            extra_tool_names=(
                [exported_tool_name(FINAL_ANSWER_TOOL_NAME)]
                if final_answer_expected(agent=agent, conversation=conversation)
                else []
            ),
        )
        encrypted = await get_secret_cipher().encrypt_json_async(mcp)
        if encrypted is None:
            raise RuntimeError("could not encrypt the refreshed MCP configuration")
        async with uow_factory() as uow:
            command = await AgentHostDispatchRepository(uow).enqueue_credential_refresh(
                run_id=agent_run_id,
                encrypted_mcp_payload=encrypted,
            )
            if command is not None:
                # Read back only by a worker that reclaims the run.
                await run_resume.record_credential_expiry(
                    uow, run_id=agent_run_id, expires_at=token_expires_at(mcp)
                )
            await uow.commit()
    except (
        AgentHostRepositoryError,
        RuntimeError,
        ValueError,
        KeyError,
        SQLAlchemyError,
    ) as exc:
        # A database that was only busy -- a deadlock, a pool with no free
        # connection -- is a refresh that did not land, like the rest of
        # these: the next cycle tries again, well inside the margin. Any
        # other database error is a bug and fails the run.
        if isinstance(exc, SQLAlchemyError) and not is_transient_database_error(exc):
            raise
        logger.warning(
            "agent.harnesses.agent_host.credential_refresh_failed.degraded",
            agent_run_id=str(agent_run_id),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return None
    if command is None:
        # The run ended while we were minting; nothing left to refresh.
        return None
    await poke_host(command.host_id)
    return token_expires_at(mcp)


def _resumed_tool_call_id(run: AgentRun | None) -> str | None:
    """Which paused tool call this run was started to answer, if any."""
    metadata = (run.metadata if run is not None else None) or {}
    value = metadata.get("resumed_tool_call_id")
    return value if isinstance(value, str) and value else None


async def enqueue_run[DepsT: AgentContext](
    *,
    uow_factory: UnitOfWorkFactory,
    event_timeout_seconds: float,
    agent: Agent,
    conversation: Conversation,
    messages: Sequence[Message],
    ctx: DepsT,
    options: HarnessOptions[DepsT],
    agent_run_id: UUID,
    run_config: AgentHostRunConfig,
) -> DispatchedRun:
    resumed = await _resumed_dispatch(
        uow_factory,
        agent_run_id=agent_run_id,
        event_timeout_seconds=event_timeout_seconds,
    )
    if resumed is not None:
        return resumed
    # Resolved first, because it decides what the prompt has to contain: a
    # run that will not even try to resume a session is talking to an agent
    # with no history, so the prompt must carry it.
    async with uow_factory() as uow:
        harness = await AgentHostRepository(uow).get_harness(
            harness_id=run_config.harness_id
        )
        if harness is None:
            raise RuntimeError("Agent Host harness is unavailable")
        session = await session_memory.resumed_session(
            uow,
            conversation_id=conversation.id,
            harness_id=run_config.harness_id,
            capabilities=harness.capabilities,
        )
        harness_id = harness.id
        host_id = harness.host_id
        steerable = supports_steering(harness.capabilities)
        pre_approving = supports_pre_approval(harness.capabilities)
        harness_key = harness.harness_key
        config_revision = harness.config_revision
        # From the same read as the revision admission pins the run to, so
        # what survives here is exactly what admission re-validates.
        run_config = offered_selections(
            run_config,
            config_options=list(harness.config_options or []),
            agent_run_id=agent_run_id,
        )
        # Set only on a run started to answer a pausing tool call. Read from
        # the run rather than passed in, because the run row is where the
        # resume recorded it and a second copy could only ever disagree.
        run = await ConversationRepository(uow).get_agent_run(agent_run_id)
        # Messages that joined this run before it was dispatched are carried in
        # the prompt below, so they are this run's to answer and must not also
        # be sent as steers. Only noted here: they are claimed in the one
        # transaction that admits the run (`claim_exactly`), so neither a
        # failed dispatch nor a crash can leave them claimed by a run that
        # never went out.
        # And only of this run's kind: a private note waits for a run that
        # answers in Lemma, and a message for the platform for one that
        # answers there (`domain/private_notes`).
        private = run_is_private(run.metadata if run is not None else None)
        messages = [
            message
            for message in messages
            if not is_queued(message.metadata)
            or is_private_note(message.metadata) == private
        ]
        carried = frozenset(
            message.id
            for message in messages
            if message.agent_run_id == agent_run_id and is_queued(message.metadata)
        )
    resumed_tool_call_id = _resumed_tool_call_id(run)
    admission = _Admission(
        resume_session_id=session.session_id if session is not None else None,
        session_last_run_id=session.last_run_id if session is not None else None,
        harness_id=harness_id,
        host_id=host_id,
        steerable=steerable,
        harness_key=harness_key,
        config_revision=config_revision,
        resumed_tool_call_id=resumed_tool_call_id,
        # Only to a host that says it honours them; any other would ignore the
        # field and ask the person again, which is what it does without one.
        pre_approvals=tuple(
            pre_approvals_for(messages, resumed_tool_call_id=resumed_tool_call_id)
            if pre_approving
            else ()
        ),
    )
    # The prompt is built with no transaction open, so the person can withdraw
    # a carried message while it is being built. The admission then finds it
    # gone, and the prompt is built again without it. Each retry only ever
    # loses messages, so a few are plenty; past that, something is wrong.
    for _attempt in range(_ADMISSION_ATTEMPTS):
        try:
            return await _admit(
                uow_factory=uow_factory,
                event_timeout_seconds=event_timeout_seconds,
                agent=agent,
                conversation=conversation,
                messages=messages,
                ctx=ctx,
                options=options,
                agent_run_id=agent_run_id,
                run_config=run_config,
                admission=admission,
                carried=carried,
            )
        except CarriedChanged as changed:
            carried = carried - changed.lost
            messages = [
                message for message in messages if message.id not in changed.lost
            ]
    raise RuntimeError(
        "the messages this turn carries kept changing while it was dispatched"
    )


_ADMISSION_ATTEMPTS = 3


async def _resumed_dispatch(
    uow_factory: UnitOfWorkFactory,
    *,
    agent_run_id: UUID,
    event_timeout_seconds: float,
) -> DispatchedRun | None:
    """The dispatch an earlier worker made for this run, if it made one.

    A run is reclaimed with its lease, its START_RUN and its host's journal
    all in place: dispatching it again could only find the same command, so
    this hands the consume loop what that dispatch settled instead of
    building a prompt and minting a credential nobody would use.
    """
    async with uow_factory() as uow:
        point = await run_resume.resume_point(uow, run_id=agent_run_id)
    if point is None:
        return None
    remaining = event_timeout_seconds
    if point.run_deadline is not None:
        remaining = (point.run_deadline - datetime.now(timezone.utc)).total_seconds()
    logger.info(
        "agent.harnesses.agent_host.dispatch_resumed",
        agent_run_id=str(agent_run_id),
        stream_cursor=point.stream_cursor,
    )
    return DispatchedRun(
        harness_key=point.harness_key,
        event_timeout_seconds=max(remaining, 0.0),
        credential_bounded=False,
        credential_expires_at=point.credential_expires_at,
        steerable=supports_steering(point.harness_capabilities),
        stream_cursor=point.stream_cursor,
        resumed=True,
    )


class CarriedChanged(Exception):
    """Carried messages were withdrawn or taken while the prompt was built."""

    def __init__(self, lost: frozenset[UUID]) -> None:
        super().__init__(f"{len(lost)} carried message(s) are no longer queued")
        self.lost = lost


async def claim_exactly(
    uow: SqlAlchemyUnitOfWork, *, agent_run_id: UUID, carried: frozenset[UUID]
) -> None:
    """Claim every message the prompt carries, or raise and claim none.

    The ``UPDATE`` locks the rows it claims until the caller's transaction
    ends, so a withdraw racing it either lands first -- and the claim comes back
    short -- or waits and then finds the message claimed. Short means the
    prompt carries something the person took back, so this raises
    `CarriedChanged` and the caller's transaction, admission and all, rolls
    back.
    """
    if not carried:
        return
    claimed = await ConversationRepository(uow).claim_queued_user_messages(
        agent_run_id, message_ids=sorted(carried)
    )
    lost = carried - frozenset(message.id for message in claimed)
    if lost:
        raise CarriedChanged(lost)


@dataclass(frozen=True, slots=True)
class _Admission:
    """What the first read settled, which the rest of dispatch has to honour."""

    resume_session_id: str | None
    session_last_run_id: UUID | None
    harness_id: UUID
    host_id: UUID
    steerable: bool
    harness_key: str
    config_revision: str
    resumed_tool_call_id: str | None
    pre_approvals: tuple[AgentHostPreApproval, ...]


#: One retry of the admitting transaction on a deadlock, as
#: ``AgentHostLinkStore.apply_control`` does for the host's frames. The lock
#: order (``HostRowLock``) removes the cycles we know about; this catches one we
#: do not, which would otherwise fail a run that did nothing wrong.
_DEADLOCK_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class _AdmittedRun:
    """Everything the admitting transaction writes, settled before it opens."""

    run_spec: AgentHostRunSpec
    host_id: UUID
    run_config: AgentHostRunConfig
    encrypted_mcp: JsonObject
    carried: frozenset[UUID]
    instructions_digest: str
    credential_expires_at: datetime | None


async def _admission_transaction(
    uow_factory: UnitOfWorkFactory, admitted: _AdmittedRun
) -> None:
    """Claim the carried messages, admit the run and commit, all or nothing."""
    run_id = admitted.run_spec.agent_run_id
    async with uow_factory() as uow:
        # First, so a claim that comes back short rolls back before anything
        # else is written; see `claim_exactly`.
        await claim_exactly(uow, agent_run_id=run_id, carried=admitted.carried)
        await AgentHostDispatchRepository(uow).enqueue_run(
            host_id=admitted.host_id,
            harness_id=admitted.run_config.harness_id,
            runtime_profile_id=admitted.run_config.runtime_profile_id,
            run_spec=admitted.run_spec,
            encrypted_mcp_payload=admitted.encrypted_mcp,
            command_ttl_seconds=admitted.run_config.wait_timeout_seconds,
        )
        # A promise, committed with the command it belongs to. It becomes a
        # record only when the host reports that it prompted, so a run that
        # dies on the way out does not leave these instructions marked
        # delivered and skipped for the rest of the conversation.
        await session_memory.record_pending_instructions(
            uow,
            conversation_id=admitted.run_spec.conversation_id,
            run_id=run_id,
            digest=admitted.instructions_digest,
        )
        await run_resume.record_credential_expiry(
            uow, run_id=run_id, expires_at=admitted.credential_expires_at
        )
        await uow.commit()


async def _commit_admission(
    uow_factory: UnitOfWorkFactory,
    admitted: _AdmittedRun,
    *,
    transaction: Callable[
        [UnitOfWorkFactory, _AdmittedRun], Awaitable[None]
    ] = _admission_transaction,
) -> None:
    """Run the admitting transaction, once more if Postgres broke a deadlock.

    Safe to run again: a deadlock aborts the whole transaction, so nothing is
    claimed and no lease or command exists when the retry starts, and it
    re-checks the host and harness from scratch. Anything that is not a
    deadlock propagates. ``transaction`` is a parameter only so a test can
    script the database's answers.
    """
    for attempt in range(_DEADLOCK_ATTEMPTS):
        try:
            await transaction(uow_factory, admitted)
            return
        except DBAPIError as exc:
            if attempt + 1 == _DEADLOCK_ATTEMPTS or not is_deadlock(exc):
                raise
            logger.warning(
                "agent.harnesses.agent_host.dispatch_deadlock_retried.degraded",
                agent_run_id=str(admitted.run_spec.agent_run_id),
                host_id=str(admitted.host_id),
                exc_info=True,
            )


async def _admit[DepsT: AgentContext](
    *,
    uow_factory: UnitOfWorkFactory,
    event_timeout_seconds: float,
    agent: Agent,
    conversation: Conversation,
    messages: Sequence[Message],
    ctx: DepsT,
    options: HarnessOptions[DepsT],
    agent_run_id: UUID,
    run_config: AgentHostRunConfig,
    admission: _Admission,
    carried: frozenset[UUID],
) -> DispatchedRun:
    resume_session_id = admission.resume_session_id
    harness_id = admission.harness_id
    host_id = admission.host_id
    payload = run_start_payload(
        agent=agent,
        conversation=conversation,
        messages=messages,
        ctx=ctx,
        agent_run_id=agent_run_id,
        runtime_instructions=load_agent_host_runtime_prompt(
            host_execution=bool(getattr(ctx, "host_runs_native_commands", False))
        ),
        carries_history=resume_session_id is None,
        resumed_tool_call_id=admission.resumed_tool_call_id,
        session_last_run_id=admission.session_last_run_id,
    )
    prompt = json_object(payload.get("prompt"))
    mcp = await mcp_payload(
        agent_run_id=agent_run_id,
        conversation_id=conversation.id,
        ctx=ctx,
        options=options,
        prompt=joined_prompt(prompt),
        # final_answer is served by the MCP route but is not in
        # options.toolsets (the in-process harness gets it via output_type),
        # so name it explicitly.
        extra_tool_names=(
            [exported_tool_name(FINAL_ANSWER_TOOL_NAME)]
            if final_answer_expected(agent=agent, conversation=conversation)
            else []
        ),
    )
    encrypted_mcp = await get_secret_cipher().encrypt_json_async(mcp)
    if encrypted_mcp is None:
        raise RuntimeError("could not encrypt MCP configuration")
    # A conversation is one provider session, and a session keeps its own
    # history — so instructions delivered when it opened are still there on
    # every later turn. Re-sending them each time put another copy of a
    # multi-kilobyte block into the provider's transcript per message, which it
    # then re-read in full on every turn after that. Skipped only when this
    # exact text is already known to have reached this session; the host still
    # sends it if it ends up opening a new one. Only conversation-stable text is
    # in it -- the task list, memory, the attached doc and open requests ride in
    # the user prompt -- so an active conversation keeps one digest.
    system_prompt = str(
        prompt.get("system_prompt") or prompt.get("recovery_system_prompt") or ""
    )
    digest = session_memory.instructions_digest(system_prompt)
    system_prompt_delivery: str | None = None
    if resume_session_id is not None:
        async with uow_factory() as uow:
            if await session_memory.instructions_already_delivered(
                uow,
                conversation_id=conversation.id,
                harness_id=harness_id,
                digest=digest,
            ):
                system_prompt_delivery = NEW_SESSION_ONLY
    dispatched_at = datetime.now(timezone.utc)
    timeout_seconds, credential_bounded = credential_bounded_timeout(
        configured_seconds=event_timeout_seconds,
        credential_expires_at=token_expires_at(mcp),
        now=dispatched_at,
        agent_run_id=agent_run_id,
    )
    run_spec = run_spec_from(
        payload,
        conversation=conversation,
        harness_id=harness_id,
        profile_revision=admission.config_revision,
        run_config=run_config,
        system_prompt=system_prompt,
        system_prompt_delivery=system_prompt_delivery,
        resume_session_id=resume_session_id,
        run_deadline=dispatched_at + timedelta(seconds=timeout_seconds),
        pre_approvals=admission.pre_approvals,
    )
    admitted = _AdmittedRun(
        run_spec=run_spec,
        host_id=host_id,
        run_config=run_config,
        encrypted_mcp=encrypted_mcp,
        carried=carried,
        instructions_digest=digest,
        credential_expires_at=token_expires_at(mcp),
    )
    await _commit_admission(uow_factory, admitted)
    await poke_host(host_id)
    return DispatchedRun(
        harness_key=admission.harness_key,
        event_timeout_seconds=timeout_seconds,
        credential_bounded=credential_bounded,
        credential_expires_at=token_expires_at(mcp),
        steerable=admission.steerable,
    )
