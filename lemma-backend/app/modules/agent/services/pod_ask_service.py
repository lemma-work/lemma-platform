"""Asking another pod: opening the conversation there that will answer.

The rules -- who may ask, how, how far a chain may go, what counts as the answer
-- are in ``domain/pod_asks.py``; bringing the answer back is
``pod_ask_delivery``. This opens (or continues) a conversation in the pod being
asked and starts a run there with the request as its message, then waits a few
seconds, so a quick lookup comes back in the same tool call.

The two modes differ in whose that conversation is and whose authority opens it:

* **As the person**: theirs, opened with their own access in the other pod --
  which is what makes "as you" mean exactly that.
* **Over a link**: the link's steward's, opened as the asking pod, whose grant
  to run that pod's assistant *is* the link. The run then answers as the asking
  pod too (``domain/outsiders``, ``core/authorization/pod_principal``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.pod_principal import build_pod_context
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pod_asks import (
    ASK_KEY,
    ASK_SOURCE,
    ASKS_OUT_KEY,
    AskChain,
    AskingRun,
    AskMode,
    AskRefused,
    PodAsk,
    answer_from,
    ask_message_metadata,
    ask_of,
    instructions_for_answering,
    plan_ask,
)
from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    AgentRunStatus,
    ConversationStatus,
)
from app.modules.agent.infrastructure.pod_ask_queries import (
    PodAskQueries,
    Teammate,
    askable_pods,
)
from app.modules.agent.infrastructure.pod_link_queries import PodLinkQueries
from app.modules.agent.services.pod_ask_delivery import ANSWER_WINDOW, BUSY
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.services.poll_backoff import poll_delay
from app.modules.identity.contracts.profiles import UserProfileRef, user_profile
from app.modules.pod.contracts.members import pod_member_id, pod_name
from app.modules.usage.contracts.execution import build_usage_service

if TYPE_CHECKING:
    from app.modules.agent.tools.context import BaseAgentContext

logger = get_logger(__name__)

#: The first pause between checks while an asker waits inline; ``poll_delay``
#: grows it, so a wait of half a minute is a handful of reads, not thirty.
_POLL_SECONDS = 1.0
_DENIED_STATUS_CODES = frozenset({401, 403})


@dataclass(frozen=True, slots=True)
class AskOutcome:
    """Where an ask stands when the asking tool call returns."""

    pod_id: UUID
    pod_name: str
    conversation_id: UUID
    #: ANSWERED, WORKING, WAITING or UNFINISHED.
    status: str
    answer: str | None = None
    detail: str | None = None


def _outcome(
    teammate: Teammate,
    conversation_id: UUID,
    *,
    status: str,
    answer: str | None = None,
    detail: str | None = None,
) -> AskOutcome:
    return AskOutcome(
        pod_id=teammate.pod_id,
        pod_name=teammate.name,
        conversation_id=conversation_id,
        status=status,
        answer=answer,
        detail=detail,
    )


def resolve_teammate(teammates: list[Teammate], said: str) -> Teammate:
    """The pod an agent named, by id or by its exact name, or a refusal listing them."""
    said = said.strip()
    for teammate in teammates:
        if str(teammate.pod_id) == said:
            return teammate
    if not teammates:
        raise AskRefused(
            "There is no other pod to ask: the person isn't in one, and none is "
            "connected to this pod."
        )
    matches = [one for one in teammates if one.name.casefold() == said.casefold()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise AskRefused(
            f"More than one pod is called {said}. Pass the pod id from "
            "`list_teammates` instead."
        )
    names = ", ".join(one.name for one in teammates)
    raise AskRefused(
        f"No pod this one can ask is called {said!r}. It can ask: {names}."
    )


@dataclass(frozen=True, slots=True)
class AskPlan:
    mode: AskMode
    chain: AskChain


def _plan(
    deps: BaseAgentContext,
    asking: Conversation | None,
    teammate: Teammate,
    *,
    latest_turn_source: str | None,
) -> AskPlan:
    if asking is None:
        raise AskRefused("This conversation no longer exists.")
    mode, chain = plan_ask(
        AskingRun(
            user_id=deps.user_id,
            pod_id=deps.pod_id,
            conversation=asking,
            is_pod_default_agent=deps.is_pod_default_agent,
            answers_outsider=deps.answers_outsider,
            surface_conversation_kind=deps.surface_conversation_kind,
            latest_turn_source=latest_turn_source,
        ),
        to_pod_id=teammate.pod_id,
        through_you=teammate.through_you,
        connected=teammate.connected,
    )
    return AskPlan(mode=mode, chain=chain)


def _person(profile: UserProfileRef | None) -> str:
    if profile is None:
        return "the person who asked"
    name = " ".join(
        part for part in (profile.first_name, profile.last_name) if part
    ).strip()
    return name or profile.email or "the person who asked"


class PodAskService:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    @staticmethod
    def _conversation_service(uow: SqlAlchemyUnitOfWork) -> ConversationService:
        return ConversationService(
            uow=uow,
            conversation_repository=ConversationRepository(uow),
            agent_repository=AgentRepository(uow),
            authorization_service=create_authorization_data_service(uow),
            usage_service=build_usage_service(uow),
        )

    async def teammates(
        self, *, user_id: UUID | None, organization_id: UUID | None, pod_id: UUID
    ) -> list[Teammate]:
        """The other pods this one can ask, through the person or over a link."""
        async with self.uow_factory() as uow:
            return await askable_pods(
                uow, user_id=user_id, organization_id=organization_id, pod_id=pod_id
            )

    async def ensure_may_ask(
        self, deps: BaseAgentContext, *, teammate: Teammate
    ) -> AskMode:
        """How the ask would be made, refused now if it can't be.

        Before anyone is asked to approve it: an ask that can't be made should
        not cost the person an approval first.
        """
        async with self.uow_factory() as uow:
            asking = await ConversationRepository(uow).get_conversation(
                deps.conversation_id
            )
            source = await PodAskQueries(uow).latest_turn_source(
                conversation_id=deps.conversation_id
            )
        return _plan(deps, asking, teammate, latest_turn_source=source).mode

    async def ask(
        self,
        deps: BaseAgentContext,
        *,
        teammate: Teammate,
        request: str,
        wait_seconds: float,
    ) -> AskOutcome:
        """Put the request to ``teammate``, and wait a little for the answer."""
        if not request.strip():
            raise AskRefused("Say what you need from it: the request is empty.")
        async with self.uow_factory() as uow:
            conversations = ConversationRepository(uow)
            asking = await conversations.get_conversation(deps.conversation_id)
            if asking is None:
                raise AskRefused("This conversation no longer exists.")
            plan = _plan(
                deps,
                asking,
                teammate,
                latest_turn_source=await PodAskQueries(uow).latest_turn_source(
                    conversation_id=asking.id
                ),
            )
            from_name = await pod_name(uow.session, deps.pod_id) or "Another pod"
            ask = PodAsk(
                from_pod_id=deps.pod_id,
                from_pod_name=from_name,
                from_conversation_id=asking.id,
                for_user_id=deps.user_id if plan.mode is AskMode.AS_PERSON else None,
                mode=plan.mode,
                chain=plan.chain,
                inline_until=(
                    datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
                    if wait_seconds > 0
                    else None
                ),
            )
            owner, context, instructions = await self._side(
                uow, deps, ask=ask, teammate=teammate
            )
            token = set_current_context(context)
            try:
                thread_id = await self._open_thread(
                    uow,
                    ask=ask,
                    owner_user_id=owner,
                    teammate=teammate,
                    instructions=instructions,
                )
                started = await self._conversation_service(
                    uow
                ).add_user_message_and_start_run(
                    conversation_id=thread_id,
                    user_id=owner,
                    content=request.strip(),
                    pod_id=teammate.pod_id,
                    agent_name=None,
                    message_metadata=ask_message_metadata(
                        from_pod_id=deps.pod_id,
                        from_pod_name=from_name,
                        from_conversation_id=asking.id,
                        mode=plan.mode,
                    ),
                )
            except DomainError as exc:
                if exc.status_code in _DENIED_STATUS_CODES:
                    raise AskRefused(
                        f"{teammate.name}'s assistant can't be asked from here: "
                        f"{exc.message}"
                    ) from exc
                raise
            finally:
                reset_current_context(token)
            # Marks this conversation as one that may be owed answers, so its
            # own completions look for any that arrived while it was paused.
            await conversations.set_conversation_metadata_key(
                asking.id, ASKS_OUT_KEY, True
            )
            await uow.commit()
        logger.info(
            "agent.pod_ask.asked",
            from_pod_id=str(deps.pod_id),
            to_pod_id=str(teammate.pod_id),
            ask_conversation_id=str(thread_id),
            depth=plan.chain.depth,
            mode=plan.mode.value,
        )
        return await self._await_answer(
            teammate=teammate,
            conversation_id=thread_id,
            run_id=started.agent_run_id,
            wait_seconds=wait_seconds,
        )

    async def _side(
        self,
        uow: SqlAlchemyUnitOfWork,
        deps: BaseAgentContext,
        *,
        ask: PodAsk,
        teammate: Teammate,
    ) -> tuple[UUID, Context, str | None]:
        """Who owns the conversation in the other pod, and whose authority opens it.

        As the person: theirs, and their own access there. Over a link: the
        link's steward's, opened as the asking pod -- the link grant is what lets
        it run that pod's assistant -- and refused when nobody looks after the
        link any more.
        """
        if ask.mode is AskMode.AS_PERSON:
            person = _person(await user_profile(uow.session, deps.user_id))
            context = await create_authorization_data_service(uow).build_user_context(
                user_id=deps.user_id, pod_id=teammate.pod_id
            )
            return (
                deps.user_id,
                context,
                instructions_for_answering(
                    from_pod_name=ask.from_pod_name, person=person
                ),
            )
        link = await PodLinkQueries(uow).link(
            answering_pod_id=teammate.pod_id, asking_pod_id=deps.pod_id
        )
        steward = link.steward_user_id if link is not None else None
        if (
            steward is None
            or await pod_member_id(uow, teammate.pod_id, steward) is None
        ):
            raise AskRefused(
                f"Nobody in {teammate.name} looks after its link to this pod any "
                "more. Someone there needs to connect the two again."
            )
        context = build_pod_context(
            session=uow.session,
            pod_id=teammate.pod_id,
            organization_id=deps.org_id,
            asking_pod_id=deps.pod_id,
        )
        return steward, context, None

    async def _open_thread(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        ask: PodAsk,
        owner_user_id: UUID,
        teammate: Teammate,
        instructions: str | None,
    ) -> UUID:
        """The conversation in the other pod this ask continues, or a new one."""
        conversations = ConversationRepository(uow)
        thread_id = await PodAskQueries(uow).find_thread(
            to_pod_id=teammate.pod_id,
            from_conversation_id=ask.from_conversation_id,
            owner_user_id=owner_user_id,
            mode=ask.mode,
        )
        if thread_id is not None:
            thread = await conversations.get_conversation(thread_id, include_runs=True)
            if thread is not None and thread.status in BUSY:
                raise AskRefused(
                    f"{teammate.name} is still on your last request. Its answer "
                    "will arrive here; ask again after that."
                )
            previous = ask_of(thread)
            kept = replace(
                ask,
                delivered_run_id=previous.delivered_run_id if previous else None,
            )
            await conversations.set_conversation_metadata_key(
                thread_id, ASK_KEY, kept.to_metadata()
            )
            return thread_id
        created = await self._conversation_service(uow).create_conversation(
            pod_id=teammate.pod_id,
            agent_name=None,
            user_id=owner_user_id,
            instructions=instructions,
            metadata={"source": ASK_SOURCE, ASK_KEY: ask.to_metadata()},
        )
        return created.id

    async def _await_answer(
        self,
        *,
        teammate: Teammate,
        conversation_id: UUID,
        run_id: UUID | None,
        wait_seconds: float,
    ) -> AskOutcome:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(wait_seconds, 0.0)
        attempt = 0
        while True:
            outcome = await self._settled_outcome(
                teammate=teammate, conversation_id=conversation_id
            )
            if outcome is not None:
                return outcome
            if loop.time() >= deadline:
                return _outcome(
                    teammate,
                    conversation_id,
                    status="WORKING",
                    detail=(
                        f"{teammate.name} is working on it. End your turn: its "
                        "answer arrives in this conversation as a new message "
                        "when it is done, and you continue from there."
                    ),
                )
            attempt += 1
            await asyncio.sleep(
                poll_delay(
                    attempt,
                    base_seconds=_POLL_SECONDS,
                    remaining_seconds=deadline - loop.time(),
                )
            )

    async def _settled_outcome(
        self, *, teammate: Teammate, conversation_id: UUID
    ) -> AskOutcome | None:
        """The ask's outcome if it has one yet, taking delivery of the answer."""
        async with self.uow_factory() as uow:
            conversations = ConversationRepository(uow)
            thread = await conversations.get_conversation(
                conversation_id, include_runs=True
            )
            if thread is None:
                return _outcome(
                    teammate,
                    conversation_id,
                    status="UNFINISHED",
                    detail="Its conversation was deleted.",
                )
            if thread.status is ConversationStatus.WAITING:
                return _outcome(
                    teammate,
                    conversation_id,
                    status="WAITING",
                    detail=(
                        f"{teammate.name} is waiting on the person to answer "
                        "something in its conversation. Tell them; its answer "
                        "arrives here once they do."
                    ),
                )
            latest = thread.agent_runs[-1] if thread.agent_runs else None
            if latest is None or latest.status in ACTIVE_AGENT_RUN_STATUSES:
                return None
            if not await PodAskQueries(uow).claim_delivery(
                conversation_id=conversation_id, run_id=latest.id
            ):
                # The completion event got there first and posted the answer
                # into this conversation; the running turn reads it from there.
                return _outcome(
                    teammate,
                    conversation_id,
                    status="ANSWERED",
                    detail="Its answer has been added to this conversation.",
                )
            messages, _ = await conversations.list_messages(
                conversation_id=conversation_id, limit=ANSWER_WINDOW
            )
            await uow.commit()
        if latest.status is AgentRunStatus.COMPLETED:
            return _outcome(
                teammate,
                conversation_id,
                status="ANSWERED",
                answer=answer_from(messages, run_id=latest.id),
            )
        return _outcome(
            teammate, conversation_id, status="UNFINISHED", detail=latest.error
        )
