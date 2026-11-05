"""Apps on the ``org`` topic: an app appearing in a pod's list, or leaving it.

Each app is told to who may read it -- ``app.read`` on the app, its owner and
visibility weighed exactly as the app list weighs them -- as ``app.added``
when it first is theirs to see, and ``app.removed`` once it is not: deleted,
or made personal or restricted away from them. Creating, changing or deleting
an app says when to read (``apps``, ``infrastructure/repositories.py``); a
grant on a restricted app moves the pod's grants epoch, which has the topic
weigh every reader again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import select

from app.core.authorization.context import ResourceRef, ResourceType, ResourceVisibility
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.org_feeds import Audience, OrgItem, PodItems
from app.modules.apps.infrastructure.models import AppModel

#: The pod signal an app's row moving sends.
APPS_SIGNAL = "apps"
#: Most apps one read returns, across the pods it covers.
MAX_APPS = 5000


def _listed(
    pod_id: UUID, app_id: UUID, name: str, owner: UUID, visibility: str
) -> OrgItem:
    said = {"app_id": str(app_id), "name": name}
    return OrgItem(
        audience=Audience(
            permission=Permissions.APP_READ,
            resource=ResourceRef(
                resource_type=ResourceType.APP,
                resource_id=app_id,
                pod_id=pod_id,
                owner_user_id=owner,
                visibility=ResourceVisibility(visibility),
            ),
        ),
        version="listed",
        held={"type": "app.added", **said},
        ended={"type": "app.removed", **said},
    )


class PodApps:
    name = "apps.listed"
    kinds = frozenset({APPS_SIGNAL})

    async def read(self, pod_ids: Sequence[UUID]) -> Mapping[UUID, PodItems]:
        app = AppModel
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            rows = await uow.session.execute(
                select(app.pod_id, app.id, app.name, app.user_id, app.visibility)
                .where(app.pod_id.in_(sorted(set(pod_ids))))
                .order_by(app.id)
                .limit(MAX_APPS)
            )
        found: dict[UUID, dict[str, OrgItem]] = {}
        for pod_id, app_id, name, owner, visibility in rows:
            found.setdefault(pod_id, {})[str(app_id)] = _listed(
                pod_id, app_id, name, owner, visibility
            )
        return found


__all__ = ["APPS_SIGNAL", "PodApps"]
