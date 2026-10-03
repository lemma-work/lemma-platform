"""The writes that end a chat signup: who they are, and where they will talk.

`onboarding_settle` decides *which* of these happens -- already reachable,
attach the one pod, ask which, or make one. This module does them, each in its
own unit of work, and each leaving the pending row on a step that says what is
true afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts.provisioning import ensure_pod_default_agent
from app.modules.agent_surfaces.domain.events import SurfaceOnboardingReadyEvent
from app.modules.agent_surfaces.domain.onboarding_state import (
    OnboardingStep,
    PendingState,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.services.onboarding_pod_choice import (
    PodChoice,
    candidate_pods,
    offer_text,
    organization_for_new_pod,
)
from app.modules.agent_surfaces.services.onboarding_transport import (
    OnboardingTransport,
)
from app.modules.agent_surfaces.services.shared_chat_surface import (
    SharedSurfaceUnavailable,
    ensure_shared_surface,
    make_default,
)
from app.modules.identity.contracts.onboarding import (
    ChallengeRejected,
    UserEntity,
    active_chat_user,
    ensure_chat_organization,
)
from app.modules.pod.contracts.members import pod_name
from app.modules.pod.contracts.personal_workspace import (
    PodLimitReachedError,
    create_named_workspace,
)
from pydantic import JsonValue

__all__ = [
    "Attached",
    "SharedSurfaceUnavailable",
    "attach_chosen_workspace",
    "bind_workspace",
    "mark_ready",
    "park_on_choice",
    "record_verified_identity",
]


@dataclass(frozen=True, slots=True)
class Attached:
    """The conversation is wired to this pod; the confirmation names it."""

    pod_id: UUID
    pod_name: str


async def park_on_choice(
    uows: UnitOfWorkFactory,
    state: PendingState,
    pods: list[dict[str, JsonValue]],
    *,
    lead: str | None = None,
) -> str:
    """Move the signup onto the workspace question, and return the question.

    One way onto AWAITING_POD for every reason there is to ask: several
    workspaces to choose from, a workspace that just refused the shared bot, a
    plan with no room for the one they would have been given. Before this the
    refusals were dead ends wearing an instruction -- the step stayed where it
    was, so the next message re-ran the same provisioning and was refused again
    in the same words, with nothing reading the answer because nothing had
    asked a question.
    """
    assert state.user_id is not None
    async with uows() as uow:
        row = await uow.session.get(PendingChatOnboarding, state.id)
        assert row is not None
        row.step = OnboardingStep.AWAITING_POD
        row.user_id = state.user_id
        row.offered_pods = pods
    return offer_text(pods, lead=lead)


async def mark_ready(uow: SqlAlchemyUnitOfWork, state: PendingState) -> None:
    """READY, and the replay of the held message set going -- exactly once."""
    pending = await uow.session.get(PendingChatOnboarding, state.id)
    assert pending is not None
    pending.step = OnboardingStep.READY
    pending.offered_pods = None
    if pending.ready_at is None:
        pending.ready_at = datetime.now(timezone.utc)
        uow.collect_events([SurfaceOnboardingReadyEvent(pending_id=pending.id)])


async def record_verified_identity(
    uows: UnitOfWorkFactory, transport: OnboardingTransport, state: PendingState
) -> UserEntity:
    """Write down who this is, before anything tries to find them a desk.

    Its own unit of work, and that is the whole point. This used to share one
    with destination allocation, and allocation can refuse -- a workspace whose
    assistant already reaches the platform on its own connection -- so the
    rollback that undid the destination also undid the proof of identity.
    The next message found no binding and started signup over, asking a person
    who had just given an email code to give another one. `PS-SURF-005` promises
    they resume "without asking for another email code, while their verified
    identity holds", and the identity held; only the write did not.

    Committing it early loses nothing. An identity with no destination is
    exactly the recognised-with-nowhere-to-talk state the workspace choice
    exists to answer, and the row is shaped to say so -- both destination
    columns are nullable, and `is_routable` reads that pair as "no route".

    A phone is only ever *replaced* by fresh proof, never cleared by its
    absence. A pending row carries one when a challenge just supplied it -- a
    WhatsApp sender's own number, a Telegram contact share -- and carries none
    when this is a returning person changing workspaces, because there was
    nothing to verify. Assigning it unconditionally wrote that nothing over a
    live proof, and it is `resolve_shared_verified_identity`, on the *ingestion*
    side, that then stopped recognising them.
    """
    assert state.user_id is not None
    async with uows() as uow:
        user = await active_chat_user(uow, state.user_id)
        if user is None:
            raise ChallengeRejected("This account cannot chat")
        identity = await uow.session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.binding_key == state.binding_key
            )
        )
        if identity is not None and identity.user_id != user.id:
            await _take_over(uow, identity, user.id)
        if identity is None:
            identity = VerifiedSurfaceIdentity(
                binding_key=state.binding_key,
                platform=transport.event.platform.value,
                tenant_id=transport.event.tenant_id or "",
                external_user_id=transport.event.sender_external_user_id or "",
                user_id=user.id,
            )
            uow.session.add(identity)
        if state.verified_phone:
            identity.verified_phone = state.verified_phone
        identity.revoked_at = None
    return user


async def _take_over(
    uow: SqlAlchemyUnitOfWork, identity: VerifiedSurfaceIdentity, user_id: UUID
) -> None:
    """Hand a binding to the account that just proved it, if nobody else holds it.

    Revoked is not "somebody else's", it is "nobody's". `verified_sender` reads
    a revoked row as an unrecognised sender and `recognize_sender` sends them
    through a fresh signup on purpose; refusing here made that invitation a
    trap. The commonest way in is a reused phone number or a handed-on work
    account: the platform actor is the same string, the person is not.

    The same goes for a binding whose account has gone -- deleted or
    deactivated. Nobody can sign in as that account, so refusing on its behalf
    stranded the person holding the phone now with an instruction nobody could
    follow. Only a live binding of a live account is somebody else's.

    Nothing of the previous owner's survives the reassignment. The destination
    columns are cleared -- `ck_surface_identity_route_is_live` refuses a revoked
    row that carries one, and a dead account's route is not this person's --
    and so is the phone, which `resolve_shared_verified_identity` would
    otherwise keep matching for the wrong person.
    """
    if (
        identity.revoked_at is None
        and await active_chat_user(uow, identity.user_id) is not None
    ):
        raise ChallengeRejected("This platform identity belongs to another account")
    identity.user_id = user_id
    identity.verified_phone = None
    identity.installation_surface_id = None
    identity.pod_id = None


async def bind_workspace(
    uow: SqlAlchemyUnitOfWork,
    *,
    transport: OnboardingTransport,
    state: PendingState,
    user_id: UUID,
    pod_id: UUID,
    assistant_id: UUID,
) -> str | None:
    """Point this conversation at a pod, the way its transport routes.

    A company installation routes a private message by the identity row, so the
    destination is written there. The shared bot routes by the person's
    default surface, so the pod gets its shared surface and the default names
    it -- always, which is what lets the profile show which pod answers and
    keeps routing's oldest-pod tiebreak from choosing for someone who chose.

    Returns a message when the binding has gone in the meantime.
    """
    if transport.surface is None:
        surface = await ensure_shared_surface(
            uow,
            pod_id=pod_id,
            assistant_id=assistant_id,
            platform=transport.event.platform,
        )
        await make_default(
            uow,
            user_id=user_id,
            platform=transport.event.platform,
            surface_id=surface.id,
        )
        return None
    identity = await uow.session.scalar(
        select(VerifiedSurfaceIdentity).where(
            VerifiedSurfaceIdentity.binding_key == state.binding_key,
            VerifiedSurfaceIdentity.revoked_at.is_(None),
        )
    )
    if identity is None:
        # Recorded moments ago and not revoked since, so this is a race with a
        # revocation rather than a missing binding -- and a revocation means
        # exactly what this says.
        return "That account needs to verify again before it can chat."
    identity.installation_surface_id = transport.surface.id
    identity.pod_id = pod_id
    return None


async def _organization_for(
    uow: SqlAlchemyUnitOfWork,
    *,
    user: UserEntity,
    transport: OnboardingTransport,
    placement: tuple[UUID, UUID] | None,
) -> UUID | None:
    """Where a newly named workspace goes, provisioning one where that is allowed.

    Two different nothings hid behind one refusal. Somebody outside a company's
    installation genuinely has to be added by an administrator -- that boundary
    is the point of the installation. Somebody arriving on the *shared* bot with
    a personal address and no organization at all was told the same thing, and
    there was no administrator to ask. For them this runs the ordinary
    first-workspace policy, which is what the web signup would have done.
    """
    if placement is not None:
        return placement[0]
    if transport.organization_id is not None:
        return None
    return await ensure_chat_organization(uow, user_id=user.id)


async def _new_pod(
    uow: SqlAlchemyUnitOfWork,
    *,
    user: UserEntity,
    transport: OnboardingTransport,
    name: str,
) -> UUID | str:
    placement = await organization_for_new_pod(
        uow, user_id=user.id, installation_organization_id=transport.organization_id
    )
    organization_id = await _organization_for(
        uow, user=user, transport=transport, placement=placement
    )
    if organization_id is None:
        return (
            "That account is not in any Lemma organization yet, so there "
            "is nowhere to put a workspace. Ask your admin to add you."
        )
    try:
        made = await create_named_workspace(
            uow, organization_id=organization_id, owner_user_id=user.id, name=name
        )
    except PodLimitReachedError as refused:
        return refused.message
    return made.pod_id


async def attach_chosen_workspace(
    uows: UnitOfWorkFactory,
    transport: OnboardingTransport,
    state: PendingState,
    choice: PodChoice,
) -> Attached | str:
    """Wire this conversation to the workspace the person picked (or was given).

    For someone who already had an account: no organization is provisioned and
    no pod is invented unless they named one. Returns the pod on success and a
    message to send back when the choice could not be honoured. Raises
    `SharedSurfaceUnavailable` when the pod cannot carry the shared bot, which
    the caller answers by offering the others.

    The proof of identity is recorded first, on the same reasoning as in
    `record_verified_identity`: every way here carries a *proven* `user_id`,
    and only a recorded one survives a refusal further down.
    """
    assert state.user_id is not None
    user = await record_verified_identity(uows, transport, state)
    async with uows() as uow:
        if choice.pod_id is None:
            assert choice.new_name is not None
            made = await _new_pod(
                uow, user=user, transport=transport, name=choice.new_name
            )
            if isinstance(made, str):
                return made
            pod_id = made
        else:
            pod_id = choice.pod_id
            # Re-proving membership rather than trusting the stored list: the
            # offer was written when the question was asked, and access can be
            # taken away between a question and its answer.
            allowed = await candidate_pods(
                uow,
                user_id=user.id,
                organization_id=transport.organization_id,
                limit=None,
            )
            if not any(UUID(str(item["id"])) == pod_id for item in allowed):
                return "That workspace is no longer available. Pick another."
        assistant_id = await ensure_pod_default_agent(
            uow, pod_id=pod_id, user_id=user.id
        )
        refusal = await bind_workspace(
            uow,
            transport=transport,
            state=state,
            user_id=user.id,
            pod_id=pod_id,
            assistant_id=assistant_id,
        )
        if refusal is not None:
            return refusal
        await mark_ready(uow, state)
        return Attached(pod_id, await pod_name(uow.session, pod_id) or "your workspace")
