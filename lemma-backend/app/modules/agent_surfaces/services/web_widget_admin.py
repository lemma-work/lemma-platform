"""A pod's web widgets, as the people who configure them change them.

The HTTP routes used to build the repository themselves; this is the seam they
go through instead. It decides nothing an HTTP status depends on beyond
"no such widget", which it answers with ``None`` or ``False`` so the route keeps
its own 404.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.web_widgets import (
    WebWidget,
    WidgetAnswer,
    mint_public_key,
    mint_secret,
)
from app.modules.agent_surfaces.infrastructure.repositories.web_widget_repository import (  # noqa: E501
    WebWidgetRepository,
)
from app.modules.agent_surfaces.services.widget_directory import forget_widget
from app.modules.contacts.contracts.visitor_sessions import (
    VisitorStrength,
    forget_session_liveness,
    revoke_visitor_sessions,
)


@dataclass(frozen=True, slots=True)
class CreatedWidget:
    widget: WebWidget
    #: Shown once, at creation; stored encrypted.
    signing_secret: str


class WebWidgetAdmin:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self._widgets = WebWidgetRepository(uow.session)

    async def list(self, pod_id: UUID) -> list[WebWidget]:
        return await self._widgets.list(pod_id=pod_id)

    async def create(
        self,
        *,
        pod_id: UUID,
        agent_id: UUID,
        name: str,
        allowed_origins: list[str],
        answer: WidgetAnswer,
        looked_after_by: UUID | None,
        host_issuer: str,
    ) -> CreatedWidget:
        secret = mint_secret()
        widget = await self._widgets.create(
            pod_id=pod_id,
            agent_id=agent_id,
            name=name,
            public_key=mint_public_key(),
            secret=secret,
            allowed_origins=allowed_origins,
            answer=answer,
            looked_after_by=looked_after_by,
            host_issuer=host_issuer,
        )
        await self.uow.commit()
        return CreatedWidget(widget=widget, signing_secret=secret)

    async def update(
        self, *, pod_id: UUID, widget_id: UUID, values: Mapping[str, object]
    ) -> WebWidget | None:
        widget = await self._widgets.update(
            pod_id=pod_id, widget_id=widget_id, values=dict(values)
        )
        if widget is None:
            return None
        await self.uow.commit()
        # Every replica reads the widget through a short cache; drop it so the
        # change reaches the next request rather than the next expiry.
        await forget_widget(widget.public_key)
        return widget

    async def reissue_secret(self, *, pod_id: UUID, widget_id: UUID) -> str | None:
        """A new signing secret, or None for no such widget.

        The sessions the old secret started end with it: a host session is the
        old secret's word for who somebody is, and a secret is reissued because
        that word is no longer trusted.
        """
        if await self._widgets.get(pod_id=pod_id, widget_id=widget_id) is None:
            return None
        secret = mint_secret()
        await self._widgets.rotate_secret(widget_id=widget_id, secret=secret)
        ended = await revoke_visitor_sessions(
            self.uow, widget_id=widget_id, strength=VisitorStrength.HOST
        )
        await self.uow.commit()
        await forget_session_liveness(ended)
        return secret

    async def delete(self, *, pod_id: UUID, widget_id: UUID) -> bool:
        widget = await self._widgets.get(pod_id=pod_id, widget_id=widget_id)
        if widget is None or not await self._widgets.delete(
            pod_id=pod_id, widget_id=widget_id
        ):
            return False
        await self.uow.commit()
        await forget_widget(widget.public_key)
        return True
