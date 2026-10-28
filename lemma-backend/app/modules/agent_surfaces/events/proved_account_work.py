"""Someone joined a pod: a contact it made for their proved Telegram account is them.

Subscribed to the pod's ``PodMemberAdded``. A member who proved their Telegram
account before joining this pod may have spoken in one of its groups as a
stranger; the contact the pod made for the account becomes them now
(``services/proved_account_folds``). A FAST item: main-database statements only.
"""

from __future__ import annotations

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
)
from app.core.infrastructure.work import (
    registry as default_registry,
)
from app.modules.agent_surfaces.services.proved_account_folds import (
    fold_proved_account,
    proved_telegram_accounts,
)
from app.modules.pod.contracts.lifecycle import PodMemberAdded

SURFACES_PROVED_ACCOUNT_JOINED = "surfaces.proved_account_joined"


async def fold_on_joining(ctx: FastContext, payload: PodMemberAdded) -> None:
    for account in await proved_telegram_accounts(ctx.uow, payload.user_id):
        await fold_proved_account(
            ctx.uow,
            user_id=payload.user_id,
            external_user_id=account,
            pods=[payload.pod_id],
        )


def register_proved_account_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACES_PROVED_ACCOUNT_JOINED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=PodMemberAdded,
    )(fold_on_joining)


register_proved_account_work(default_registry)
facts.subscribe(
    PodMemberAdded,
    kind=SURFACES_PROVED_ACCOUNT_JOINED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"surfaces-proved-account-joined:{fact.pod_id}:{fact.user_id}",
)
