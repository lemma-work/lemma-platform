"""Where a proven person talks: one policy, whichever way they proved it.

Every way into a finished signup -- an email code, a recognised Telegram
contact, a returning sender with no route -- used to reach a different piece of
code with a different idea of what to do next. The email path ran the web's
first-workspace policy, which reuses a pod only if the person owns it alone and
otherwise *makes one*. For an existing user with pods on a plan with room for
two, that refused, `ensure_personal_workspace` answered None, an assertion
fired, and the person who had just typed their code got no reply at all while
the row sat on VERIFIED for good.

So this asks the same four questions in the same order for everyone:

1. Can they already be answered here? Then nothing is chosen for them -- the
   saved default and routing stay exactly as they were.
2. Is there exactly one pod they could talk to? Attach it, and say how to
   change it.
3. Several? Ask which.
4. None? Make one -- and if the plan has no room, say so in the plan's own
   words and wait for `new <name>`.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts.provisioning import ensure_pod_default_agent
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.onboarding_state import (
    OnboardingIngressResult,
    OnboardingStep,
    PendingState,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
)
from app.modules.agent_surfaces.services.onboarding_answers import OnboardingAnswers
from app.modules.agent_surfaces.services.onboarding_pod_choice import (
    ChoiceProblem,
    PodChoice,
    candidate_pods,
    has_somewhere_to_talk,
    offer_text,
    read_choice,
)
from app.modules.agent_surfaces.services.onboarding_replies import ready_message
from app.modules.agent_surfaces.services.onboarding_transport import (
    OnboardingTransport,
)
from app.modules.agent_surfaces.services.onboarding_workspace import (
    Attached,
    attach_chosen_workspace,
    bind_workspace,
    mark_ready,
    park_on_choice,
    record_verified_identity,
)
from app.modules.agent_surfaces.services.shared_chat_surface import (
    SharedSurfaceUnavailable,
    pods_blocked_for_shared_bot,
)
from app.modules.identity.contracts.onboarding import (
    ChallengeRejected,
    accept_chat_invitations,
    account_created_since,
    claim_chat_phone,
    ensure_chat_workspace,
)
from app.modules.pod.contracts.members import pod_name

#: Said when an installation's organization has not let them in yet.
WAITING_ON_ADMIN_MESSAGE = (
    "Your account is ready. Ask your team admin to add you to this Lemma organization."
)


@dataclass(frozen=True, slots=True)
class Settled:
    """What to say, and the step the reply is asking about (if it asks)."""

    message: str
    step: str | None = None


async def usable_pods(
    uows: UnitOfWorkFactory,
    transport: OnboardingTransport,
    user_id: UUID,
    *,
    excluding: UUID | None = None,
) -> list[dict[str, JsonValue]]:
    """The pods this conversation could be attached to, as they will be offered.

    On the shared bot, minus the pods whose assistant already answers on the
    platform through a connection of its own -- they cannot carry the shared
    number, so offering them is offering a refusal. `excluding` drops a pod that
    has just refused, in case the read raced the write that made it refuse.
    """
    async with uows() as uow:
        pods = await candidate_pods(
            uow, user_id=user_id, organization_id=transport.organization_id
        )
        blocked: set[UUID] = set()
        if transport.surface is None:
            blocked = await pods_blocked_for_shared_bot(
                uow,
                pod_ids=[UUID(str(pod["id"])) for pod in pods],
                platform=transport.event.platform,
            )
    if excluding is not None:
        blocked.add(excluding)
    return [pod for pod in pods if UUID(str(pod["id"])) not in blocked]


async def _reachable_already(
    uows: UnitOfWorkFactory, transport: OnboardingTransport, user_id: UUID
) -> bool:
    async with uows() as uow:
        return await has_somewhere_to_talk(
            uow,
            user_id=user_id,
            platform=transport.event.platform,
            parsed=transport.event,
            system_credentials_only=transport.surface is None,
            receiver_surface_ids=transport.receiver_surface_ids,
        )


async def settle_workspace(
    uows: UnitOfWorkFactory, transport: OnboardingTransport, state: PendingState
) -> Settled:
    """Finish a proven signup; see the module docstring for the order.

    Raises `ChallengeRejected` only for refusals with no next step inside this
    signup -- the phone or the platform identity proven by another live
    account, an account that cannot chat -- which the caller ends the signup on.
    """
    assert state.user_id is not None
    await claim_chat_phone(
        uows,
        user_id=state.user_id,
        verified_phone=state.verified_phone,
        full_name=transport.event.sender_display_name,
    )
    user = await record_verified_identity(uows, transport, state)
    new_account = await account_created_since(
        uows, user_id=user.id, since=state.created_at
    )
    if await _reachable_already(uows, transport, user.id):
        async with uows() as uow:
            await mark_ready(uow, state)
        return Settled(ready_message(new_account=new_account))
    invited_pod_id = await accept_chat_invitations(uows, user_id=user.id)
    pods = await usable_pods(uows, transport, user.id)
    if len(pods) == 1:
        return await _attach_only(
            uows, transport, state, pods[0], new_account, invited_pod_id
        )
    if pods:
        return Settled(
            await park_on_choice(uows, state, pods), OnboardingStep.AWAITING_POD
        )
    return await _make_first(uows, transport, state, new_account)


async def _attach_only(
    uows: UnitOfWorkFactory,
    transport: OnboardingTransport,
    state: PendingState,
    pod: dict[str, JsonValue],
    new_account: bool,
    invited_pod_id: UUID | None,
) -> Settled:
    """Their one usable pod: nothing to ask, so attach it and say where to change it."""
    pod_id = UUID(str(pod["id"]))
    try:
        attached = await attach_chosen_workspace(
            uows, transport, state, PodChoice(pod_id=pod_id)
        )
    except SharedSurfaceUnavailable as conflict:
        return await _offer_others(uows, transport, state, conflict)
    if isinstance(attached, str):
        return Settled(attached)
    invited = attached.pod_name if attached.pod_id == invited_pod_id else None
    return Settled(
        ready_message(
            invited,
            new_account=new_account,
            connected_pod_name=None if invited else attached.pod_name,
        )
    )


async def _offer_others(
    uows: UnitOfWorkFactory,
    transport: OnboardingTransport,
    state: PendingState,
    conflict: SharedSurfaceUnavailable,
) -> Settled:
    """A pod refused the shared bot: ask about the rest, leading with why."""
    assert state.user_id is not None
    pods = await usable_pods(uows, transport, state.user_id, excluding=conflict.pod_id)
    question = await park_on_choice(uows, state, pods, lead=conflict.message)
    return Settled(question, OnboardingStep.AWAITING_POD)


async def _make_first(
    uows: UnitOfWorkFactory,
    transport: OnboardingTransport,
    state: PendingState,
    new_account: bool,
) -> Settled:
    """No pod to use: run the first-workspace policy, and answer what it says.

    It can say three things and each gets words. "Ready" binds the pod.
    "Organization access required" waits on an admin. "Pod limit reached" --
    the case that used to be an assertion -- parks on the workspace question
    with the plan's own sentence in front, so `new <name>` works the moment
    they have made room, and anything else re-asks rather than re-refusing.
    """
    assert state.user_id is not None
    workspace = await ensure_chat_workspace(
        uows,
        user_id=state.user_id,
        installation_organization_id=transport.organization_id,
    )
    if workspace.status == "organization_access_required":
        async with uows() as uow:
            pending = await uow.session.get(PendingChatOnboarding, state.id)
            assert pending is not None
            pending.step = OnboardingStep.ORGANIZATION_ACCESS_REQUIRED
        return Settled(WAITING_ON_ADMIN_MESSAGE)
    if workspace.status == "pod_limit_reached" or workspace.pod_id is None:
        question = await park_on_choice(
            uows,
            state,
            [],
            lead=workspace.refusal or "There is no room for a workspace.",
        )
        return Settled(question, OnboardingStep.AWAITING_POD)
    try:
        return await _bind_made(uows, transport, state, workspace.pod_id, new_account)
    except SharedSurfaceUnavailable as conflict:
        return await _offer_others(uows, transport, state, conflict)


async def _bind_made(
    uows: UnitOfWorkFactory,
    transport: OnboardingTransport,
    state: PendingState,
    pod_id: UUID,
    new_account: bool,
) -> Settled:
    assert state.user_id is not None
    async with uows() as uow:
        assistant_id = await ensure_pod_default_agent(
            uow, pod_id=pod_id, user_id=state.user_id
        )
        refusal = await bind_workspace(
            uow,
            transport=transport,
            state=state,
            user_id=state.user_id,
            pod_id=pod_id,
            assistant_id=assistant_id,
        )
        if refusal is not None:
            return Settled(refusal)
        await mark_ready(uow, state)
        name = await pod_name(uow.session, pod_id)
    return Settled(ready_message(new_account=new_account, connected_pod_name=name))


class OnboardingSettle(OnboardingAnswers):
    """The steps that decide where a proven person talks.

    On top of the answers rather than beside them: one chain, a link per file,
    so the coordinator is built by single inheritance and nobody has to work out
    which of two parents a method came from.
    """

    async def _settle(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> None:
        try:
            settled = await settle_workspace(self._uows, transport, state)
        except ChallengeRejected as error:
            await self._refused(transport, state, destination, error)
            return
        await self._reply(transport, destination, settled.message, step=settled.step)

    async def _pod(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        """Read which workspace they picked, and wire the chat to it.

        An unreadable answer re-asks, saying what was wrong, rather than
        guesses: the cost of guessing is a conversation attached to somebody
        else's workspace.
        """
        offered = state.offered_pods or []
        choice = read_choice(transport.event.message_text, offered)
        if isinstance(choice, ChoiceProblem):
            await self._ask_workspace_choice(
                transport, destination, choice.message, offered
            )
            return OnboardingIngressResult(True)
        try:
            attached = await attach_chosen_workspace(
                self._uows, transport, state, choice
            )
        except SharedSurfaceUnavailable as conflict:
            # Before `ChallengeRejected`, which it is: this refusal has a next
            # step, and ending the signup on it would throw that away.
            settled = await _offer_others(self._uows, transport, state, conflict)
            await self._reply(
                transport, destination, settled.message, step=settled.step
            )
            return OnboardingIngressResult(True)
        except ChallengeRejected as error:
            # `attach_chosen_workspace` records the identity first, so the
            # permanent refusals reach here as well -- and a question is no
            # better an exit than a step.
            await self._refused(transport, state, destination, error)
            return OnboardingIngressResult(True)
        if isinstance(attached, str):
            await self._ask_workspace_choice(transport, destination, attached, offered)
            return OnboardingIngressResult(True)
        # The row is READY by now and the ready event is published, so this
        # reply is not guarded the way `_email` is: the replay the event starts
        # answers the person's original message either way.
        assert state.user_id is not None
        new_account = await account_created_since(
            self._uows, user_id=state.user_id, since=state.created_at
        )
        await self._reply(
            transport,
            destination,
            ready_message(
                new_account=new_account, connected_pod_name=attached.pod_name
            ),
        )
        return OnboardingIngressResult(True)

    async def _ask_workspace_choice(
        self,
        transport: OnboardingTransport,
        destination: ParsedInboundSurfaceEvent,
        reason: str,
        offered: list[dict[str, JsonValue]],
    ) -> None:
        await self._reply(
            transport,
            destination,
            offer_text(offered, lead=reason),
            step=OnboardingStep.AWAITING_POD,
        )


__all__ = ["Attached", "OnboardingSettle", "Settled", "settle_workspace", "usable_pods"]
