"""`/start link_<token>`: bind a Telegram chat to the user who minted the link.

The other way into a signed-in account from the shared Telegram bot. Signup
proves who a stranger is with a contact share and an emailed code; this proves
it with a link the Lemma app minted for its signed-in user, which Telegram
hands back as the `/start` payload when that user presses Start. Whoever sends
it is the person who opened the link -- the payload never leaves their own
Telegram -- so the chat becomes theirs without an email ever being sent.

It lands on the same rails signup finishes on: a pending row with the user set,
the workspace attached by `onboarding_workspace`, READY and the ready event, and
the replay of a plain `/start` that `telegram_command_service` answers with the
greeting. Nothing downstream can tell the two proofs apart except the identity
row's `proof`, which is the one place they should differ.

Runs before the state machine, on every private message that carries a link,
so a chat that is already linked can be relinked or pointed at another pod by
pressing the button again.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.onboarding_state import (
    IdentityProof,
    OnboardingIngressResult,
    OnboardingStep,
    PendingState,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
)
from app.modules.agent_surfaces.services.onboarding_outcomes import (
    OnboardingOutcomes,
)
from app.modules.agent_surfaces.services.onboarding_pod_choice import (
    PodChoice,
    candidate_pods,
    offer_text,
)
from app.modules.agent_surfaces.services.onboarding_replies import (
    LINK_BUTTON_PATH,
    say_privately,
)
from app.modules.agent_surfaces.services.onboarding_sender import (
    create_pending,
    require_state,
)
from app.modules.agent_surfaces.services.onboarding_transport import (
    OnboardingTransport,
)
from app.modules.agent_surfaces.services.onboarding_workspace import (
    WorkspaceChoiceAsked,
    attach_chosen_workspace,
    complete_onboarding_workspace,
)
from app.modules.agent_surfaces.services.telegram_link_tokens import (
    TelegramLinkGrant,
    TelegramLinkTokenStore,
    link_token_from_start,
)
from app.modules.identity.contracts.onboarding import (
    ChallengeRejected,
    active_chat_user,
)
from app.modules.pod.contracts.members import pod_name

EXPIRED_LINK_MESSAGE = (
    f"That link has expired or was already used. Open {LINK_BUTTON_PATH} again "
    "for a fresh one."
)


def link_token_in(event: ParsedInboundSurfaceEvent) -> str | None:
    """The link token a private Telegram `/start` carries, if it carries one.

    Private chats only: in a group the bot sees `/start` from anyone in the
    room, and a link pasted there would bind whoever pressed it first.
    """
    if event.platform != SurfacePlatform.TELEGRAM or not event.is_dm:
        return None
    return link_token_from_start(event.message_text)


def _without_token(event: ParsedInboundSurfaceEvent) -> ParsedInboundSurfaceEvent:
    """The event as it is kept: a bare `/start`, and no raw update behind it.

    Kept as the request to replay and as the reply destination, so the token
    would otherwise sit in the pending row long after it was spent. A bare
    `/start` is also exactly what the replay should say: it is what
    `telegram_command_service` answers with the agent's greeting.
    """
    return event.model_copy(update={"message_text": "/start", "raw_payload": {}})


class TelegramChatLinker:
    def __init__(
        self,
        *,
        uows: UnitOfWorkFactory,
        adapters: SurfacePlatformAdapterRegistry,
        outcomes: OnboardingOutcomes,
        tokens: TelegramLinkTokenStore,
    ) -> None:
        self._uows = uows
        self._adapters = adapters
        self._outcomes = outcomes
        self._tokens = tokens

    async def _say(
        self,
        transport: OnboardingTransport,
        destination: ParsedInboundSurfaceEvent,
        message: str,
        *,
        step: str | None = None,
    ) -> None:
        await say_privately(
            self._adapters, self._uows, transport, destination, message, step=step
        )

    async def link(
        self, transport: OnboardingTransport, token: str
    ) -> OnboardingIngressResult:
        destination = _without_token(transport.event)
        grant = await self._tokens.consume(token)
        if grant is None:
            await self._say(transport, destination, EXPIRED_LINK_MESSAGE)
            return OnboardingIngressResult(True)
        state = await self._pending(transport, destination, grant)
        try:
            if grant.pod_id is not None:
                refusal = await attach_chosen_workspace(
                    self._uows,
                    transport,
                    state,
                    PodChoice(pod_id=grant.pod_id),
                    proof=IdentityProof.LINK_TOKEN,
                )
                if refusal is not None:
                    await self._offer_other_pods(transport, destination, state, refusal)
                    return OnboardingIngressResult(True)
            else:
                outcome = await complete_onboarding_workspace(
                    self._uows, transport, state, proof=IdentityProof.LINK_TOKEN
                )
                if outcome.waiting_on_an_admin:
                    await self._say(
                        transport,
                        destination,
                        "Your account is linked. Ask your team admin to add you "
                        "to this Lemma organization.",
                    )
                    return OnboardingIngressResult(True)
        except WorkspaceChoiceAsked as parked:
            await self._say(
                transport, destination, parked.message, step=OnboardingStep.AWAITING_POD
            )
            return OnboardingIngressResult(True)
        except ChallengeRejected as error:
            await self._outcomes.refused(transport, state, destination, error)
            return OnboardingIngressResult(True)
        await self._say(transport, destination, await self._linked_message(grant))
        return OnboardingIngressResult(True)

    async def _pending(
        self,
        transport: OnboardingTransport,
        destination: ParsedInboundSurfaceEvent,
        grant: TelegramLinkGrant,
    ) -> PendingState:
        """A fresh pending row for this chat, already verified as the grant's user.

        Fresh even for a chat mid-signup or long since linked: the link is a
        complete answer to every question signup could still be asking, and
        `create_pending` replaces whatever row the binding had.
        """
        state = await create_pending(self._uows, transport, destination)
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.user_id = grant.user_id
            row.step = OnboardingStep.VERIFIED
            row.destination = destination.model_dump(mode="json")
        return await require_state(self._uows, transport.binding_key)

    async def _offer_other_pods(
        self,
        transport: OnboardingTransport,
        destination: ParsedInboundSurfaceEvent,
        state: PendingState,
        refusal: str,
    ) -> None:
        """The chosen pod could not be used; ask which one instead.

        The identity is already recorded by then, so this is the same question
        a recognised sender with nowhere to talk is asked, and the coordinator's
        AWAITING_POD step reads the answer.
        """
        assert state.user_id is not None
        async with self._uows() as uow:
            pods = await candidate_pods(uow, user_id=state.user_id)
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.step = OnboardingStep.AWAITING_POD
            row.offered_pods = pods
        await self._say(
            transport,
            destination,
            f"{refusal}\n\n{offer_text(pods)}",
            step=OnboardingStep.AWAITING_POD,
        )

    async def _linked_message(self, grant: TelegramLinkGrant) -> str:
        """Say whose account this chat now belongs to.

        Named outright because a link can be forwarded: whoever pressed Start
        should see at once which account they just connected to, and a chat
        that landed on somebody else's account says so in its first line.
        """
        async with self._uows() as uow:
            user = await active_chat_user(uow, grant.user_id)
            where = (
                await pod_name(uow.session, grant.pod_id)
                if grant.pod_id is not None
                else None
            )
        who = str(user.email) if user is not None else "your Lemma account"
        return f"This chat is now linked to {who}" + (
            f" and answers from {where}." if where else "."
        )
