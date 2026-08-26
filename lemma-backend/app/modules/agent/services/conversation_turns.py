"""Starting and stopping a turn.

A turn is one user message plus the run that answers it. Starting one is mostly
a question of *whether* to start a run at all -- a message typed while a run is
already going joins that run instead of racing a second one -- and the answer
has to be decided under the conversation lock, because two browser tabs asking
at once must not each get a run.

Stopping is the same question in reverse, and the asymmetry is deliberate: a
stop closes any pause the run was sitting on, but never starts the resume run
that closing a pause normally triggers. Stop means stop.

Split from `ConversationService` because this is the part with the lock, the
ordering constraints and the outbox; the rest of that class is storage.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.composition.agent_snooze_scheduler import cancel_snooze_wake
from app.composition.agent_usage import UsageLimitExceededError, UsageService
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.events import (
    AgentRunStartedEvent,
    AgentRunStopRequestedEvent,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStartResult,
    AgentRunStatus,
    AgentRuntimeConfig,
    ConversationStatus,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.conversation_access import (
    require_agent_action,
    resolve_agent,
    resolve_expected_agent_id,
    validate_conversation_access,
)
from app.modules.agent.services.conversation_approvals import ApprovalCoordinator
from app.modules.agent.services.pause_resume import PauseResume
from app.modules.agent.services.pod_runtime_defaults import (
    default_agent_runtime_for_pod,
)
from app.modules.agent.services.realtime import (
    input_added_payload,
    message_payload,
    publish_conversation_event,
)
from app.modules.agent.services.run_dispatch import run_enqueue_suppressed
from app.modules.agent.services.serialization import message_to_payload
from app.modules.agent.tools.snooze.models import (
    build_snooze_result,
    elapsed_seconds,
)

logger = get_logger(__name__)


class TurnCoordinator:
    """Starts the run that answers a message, and stops the one in flight."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        conversation_repository: object,
        agent_repository: object,
        approvals: ApprovalCoordinator,
        pauses: PauseResume,
        usage_service: UsageService | None,
    ) -> None:
        self.uow = uow
        self.conversation_repository = conversation_repository
        self.agent_repository = agent_repository
        self.approvals = approvals
        self.pauses = pauses
        self.usage_service = usage_service

    async def start(
        self,
        conversation: Conversation,
        *,
        user_id: UUID,
        pod_id: UUID,
        content: str,
        agent_name: str | None,
        message_metadata: dict[str, object] | None,
    ) -> AgentRunStartResult:
        """Append the message and return the run that will answer it.

        The conversation is already loaded and access-checked by the caller --
        this is the half that needs the lock.
        """
        # Resolve the agent (a read) before taking the conversation lock, so the
        # FOR UPDATE span covers only the active-run check + run/message writes.
        agent = await resolve_agent(
            conversation,
            user_id=user_id,
            agent_repository=self.agent_repository,
        )

        await self.conversation_repository.lock_conversation(conversation.id)
        active_run = await self.conversation_repository.get_active_agent_run_for_update(
            conversation.id
        )
        started_new_run = active_run is None
        superseded_returns: list[Message] = []
        if active_run is None:
            # A prior run may have paused on ask_user/request_approval (conversation
            # -> WAITING) without the user ever resolving it — the composer stays
            # enabled during WAITING, so the user can type past the card. Deny any
            # such leftover call now: otherwise this new run's history rebuild finds
            # no matching return for it and silently drops it (see
            # PydanticAIHarness._build_tool_batch), permanently losing the model's
            # memory of asking and leaving the UI card stuck "needs your input".
            superseded_returns = (
                await self.approvals.supersede_stale_pending_interactions(
                    conversation=conversation,
                    user_id=user_id,
                )
            )
            selected_agent_runtime = (
                conversation.agent_runtime
                or agent.agent_runtime
                or await default_agent_runtime_for_pod(
                    self.uow, pod_id=conversation.pod_id
                )
            )
            await self.assert_usage_preflight_allowed(
                organization_id=conversation.organization_id,
                user_id=user_id,
                agent_runtime=selected_agent_runtime,
            )
            active_run = await self.conversation_repository.create_agent_run(
                conversation_id=conversation.id,
                agent_id=conversation.agent_id,
                agent_runtime=selected_agent_runtime,
                metadata={"source": "user_message"},
            )

        # The flag goes on *after* the caller's metadata, not before it: a
        # surface builds this dict out of webhook fields, and the run this
        # message belongs to is not something the sender gets a vote on.
        metadata = {
            **(message_metadata or {}),
            "during_active_run": not started_new_run,
        }
        metadata.pop("author_user_id", None)
        metadata.pop("agent_run_id", None)

        saved_user_message = await self.conversation_repository.append_message(
            conversation_id=conversation.id,
            agent_run_id=active_run.id,
            draft=MessageDraft.of_text(
                content,
                role=MessageRole.USER,
                metadata=metadata,
            ),
        )

        if started_new_run and not run_enqueue_suppressed():
            self.uow.collect_events(
                [
                    AgentRunStartedEvent(
                        conversation_id=conversation.id,
                        agent_run_id=active_run.id,
                        user_id=user_id,
                        pod_id=pod_id,
                        agent_name=agent_name,
                    )
                ]
            )

        # Streaming endpoints need the message/run and its outbox event committed
        # atomically before the worker can safely load them; normal CRUD methods
        # still rely on the request UoW.
        await self.uow.commit()
        # After the commit, not inside it: this claimed to run "now that they're
        # durably committed", but the commit is the caller's, so it held a
        # connection across a Redis round trip with the row locked. Not the
        # outbox -- these are live UI frames; the next fetch recovers a lost one.
        frames = [
            message_payload(item.agent_run_id, message_to_payload(item))
            for item in superseded_returns
        ] + [input_added_payload(active_run.id, message_to_payload(saved_user_message))]

        async def _publish_frames() -> None:
            for frame in frames:
                await publish_conversation_event(conversation.id, frame)

        self.uow.after_commit(_publish_frames)
        return AgentRunStartResult(
            conversation_id=conversation.id,
            agent_run_id=active_run.id,
            started_new_run=started_new_run,
        )

    async def start_queued_followup(
        self,
        *,
        conversation: Conversation,
        completed_run_id: UUID,
    ) -> tuple[UUID, list[Message]] | None:
        """The backstop for messages no run ever read.

        Normally nothing reaches here. A message sent while a run is in flight is
        steered into that run by `PendingUserMessagesCapability`, which claims it
        and hands it to the model -- so by the time the run ends there is nothing
        left owing. Two cases still get past that, and both leave a person
        waiting on an answer that will otherwise never come:

        - **A run with no capabilities.** Only the in-process LEMMA harness is
          built out of them; an Agent Host run has no `ctx.enqueue` to steer.
        - **A run that died before draining**, so the messages are still
          unclaimed.

        Returns the new run's id and the live UI frames the caller owes the
        conversation, or None when there is nothing to answer. The frames come
        back rather than going out from here because publishing is a Redis round
        trip and this still holds a pooled connection -- and unlike `start`,
        whose caller commits again later and drains `after_commit`, the caller
        here is a worker job with no second commit to hang them on.

        This cannot recur: the queued messages belong to ``completed_run_id``,
        never to the run started here, so the run started here has an empty
        queue of its own unless the person types during it too -- which is the
        case that should chain.
        """
        if run_enqueue_suppressed():
            # The caller executes runs itself and only asked for the ones it
            # started. Creating one here would leave a RUNNING row nobody runs.
            return None
        if conversation.status == ConversationStatus.WAITING:
            # The run ended by asking the person something. Starting a turn now
            # would auto-deny that question before they had a chance to see it,
            # and the queue does not need it: their answer starts a run whose
            # history rebuild picks these messages up along the way.
            return None
        if not await self.conversation_repository.count_queued_user_messages(
            completed_run_id
        ):
            return None

        await self.conversation_repository.lock_conversation(conversation.id)
        if await self.conversation_repository.get_active_agent_run_for_update(
            conversation.id
        ):
            # Someone typed between that run finishing and this check. Their
            # message started a turn whose history already covers the queue.
            return None

        # Same reason as in `start`: a pausing call left unresolved by the run
        # that just ended would have no matching return in the next run's
        # history rebuild, and would be dropped along with the model's memory of
        # having asked.
        superseded_returns = await self.approvals.supersede_stale_pending_interactions(
            conversation=conversation,
            user_id=conversation.user_id,
        )
        completed_run = await self.conversation_repository.get_agent_run(
            completed_run_id
        )
        agent_runtime = (
            (completed_run.agent_runtime if completed_run is not None else None)
            or conversation.agent_runtime
            or await default_agent_runtime_for_pod(self.uow, pod_id=conversation.pod_id)
        )
        # Continuing an unanswered message is still a run someone pays for.
        await self._assert_usage_preflight_allowed(
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
            agent_runtime=agent_runtime,
        )
        followup_run = await self.conversation_repository.create_agent_run(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            agent_runtime=agent_runtime,
            metadata={
                "source": "queued_messages",
                "queued_behind_agent_run_id": str(completed_run_id),
            },
        )
        self.uow.collect_events(
            [
                AgentRunStartedEvent(
                    conversation_id=conversation.id,
                    agent_run_id=followup_run.id,
                    user_id=conversation.user_id,
                    pod_id=conversation.pod_id,
                    # Resolved from the conversation, as it is for every run
                    # nobody named an agent for.
                    agent_name=None,
                )
            ]
        )
        await self.uow.commit()
        return followup_run.id, superseded_returns

    async def stop_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
    ) -> Conversation:
        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        conversation = await self.conversation_repository.get_conversation(
            conversation_id
        )
        validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        await require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=conversation.agent_id,
            action=Permissions.AGENT_EXECUTE,
        )
        active_run = await self.conversation_repository.get_active_agent_run_for_update(
            conversation.id
        )
        if active_run is not None:
            finish_result = await self.conversation_repository.finish_agent_run(
                agent_run_id=active_run.id,
                status=AgentRunStatus.STOP_REQUESTED,
            )
            if finish_result is not None:
                conversation.status = finish_result.conversation_status
            self.conversation_repository.collect_events(
                [
                    AgentRunStopRequestedEvent(
                        conversation_id=conversation.id,
                        agent_run_id=active_run.id,
                        user_id=user_id,
                    )
                ]
            )
            await self.uow.commit()
            return conversation

        # No active run, but the conversation may still be suspended. A snoozed
        # turn has *no* run by construction — it ended cleanly when the tool
        # paused it — so without this, Stop silently did nothing and the timer
        # still fired later.
        await self._cancel_active_snooze(conversation=conversation)
        return conversation

    @property
    def wait_repository(self) -> AgentConversationWaitRepository:
        # Built on demand rather than in __init__: the repository binds a session
        # eagerly, and plenty of callers construct this service without a real
        # unit of work to exercise paths that never touch the database.
        return AgentConversationWaitRepository(self.uow)

    async def _cancel_active_snooze(self, *, conversation: Conversation) -> None:
        """Stop a sleeping agent for good: drop the timer, never resume.

        The CANCELLED tool return is still written, so the paused call is not
        left dangling in history — a tool call with no return is dropped when
        history is rebuilt, and the model would see a turn that ends mid-thought.
        What is deliberately skipped is ``start_resume_run_if_ready``: Stop means
        the agent does not wake.
        """
        wait = await self.wait_repository.find_active_for_conversation(conversation.id)
        if wait is None:
            return

        wait.cancel()
        await self.wait_repository.update(wait)
        await self.conversation_repository.set_conversation_status(
            conversation_id=conversation.id,
            status=ConversationStatus.STOPPED,
        )
        conversation.status = ConversationStatus.STOPPED
        await self.pauses.append_pause_tool_return(
            conversation=conversation,
            paused_run_id=wait.agent_run_id,
            tool_call_id=wait.tool_call_id,
            tool_name="snooze",
            tool_result=build_snooze_result(
                woke_because="CANCELLED",
                slept_seconds=elapsed_seconds((wait.spec or {}).get("started_at")),
                note_to_self=(wait.spec or {}).get("note_to_self"),
            ),
        )
        if wait.external_ref:
            await cancel_snooze_wake(wait.external_ref)

    async def assert_usage_preflight_allowed(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
        agent_runtime: AgentRuntimeConfig,
    ) -> None:
        """Refuse to start a run the account has no headroom for.

        Public because starting a turn is not the only way a run begins:
        `ConversationRetryService.retry_failed_run` starts one too, and a retry
        that skipped this would be a free way past a limit the first attempt
        was held to. Reached through `ConversationService.turns`, like `start`
        and `stop_conversation`.
        """
        if self.usage_service is None:
            return
        if not agent_runtime.profile_id.startswith("system:"):
            return
        limits = await self.usage_service.get_usage_limits(
            organization_id=organization_id,
            user_id=user_id,
        )
        if limits["allowed"]:
            return
        # Say which limit was reached, not just that one was: a person told
        # only "limit exceeded" cannot tell whether to wait for the month to
        # turn or to ask an admin for headroom. See PS-OPS-012.
        reached = [
            name
            for name, key in (
                ("organization monthly", "org_monthly"),
                ("user weekly", "user_weekly"),
                ("user monthly", "user_monthly"),
            )
            if not limits[key]["allowed"]
        ]
        raise UsageLimitExceededError(
            "LLM usage limit exceeded: "
            + ", ".join(f"{name} limit reached" for name in reached)
        )
