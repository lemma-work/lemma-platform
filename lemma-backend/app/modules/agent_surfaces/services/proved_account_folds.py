"""A Telegram account its owner proved: the contacts the pods made for it are them.

A member whose account Lemma did not know was, in a group, a contact. Once they
prove the account (``telegram_identity_link``), in each pod they are a member
of the contact holding that account becomes them (``contacts``'
``fold_into_member``): its lines and seats in the pod's groups are theirs, and
it stops being listed as somebody outside. Done when they confirm, and again
when they join a pod later, since that pod may know the account as a stranger.

A contact that held other handles too keeps them and stays a contact; only the
Telegram account is taken from it, and the pod's administrators are told, so a
person can look at the rest.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.verified_surface_identity_repository import (  # noqa: E501
    VerifiedSurfaceIdentityRepository,
)
from app.modules.agent_surfaces.services.direct_messages import tell_member
from app.modules.contacts.contracts import IdentityKind, fold_into_member
from app.modules.pod.contracts.members import pod_administrators


async def fold_proved_account(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    external_user_id: str,
    pods: list[UUID] | None = None,
) -> None:
    """The contacts holding this Telegram account, in ``pods`` (every pod the
    person is a member of, when not said), become them."""
    member_of = (
        pods
        if pods is not None
        else await SqlAlchemySurfaceRoutingResolutionAdapter(uow).get_user_pod_ids(
            user_id
        )
    )
    folds = await fold_into_member(
        uow,
        kind=IdentityKind.TELEGRAM,
        value=external_user_id,
        user_id=user_id,
        pods=set(member_of),
    )
    for fold in folds:
        if fold.folded:
            continue
        for admin in await pod_administrators(uow, fold.pod_id):
            await tell_member(
                uow,
                pod_id=fold.pod_id,
                user_id=admin.user_id,
                text=(
                    "A member proved that a Telegram account in Contacts is "
                    "theirs, so it was taken from that contact. The contact "
                    "keeps their other ways to be reached; look at them in "
                    "Contacts if they are the same person."
                ),
                key=f"proved-account-detached:{fold.contact_id}:{admin.user_id}",
            )


async def proved_telegram_accounts(
    uow: SqlAlchemyUnitOfWork, user_id: UUID
) -> list[str]:
    """The Telegram accounts this person proved from a Lemma session, still theirs."""
    return await VerifiedSurfaceIdentityRepository(uow).session_proved_accounts(
        user_id, SurfacePlatform.TELEGRAM.value
    )
